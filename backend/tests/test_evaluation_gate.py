"""Evaluation gate on publish (Backlog: publish gate).

Covers the three gate outcomes plus policy CRUD. The repo lookup is
monkeypatched so tests stay deterministic without a running evaluation
manager; the gate logic itself (threshold comparison) is what is asserted.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_auth import (
    EDITOR_TOKEN,
    VIEWER_TOKEN,
    auth_settings,
    bearer,
    workflow_body,
)


def _create_workflow(client: TestClient, token: str) -> dict:
    response = client.post("/api/workflows", headers=bearer(token), json=workflow_body())
    assert response.status_code == 201, response.text
    return response.json()


def _install_gate_lookup(monkeypatch, runs: list[dict] | None) -> None:
    """Replace EvaluationRunRepo.get_latest_completed with a fake."""
    from app.db.repositories import evaluations as repo_mod

    async def fake_latest_completed(
        self, dataset_version_id: str, workflow_version_id: str
    ) -> object | None:
        if not runs:
            return None
        summary = runs[0]
        return SimpleNamespace(summary_json=summary)

    monkeypatch.setattr(
        repo_mod.EvaluationRunRepo, "get_latest_completed", fake_latest_completed
    )


def _set_policy(
    client: TestClient, workflow_id: str, dataset_version_id: str, threshold: float
) -> dict:
    response = client.put(
        f"/api/workflows/{workflow_id}/evaluation-policy",
        headers=bearer(EDITOR_TOKEN),
        json={"dataset_version_id": dataset_version_id, "threshold": threshold},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_publish_allowed_without_policy(tmp_path, monkeypatch) -> None:
    _install_gate_lookup(monkeypatch, runs=None)
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = _create_workflow(client, EDITOR_TOKEN)
        response = client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=bearer(EDITOR_TOKEN)
        )
        assert response.status_code == 200, response.text


def test_publish_blocked_when_no_completed_run(tmp_path, monkeypatch) -> None:
    _install_gate_lookup(monkeypatch, runs=[])
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = _create_workflow(client, EDITOR_TOKEN)
        _set_policy(client, workflow["id"], "dataset-v-1", 0.8)
        response = client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=bearer(EDITOR_TOKEN)
        )
        assert response.status_code == 409, response.text
        assert "no completed run" in response.json()["detail"]


def test_publish_blocked_when_below_threshold(tmp_path, monkeypatch) -> None:
    _install_gate_lookup(
        monkeypatch, runs=[{"passed": 3, "failed": 3, "error": 0}]
    )
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = _create_workflow(client, EDITOR_TOKEN)
        _set_policy(client, workflow["id"], "dataset-v-1", 0.8)
        response = client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=bearer(EDITOR_TOKEN)
        )
        assert response.status_code == 409, response.text
        assert "below threshold" in response.json()["detail"]


def test_publish_allowed_when_meets_threshold(tmp_path, monkeypatch) -> None:
    _install_gate_lookup(
        monkeypatch, runs=[{"passed": 5, "failed": 1, "error": 0}]
    )
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = _create_workflow(client, EDITOR_TOKEN)
        _set_policy(client, workflow["id"], "dataset-v-1", 0.8)
        response = client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=bearer(EDITOR_TOKEN)
        )
        assert response.status_code == 200, response.text


def test_policy_roundtrip_and_clear(tmp_path, monkeypatch) -> None:
    _install_gate_lookup(monkeypatch, runs=None)
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = _create_workflow(client, EDITOR_TOKEN)
        policy = _set_policy(client, workflow["id"], "dataset-v-9", 0.9)
        assert policy == {"dataset_version_id": "dataset-v-9", "threshold": 0.9}

        fetched = client.get(
            f"/api/workflows/{workflow['id']}/evaluation-policy",
            headers=bearer(VIEWER_TOKEN),
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == {"dataset_version_id": "dataset-v-9", "threshold": 0.9}

        cleared = client.put(
            f"/api/workflows/{workflow['id']}/evaluation-policy",
            headers=bearer(EDITOR_TOKEN),
            json={"dataset_version_id": "dataset-v-9", "threshold": 0.0},
        )
        assert cleared.status_code == 200, cleared.text

        after = client.get(
            f"/api/workflows/{workflow['id']}/evaluation-policy",
            headers=bearer(VIEWER_TOKEN),
        )
        assert after.json() is None
