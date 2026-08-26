"""Tenant authorization and HTTP contracts for project quotas."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_tenancy import _login_as, _register, auth_settings


def test_project_quota_read_update_validation_and_audit(tmp_path: Path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "quota-owner@example.com")
        first_org = client.post("/api/organizations", json={"name": "Quota First"}).json()
        second_org = client.post("/api/organizations", json={"name": "Quota Second"}).json()
        _register(client, "quota-viewer@example.com", role="viewer")
        _register(client, "quota-org-admin@example.com", role="viewer")
        _login_as(client, "quota-owner@example.com")
        assert (
            client.post(
                f"/api/organizations/{first_org['id']}/members",
                json={"email": "quota-viewer@example.com", "role": "viewer"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/organizations/{first_org['id']}/members",
                json={"email": "quota-org-admin@example.com", "role": "admin"},
            ).status_code
            == 201
        )
        first_project = client.post(
            f"/api/organizations/{first_org['id']}/projects", json={"name": "First"}
        ).json()
        second_project = client.post(
            f"/api/organizations/{second_org['id']}/projects", json={"name": "Second"}
        ).json()
        first_url = f"/api/projects/{first_project['id']}/quotas"
        second_url = f"/api/projects/{second_project['id']}/quotas"

        _login_as(client, "quota-viewer@example.com")
        visible = client.get("/api/projects")
        assert visible.status_code == 200
        assert [project["id"] for project in visible.json()] == [first_project["id"]]
        initial = client.get(first_url)
        assert initial.status_code == 200
        assert initial.json()["can_update"] is False
        assert initial.json()["monthly_model_cost_usd_limit"] is None
        assert initial.json()["model_cost_usd"] == "0.000000000000"
        assert client.put(first_url, json={"storage_bytes_limit": 1}).status_code == 403
        assert client.get(second_url).status_code == 403

        _login_as(client, "quota-org-admin@example.com")
        configured = client.put(
            first_url,
            json={
                "concurrent_execution_limit": 2,
                "storage_bytes_limit": 1024,
                "monthly_embedding_input_bytes_limit": 2048,
                "monthly_model_cost_usd_limit": "0.000001500000",
                "stdio_mcp_process_limit": 1,
            },
        )
        assert configured.status_code == 200, configured.text
        body = configured.json()
        assert body["can_update"] is True
        assert body["concurrent_execution_limit"] == 2
        assert body["concurrent_executions_remaining"] == 2
        assert body["storage_bytes_remaining"] == 1024
        assert body["embedding_input_bytes_remaining"] == 2048
        assert body["monthly_model_cost_usd_limit"] == "0.000001500000"
        assert body["model_cost_usd_remaining"] == "0.000001500000"
        assert body["stdio_mcp_processes_remaining"] == 1
        assert client.get(first_url).json() == body

        assert client.put(first_url, json={}).status_code == 422
        assert client.put(first_url, json={"concurrent_execution_limit": -1}).status_code == 422
        assert (
            client.put(
                first_url,
                json={"monthly_model_cost_usd_limit": "0.0000000000001"},
            ).status_code
            == 422
        )
        assert client.put(second_url, json={"storage_bytes_limit": 1}).status_code == 403

        cleared = client.put(
            first_url,
            json={
                "storage_bytes_limit": None,
                "monthly_model_cost_usd_limit": None,
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["storage_bytes_limit"] is None
        assert cleared.json()["storage_bytes_remaining"] is None
        assert cleared.json()["monthly_model_cost_usd_limit"] is None
        assert cleared.json()["model_cost_usd_remaining"] is None

        _login_as(client, "quota-owner@example.com")
        assert {project["id"] for project in client.get("/api/projects").json()} == {
            first_project["id"],
            second_project["id"],
        }
        audit = client.get(
            "/api/audit-logs",
            params={
                "project_id": first_project["id"],
                "action": "project_quota.updated",
                "limit": 100,
            },
        )
        assert audit.status_code == 200
        events = audit.json()["items"]
        assert len(events) == 2
        assert {event["project_id"] for event in events} == {first_project["id"]}
        assert all(event["resource_type"] == "project_quota" for event in events)
        assert {tuple(event["details"]["changed_fields"]) for event in events} == {
            (
                "concurrent_execution_limit",
                "monthly_embedding_input_bytes_limit",
                "monthly_model_cost_usd_limit",
                "stdio_mcp_process_limit",
                "storage_bytes_limit",
            ),
            ("monthly_model_cost_usd_limit", "storage_bytes_limit"),
        }
