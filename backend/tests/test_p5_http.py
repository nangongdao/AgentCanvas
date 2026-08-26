"""HTTP acceptance paths for human approval and persisted Chat resources."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.engine.checkpoint import CheckpointerUnavailable
from app.main import create_app


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        local_embedding_dimensions=64,
        rate_limit_execution_requests=100,
    )


def _human_workflow() -> dict:
    return {
        "name": "HTTP human approval",
        "dsl": {
            "version": "1.0",
            "name": "HTTP human approval",
            "variables": [],
            "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                {
                    "id": "approve",
                    "type": "human",
                    "position": {"x": 200, "y": 0},
                    "config": {"title": "Release approval", "instruction": "Approve release"},
                },
                {"id": "end", "type": "end", "position": {"x": 400, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "approve"},
                {"id": "e2", "source": "approve", "target": "end"},
            ],
        },
    }


def _wait_status(client: TestClient, execution_id: str, expected: set[str]) -> dict:
    deadline = time.monotonic() + 15
    current: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/executions/{execution_id}")
        if response.status_code != 200:
            if response.status_code == 429:
                time.sleep(response.json().get("retry_after", 1))
                continue
            raise AssertionError(f"unexpected status {response.status_code}: {response.text}")
        current = response.json()
        if current["status"] in expected:
            return current
        time.sleep(0.1)
    raise AssertionError(f"execution did not reach {expected}: {current}")


def test_human_pause_and_resume_over_http(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        created = client.post("/api/workflows", json=_human_workflow())
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]
        started = client.post(f"/api/workflows/{workflow_id}/run", json={})
        assert started.status_code == 201, started.text
        execution_id = started.json()["id"]
        _wait_status(client, execution_id, {"waiting_approval"})

        resumed = client.post(
            f"/api/executions/{execution_id}/resume",
            json={"decision": {"approved": True}},
        )
        assert resumed.status_code == 200, resumed.text
        completed = _wait_status(client, execution_id, {"succeeded", "failed"})
        assert completed["status"] == "succeeded", completed


def test_waiting_human_cancel_is_idempotent(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        workflow_id = client.post("/api/workflows", json=_human_workflow()).json()["id"]
        execution_id = client.post(f"/api/workflows/{workflow_id}/run", json={}).json()["id"]
        _wait_status(client, execution_id, {"waiting_approval"})

        first = client.post(
            f"/api/executions/{execution_id}/cancel",
            headers={"Idempotency-Key": "cancel-once"},
        )
        second = client.post(
            f"/api/executions/{execution_id}/cancel",
            headers={"Idempotency-Key": "cancel-once"},
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json() == second.json() == {"cancelled": True}
        assert _wait_status(client, execution_id, {"cancelled"})["status"] == "cancelled"
        assert client.post("/api/executions/missing/cancel").status_code == 404


def test_startup_rejects_waiting_approval_without_checkpoint(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        workflow_id = client.post("/api/workflows", json=_human_workflow()).json()["id"]
        execution_id = client.post(
            f"/api/workflows/{workflow_id}/run",
            json={},
        ).json()["id"]
        _wait_status(client, execution_id, {"waiting_approval"})

    settings.checkpoint_db_path.replace(tmp_path / "unmigrated-checkpoints.db")
    with (
        pytest.raises(CheckpointerUnavailable, match="waiting approval"),
        TestClient(create_app(settings)),
    ):
        pass
