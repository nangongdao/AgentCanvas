"""C5-1 operational overview API contracts."""

from __future__ import annotations

import time
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.services import overview as overview_service
from tests.test_app_usage import _agent_dsl
from tests.test_tenancy import (
    ADMIN_TOKEN,
    _login_as,
    _register,
    _workflow_body,
    auth_settings,
)


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _wait_for_terminal(client: TestClient, execution_id: str) -> dict:
    for _ in range(500):
        response = client.get(f"/api/executions/{execution_id}", headers=_admin_headers())
        assert response.status_code == 200, response.text
        execution = response.json()
        if execution["status"] in {"succeeded", "failed", "cancelled"}:
            return execution
        time.sleep(0.01)
    raise AssertionError("execution did not reach a terminal state")


def _agent_workflow_body(name: str, model_id: str) -> dict:
    dsl = _agent_dsl(name)
    agent = next(node for node in dsl["nodes"] if node["type"] == "agent")
    agent["config"]["model_config_id"] = model_id
    return {"name": name, "dsl": dsl}


def _create_mock_model(client: TestClient, model_id: str, *, priced: bool) -> None:
    response = client.post(
        "/api/models",
        headers=_admin_headers(),
        json={
            "id": model_id,
            "name": model_id,
            "provider": "mock",
            "model_name": model_id,
            **(
                {
                    "prompt_price_per_million_usd": "1.5",
                    "completion_price_per_million_usd": "2.5",
                }
                if priced
                else {}
            ),
        },
    )
    assert response.status_code == 201, response.text


def test_overview_reports_recent_workflows_execution_health_and_daily_cost(
    tmp_path, monkeypatch
) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _create_mock_model(client, "overview-priced", priced=True)
        created = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json=_agent_workflow_body("Overview Workflow", "overview-priced"),
        )
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]

        started = client.post(
            f"/api/workflows/{workflow_id}/run",
            headers=_admin_headers(),
            json={"inputs": {"user_query": "price this execution"}},
        )
        assert started.status_code == 201, started.text
        assert _wait_for_terminal(client, started.json()["id"])["status"] == "succeeded"

        response = client.get("/api/overview?days=7", headers=_admin_headers())
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["days"] == 7
        assert [item["id"] for item in body["recent_workflows"]][0] == workflow_id
        assert body["execution_summary"]["total"] == 1
        assert body["execution_summary"]["succeeded"] == 1
        assert body["execution_summary"]["failed"] == 0
        assert body["execution_summary"]["active"] == 0
        assert body["execution_summary"]["cancelled"] == 0
        assert body["execution_summary"]["success_rate"] == 1.0
        assert body["cost_known"] is True
        initial_cost = body["estimated_cost_usd"]
        assert Decimal(initial_cost) > 0
        assert len(body["daily"]) == 7
        assert sum(day["executions"] for day in body["daily"]) == 1
        assert body["daily"][-1]["executions"] == 1
        assert Decimal(body["daily"][-1]["estimated_cost_usd"]) > 0

        with monkeypatch.context() as legacy:
            legacy.setattr(overview_service, "_persisted_cost", lambda _events: None)
            fallback = client.get("/api/overview?days=7", headers=_admin_headers()).json()
        assert fallback["estimated_cost_usd"] == initial_cost
        assert fallback["daily"][-1]["estimated_cost_usd"] == body["daily"][-1][
            "estimated_cost_usd"
        ]

        repriced = client.put(
            "/api/models/overview-priced",
            headers=_admin_headers(),
            json={
                "prompt_price_per_million_usd": "999",
                "completion_price_per_million_usd": "999",
                "pricing_version": "future-price",
            },
        )
        assert repriced.status_code == 200, repriced.text
        archived = client.delete(f"/api/workflows/{workflow_id}", headers=_admin_headers())
        assert archived.status_code == 204, archived.text

        historical = client.get("/api/overview?days=7", headers=_admin_headers()).json()
        assert historical["execution_summary"]["total"] == 1
        assert historical["estimated_cost_usd"] == initial_cost
        assert historical["daily"][-1]["estimated_cost_usd"] == body["daily"][-1][
            "estimated_cost_usd"
        ]
        assert workflow_id not in {row["id"] for row in historical["recent_workflows"]}


def test_overview_never_silently_reports_unpriced_usage_as_zero(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _create_mock_model(client, "overview-unpriced", priced=False)
        created = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json=_agent_workflow_body("Unpriced Overview", "overview-unpriced"),
        )
        assert created.status_code == 201, created.text
        started = client.post(
            f"/api/workflows/{created.json()['id']}/run",
            headers=_admin_headers(),
            json={"inputs": {"user_query": "unpriced execution"}},
        )
        assert started.status_code == 201, started.text
        assert _wait_for_terminal(client, started.json()["id"])["status"] == "succeeded"

        body = client.get("/api/overview", headers=_admin_headers()).json()
        assert body["cost_known"] is False
        assert body["estimated_cost_usd"] is None
        assert body["daily"][-1]["cost_known"] is False
        assert body["daily"][-1]["estimated_cost_usd"] is None


def test_overview_uses_the_existing_project_authorization_scope(tmp_path, monkeypatch) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        organization = client.post("/api/organizations", json={"name": "Overview Org"}).json()
        project = client.post(
            f"/api/organizations/{organization['id']}/projects",
            json={"name": "Overview Project"},
        ).json()
        project_workflow = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json={**_workflow_body("Project Workflow"), "project_id": project["id"]},
        )
        assert project_workflow.status_code == 201, project_workflow.text
        started = client.post(
            f"/api/workflows/{project_workflow.json()['id']}/run",
            headers=_admin_headers(),
            json={"inputs": {}},
        )
        assert started.status_code == 201, started.text
        assert _wait_for_terminal(client, started.json()["id"])["status"] == "succeeded"
        second = client.post(
            f"/api/workflows/{project_workflow.json()['id']}/run",
            headers=_admin_headers(),
            json={"inputs": {}},
        )
        assert second.status_code == 201, second.text
        assert _wait_for_terminal(client, second.json()["id"])["status"] == "succeeded"
        monkeypatch.setattr(overview_service, "EXECUTION_BATCH_SIZE", 1)

        unscoped = client.get("/api/overview", headers=_admin_headers())
        assert unscoped.status_code == 200, unscoped.text
        assert project_workflow.json()["id"] not in {
            item["id"] for item in unscoped.json()["recent_workflows"]
        }
        assert unscoped.json()["execution_summary"]["total"] == 0

        scoped = client.get(
            "/api/overview", params={"project_id": project["id"]}, headers=_admin_headers()
        )
        assert scoped.status_code == 200, scoped.text
        assert [item["id"] for item in scoped.json()["recent_workflows"]] == [
            project_workflow.json()["id"]
        ]
        assert scoped.json()["execution_summary"]["total"] == 2
        assert sum(day["executions"] for day in scoped.json()["daily"]) == 2

        _register(client, "outsider@example.com", role="viewer")
        _login_as(client, "outsider@example.com")
        denied = client.get("/api/overview", params={"project_id": project["id"]})
        assert denied.status_code == 403


def test_overview_validates_the_bounded_trend_window(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert client.get("/api/overview?days=0", headers=_admin_headers()).status_code == 422
        assert client.get("/api/overview?days=31", headers=_admin_headers()).status_code == 422
