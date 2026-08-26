"""End-to-end debug run (C2-7): start with breakpoints, resume with patch."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_auth import EDITOR_TOKEN, auth_settings, bearer


def _debug_workflow_body() -> dict:
    return {
        "name": "debug-e2e",
        "dsl": {
            "version": "1.0",
            "name": "debug-e2e",
            "variables": [],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 30,
                "recursion_limit": 50,
            },
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "n", "type": "number", "required": True}]
                    },
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "addone",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": 'output = {"value": inputs["n"] + 1}',
                        "inputs": {"n": "{{input.n}}"},
                        "timeout_seconds": 5.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                        "allow_network": False,
                        "allow_filesystem": [],
                    },
                    "position": {"x": 300, "y": 0},
                },
                {
                    "id": "double",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": 'output = {"value": inputs["prev"] * 2}',
                        "inputs": {"prev": "{{nodes.addone.output.value}}"},
                        "timeout_seconds": 5.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                        "allow_network": False,
                        "allow_filesystem": [],
                    },
                    "position": {"x": 600, "y": 0},
                },
                {"id": "end", "type": "end", "config": {"output_template": {"result": "{{nodes.double.output.value}}"}}, "position": {"x": 900, "y": 0}},
            ],
            "edges": [
                {"id": "1", "source": "start", "target": "addone"},
                {"id": "2", "source": "addone", "target": "double"},
                {"id": "3", "source": "double", "target": "end"},
            ],
        },
    }


def _wait_for_status(client: TestClient, execution_id: str, token: str, target: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/executions/{execution_id}", headers=bearer(token))
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] == target:
            return last
        time.sleep(0.1)
    return last


def test_debug_run_pauses_and_resume_patch_reflects_in_output(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = client.post(
            "/api/workflows", headers=bearer(EDITOR_TOKEN), json=_debug_workflow_body()
        ).json()
        workflow_id = workflow["id"]

        # Start a debug run with a breakpoint after addone.
        start = client.post(
            f"/api/workflows/{workflow_id}/run",
            headers=bearer(EDITOR_TOKEN),
            json={
                "inputs": {"n": 10},
                "debug": {"breakpoints": ["addone"], "single_step": False},
            },
        )
        assert start.status_code == 201, start.text
        execution_id = start.json()["id"]

        # The run pauses at the breakpoint (waiting_approval).
        paused = _wait_for_status(client, execution_id, EDITOR_TOKEN, "waiting_approval")
        assert paused["status"] == "waiting_approval", paused

        # Resume rewriting addone's output.value from 11 to 100; double then
        # computes 100*2 = 200 in the final result.
        resume = client.post(
            f"/api/executions/{execution_id}/resume",
            headers=bearer(EDITOR_TOKEN),
            json={
                "decision": {"resume": True, "state_patch": {"addone": {"output": {"value": 100}}}},
                "debug": {"breakpoints": ["addone"], "single_step": False},
            },
        )
        assert resume.status_code == 200, resume.text

        finished = _wait_for_status(client, execution_id, EDITOR_TOKEN, "succeeded", timeout=15.0)
        assert finished["status"] == "succeeded", finished
        output = finished["output_json"] or {}
        assert output.get("result") == 200


def test_debug_run_without_breakpoints_runs_normally(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = client.post(
            "/api/workflows", headers=bearer(EDITOR_TOKEN), json=_debug_workflow_body()
        ).json()
        workflow_id = workflow["id"]

        start = client.post(
            f"/api/workflows/{workflow_id}/run",
            headers=bearer(EDITOR_TOKEN),
            json={"inputs": {"n": 10}},
        )
        assert start.status_code == 201, start.text
        execution_id = start.json()["id"]

        finished = _wait_for_status(client, execution_id, EDITOR_TOKEN, "succeeded", timeout=15.0)
        assert finished["status"] == "succeeded", finished
        assert (finished["output_json"] or {}).get("result") == 22
