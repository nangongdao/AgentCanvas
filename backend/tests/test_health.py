"""Readiness / liveness probe tests (R-08 / U2-5).

``/livez`` is cheap liveness; ``/readyz`` verifies the dependencies a request
actually needs (database, Alembic head, durable checkpointer, config safety,
vector store) and returns 503 until every blocking check passes.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
    )


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def test_healthz_and_livez_are_unauthenticated_200(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/livez").status_code == 200
    assert client.get("/livez").json()["status"] == "alive"


def test_metrics_are_unauthenticated_and_request_id_is_exposed(client: TestClient) -> None:
    assert client.get("/livez").status_code == 200
    response = client.get("/metrics", headers={"X-Request-ID": "metrics-check"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "metrics-check"
    assert "agentcanvas_http_requests_total" in response.text


def test_readyz_ready_when_dependencies_ok(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    checks = body["checks"]
    for name in ("database", "migrations", "checkpointer", "config"):
        assert checks[name]["state"] == "ready", name
    assert checks["database"]["backend"] == "sqlite"
    # Checkpointer is eagerly built at startup now, so it reports sqlite.
    assert checks["checkpointer"]["backend"] == "sqlite"
    # Default SQLite deployments use embedded Chroma; readiness uses public health().
    assert checks["vector_store"]["state"] == "ready"
    assert checks["vector_store"]["backend"] == "chroma"


def test_readyz_503_when_migration_head_mismatches(
    client: TestClient, monkeypatch
) -> None:
    import app.api.routes.health as health_mod

    monkeypatch.setattr(health_mod, "CURRENT_REVISION", "not-the-real-head")
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["migrations"]["state"] == "unavailable"


def test_production_startup_fails_closed_when_checkpointer_broken(
    tmp_path, monkeypatch
) -> None:
    import app.engine.checkpoint as cp

    def _boom(_settings: Settings) -> None:
        raise OSError("corrupt checkpoint db")

    monkeypatch.setattr(cp, "_try_build_sqlite_saver", _boom)
    settings = Settings(
        data_dir=tmp_path,
        environment="production",
        auth_mode="token",
        admin_api_token="x" * 32,
        editor_api_token="x" * 32,
        viewer_api_token="x" * 32,
        secret_key=FERNET_KEY,
    )

    with pytest.raises(Exception) as exc_info, TestClient(create_app(settings)):
        pass
    assert "checkpointer" in str(exc_info.value).lower()
