"""Single-node dry-run (C2-6): synchronous node execution without history."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_auth import EDITOR_TOKEN, VIEWER_TOKEN, auth_settings, bearer, workflow_body


def _code_node_config() -> dict:
    return {
        "language": "python",
        "source": 'output = {"doubled": inputs["n"] * 2}',
        "inputs": {"n": "{{input.n}}"},
        "timeout_seconds": 5.0,
        "memory_limit_mb": 64,
        "process_count": 8,
        "allow_network": False,
        "allow_filesystem": [],
    }


def _condition_node_config() -> dict:
    return {
        "branches": [
            {
                "id": "big",
                "group": {
                    "op": "and",
                    "rules": [{"left": "{{input.n}}", "operator": "gt", "right": 10}],
                },
            }
        ],
        "default_branch": "small",
    }


def _create_workflow_with_code(client: TestClient) -> str:
    body = workflow_body()
    # Replace the minimal start->end with start->code->end.
    body["dsl"]["nodes"] = [
        {
            "id": "start",
            "type": "start",
            "config": {"input_schema": [{"name": "n", "type": "number", "required": True}]},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "transform",
            "type": "code",
            "config": _code_node_config(),
            "position": {"x": 300, "y": 0},
        },
        {"id": "end", "type": "end", "config": {}, "position": {"x": 600, "y": 0}},
    ]
    body["dsl"]["edges"] = [
        {"id": "e1", "source": "start", "target": "transform"},
        {"id": "e2", "source": "transform", "target": "end"},
    ]
    resp = client.post("/api/workflows", headers=bearer(EDITOR_TOKEN), json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def test_dry_run_code_node_returns_output_and_events(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow_id = _create_workflow_with_code(client)
        resp = client.post(
            f"/api/workflows/{workflow_id}/dry-run",
            headers=bearer(EDITOR_TOKEN),
            json={
                "node_id": "transform",
                "node_type": "code",
                "node_config": _code_node_config(),
                "inputs": {"n": 21},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["node_id"] == "transform"
        assert body["output"]["doubled"] == 42
        event_types = [e["event_type"] for e in body["events"]]
        assert "node_started" in event_types
        assert "node_finished" in event_types


def test_dry_run_does_not_persist_execution_history(tmp_path) -> None:
    settings = auth_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        workflow_id = _create_workflow_with_code(client)
        before = client.get(
            f"/api/workflows/{workflow_id}/executions",
            headers=bearer(VIEWER_TOKEN),
        )
        assert before.status_code == 200
        count_before = len(before.json()["items"])

        resp = client.post(
            f"/api/workflows/{workflow_id}/dry-run",
            headers=bearer(EDITOR_TOKEN),
            json={
                "node_id": "transform",
                "node_type": "code",
                "node_config": _code_node_config(),
                "inputs": {"n": 5},
            },
        )
        assert resp.status_code == 200, resp.text

        after = client.get(
            f"/api/workflows/{workflow_id}/executions",
            headers=bearer(VIEWER_TOKEN),
        )
        count_after = len(after.json()["items"])
        assert count_after == count_before, "dry-run must not create an execution row"


def test_dry_run_condition_node_routes_and_records_branch(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow_id = _create_workflow_with_code(client)
        resp = client.post(
            f"/api/workflows/{workflow_id}/dry-run",
            headers=bearer(EDITOR_TOKEN),
            json={
                "node_id": "switch",
                "node_type": "condition",
                "node_config": _condition_node_config(),
                "inputs": {"n": 50},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["output"] == "big"


def test_dry_run_rejects_unsupported_node_types(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow_id = _create_workflow_with_code(client)
        for bad_type in ("start", "end", "subworkflow"):
            resp = client.post(
                f"/api/workflows/{workflow_id}/dry-run",
                headers=bearer(EDITOR_TOKEN),
                json={
                    "node_id": "x",
                    "node_type": bad_type,
                    "node_config": {},
                    "inputs": {},
                },
            )
            assert resp.status_code == 422, (bad_type, resp.text)


def test_dry_run_requires_editor_role(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow_id = _create_workflow_with_code(client)
        resp = client.post(
            f"/api/workflows/{workflow_id}/dry-run",
            headers=bearer(VIEWER_TOKEN),
            json={
                "node_id": "transform",
                "node_type": "code",
                "node_config": _code_node_config(),
                "inputs": {"n": 1},
            },
        )
        assert resp.status_code == 403


def test_dry_run_unknown_workflow_returns_404(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        resp = client.post(
            "/api/workflows/nonexistent/dry-run",
            headers=bearer(EDITOR_TOKEN),
            json={
                "node_id": "transform",
                "node_type": "code",
                "node_config": _code_node_config(),
                "inputs": {"n": 1},
            },
        )
        assert resp.status_code == 404


def test_dry_run_surfaces_node_failure_as_422(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow_id = _create_workflow_with_code(client)
        bad_config = _code_node_config()
        bad_config["source"] = "raise RuntimeError('boom')"
        resp = client.post(
            f"/api/workflows/{workflow_id}/dry-run",
            headers=bearer(EDITOR_TOKEN),
            json={
                "node_id": "transform",
                "node_type": "code",
                "node_config": bad_config,
                "inputs": {"n": 1},
            },
        )
        assert resp.status_code == 422, resp.text
        assert "boom" in str(resp.json())
