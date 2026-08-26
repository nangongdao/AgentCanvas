"""D1 workflow snapshot lifecycle and execution provenance tests."""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_auth import (
    EDITOR_TOKEN,
    VIEWER_TOKEN,
    auth_settings,
    bearer,
    workflow_body,
)


def _wait_status(client: TestClient, execution_id: str, expected: set[str]) -> dict:
    deadline = time.monotonic() + 15
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/executions/{execution_id}", headers=bearer(VIEWER_TOKEN))
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] in expected:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"execution did not reach {expected}: {latest}")


def _human_workflow() -> dict:
    body = workflow_body()
    body["name"] = "Versioned human workflow"
    body["dsl"]["name"] = body["name"]
    body["dsl"]["nodes"] = [
        {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
        {
            "id": "approve",
            "type": "human",
            "position": {"x": 200, "y": 0},
            "config": {"title": "Approve", "instruction": "Continue?"},
        },
        {
            "id": "end",
            "type": "end",
            "position": {"x": 400, "y": 0},
            "config": {"output_template": {"snapshot": "one: {{nodes.approve.approved}}"}},
        },
    ]
    body["dsl"]["edges"] = [
        {"id": "e1", "source": "start", "target": "approve"},
        {"id": "e2", "source": "approve", "target": "end"},
    ]
    return body


def test_version_publish_diff_rollback_clone_and_rbac(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        viewer = bearer(VIEWER_TOKEN)
        created = client.post("/api/workflows", headers=editor, json=workflow_body())
        assert created.status_code == 201, created.text
        workflow = created.json()
        workflow_id = workflow["id"]

        initial = client.get(f"/api/workflows/{workflow_id}/versions", headers=viewer)
        assert initial.status_code == 200, initial.text
        version_1 = initial.json()[0]
        assert (version_1["number"], version_1["status"]) == (1, "draft")

        updated_dsl = deepcopy(workflow["dsl"])
        updated_dsl["nodes"][1]["position"]["x"] = 240
        updated = client.put(
            f"/api/workflows/{workflow_id}",
            headers=editor,
            json={
                "dsl": updated_dsl,
                "version": 1,
                "change_summary": "Move the end node",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 2

        versions = client.get(f"/api/workflows/{workflow_id}/versions", headers=viewer).json()
        version_2, immutable_version_1 = versions
        assert version_2["status"] == "draft"
        assert immutable_version_1["status"] == "archived"
        assert immutable_version_1["dsl"] == version_1["dsl"]

        published = client.post(f"/api/workflows/{workflow_id}/publish", headers=editor)
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"

        next_dsl = deepcopy(updated.json()["dsl"])
        next_dsl["nodes"][1]["position"]["x"] = 320
        next_dsl["edges"][0]["label"] = "success"
        next_dsl["canvas"] = {
            "groups": [
                {
                    "id": "group-main",
                    "name": "Main path",
                    "position": {"x": -24, "y": -52},
                    "width": 520,
                    "height": 220,
                    "node_ids": ["start", "end"],
                    "collapsed": False,
                    "color": "#22d3ee",
                }
            ],
            "notes": [
                {
                    "id": "note-main",
                    "text": "Release path",
                    "position": {"x": 80, "y": 260},
                    "width": 240,
                    "height": 140,
                    "color": "#fbbf24",
                }
            ],
        }
        edited = client.put(
            f"/api/workflows/{workflow_id}",
            headers=editor,
            json={"dsl": next_dsl, "version": 2},
        )
        assert edited.status_code == 200, edited.text

        after_edit = client.get(f"/api/workflows/{workflow_id}/versions", headers=viewer).json()
        assert [(row["number"], row["status"]) for row in after_edit] == [
            (3, "draft"),
            (2, "published"),
            (1, "archived"),
        ]

        diff = client.get(
            f"/api/workflows/{workflow_id}/versions/diff",
            headers=viewer,
            params={"base_id": version_2["id"], "target_id": after_edit[0]["id"]},
        )
        assert diff.status_code == 200, diff.text
        assert diff.json()["changed_nodes"] == ["end"]
        assert diff.json()["changed_edges"] == ["edge"]
        assert diff.json()["canvas_changed"] is True

        rollback = client.post(
            f"/api/workflows/{workflow_id}/versions/{version_1['id']}/rollback",
            headers=editor,
            json={"change_summary": "Restore the initial layout"},
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["number"] == 4
        assert rollback.json()["dsl"] == version_1["dsl"]

        clone = client.post(
            f"/api/workflows/{workflow_id}/clone",
            headers=editor,
            json={"version_id": version_2["id"], "name": "Version two copy"},
        )
        assert clone.status_code == 201, clone.text
        clone_body = clone.json()
        assert clone_body["name"] == "Version two copy"
        assert clone_body["dsl"] == {
            **version_2["dsl"],
            "name": "Version two copy",
        }
        clone_versions = client.get(
            f"/api/workflows/{clone_body['id']}/versions", headers=viewer
        ).json()
        assert len(clone_versions) == 1
        assert clone_versions[0]["number"] == 1

        assert (
            client.post(f"/api/workflows/{workflow_id}/publish", headers=viewer).status_code == 403
        )


def test_execution_and_resume_use_the_start_snapshot(tmp_path) -> None:
    settings = auth_settings(tmp_path)
    settings = replace(settings, rate_limit_execution_requests=100)
    with TestClient(create_app(settings)) as client:
        editor = bearer(EDITOR_TOKEN)
        viewer = bearer(VIEWER_TOKEN)
        created = client.post("/api/workflows", headers=editor, json=_human_workflow()).json()
        versions = client.get(f"/api/workflows/{created['id']}/versions", headers=viewer).json()
        start_version = versions[0]

        started = client.post(f"/api/workflows/{created['id']}/run", headers=editor, json={})
        assert started.status_code == 201, started.text
        execution_id = started.json()["id"]
        assert started.json()["workflow_version_id"] == start_version["id"]
        assert started.json()["workflow_version_number"] == 1
        _wait_status(client, execution_id, {"waiting_approval"})

        changed_dsl = deepcopy(created["dsl"])
        changed_dsl["nodes"][2]["config"]["output_template"] = {
            "snapshot": "two: {{nodes.approve.approved}}"
        }
        changed = client.put(
            f"/api/workflows/{created['id']}",
            headers=editor,
            json={"dsl": changed_dsl, "version": created["version"]},
        )
        assert changed.status_code == 200, changed.text

        resumed = client.post(
            f"/api/executions/{execution_id}/resume",
            headers=editor,
            json={"decision": {"approved": True}},
        )
        assert resumed.status_code == 200, resumed.text
        completed = _wait_status(client, execution_id, {"succeeded", "failed"})
        assert completed["status"] == "succeeded", completed
        assert completed["output_json"] == {"snapshot": "one: True"}
        assert completed["workflow_version_id"] == start_version["id"]
        assert completed["workflow_version_number"] == 1
