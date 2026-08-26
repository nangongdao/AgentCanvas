"""Authentication and RBAC integration tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth import AuthService, Role
from app.core.config import Settings, validate_runtime_settings
from app.main import create_app

ADMIN_TOKEN = "admin-token-with-more-than-16-characters"
EDITOR_TOKEN = "editor-token"
VIEWER_TOKEN = "viewer-token"


def auth_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
    )


def workflow_body() -> dict:
    return {
        "name": "Authorized",
        "dsl": {
            "version": "1.0",
            "name": "Authorized",
            "variables": [],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 30,
                "recursion_limit": 50,
            },
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_auth_service_roles_and_disabled_mode(tmp_path) -> None:
    service = AuthService(auth_settings(tmp_path))
    assert service.authenticate(ADMIN_TOKEN).role == Role.ADMIN  # type: ignore[union-attr]
    assert service.authenticate(EDITOR_TOKEN).role == Role.EDITOR  # type: ignore[union-attr]
    assert service.authenticate(VIEWER_TOKEN).role == Role.VIEWER  # type: ignore[union-attr]
    assert service.authenticate("wrong") is None

    local = AuthService(Settings(data_dir=tmp_path))
    assert local.authenticate(None).role == Role.ADMIN  # type: ignore[union-attr]


def test_token_auth_and_rbac_over_http(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        meta = client.get("/api/meta")
        assert meta.status_code == 200
        assert meta.json()["auth_enabled"] is True

        assert client.get("/api/workflows").status_code == 401
        assert client.get("/api/workflows", headers=bearer(VIEWER_TOKEN)).status_code == 200
        assert (
            client.post(
                "/api/workflows",
                headers=bearer(VIEWER_TOKEN),
                json=workflow_body(),
            ).status_code
            == 403
        )

        created = client.post(
            "/api/workflows", headers=bearer(EDITOR_TOKEN), json=workflow_body()
        )
        assert created.status_code == 201
        assert (
            client.post(
                "/api/mcp/servers",
                headers=bearer(EDITOR_TOKEN),
                json={"name": "Denied", "transport": "stdio", "command": "python"},
            ).status_code
            == 403
        )

        login = client.post("/api/auth/login", json={"token": VIEWER_TOKEN})
        assert login.status_code == 200
        assert login.json()["role"] == "viewer"
        assert client.get("/api/workflows").status_code == 200
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/workflows").status_code == 401


def test_production_rejects_disabled_auth() -> None:
    with pytest.raises(RuntimeError, match="AUTH_MODE=token"):
        validate_runtime_settings(Settings(environment="production"))


def test_runtime_settings_validates_cost_governance_ceilings(tmp_path) -> None:
    base = dict(data_dir=tmp_path, environment="test")
    # 0 means "no ceiling" and is a valid default for the per-execution caps.
    validate_runtime_settings(Settings(**base))
    with pytest.raises(RuntimeError, match="MODEL_MAX_TOKENS_PER_EXECUTION"):
        validate_runtime_settings(Settings(**base, model_max_tokens_per_execution=-1))
    with pytest.raises(RuntimeError, match="MODEL_MAX_CONCURRENT_PER_EXECUTION"):
        validate_runtime_settings(Settings(**base, model_max_concurrent_per_execution=-1))
    with pytest.raises(RuntimeError, match="MODEL_MAX_COST_USD_PER_EXECUTION"):
        validate_runtime_settings(Settings(**base, model_max_cost_usd_per_execution="abc"))
    with pytest.raises(RuntimeError, match="MODEL_MAX_COST_USD_PER_EXECUTION"):
        validate_runtime_settings(Settings(**base, model_max_cost_usd_per_execution="-0.5"))
    validate_runtime_settings(
        Settings(**base, model_max_cost_usd_per_execution="0.01", model_max_tokens_per_execution=100)
    )


def test_run_idempotency_key_returns_one_execution(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = client.post(
            "/api/workflows", headers=bearer(EDITOR_TOKEN), json=workflow_body()
        ).json()
        headers = {**bearer(EDITOR_TOKEN), "Idempotency-Key": "run-once-1"}
        first = client.post(
            f"/api/workflows/{workflow['id']}/run",
            headers=headers,
            json={"session_id": "session-a"},
        )
        assert first.status_code == 201, first.text
        second = client.post(
            f"/api/workflows/{workflow['id']}/run",
            headers=headers,
            json={"session_id": "session-a"},
        )
        assert second.status_code == 201, second.text
        assert second.json()["id"] == first.json()["id"]

        conflict = client.post(
            f"/api/workflows/{workflow['id']}/run",
            headers=headers,
            json={"session_id": "session-b"},
        )
        assert conflict.status_code == 409
