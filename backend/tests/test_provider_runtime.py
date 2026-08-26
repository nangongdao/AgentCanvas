"""Exact workflow model loading for initial and resumed executions."""

from __future__ import annotations

import time
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.model_costs import format_cost_usd
from app.main import create_app


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        rate_limit_execution_requests=100,
    )


def _agent_workflow(model_id: str, *, approval_first: bool = False) -> dict[str, Any]:
    middle: list[dict[str, Any]] = []
    if approval_first:
        middle.append(
            {
                "id": "approval",
                "type": "human",
                "config": {"title": "Approve", "instruction": "Continue?"},
            }
        )
    middle.append(
        {
            "id": "agent",
            "type": "agent",
            "config": {"model_config_id": model_id},
        }
    )
    ids = ["start", *(node["id"] for node in middle), "end"]
    return {
        "name": "Exact model",
        "dsl": {
            "version": "1.0",
            "name": "Exact model",
            "settings": {"timeout_seconds": 10},
            "nodes": [
                {"id": "start", "type": "start"},
                *middle,
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": f"edge-{index}", "source": source, "target": target}
                for index, (source, target) in enumerate(pairwise(ids), start=1)
            ],
        },
    }


def _agent_pause_agent_workflow(model_id: str) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        {"id": "start", "type": "start"},
        {
            "id": "draft",
            "type": "agent",
            "config": {"model_config_id": model_id, "user_prompt": "same"},
        },
        {
            "id": "approval",
            "type": "human",
            "config": {"title": "Approve", "instruction": "Continue?"},
        },
        {
            "id": "final",
            "type": "agent",
            "config": {"model_config_id": model_id, "user_prompt": "same"},
        },
        {"id": "end", "type": "end"},
    ]
    return {
        "name": "Durable model usage",
        "dsl": {
            "version": "1.0",
            "name": "Durable model usage",
            "settings": {"timeout_seconds": 15},
            "nodes": nodes,
            "edges": [
                {"id": f"edge-{index}", "source": source, "target": target}
                for index, (source, target) in enumerate(
                    pairwise(node["id"] for node in nodes), start=1
                )
            ],
        },
    }


def _create_model(client: TestClient, model_id: str) -> None:
    response = client.post(
        "/api/models",
        json={
            "id": model_id,
            "name": model_id,
            "provider": "mock",
            "model_name": model_id,
        },
    )
    assert response.status_code == 201, response.text


def _wait_status(
    client: TestClient,
    execution_id: str,
    expected: set[str],
) -> dict[str, Any]:
    current: dict[str, Any] = {}
    for _ in range(120):
        response = client.get(f"/api/executions/{execution_id}")
        assert response.status_code == 200, response.text
        current = response.json()
        if current["status"] in expected:
            return current
        time.sleep(0.025)
    raise AssertionError(f"execution did not reach {expected}: {current}")


def test_initial_execution_never_substitutes_default_for_deleted_custom_model(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _create_model(client, "custom-exact")
        workflow = client.post("/api/workflows", json=_agent_workflow("custom-exact")).json()
        assert client.delete("/api/models/custom-exact").status_code == 204

        started = client.post(f"/api/workflows/{workflow['id']}/run", json={})
        assert started.status_code == 201, started.text
        failed = _wait_status(client, started.json()["id"], {"failed"})
        assert "workflow model(s) not found: custom-exact" in failed["error"]


def test_resume_reloads_exact_models_instead_of_reusing_default(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _create_model(client, "resume-exact")
        workflow = client.post(
            "/api/workflows",
            json=_agent_workflow("resume-exact", approval_first=True),
        ).json()
        started = client.post(f"/api/workflows/{workflow['id']}/run", json={})
        assert started.status_code == 201, started.text
        execution_id = started.json()["id"]
        _wait_status(client, execution_id, {"waiting_approval"})

        assert client.delete("/api/models/resume-exact").status_code == 204
        resumed = client.post(
            f"/api/executions/{execution_id}/resume",
            json={"decision": {"approved": True}},
        )
        assert resumed.status_code == 200, resumed.text
        failed = _wait_status(client, execution_id, {"failed"})
        assert "workflow model(s) not found: resume-exact" in failed["error"]


def test_resume_after_restart_keeps_model_usage_from_both_sides_of_pause(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as first_client:
        created_model = first_client.post(
            "/api/models",
            json={
                "id": "durable-cost",
                "name": "durable-cost",
                "provider": "mock",
                "model_name": "durable-cost",
                "prompt_price_per_million_usd": "1",
                "completion_price_per_million_usd": "1",
                "pricing_version": "durable-v1",
            },
        )
        assert created_model.status_code == 201, created_model.text
        workflow = first_client.post(
            "/api/workflows",
            json=_agent_pause_agent_workflow("durable-cost"),
        ).json()
        started = first_client.post(f"/api/workflows/{workflow['id']}/run", json={})
        assert started.status_code == 201, started.text
        execution_id = started.json()["id"]
        _wait_status(first_client, execution_id, {"waiting_approval"})

    with TestClient(create_app(settings)) as restarted_client:
        resumed = restarted_client.post(
            f"/api/executions/{execution_id}/resume",
            json={"decision": {"approved": True}},
        )
        assert resumed.status_code == 200, resumed.text
        completed = _wait_status(restarted_client, execution_id, {"succeeded", "failed"})
        assert completed["status"] == "succeeded", completed

        inspection = restarted_client.get(
            f"/api/executions/{execution_id}/inspection"
        ).json()
        expected_cost = format_cost_usd(
            Decimal(inspection["prompt_tokens"] + inspection["completion_tokens"])
            / Decimal(1_000_000)
        )
        overview = restarted_client.get("/api/overview?days=30")
        assert overview.status_code == 200, overview.text
        assert overview.json()["execution_summary"]["total"] == 1
        assert overview.json()["estimated_cost_usd"] == expected_cost


def test_cancel_while_paused_keeps_original_model_cost_after_repricing(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        created_model = client.post(
            "/api/models",
            json={
                "id": "durable-cancel-cost",
                "name": "durable-cancel-cost",
                "provider": "mock",
                "model_name": "durable-cancel-cost",
                "prompt_price_per_million_usd": "1",
                "completion_price_per_million_usd": "1",
                "pricing_version": "original-v1",
            },
        )
        assert created_model.status_code == 201, created_model.text
        workflow = client.post(
            "/api/workflows",
            json=_agent_pause_agent_workflow("durable-cancel-cost"),
        ).json()
        started = client.post(f"/api/workflows/{workflow['id']}/run", json={})
        assert started.status_code == 201, started.text
        execution_id = started.json()["id"]
        _wait_status(client, execution_id, {"waiting_approval"})

        cancelled = client.post(f"/api/executions/{execution_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert _wait_status(client, execution_id, {"cancelled"})["status"] == "cancelled"
        inspection = client.get(f"/api/executions/{execution_id}/inspection").json()
        expected_cost = format_cost_usd(
            Decimal(inspection["prompt_tokens"] + inspection["completion_tokens"])
            / Decimal(1_000_000)
        )

        repriced = client.put(
            "/api/models/durable-cancel-cost",
            json={
                "prompt_price_per_million_usd": "999",
                "completion_price_per_million_usd": "999",
                "pricing_version": "future-v2",
            },
        )
        assert repriced.status_code == 200, repriced.text
        overview = client.get("/api/overview?days=30")
        assert overview.status_code == 200, overview.text
        assert overview.json()["execution_summary"]["total"] == 1
        assert overview.json()["estimated_cost_usd"] == expected_cost
