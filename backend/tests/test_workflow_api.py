"""Published workflow API lifecycle and execution contract coverage."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.core.auth import _sha256
from app.core.config import Settings
from app.main import create_app

ADMIN_TOKEN = "workflow-api-admin-token-with-more-than-16-characters"


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        rate_limit_workflow_api_requests=100,
        rate_limit_execution_requests=100,
    )


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _dsl(name: str = "Published API") -> dict:
    return {
        "version": "1.0",
        "name": name,
        "variables": [],
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {
                    "input_schema": [
                        {"name": "message", "type": "string", "required": True},
                        {"name": "retries", "type": "number", "required": False, "default": 1},
                        {"name": "items", "type": "array", "required": False, "default": []},
                    ]
                },
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 200, "y": 0},
                "config": {"output_template": {"answer": "{{nodes.prev.output}}"}},
            },
        ],
        "edges": [{"id": "edge", "source": "start", "target": "end"}],
    }


def _project_and_workflow(client: TestClient) -> tuple[str, str]:
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "workflow-api-admin@example.com",
            "password": "workflow-api-password",
            "display_name": "API Admin",
            "role": "admin",
        },
    )
    assert registered.status_code == 201, registered.text
    org = client.post("/api/organizations", json={"name": "Workflow API Org"})
    assert org.status_code == 201, org.text
    project = client.post(f"/api/organizations/{org.json()['id']}/projects", json={"name": "API Project"})
    assert project.status_code == 201, project.text
    workflow = client.post(
        "/api/workflows",
        headers=_admin(),
        json={"name": "Published API", "project_id": project.json()["id"], "dsl": _dsl()},
    )
    assert workflow.status_code == 201, workflow.text
    return project.json()["id"], workflow.json()["id"]


def _service_account(client: TestClient, project_id: str) -> tuple[str, str]:
    account = client.post(
        "/api/service-accounts",
        headers=_admin(),
        json={"name": "workflow-api-runner", "project_id": project_id, "role": "editor"},
    )
    assert account.status_code == 201, account.text
    issued = client.post(
        f"/api/service-accounts/{account.json()['id']}/tokens",
        headers=_admin(),
        json={"name": "bootstrap"},
    )
    assert issued.status_code == 201, issued.text
    return account.json()["id"], issued.json()["plaintext"]


def test_workflow_api_publishes_schema_executes_and_rotates(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        project_id, workflow_id = _project_and_workflow(client)
        account_id, _unused_account_token = _service_account(client, project_id)
        assert client.post(f"/api/workflows/{workflow_id}/publish", headers=_admin()).status_code == 200

        published = client.post(
            f"/api/workflows/{workflow_id}/api",
            headers=_admin(),
            json={"service_account_id": account_id},
        )
        assert published.status_code == 201, published.text
        body = published.json()
        token = body["token"]
        assert body["publication"]["published_version_number"] == 1
        assert body["openapi"]["components"]["schemas"]["WorkflowInput"]["required"] == ["message"]
        assert body["openapi"]["components"]["schemas"]["WorkflowInput"]["properties"]["items"] == {
            "type": "array",
            "items": {},
            "default": [],
        }
        assert body["openapi"]["components"]["schemas"]["WorkflowOutput"]["properties"] == {"answer": {}}
        assert "curl" in body["examples"] and "python" in body["examples"] and "javascript" in body["examples"]
        assert client.get("/api/workflows", headers={"Authorization": f"Bearer {token}"}).status_code == 403

        invalid = client.post(
            f"/api/workflow-apis/{workflow_id}/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": 42},
        )
        assert invalid.status_code == 422
        queued = client.post(
            f"/api/workflow-apis/{workflow_id}/execute",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "api-once"},
            json={"message": "hello"},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["input_json"] == {"message": "hello", "retries": 1, "items": []}
        duplicate = client.post(
            f"/api/workflow-apis/{workflow_id}/execute",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "api-once"},
            json={"message": "hello"},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["execution_id"] == queued.json()["execution_id"]
        execution = client.get(
            f"/api/executions/{queued.json()['execution_id']}", headers=_admin()
        )
        assert execution.status_code == 200
        assert execution.json()["trigger_source"] == "api"

        with sqlite3.connect(tmp_path / "app.db") as database:
            api_row = database.execute(
                "SELECT t.token_hash, p.status FROM workflow_api_publications p JOIN api_tokens t ON t.id = p.api_token_id WHERE p.workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        assert api_row == (_sha256(token), "active")

        rotated = client.put(
            f"/api/workflows/{workflow_id}/api",
            headers=_admin(),
            json={"service_account_id": account_id},
        )
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["token"]
        assert new_token != token
        old_response = client.post(
            f"/api/workflow-apis/{workflow_id}/execute",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "old"},
        )
        assert old_response.status_code == 401
        assert client.post(
            f"/api/workflow-apis/{workflow_id}/execute",
            headers={"Authorization": f"Bearer {new_token}"},
            json={"message": "new"},
        ).status_code == 202

        assert client.delete(f"/api/workflows/{workflow_id}/api", headers=_admin()).status_code == 204
        assert client.post(
            f"/api/workflow-apis/{workflow_id}/execute",
            headers={"Authorization": f"Bearer {new_token}"},
            json={"message": "disabled"},
        ).status_code == 401


def test_workflow_api_rebinds_on_publish_and_rejects_unscoped_workflow(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        project_id, workflow_id = _project_and_workflow(client)
        account_id, _unused = _service_account(client, project_id)
        assert client.post(f"/api/workflows/{workflow_id}/api", headers=_admin(), json={"service_account_id": account_id}).status_code == 409
        assert client.post(f"/api/workflows/{workflow_id}/publish", headers=_admin()).status_code == 200
        published = client.post(f"/api/workflows/{workflow_id}/api", headers=_admin(), json={"service_account_id": account_id})
        assert published.status_code == 201
        first_version = published.json()["publication"]["published_version_id"]
        updated = client.put(
            f"/api/workflows/{workflow_id}",
            headers=_admin(),
            json={"dsl": {**_dsl("Published API v2"), "name": "Published API v2"}, "version": 1},
        )
        assert updated.status_code == 200, updated.text
        assert client.post(f"/api/workflows/{workflow_id}/publish", headers=_admin()).status_code == 200
        current = client.get(f"/api/workflows/{workflow_id}/api", headers=_admin())
        assert current.status_code == 200
        assert current.json()["published_version_id"] != first_version
