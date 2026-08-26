"""Project ownership and stdio-process quota coverage for MCP servers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import BACKEND_DIR, Settings
from app.core.mcp_policy import McpStdioPolicy
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import McpServer, Organization
from app.db.repositories import ProjectRepo
from app.main import create_app
from app.mcphub import McpConnectionError, McpManager, McpServerScopeError
from app.services.project_quotas import ProjectQuotaExceeded, ProjectQuotaService
from tests.test_tenancy import _login_as, _register, auth_settings


async def _environment(
    tmp_path: Path,
) -> tuple[
    Settings,
    AsyncEngine,
    async_sessionmaker[AsyncSession],
    str,
    str,
]:
    settings = Settings(
        data_dir=tmp_path,
        mcp_stdio_allowed_commands=(sys.executable, "python", "python.exe"),
        mcp_stdio_allowed_roots=(BACKEND_DIR / "mcp_servers",),
    )
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    async with sessions() as session:
        organization = Organization(name="MCP Quota Org", slug="mcp-quota-org")
        session.add(organization)
        await session.flush()
        first = await ProjectRepo(session).create(organization.id, "First Project", "first-project")
        second = await ProjectRepo(session).create(
            organization.id, "Second Project", "second-project"
        )
        await session.commit()
        return settings, engine, sessions, first.id, second.id


def _manager(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    idle_timeout: float = 600,
    reaper_interval: float = 60,
) -> McpManager:
    return McpManager(
        sessions,
        create_secret_box(settings),
        McpStdioPolicy.from_settings(settings),
        connect_timeout=5,
        idle_timeout=idle_timeout,
        reaper_interval=reaper_interval,
    )


async def _add_stdio_server(
    sessions: async_sessionmaker[AsyncSession],
    server_id: str,
    project_id: str,
    *,
    cached: bool = False,
) -> None:
    script = BACKEND_DIR / "mcp_servers" / "calculator.py"
    async with sessions() as session:
        session.add(
            McpServer(
                id=server_id,
                project_id=project_id,
                name=server_id,
                transport="stdio",
                command=sys.executable,
                args_json=[str(script)],
                tools_cache_json=(
                    [{"name": "cached", "description": "", "input_schema": {}}] if cached else []
                ),
                enabled=True,
            )
        )
        await session.commit()


async def _stdio_usage(sessions: async_sessionmaker[AsyncSession], project_id: str) -> int:
    async with sessions() as session:
        return (await ProjectQuotaService(session).snapshot(project_id)).stdio_mcp_processes


async def test_stdio_limit_disconnect_and_shutdown_release_slots(tmp_path: Path) -> None:
    settings, engine, sessions, project_id, _foreign_id = await _environment(tmp_path)
    manager = _manager(settings, sessions)
    await _add_stdio_server(sessions, "quota-first", project_id)
    await _add_stdio_server(sessions, "quota-second", project_id)
    async with sessions() as session:
        await ProjectQuotaService(session).configure(project_id, stdio_mcp_process_limit=1)
        await session.commit()

    try:
        await manager.list_tools("quota-first", refresh=True, project_id=project_id)
        assert await _stdio_usage(sessions, project_id) == 1

        with pytest.raises(ProjectQuotaExceeded):
            await manager.list_tools("quota-second", refresh=True, project_id=project_id)
        assert manager.status() == {"quota-first": "connected"}
        assert await _stdio_usage(sessions, project_id) == 1

        await manager.disconnect("quota-first")
        assert await _stdio_usage(sessions, project_id) == 0

        await manager.list_tools("quota-second", refresh=True, project_id=project_id)
        assert await _stdio_usage(sessions, project_id) == 1
        await manager.shutdown()
        assert await _stdio_usage(sessions, project_id) == 0
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_connect_failure_releases_reserved_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, sessions, project_id, _foreign_id = await _environment(tmp_path)
    manager = _manager(settings, sessions)
    await _add_stdio_server(sessions, "broken-project-server", project_id)

    class BrokenConnection:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.alive = False

        async def start(self) -> None:
            raise McpConnectionError("synthetic project connect failure")

        async def stop(self) -> None:
            self.alive = False

    monkeypatch.setattr("app.mcphub.manager.McpConnection", BrokenConnection)
    try:
        with pytest.raises(McpConnectionError, match="synthetic project"):
            await manager.get_connection("broken-project-server", project_id=project_id)
        assert await _stdio_usage(sessions, project_id) == 0
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_idle_reaper_releases_project_slot(tmp_path: Path) -> None:
    settings, engine, sessions, project_id, _foreign_id = await _environment(tmp_path)
    manager = _manager(settings, sessions, idle_timeout=0.01, reaper_interval=0.01)
    await _add_stdio_server(sessions, "idle-project-server", project_id)
    try:
        await manager.list_tools("idle-project-server", refresh=True, project_id=project_id)
        assert await _stdio_usage(sessions, project_id) == 1
        manager.start_reaper()
        for _ in range(200):
            if not manager.status() and await _stdio_usage(sessions, project_id) == 0:
                break
            await asyncio.sleep(0.01)
        assert manager.status() == {}
        assert await _stdio_usage(sessions, project_id) == 0
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_two_managers_cannot_start_duplicate_project_process(tmp_path: Path) -> None:
    settings, engine, sessions, project_id, _foreign_id = await _environment(tmp_path)
    first = _manager(settings, sessions)
    second = _manager(settings, sessions)
    await _add_stdio_server(sessions, "shared-project-server", project_id)
    try:
        results = await asyncio.gather(
            first.get_connection("shared-project-server", project_id=project_id),
            second.get_connection("shared-project-server", project_id=project_id),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        assert len(failures) == 1
        assert isinstance(failures[0], McpConnectionError)
        assert "already active" in str(failures[0])
        assert len(first.status()) + len(second.status()) == 1
        assert await _stdio_usage(sessions, project_id) == 1
    finally:
        await first.shutdown()
        await second.shutdown()
        assert await _stdio_usage(sessions, project_id) == 0
        await engine.dispose()


async def test_cached_tools_and_calls_reject_foreign_project(tmp_path: Path) -> None:
    settings, engine, sessions, project_id, foreign_id = await _environment(tmp_path)
    manager = _manager(settings, sessions)
    await _add_stdio_server(sessions, "cached-project-server", project_id, cached=True)
    try:
        assert await manager.list_tools("cached-project-server", project_id=project_id) == [
            {"name": "cached", "description": "", "input_schema": {}}
        ]
        with pytest.raises(McpServerScopeError):
            await manager.list_tools("cached-project-server", project_id=foreign_id)
        with pytest.raises(McpServerScopeError):
            await manager.call_tool("cached-project-server", "cached", {}, project_id=foreign_id)
        assert manager.status() == {}
        assert await _stdio_usage(sessions, project_id) == 0
    finally:
        await manager.shutdown()
        await engine.dispose()


def test_mcp_routes_enforce_project_admin_scope_and_report_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "mcp-admin@example.com")
        first_org = client.post("/api/organizations", json={"name": "MCP First"}).json()
        second_org = client.post("/api/organizations", json={"name": "MCP Second"}).json()
        _register(client, "mcp-member@example.com", role="viewer")
        _login_as(client, "mcp-admin@example.com")
        assert (
            client.post(
                f"/api/organizations/{first_org['id']}/members",
                json={"email": "mcp-member@example.com", "role": "admin"},
            ).status_code
            == 201
        )
        first_project = client.post(
            f"/api/organizations/{first_org['id']}/projects", json={"name": "First"}
        ).json()
        second_project = client.post(
            f"/api/organizations/{second_org['id']}/projects", json={"name": "Second"}
        ).json()

        def server(name: str, project_id: str | None = None) -> dict[str, Any]:
            return {
                "name": name,
                "project_id": project_id,
                "transport": "streamable_http",
                "url": "https://mcp.invalid/service",
            }

        global_server = client.post("/api/mcp/servers", json=server("Global")).json()
        first_server = client.post(
            "/api/mcp/servers", json=server("First MCP", first_project["id"])
        ).json()
        second_server = client.post(
            "/api/mcp/servers", json=server("Second MCP", second_project["id"])
        ).json()
        assert first_server["project_id"] == first_project["id"]

        _login_as(client, "mcp-member@example.com")
        global_rows = client.get("/api/mcp/servers")
        assert global_rows.status_code == 200
        global_ids = {row["id"] for row in global_rows.json()}
        assert global_server["id"] in global_ids
        assert first_server["id"] not in global_ids
        assert second_server["id"] not in global_ids
        first_rows = client.get("/api/mcp/servers", params={"project_id": first_project["id"]})
        assert first_rows.status_code == 200
        assert [row["id"] for row in first_rows.json()] == [first_server["id"]]
        assert (
            client.get("/api/mcp/servers", params={"project_id": second_project["id"]}).status_code
            == 403
        )

        member_created = client.post(
            "/api/mcp/servers",
            json=server("Member MCP", first_project["id"]),
        )
        assert member_created.status_code == 201, member_created.text
        assert client.post("/api/mcp/servers", json=server("Denied global")).status_code == 403
        assert (
            client.put(
                f"/api/mcp/servers/{first_server['id']}", json={"enabled": False}
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/mcp/servers/{second_server['id']}", json={"enabled": False}
            ).status_code
            == 403
        )
        assert client.get(f"/api/mcp/servers/{second_server['id']}/tools").status_code == 403
        assert client.post(f"/api/mcp/servers/{second_server['id']}/test").status_code == 403
        assert client.get(f"/api/mcp/servers/{second_server['id']}/health").status_code == 403

        async def reject_quota(_server_id: str, **_kwargs: Any) -> list[dict[str, Any]]:
            raise ProjectQuotaExceeded("stdio_mcp_process", 1, 1, 1)

        app = cast(FastAPI, client.app)
        monkeypatch.setattr(app.state.container.mcp_manager, "list_tools", reject_quota)
        rejected = client.get(f"/api/mcp/servers/{first_server['id']}/tools")
        assert rejected.status_code == 429
        assert rejected.headers["Retry-After"] == "1"

        assert client.delete(f"/api/mcp/servers/{first_server['id']}").status_code == 204
        assert client.delete(f"/api/mcp/servers/{second_server['id']}").status_code == 403

        _login_as(client, "mcp-admin@example.com")
        audit = client.get(
            "/api/audit-logs",
            params={"project_id": first_project["id"], "limit": 100},
        )
        assert audit.status_code == 200
        mcp_events = [row for row in audit.json()["items"] if row["resource_type"] == "mcp_server"]
        assert {row["action"] for row in mcp_events} >= {
            "mcp_server.created",
            "mcp_server.updated",
            "mcp_server.deleted",
        }
        assert {row["project_id"] for row in mcp_events} == {first_project["id"]}
