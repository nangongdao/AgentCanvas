"""HTTP contracts for recoverable workflow conflict merging."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_auth import EDITOR_TOKEN, VIEWER_TOKEN, auth_settings, bearer, workflow_body


def test_disjoint_merge_creates_snapshot_and_overlap_does_not_write(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        viewer = bearer(VIEWER_TOKEN)
        created = client.post("/api/workflows", headers=editor, json=workflow_body())
        assert created.status_code == 201, created.text
        base: dict[str, Any] = created.json()
        workflow_id = base["id"]
        base_version_id = client.get(
            f"/api/workflows/{workflow_id}/versions", headers=viewer
        ).json()[0]["id"]

        remote_dsl = deepcopy(base["dsl"])
        remote_dsl["nodes"][1]["position"]["x"] = 320
        remote_update = client.put(
            f"/api/workflows/{workflow_id}",
            headers=editor,
            json={"dsl": remote_dsl, "version": 1, "change_summary": "Remote move"},
        )
        assert remote_update.status_code == 200, remote_update.text
        assert remote_update.json()["version"] == 2

        local_dsl = deepcopy(base["dsl"])
        local_dsl["name"] = "Local name"
        merged = client.post(
            f"/api/workflows/{workflow_id}/versions/merge",
            headers=editor,
            json={
                "base_version_id": base_version_id,
                "remote_version": 2,
                "local_name": "Local name",
                "local_dsl": local_dsl,
                "change_summary": "Merge local name with remote layout",
            },
        )
        assert merged.status_code == 200, merged.text
        merged_body = merged.json()
        assert merged_body["status"] == "merged"
        assert merged_body["saved_version"] == 3
        assert merged_body["name"] == "Local name"
        assert merged_body["dsl"]["name"] == "Local name"
        assert merged_body["dsl"]["nodes"][1]["position"]["x"] == 320

        current = client.get(f"/api/workflows/{workflow_id}", headers=viewer).json()
        assert current["version"] == 3
        current_version_id = client.get(
            f"/api/workflows/{workflow_id}/versions", headers=viewer
        ).json()[0]["id"]
        conflict_base = deepcopy(current["dsl"])
        next_remote = deepcopy(conflict_base)
        next_remote["nodes"][1]["position"]["x"] = 400
        advanced = client.put(
            f"/api/workflows/{workflow_id}",
            headers=editor,
            json={"dsl": next_remote, "version": 3},
        )
        assert advanced.status_code == 200, advanced.text
        assert advanced.json()["version"] == 4

        overlapping_local = deepcopy(conflict_base)
        overlapping_local["nodes"][1]["position"]["x"] = 500
        conflict = client.post(
            f"/api/workflows/{workflow_id}/versions/merge",
            headers=editor,
            json={
                "base_version_id": current_version_id,
                "remote_version": 4,
                "local_name": current["name"],
                "local_dsl": overlapping_local,
            },
        )
        assert conflict.status_code == 200, conflict.text
        conflict_body = conflict.json()
        assert conflict_body["status"] == "conflict"
        assert conflict_body["saved_version"] is None
        assert [item["path"] for item in conflict_body["conflicts"]] == [
            "dsl.nodes[id=end].position.x"
        ]
        unchanged = client.get(f"/api/workflows/{workflow_id}", headers=viewer).json()
        assert unchanged["version"] == 4
        assert unchanged["dsl"]["nodes"][1]["position"]["x"] == 400


def test_merge_rejects_stale_remote_invalid_local_and_foreign_base(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        created: dict[str, Any] = client.post(
            "/api/workflows", headers=editor, json=workflow_body()
        ).json()
        path = f"/api/workflows/{created['id']}/versions/merge"
        base_version_id = client.get(
            f"/api/workflows/{created['id']}/versions", headers=editor
        ).json()[0]["id"]
        valid = {
            "base_version_id": base_version_id,
            "remote_version": 1,
            "local_name": created["name"],
            "local_dsl": created["dsl"],
        }
        stale = client.post(path, headers=editor, json={**valid, "remote_version": 2})
        assert stale.status_code == 409
        missing = client.post(
            path,
            headers=editor,
            json={**valid, "base_version_id": "0" * 32},
        )
        assert missing.status_code == 404
        foreign_body = workflow_body()
        foreign_body["name"] = "Foreign workflow"
        foreign_body["dsl"]["name"] = "Foreign workflow"
        other = client.post(
            "/api/workflows",
            headers=editor,
            json=foreign_body,
        ).json()
        foreign_version_id = client.get(
            f"/api/workflows/{other['id']}/versions", headers=editor
        ).json()[0]["id"]
        foreign = client.post(
            path,
            headers=editor,
            json={**valid, "base_version_id": foreign_version_id},
        )
        assert foreign.status_code == 404
        invalid = client.post(
            path,
            headers=editor,
            json={**valid, "local_dsl": {"version": "1.0"}},
        )
        assert invalid.status_code == 400
        assert client.post(path, headers=bearer(VIEWER_TOKEN), json=valid).status_code == 403
