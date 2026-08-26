"""Secret encryption, migration, and MCP masking tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.container import migrate_stored_secrets
from app.core.secret_providers import SecretResolver, build_secret_provider_chain
from app.core.security import SecretBox, create_secret_box
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import McpServer
from app.main import create_app
from app.mcphub.manager import spec_from_row
from tests.test_auth import ADMIN_TOKEN, auth_settings, bearer


def test_generated_local_key_is_stable(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    first = create_secret_box(settings)
    encrypted = first.encrypt("local-secret")

    second = create_secret_box(settings)

    assert second.decrypt(encrypted) == "local-secret"
    assert (tmp_path / ".agentcanvas.key").is_file()
    assert "local-secret" not in encrypted


def test_invalid_explicit_key_is_rejected(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, secret_key="not-a-fernet-key")
    try:
        create_secret_box(settings)
    except RuntimeError as error:
        assert "valid Fernet" in str(error)
    else:
        raise AssertionError("invalid Fernet key was accepted")


def test_secret_box_empty_plaintext_invalid_token_and_mapping_shapes() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    assert box.encrypt("") == ""
    assert box.decrypt("") == ""
    assert box.decrypt("plain:legacy") == "legacy"
    assert box.encrypt_mapping({}) == ""
    assert box.decrypt_mapping(None) == {}

    with pytest.raises(RuntimeError, match="failed to decrypt"):
        box.decrypt("not-a-fernet-token")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        box.decrypt_mapping(box.encrypt("not-json"))
    with pytest.raises(RuntimeError, match="invalid shape"):
        box.decrypt_mapping(box.encrypt('["not", "a mapping"]'))
    with pytest.raises(RuntimeError, match="invalid shape"):
        box.decrypt_mapping(box.encrypt('{"key": 123}'))


def test_production_requires_explicit_secret_key(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="production requires"):
        create_secret_box(Settings(data_dir=tmp_path, environment="production"))


async def test_legacy_mcp_secrets_are_encrypted_and_resolved(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            session.add(
                McpServer(
                    id="legacy-secrets",
                    name="Legacy",
                    transport="sse",
                    url="https://mcp.invalid/sse",
                    env_json={"API_TOKEN": "env-secret"},
                    headers_json={"Authorization": "Bearer header-secret"},
                )
            )
            await session.commit()

        secret_box = create_secret_box(settings)
        assert await migrate_stored_secrets(session_factory, secret_box) == 2

        async with session_factory() as session:
            row = await session.get(McpServer, "legacy-secrets")
            assert row is not None
            assert row.env_json == {}
            assert row.headers_json == {}
            assert "env-secret" not in (row.env_encrypted or "")
            assert "header-secret" not in (row.headers_encrypted or "")
            spec = spec_from_row(row, secret_box)
            assert spec.env == {"API_TOKEN": "env-secret"}
            assert spec.headers == {"Authorization": "Bearer header-secret"}
    finally:
        await engine.dispose()


def test_mcp_api_masks_and_preserves_secrets(tmp_path) -> None:
    settings = auth_settings(tmp_path)
    settings = replace(settings, secret_key=Fernet.generate_key().decode())
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/mcp/servers",
            headers=bearer(ADMIN_TOKEN),
            json={
                "name": "Remote secrets",
                "transport": "sse",
                "url": "https://mcp.invalid/sse",
                "env": {"API_TOKEN": "env-secret"},
                "headers": {"Authorization": "Bearer header-secret"},
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["env"] == {"API_TOKEN": "********"}
        assert body["headers"] == {"Authorization": "********"}
        assert "env-secret" not in created.text
        assert "header-secret" not in created.text

        updated = client.put(
            f"/api/mcp/servers/{body['id']}",
            headers=bearer(ADMIN_TOKEN),
            json={"name": "Renamed", "env": body["env"], "headers": body["headers"]},
        )
        assert updated.status_code == 200, updated.text

    engine = create_engine(settings)

    async def verify() -> None:
        try:
            async with create_session_factory(engine)() as session:
                row = await session.get(McpServer, body["id"])
                assert row is not None
                secret_box = create_secret_box(settings)
                assert secret_box.decrypt_mapping(row.env_encrypted) == {
                    "API_TOKEN": "env-secret"
                }
                assert secret_box.decrypt_mapping(row.headers_encrypted) == {
                    "Authorization": "Bearer header-secret"
                }
        finally:
            await engine.dispose()

    asyncio.run(verify())


def test_secret_references_are_masked_stored_as_uris_and_resolved_at_runtime(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENTCANVAS_MODEL_KEY", "model-runtime-secret")
    monkeypatch.setenv("AGENTCANVAS_MCP_KEY", "mcp-runtime-secret")
    settings = auth_settings(tmp_path)
    settings = replace(settings, secret_key=Fernet.generate_key().decode())
    with TestClient(create_app(settings)) as client:
        model = client.post(
            "/api/models",
            headers=bearer(ADMIN_TOKEN),
            json={
                "id": "reference-model",
                "name": "Reference model",
                "provider": "mock",
                "model_name": "mock",
                "api_key": "env://AGENTCANVAS_MODEL_KEY",
                "kind": "chat",
            },
        )
        assert model.status_code == 201, model.text
        assert model.json()["has_api_key"] is True
        assert model.json()["api_key_source"] == "env"
        assert "model-runtime-secret" not in model.text

        server = client.post(
            "/api/mcp/servers",
            headers=bearer(ADMIN_TOKEN),
            json={
                "name": "Reference MCP",
                "transport": "sse",
                "url": "https://mcp.invalid/sse",
                "env": {"TOKEN": "env://AGENTCANVAS_MCP_KEY"},
                "headers": {"Authorization": "env://AGENTCANVAS_MCP_KEY"},
            },
        )
        assert server.status_code == 201, server.text
        body = server.json()
        assert body["env"] == {"TOKEN": "********"}
        assert body["env_sources"] == {"TOKEN": "env"}
        assert body["headers_sources"] == {"Authorization": "env"}
        assert "mcp-runtime-secret" not in server.text

        monkeypatch.delenv("AGENTCANVAS_MCP_KEY")
        listed = client.get("/api/mcp/servers", headers=bearer(ADMIN_TOKEN))
        assert listed.status_code == 200, listed.text
        listed_body = next(row for row in listed.json() if row["id"] == body["id"])
        assert listed_body["env_sources"] == {"TOKEN": "env"}
        assert listed_body["headers_sources"] == {"Authorization": "env"}
        preserved = client.put(
            f"/api/mcp/servers/{body['id']}",
            headers=bearer(ADMIN_TOKEN),
            json={"env": listed_body["env"], "headers": listed_body["headers"]},
        )
        assert preserved.status_code == 200, preserved.text
        monkeypatch.setenv("AGENTCANVAS_MCP_KEY", "mcp-runtime-secret")

    engine = create_engine(settings)

    async def verify() -> None:
        try:
            async with create_session_factory(engine)() as session:
                row = await session.get(McpServer, body["id"])
                assert row is not None
                secret_box = create_secret_box(settings)
                resolver = SecretResolver(
                    secret_box,
                    build_secret_provider_chain(settings.docker_secret_dir),
                )
                raw = resolver.raw_mapping(row.env_encrypted)
                assert raw == {"TOKEN": "env://AGENTCANVAS_MCP_KEY"}
                assert resolver.decrypt_mapping(row.env_encrypted) == {
                    "TOKEN": "mcp-runtime-secret"
                }
        finally:
            await engine.dispose()

    asyncio.run(verify())


def test_external_secret_resolver_is_injected_at_application_boundary(tmp_path) -> None:
    calls: list[str] = []

    def resolve_external(key: str) -> str:
        calls.append(key)
        return "external-runtime-secret"

    settings = auth_settings(tmp_path)
    settings = replace(settings, secret_key=Fernet.generate_key().decode())
    app = create_app(
        settings,
        external_secret_resolver=resolve_external,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/models",
            headers=bearer(ADMIN_TOKEN),
            json={
                "id": "external-reference-model",
                "name": "External reference",
                "provider": "mock",
                "model_name": "mock",
                "api_key": "external://team/model",
                "kind": "chat",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["api_key_source"] == "external"
        container = app.state.container
        stored = container.secret_resolver.encrypt("external://team/model")
        assert container.secret_resolver.decrypt(stored) == "external-runtime-secret"
        assert calls == ["team/model"]
