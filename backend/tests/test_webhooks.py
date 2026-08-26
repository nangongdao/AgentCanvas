"""Webhook trigger lifecycle and signed invocation coverage."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3

from fastapi.testclient import TestClient

from app.core.auth import _sha256
from app.core.config import Settings
from app.main import create_app

EDITOR_TOKEN = "webhook-editor-token-with-more-than-16-characters"
VIEWER_TOKEN = "webhook-viewer-token-with-more-than-16-characters"
SECRET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
        admin_api_token="webhook-admin-token-with-more-than-16-characters",
        secret_key=SECRET_KEY,
        rate_limit_execution_requests=100,
        rate_limit_webhook_requests=100,
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workflow_body() -> dict:
    return {
        "name": "Signed webhook",
        "dsl": {
            "version": "1.0",
            "name": "Signed webhook",
            "variables": [],
            "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "config": {
                        "input_schema": [
                            {"name": "message", "type": "string", "required": True}
                        ]
                    },
                },
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


def _human_workflow_body() -> dict:
    body = _workflow_body()
    body["name"] = "Waiting webhook"
    body["dsl"]["name"] = "Waiting webhook"
    body["dsl"]["nodes"] = [
        body["dsl"]["nodes"][0],
        {
            "id": "approval",
            "type": "human",
            "position": {"x": 150, "y": 0},
            "config": {"title": "Approve", "instruction": "Review webhook input"},
        },
        body["dsl"]["nodes"][-1],
    ]
    body["dsl"]["edges"] = [
        {"id": "e1", "source": "start", "target": "approval"},
        {"id": "e2", "source": "approval", "target": "end"},
    ]
    return body


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_requires_published_version_and_keeps_secrets_hashed(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        editor = _headers(EDITOR_TOKEN)
        viewer = _headers(VIEWER_TOKEN)
        workflow = client.post("/api/workflows", headers=editor, json=_workflow_body()).json()
        workflow_id = workflow["id"]

        pending = client.post(
            f"/api/workflows/{workflow_id}/webhook",
            headers=editor,
            json={"secret": "signing-secret-123456"},
        )
        assert pending.status_code == 409, pending.text

        assert client.post(f"/api/workflows/{workflow_id}/publish", headers=editor).status_code == 200
        created = client.post(
            f"/api/workflows/{workflow_id}/webhook",
            headers=editor,
            json={"secret": "signing-secret-123456", "ip_allowlist": ["127.0.0.1/32"]},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["secret"] == "signing-secret-123456"
        assert body["token"]
        assert body["trigger"]["has_signing_secret"] is True
        assert client.get(f"/api/workflows/{workflow_id}/webhook", headers=viewer).json()["token_prefix"] == body[
            "trigger"
        ]["token_prefix"]

        updated_dsl = _workflow_body()["dsl"]
        updated_dsl["name"] = "Signed webhook v2"
        updated = client.put(
            f"/api/workflows/{workflow_id}",
            headers=editor,
            json={"dsl": updated_dsl, "version": 1},
        )
        assert updated.status_code == 200, updated.text
        published_v2 = client.post(f"/api/workflows/{workflow_id}/publish", headers=editor)
        assert published_v2.status_code == 200, published_v2.text
        rebound = client.get(f"/api/workflows/{workflow_id}/webhook", headers=viewer)
        assert rebound.status_code == 200
        rebound_body = rebound.json()
        assert rebound_body["published_version_number"] == 2
        assert rebound_body["token_prefix"] == body["trigger"]["token_prefix"]

        with sqlite3.connect(tmp_path / "app.db") as database:
            row = database.execute(
                "SELECT token_hash, secret_encrypted FROM webhook_triggers WHERE id = ?",
                (body["trigger"]["id"],),
            ).fetchone()
        assert row is not None
        assert row[0] == _sha256(body["token"])
        assert body["token"] not in row[0]
        assert row[1] != body["secret"]


def test_signed_webhook_enqueues_published_snapshot_and_rejects_bad_requests(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        editor = _headers(EDITOR_TOKEN)
        workflow = client.post("/api/workflows", headers=editor, json=_workflow_body()).json()
        workflow_id = workflow["id"]
        assert client.post(f"/api/workflows/{workflow_id}/publish", headers=editor).status_code == 200
        issued = client.post(
            f"/api/workflows/{workflow_id}/webhook",
            headers=editor,
            json={"secret": "signing-secret-123456"},
        ).json()
        token = issued["token"]
        url = f"/api/hooks/{workflow_id}/{token}"
        raw = json.dumps({"message": "hello"}, separators=(",", ":")).encode()

        assert client.post(url, content=raw).status_code == 401
        assert client.post(
            url,
            content=raw,
            headers={"X-AgentCanvas-Signature": _signature("wrong-secret", raw)},
        ).status_code == 401
        invalid = b'{"message": 42}'
        invalid_response = client.post(
            url,
            content=invalid,
            headers={"X-AgentCanvas-Signature": _signature("signing-secret-123456", invalid)},
        )
        assert invalid_response.status_code == 422
        assert client.post(
            url,
            content=raw,
            headers={"X-AgentCanvas-Signature": _signature("signing-secret-123456", raw)},
        ).status_code == 202

        duplicate = client.post(
            url,
            content=raw,
            headers={
                "X-AgentCanvas-Signature": _signature("signing-secret-123456", raw),
                "Idempotency-Key": "webhook-once",
            },
        )
        assert duplicate.status_code == 202, duplicate.text
        first = client.post(
            url,
            content=raw,
            headers={
                "X-AgentCanvas-Signature": _signature("signing-secret-123456", raw),
                "Idempotency-Key": "webhook-idempotent",
            },
        )
        second = client.post(
            url,
            content=raw,
            headers={
                "X-AgentCanvas-Signature": _signature("signing-secret-123456", raw),
                "Idempotency-Key": "webhook-idempotent",
            },
        )
        assert first.status_code == second.status_code == 202
        assert first.json()["execution_id"] == second.json()["execution_id"]
        execution = client.get(
            f"/api/executions/{first.json()['execution_id']}", headers=editor
        )
        assert execution.status_code == 200
        assert execution.json()["trigger_source"] == "webhook"
        assert first.json()["accepted_version_id"] == issued["trigger"]["published_version_id"]

        disabled = client.delete(f"/api/workflows/{workflow_id}/webhook", headers=editor)
        assert disabled.status_code == 204
        assert client.post(
            url,
            content=raw,
            headers={"X-AgentCanvas-Signature": _signature("signing-secret-123456", raw)},
        ).status_code == 404


def test_signed_webhook_sync_mode_waits_for_terminal_with_bounded_timeout(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        editor = _headers(EDITOR_TOKEN)
        workflow = client.post("/api/workflows", headers=editor, json=_workflow_body()).json()
        workflow_id = workflow["id"]
        assert client.post(f"/api/workflows/{workflow_id}/publish", headers=editor).status_code == 200
        issued = client.post(
            f"/api/workflows/{workflow_id}/webhook",
            headers=editor,
            json={"secret": "signing-secret-123456"},
        ).json()
        raw = json.dumps({"message": "hello"}, separators=(",", ":")).encode()
        headers = {"X-AgentCanvas-Signature": _signature("signing-secret-123456", raw)}
        url = f"/api/hooks/{workflow_id}/{issued['token']}"

        completed = client.post(
            f"{url}?sync=true&timeout_seconds=2",
            content=raw,
            headers=headers,
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "succeeded"
        assert completed.json()["output_json"] is not None
        assert completed.json()["error"] is None
        assert client.post(
            f"{url}?sync=true&timeout_seconds=61",
            content=raw,
            headers=headers,
        ).status_code == 422

        waiting = client.post(
            "/api/workflows", headers=editor, json=_human_workflow_body()
        ).json()
        waiting_id = waiting["id"]
        assert client.post(f"/api/workflows/{waiting_id}/publish", headers=editor).status_code == 200
        waiting_issue = client.post(
            f"/api/workflows/{waiting_id}/webhook",
            headers=editor,
            json={"secret": "waiting-secret-123456"},
        ).json()
        waiting_url = f"/api/hooks/{waiting_id}/{waiting_issue['token']}"
        timed_out = client.post(
            f"{waiting_url}?sync=true&timeout_seconds=0.1",
            content=raw,
            headers={"X-AgentCanvas-Signature": _signature("waiting-secret-123456", raw)},
        )
        assert timed_out.status_code == 202, timed_out.text
        assert timed_out.json()["status"] in {"queued", "running", "waiting_approval"}
        assert timed_out.json()["output_json"] is None
