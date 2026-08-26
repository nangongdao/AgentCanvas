"""Tests for the P5 model management CRUD endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ADMIN_TOKEN = "admin-model-token-1234567890"
EDITOR_TOKEN = "editor-model-token-1234567890"
VIEWER_TOKEN = "viewer-model-token-1234567890"
FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path) -> Settings:
    # auth disabled -> local-development principal resolves to Admin
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
    )


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def test_list_providers_includes_p5_additions(client: TestClient) -> None:
    resp = client.get("/api/models/providers")
    assert resp.status_code == 200
    providers = set(resp.json())
    assert {"anthropic", "ollama", "openai_compat", "mock"} <= providers

    matrix = client.get("/api/models/provider-capabilities")
    assert matrix.status_code == 200
    descriptors = {row["id"]: row["capabilities"] for row in matrix.json()}
    assert descriptors["openai_compat"]["json_mode"] is True
    assert descriptors["anthropic"]["json_mode"] is False
    assert descriptors["mock"]["tools"] is False


def test_create_update_delete_model_roundtrip(client: TestClient) -> None:
    payload = {
        "id": "test-claude",
        "name": "Claude Sonnet",
        "provider": "anthropic",
        "model_name": "claude-sonnet-5",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-ant-test-1234567890",
        "kind": "chat",
        "is_default": False,
        "prompt_price_per_million_usd": "2.5",
        "completion_price_per_million_usd": "10",
        "pricing_version": "vendor-2026-08",
        "capability_overrides": {"tools": False, "stream": True},
    }
    created = client.post("/api/models", json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"] == "test-claude"
    assert body["provider"] == "anthropic"
    assert body["has_api_key"] is True
    assert body["prompt_price_per_million_usd"] == "2.5"
    assert body["completion_price_per_million_usd"] == "10"
    assert body["pricing_version"] == "vendor-2026-08"
    assert body["capability_overrides"] == {"tools": False}
    assert body["capabilities"]["tools"] is False
    assert body["capabilities"]["usage"] is True
    assert body["capabilities"]["cost"] is True
    assert body["capability_error"] is None

    # list reflects new model
    listed = client.get("/api/models").json()
    assert any(m["id"] == "test-claude" for m in listed)

    # update: rename + clear api key
    updated = client.put(
        "/api/models/test-claude",
        json={
            "name": "Claude Opus",
            "api_key": "",
            "prompt_price_per_million_usd": None,
            "completion_price_per_million_usd": None,
            "pricing_version": None,
            "capability_overrides": {"tools": True},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Claude Opus"
    assert updated.json()["has_api_key"] is False
    assert updated.json()["prompt_price_per_million_usd"] is None
    assert updated.json()["capability_overrides"] == {}
    assert updated.json()["capabilities"]["tools"] is True
    assert updated.json()["capabilities"]["cost"] is False

    # delete
    deleted = client.delete("/api/models/test-claude")
    assert deleted.status_code == 204
    assert client.get("/api/models/test-claude-delete")  # ensures router alive
    listed_after = client.get("/api/models").json()
    assert all(m["id"] != "test-claude" for m in listed_after)


def test_create_model_rejects_unknown_provider(client: TestClient) -> None:
    resp = client.post(
        "/api/models",
        json={
            "id": "bad",
            "name": "Bad",
            "provider": "nope",
            "model_name": "x",
            "kind": "chat",
        },
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"json_mode": True}, "not supported"),
        ({"tools": "false"}, "Input should be a valid boolean"),
        ({"audio": False}, "Extra inputs are not permitted"),
    ],
)
def test_create_model_rejects_invalid_capability_overrides(
    client: TestClient,
    overrides: dict[str, object],
    detail: str,
) -> None:
    response = client.post(
        "/api/models",
        json={
            "id": "invalid-capabilities",
            "name": "Invalid",
            "provider": "anthropic",
            "model_name": "claude-test",
            "capability_overrides": overrides,
        },
    )
    assert response.status_code == 422
    assert detail in response.text
    assert all(row["id"] != "invalid-capabilities" for row in client.get("/api/models").json())


def test_embedding_model_rejects_chat_capability_overrides(client: TestClient) -> None:
    response = client.post(
        "/api/models",
        json={
            "id": "invalid-embedding-capabilities",
            "name": "Embedding",
            "provider": "openai_compat",
            "model_name": "embedding-test",
            "kind": "embedding",
            "capability_overrides": {"stream": False},
        },
    )
    assert response.status_code == 422
    assert "embedding models" in response.text


def test_create_model_conflict_on_duplicate_id(client: TestClient) -> None:
    body = {
        "id": "dup",
        "name": "Dup",
        "provider": "ollama",
        "model_name": "llama3",
        "kind": "chat",
    }
    assert client.post("/api/models", json=body).status_code == 201
    second = client.post("/api/models", json=body)
    assert second.status_code == 409


def test_update_missing_model_returns_404(client: TestClient) -> None:
    resp = client.put("/api/models/ghost", json={"name": "Ghost"})
    assert resp.status_code == 404


def test_delete_missing_model_returns_404(client: TestClient) -> None:
    assert client.delete("/api/models/ghost").status_code == 404


def test_model_crud_is_admin_only_with_token_auth(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
        secret_key=FERNET_KEY,
    )
    body = {
        "id": "rbac-model",
        "name": "RBAC model",
        "provider": "mock",
        "model_name": "mock",
        "kind": "chat",
    }
    with TestClient(create_app(settings)) as token_client:
        viewer = {"Authorization": f"Bearer {VIEWER_TOKEN}"}
        editor = {"Authorization": f"Bearer {EDITOR_TOKEN}"}
        admin = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        assert token_client.get("/api/models", headers=viewer).status_code == 200
        assert token_client.post("/api/models", headers=viewer, json=body).status_code == 403
        assert token_client.post("/api/models", headers=editor, json=body).status_code == 403
        created = token_client.post("/api/models", headers=admin, json=body)
        assert created.status_code == 201, created.text
        assert '"api_key":' not in created.text
        assert token_client.delete("/api/models/rbac-model", headers=editor).status_code == 403
        assert token_client.delete("/api/models/rbac-model", headers=admin).status_code == 204
