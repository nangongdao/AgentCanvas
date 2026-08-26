"""D3 Phase 5 administrative audit coverage and secret-boundary tests."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import Principal, Role
from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models.audit import AuditLog
from app.main import create_app
from app.services.audit import record_audit

ADMIN_TOKEN = "audit-admin-token-with-more-than-16-characters"
EDITOR_TOKEN = "audit-editor-token-with-more-than-16-characters"
VIEWER_TOKEN = "audit-viewer-token-with-more-than-16-characters"
PASSWORD = "audit-user-password"
SECRET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
        secret_key=SECRET_KEY,
        rate_limit_execution_requests=100,
    )


def _headers(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workflow_body(name: str, *, node_marker: str = "Start") -> dict:
    return {
        "name": name,
        "description": "Workflow description",
        "dsl": {
            "version": "1.0",
            "name": name,
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
                    "name": node_marker,
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "end",
                    "type": "end",
                    "position": {"x": 200, "y": 0},
                },
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


def _human_workflow_body(name: str) -> dict:
    body = _workflow_body(name)
    body["dsl"]["nodes"] = [
        {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
        {
            "id": "approval",
            "type": "human",
            "position": {"x": 150, "y": 0},
            "config": {
                "title": "Approve",
                "instruction": "Review",
                "form_schema": {
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                },
            },
        },
        {"id": "end", "type": "end", "position": {"x": 300, "y": 0}},
    ]
    body["dsl"]["edges"] = [
        {"id": "e1", "source": "start", "target": "approval"},
        {"id": "e2", "source": "approval", "target": "end"},
    ]
    return body


def _audit_items(
    client: TestClient, **params: str | int | float | bool | None
) -> list[dict]:
    response = client.get("/api/audit-logs", headers=_headers(), params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_audit_service_rejects_sensitive_or_oversized_details(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path, environment="test")
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    principal = Principal("auditor", Role.ADMIN, "test")
    try:
        async with sessions() as session:
            with pytest.raises(ValueError, match="sensitive audit detail key"):
                await record_audit(
                    session,
                    principal,
                    action="probe.created",
                    resource_type="probe",
                    resource_id="probe",
                    details={"token": "must-not-be-stored"},
                )
            with pytest.raises(ValueError, match="8 KiB"):
                await record_audit(
                    session,
                    principal,
                    action="probe.created",
                    resource_type="probe",
                    resource_id="probe",
                    details={"safe_text": "x" * 9000},
                )
            result = await session.execute(select(AuditLog))
            assert list(result.scalars()) == []
    finally:
        await engine.dispose()


def test_audit_query_is_admin_only_paginated_and_filter_bound(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        for model_id in ("audit-one", "audit-two"):
            created = client.post(
                "/api/models",
                headers=_headers(),
                json={
                    "id": model_id,
                    "name": model_id,
                    "provider": "mock",
                    "model_name": "mock",
                },
            )
            assert created.status_code == 201, created.text

        assert client.get(
            "/api/audit-logs", headers=_headers(EDITOR_TOKEN)
        ).status_code == 403
        assert client.get(
            "/api/audit-logs", headers=_headers(VIEWER_TOKEN)
        ).status_code == 403

        first = client.get(
            "/api/audit-logs", headers=_headers(), params={"limit": 1}
        )
        assert first.status_code == 200
        assert first.json()["has_more"] is True
        cursor = first.json()["next_cursor"]
        second = client.get(
            "/api/audit-logs",
            headers=_headers(),
            params={"limit": 1, "cursor": cursor},
        )
        assert second.status_code == 200
        assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]

        mismatch = client.get(
            "/api/audit-logs",
            headers=_headers(),
            params={"limit": 1, "cursor": cursor, "action": "model.created"},
        )
        assert mismatch.status_code == 422
        filtered = _audit_items(
            client,
            action="model.created",
            resource_type="model",
            search="audit-one",
        )
        assert [item["resource_id"] for item in filtered] == ["audit-one"]
        actor_key = filtered[0]["actor_key"]
        assert _audit_items(client, actor_key=actor_key)

        duplicate = client.post(
            "/api/models",
            headers=_headers(),
            json={
                "id": "audit-one",
                "name": "duplicate",
                "provider": "mock",
                "model_name": "mock",
            },
        )
        assert duplicate.status_code == 409
        assert len(_audit_items(client, action="model.created")) == 2


def test_all_governance_mutations_are_scoped_and_secrets_are_absent(
    tmp_path: Path,
) -> None:
    model_secret = "MODEL-KEY-DO-NOT-AUDIT"
    mcp_env_secret = "MCP-ENV-DO-NOT-AUDIT"
    mcp_header_secret = "MCP-HEADER-DO-NOT-AUDIT"
    dsl_secret = "DSL-NODE-DO-NOT-AUDIT"
    review_secret = "REVIEW-TEXT-DO-NOT-AUDIT"
    approval_secret = "APPROVAL-PAYLOAD-DO-NOT-AUDIT"

    with TestClient(create_app(_settings(tmp_path))) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "email": "audit-admin@example.com",
                "password": PASSWORD,
                "display_name": "Audit Admin",
                "role": "admin",
            },
        )
        assert registered.status_code == 201, registered.text
        assert client.post(
            "/api/auth/login",
            json={"email": "audit-admin@example.com", "password": PASSWORD},
        ).status_code == 200

        org = client.post("/api/organizations", json={"name": "Audit Org"}).json()
        project = client.post(
            f"/api/organizations/{org['id']}/projects", json={"name": "Audit Project"}
        ).json()
        bob = client.post(
            "/api/auth/register",
            json={
                "email": "audit-bob@example.com",
                "password": PASSWORD,
                "display_name": "Audit Bob",
                "role": "viewer",
            },
        ).json()
        member = client.post(
            f"/api/organizations/{org['id']}/members",
            json={"email": "audit-bob@example.com", "role": "viewer"},
        )
        assert member.status_code == 201
        assert client.put(
            f"/api/organizations/{org['id']}/members/{bob['id']}",
            json={"role": "editor"},
        ).status_code == 200
        assert client.delete(
            f"/api/organizations/{org['id']}/members/{bob['id']}"
        ).status_code == 204

        model = {
            "id": "audit-sensitive-model",
            "name": "Sensitive model",
            "provider": "mock",
            "model_name": "mock",
            "api_key": model_secret,
        }
        assert client.post(
            "/api/models", headers=_headers(), json=model
        ).status_code == 201
        assert client.put(
            "/api/models/audit-sensitive-model",
            headers=_headers(),
            json={"api_key": model_secret + "-ROTATED", "name": "Rotated model"},
        ).status_code == 200

        mcp = client.post(
            "/api/mcp/servers",
            headers=_headers(),
            json={
                "name": "Audit MCP",
                "transport": "streamable_http",
                "url": "https://mcp.invalid/service",
                "env": {"PRIVATE_ENV": mcp_env_secret},
                "headers": {"Authorization": mcp_header_secret},
            },
        )
        assert mcp.status_code == 201, mcp.text
        mcp_id = mcp.json()["id"]
        assert client.put(
            f"/api/mcp/servers/{mcp_id}",
            headers=_headers(),
            json={"enabled": False, "headers": {"Authorization": mcp_header_secret + "-2"}},
        ).status_code == 200

        account = client.post(
            "/api/service-accounts",
            headers=_headers(),
            json={"name": "audit-service", "role": "editor"},
        ).json()
        assert client.put(
            f"/api/service-accounts/{account['id']}",
            headers=_headers(),
            json={"role": "viewer"},
        ).status_code == 200
        issued = client.post(
            f"/api/service-accounts/{account['id']}/tokens",
            headers=_headers(),
            json={"name": "audit credential"},
        ).json()
        raw_token = issued["plaintext"]
        token_id = issued["token"]["id"]
        assert client.post(
            f"/api/service-accounts/{account['id']}/tokens/{token_id}/revoke",
            headers=_headers(),
        ).status_code == 200

        workflow_payload = {
            **_workflow_body("Audited Workflow", node_marker=dsl_secret),
            "project_id": project["id"],
        }
        created = client.post(
            "/api/workflows", headers=_headers(), json=workflow_payload
        )
        assert created.status_code == 201, created.text
        workflow = created.json()
        versions = client.get(
            f"/api/workflows/{workflow['id']}/versions", headers=_headers()
        ).json()
        base = versions[0]
        assert client.put(
            f"/api/workflows/{workflow['id']}",
            headers=_headers(),
            json={"name": "Remote Workflow", "version": workflow["version"]},
        ).status_code == 200
        local_dsl = workflow_payload["dsl"]
        local_dsl["nodes"][0]["name"] = dsl_secret + "-LOCAL"
        merged = client.post(
            f"/api/workflows/{workflow['id']}/versions/merge",
            headers=_headers(),
            json={
                "base_version_id": base["id"],
                "remote_version": 2,
                "local_name": workflow["name"],
                "local_dsl": local_dsl,
            },
        )
        assert merged.status_code == 200, merged.text
        assert merged.json()["status"] == "merged"
        assert client.post(
            f"/api/workflows/{workflow['id']}/publish", headers=_headers()
        ).status_code == 200

        review = client.post(
            f"/api/workflows/{workflow['id']}/reviews",
            headers=_headers(),
            json={"version_id": base["id"], "summary": review_secret},
        )
        assert review.status_code == 201, review.text
        assert client.put(
            f"/api/workflows/{workflow['id']}/reviews/{review.json()['id']}/decision",
            json={"decision": "approved", "summary": review_secret + "-DECISION"},
        ).status_code == 200
        assert client.post(
            f"/api/workflows/{workflow['id']}/versions/{base['id']}/rollback",
            headers=_headers(),
            json={"change_summary": review_secret + "-ROLLBACK"},
        ).status_code == 200
        assert client.post(
            f"/api/workflows/{workflow['id']}/clone",
            headers=_headers(),
            json={"version_id": base["id"], "name": "Audit Clone"},
        ).status_code == 201
        imported = client.post(
            "/api/workflows/import",
            headers=_headers(),
            json={
                "format": "agentcanvas-workflow",
                "format_version": 1,
                "name": "Audit Import",
                "description": review_secret + "-IMPORT",
                "dsl": _workflow_body("Imported")["dsl"],
            },
        )
        assert imported.status_code == 201, imported.text
        assert client.delete(
            f"/api/workflows/{workflow['id']}", headers=_headers()
        ).status_code == 204

        human = client.post(
            "/api/workflows",
            headers=_headers(),
            json=_human_workflow_body("Audit Human"),
        ).json()
        run = client.post(
            f"/api/workflows/{human['id']}/run", headers=_headers(), json={}
        )
        assert run.status_code == 201, run.text
        execution_id = run.json()["id"]
        for _ in range(100):
            status = client.get(
                f"/api/executions/{execution_id}", headers=_headers()
            ).json()["status"]
            if status == "waiting_approval":
                break
            time.sleep(0.02)
        assert status == "waiting_approval"
        resumed = client.post(
            f"/api/executions/{execution_id}/resume",
            headers=_headers(),
            json={"decision": {"approved": True, "comment": approval_secret}},
        )
        assert resumed.status_code == 200, resumed.text

        assert client.delete(
            "/api/models/audit-sensitive-model", headers=_headers()
        ).status_code == 204
        assert client.delete(
            f"/api/mcp/servers/{mcp_id}", headers=_headers()
        ).status_code == 204
        assert client.delete(
            f"/api/service-accounts/{account['id']}", headers=_headers()
        ).status_code == 204

        logs = _audit_items(client, limit=200)
        actions = {item["action"] for item in logs}
        assert {
            "organization.created",
            "project.created",
            "membership.added",
            "membership.role_changed",
            "membership.removed",
            "model.created",
            "model.updated",
            "model.deleted",
            "mcp_server.created",
            "mcp_server.updated",
            "mcp_server.deleted",
            "service_account.created",
            "service_account.updated",
            "service_account.deleted",
            "api_token.issued",
            "api_token.revoked",
            "workflow.created",
            "workflow.updated",
            "workflow.merged",
            "workflow_version.published",
            "workflow_version.rolled_back",
            "workflow.cloned",
            "workflow.imported",
            "workflow.archived",
            "workflow_review.requested",
            "workflow_review.decided",
            "human_approval.submitted",
        } <= actions

        project_logs = _audit_items(client, project_id=project["id"])
        assert project_logs
        assert all(item["organization_id"] == org["id"] for item in project_logs)
        serialized = json.dumps(logs, ensure_ascii=False)
        for secret in (
            model_secret,
            model_secret + "-ROTATED",
            mcp_env_secret,
            mcp_header_secret,
            mcp_header_secret + "-2",
            raw_token,
            raw_token[:12],
            dsl_secret,
            dsl_secret + "-LOCAL",
            review_secret,
            approval_secret,
        ):
            assert secret not in serialized
