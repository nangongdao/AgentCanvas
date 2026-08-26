"""MCP manager lifecycle, cache, retry, and reaper tests."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import BACKEND_DIR, Settings
from app.core.mcp_policy import McpStdioPolicy
from app.core.resilience import ResilienceBackendUnavailable
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import McpServer
from app.main import create_app
from app.mcphub.connection import McpConnectionError
from app.mcphub.manager import McpManager


async def _manager(tmp_path) -> tuple[McpManager, Any, Any]:
    settings = Settings(
        data_dir=tmp_path,
        mcp_stdio_allowed_commands=(sys.executable, "python", "python.exe"),
        mcp_stdio_allowed_roots=(BACKEND_DIR / "mcp_servers",),
    )
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    manager = McpManager(
        session_factory,
        create_secret_box(settings),
        McpStdioPolicy.from_settings(settings),
        # Windows CI and coverage instrumentation can make the stdio child
        # startup exceed two seconds; keep the lifecycle assertion deterministic.
        connect_timeout=5,
        request_timeout=settings.mcp_timeout_seconds,
        idle_timeout=0.01,
        reaper_interval=0.01,
    )
    return manager, engine, session_factory


def test_execution_manager_preserves_request_timeout(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, mcp_timeout_seconds=7.5)
    manager = McpManager(
        cast(Any, object()),
        create_secret_box(settings),
        McpStdioPolicy.from_settings(settings),
        request_timeout=settings.mcp_timeout_seconds,
    )

    child = manager.for_execution("execution-1")

    assert child.request_timeout == 7.5


async def _add_server(session_factory: Any, **values: Any) -> None:
    async with session_factory() as session:
        session.add(McpServer(**values))
        await session.commit()


async def test_manager_real_round_trip_cache_disconnect_and_reconnect(tmp_path) -> None:
    manager, engine, session_factory = await _manager(tmp_path)
    script = Path(BACKEND_DIR / "mcp_servers" / "calculator.py")
    await _add_server(
        session_factory,
        id="calculator",
        name="Calculator",
        transport="stdio",
        command=sys.executable,
        args_json=[str(script)],
        enabled=True,
    )
    try:
        tools = await manager.list_tools("calculator", refresh=True)
        assert {tool["name"] for tool in tools} >= {"eval_expr", "convert_units"}
        assert manager.status() == {"calculator": "connected"}

        connection = manager._connections["calculator"]  # noqa: SLF001
        cached = await manager.list_tools("calculator")
        assert cached == tools
        assert manager._connections["calculator"] is connection  # noqa: SLF001
        assert await manager.call_tool("calculator", "eval_expr", {"expression": "9 * 9"}) == {
            "content": "81",
            "is_error": False,
        }
        health = await manager.health_check("calculator")
        assert health["state"] == "healthy"
        assert health["connected"] is True
        assert health["tool_count"] >= 2
        assert {tool["name"] for tool in health["tools"]} >= {"eval_expr", "convert_units"}
        assert health["resilience"]["state"] == "closed"

        await manager.disconnect("calculator")
        assert manager.status() == {}
        async with session_factory() as session:
            row = await session.get(McpServer, "calculator")
            assert row is not None
            assert row.last_status == "disconnected"

        reconnected = await manager.get_connection("calculator")
        assert reconnected.alive
        assert await manager.get_connection("calculator") is reconnected
        await manager.disconnect("missing")
        await manager._set_status("missing", "ignored")  # noqa: SLF001
        await manager._save_tools_cache("missing", [])  # noqa: SLF001
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_manager_health_uses_async_shared_resilience_snapshot(tmp_path, monkeypatch) -> None:
    manager, engine, _session_factory = await _manager(tmp_path)

    class SharedResilience:
        def __init__(self) -> None:
            self.keys: list[str] = []

        async def snapshot_async(self, key: str) -> dict[str, Any]:
            self.keys.append(key)
            return {"key": key, "state": "open"}

    class UnavailableResilience:
        async def snapshot_async(self, _key: str) -> dict[str, Any]:
            raise ResilienceBackendUnavailable("shared resilience backend unavailable")

    shared = SharedResilience()

    async def list_tools(
        _server_id: str,
        *,
        refresh: bool = False,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        del refresh, project_id
        return []

    manager.resilience = cast(Any, shared)
    monkeypatch.setattr(manager, "list_tools", list_tools)
    try:
        health = await manager.health_check("shared")

        assert health["resilience"] == {
            "key": "mcp:shared:list_tools",
            "state": "open",
        }
        assert shared.keys == ["mcp:shared:list_tools"]

        manager.resilience = cast(Any, UnavailableResilience())
        unavailable = await manager.health_check("shared")
        assert unavailable["state"] == "degraded"
        assert unavailable["resilience"] == {
            "key": "mcp:shared:list_tools",
            "state": "unavailable",
            "error": "shared resilience backend unavailable",
        }
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_manager_coalesces_concurrent_tool_refreshes(tmp_path, monkeypatch) -> None:
    manager, engine, _session_factory = await _manager(tmp_path)
    row = McpServer(
        id="shared-refresh",
        name="Shared refresh",
        transport="stdio",
        command=sys.executable,
        args_json=[],
        enabled=True,
    )

    class FakeConnection:
        calls = 0

        async def list_tools(self) -> list[dict[str, Any]]:
            self.calls += 1
            await asyncio.sleep(0.02)
            return [{"name": "echo", "description": "", "input_schema": {}}]

    connection = FakeConnection()

    async def load_scoped_row(_server_id: str, _project_id: str | None) -> McpServer:
        return row

    async def get_connection(_server_id: str, *, project_id: str | None = None) -> FakeConnection:
        return connection

    async def save_tools_cache(_server_id: str, _tools: list[dict[str, Any]]) -> None:
        return None

    monkeypatch.setattr(manager, "_load_scoped_row", load_scoped_row)
    monkeypatch.setattr(manager, "get_connection", get_connection)
    monkeypatch.setattr(manager, "_save_tools_cache", save_tools_cache)
    try:
        results = await asyncio.gather(
            *(manager.list_tools("shared-refresh", refresh=True) for _ in range(50))
        )
        assert connection.calls == 1
        assert all(result == results[0] for result in results)
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_concurrent_tool_refreshes_release_route_database_sessions(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        mcp_max_concurrent=32,
        mcp_timeout_seconds=5,
        rate_limit_default_requests=1_000,
        rate_limit_mcp_requests=1_000,
        log_level="WARNING",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        container = app.state.container
        barrier = asyncio.Event()
        reached = 0

        async def synchronized_list_tools(
            _server_id: str,
            *,
            refresh: bool = False,
            project_id: str | None = None,
        ) -> list[dict[str, Any]]:
            nonlocal reached
            reached += 1
            if reached >= 15:
                barrier.set()
            await barrier.wait()
            async with container.session_factory() as session:
                await session.execute(select(McpServer.id).limit(1))
            return [{"name": "echo", "description": "", "input_schema": {}}]

        monkeypatch.setattr(container.mcp_manager, "list_tools", synchronized_list_tools)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.get(
                        "/api/mcp/servers/demo-calculator/tools",
                        params={"refresh": "true"},
                    )
                    for _ in range(16)
                )
            )

        assert [response.status_code for response in responses] == [200] * 16


async def test_manager_rejects_missing_disabled_and_disallowed_servers(tmp_path) -> None:
    manager, engine, session_factory = await _manager(tmp_path)
    await _add_server(
        session_factory,
        id="disabled",
        name="Disabled",
        transport="sse",
        url="http://127.0.0.1:1/sse",
        enabled=False,
    )
    await _add_server(
        session_factory,
        id="denied",
        name="Denied",
        transport="stdio",
        command="powershell.exe",
        args_json=[],
        enabled=True,
    )
    try:
        with pytest.raises(McpConnectionError, match="not found"):
            await manager.get_connection("missing")
        with pytest.raises(McpConnectionError, match="disabled"):
            await manager.get_connection("disabled")
        with pytest.raises(McpConnectionError, match="ALLOWED_COMMANDS"):
            await manager.get_connection("denied")
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_manager_records_connect_failure(tmp_path, monkeypatch) -> None:
    manager, engine, session_factory = await _manager(tmp_path)
    await _add_server(
        session_factory,
        id="broken",
        name="Broken",
        transport="sse",
        url="http://127.0.0.1:1/sse",
        enabled=True,
    )

    class BrokenConnection:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def start(self) -> None:
            raise McpConnectionError("synthetic connect failure")

    monkeypatch.setattr("app.mcphub.manager.McpConnection", BrokenConnection)
    try:
        with pytest.raises(McpConnectionError, match="synthetic"):
            await manager.get_connection("broken")
        async with session_factory() as session:
            row = await session.get(McpServer, "broken")
            assert row is not None
            assert row.last_status == "error: synthetic connect failure"
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_manager_retries_dead_calls_and_reports_exhaustion(tmp_path, monkeypatch) -> None:
    manager, engine, _session_factory = await _manager(tmp_path)

    class FakeConnection:
        def __init__(self, result: dict[str, Any] | Exception) -> None:
            self.result = result
            self.alive = True
            self.last_used = 0.0
            self.stopped = False

        async def call_tool(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        async def stop(self) -> None:
            self.stopped = True
            self.alive = False

    first = FakeConnection(OSError("connection reset"))
    second = FakeConnection({"content": "recovered", "is_error": False})
    attempts = iter((first, second))

    async def next_connection(_server_id: str) -> FakeConnection:
        connection = next(attempts)
        manager._connections["remote"] = connection  # type: ignore[assignment]  # noqa: SLF001
        return connection

    monkeypatch.setattr(manager, "get_connection", next_connection)
    try:
        result = await manager.call_tool("remote", "echo", {})
        assert result == {"content": "recovered", "is_error": False}
        assert first.stopped

        failures = iter(
            (
                FakeConnection(McpConnectionError("dead one")),
                FakeConnection(TimeoutError("dead two")),
            )
        )

        async def failing_connection(_server_id: str) -> FakeConnection:
            connection = next(failures)
            manager._connections["remote"] = connection  # type: ignore[assignment]  # noqa: SLF001
            return connection

        monkeypatch.setattr(manager, "get_connection", failing_connection)
        with pytest.raises(McpConnectionError, match="after 2 attempts"):
            await manager.call_tool("remote", "echo", {})
        degraded = await manager.health_check("remote")
        assert degraded["state"] == "degraded"
    finally:
        await manager.shutdown()
        await engine.dispose()


async def test_manager_reaper_closes_dead_and_idle_connections(tmp_path) -> None:
    manager, engine, _session_factory = await _manager(tmp_path)

    class ReapableConnection:
        def __init__(self, *, alive: bool, last_used: float) -> None:
            self.alive = alive
            self.last_used = last_used
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    dead = ReapableConnection(alive=False, last_used=0)
    idle = ReapableConnection(alive=True, last_used=0)
    manager._connections = cast(  # noqa: SLF001
        Any,
        {"dead": dead, "idle": idle},
    )
    fresh = ReapableConnection(alive=True, last_used=time.monotonic() + 60)
    manager.start_reaper()
    manager.start_reaper()
    try:
        for _ in range(100):
            if not manager._connections:  # noqa: SLF001
                break
            await asyncio.sleep(0.01)
        assert manager.status() == {}
        assert dead.stopped and idle.stopped

        manager._connections["fresh"] = cast(Any, fresh)  # noqa: SLF001
        await asyncio.sleep(0.03)
        assert manager.status() == {"fresh": "connected"}
        assert not fresh.stopped
    finally:
        await manager.shutdown()
        assert fresh.stopped
        await manager.shutdown()
        await engine.dispose()


async def test_for_execution_isolates_live_sessions_from_control_plane(tmp_path) -> None:
    """Worker-owned managers must not share or transfer live MCP sessions."""
    manager, engine, session_factory = await _manager(tmp_path)
    script = Path(BACKEND_DIR / "mcp_servers" / "calculator.py")
    await _add_server(
        session_factory,
        id="calculator",
        name="Calculator",
        transport="stdio",
        command=sys.executable,
        args_json=[str(script)],
        enabled=True,
    )
    try:
        worker = manager.for_execution("exec-owned")
        assert worker is not manager
        assert worker.owner_scope == "execution:exec-owned"
        tools = await worker.list_tools("calculator", refresh=True)
        assert {tool["name"] for tool in tools} >= {"eval_expr", "convert_units"}
        assert worker.status() == {"calculator": "connected"}
        # Control-plane manager must not inherit the worker live session.
        assert manager.status() == {}
        assert manager._connections == {}  # noqa: SLF001

        peer = manager.for_execution("exec-peer")
        assert peer.owner_scope == "execution:exec-peer"
        assert peer._connections == {}  # noqa: SLF001
        peer_tools = await peer.list_tools("calculator", refresh=True)
        assert peer_tools
        assert peer.status() == {"calculator": "connected"}
        assert worker.status() == {"calculator": "connected"}
        assert worker._connections["calculator"] is not peer._connections["calculator"]  # noqa: SLF001

        await worker.shutdown()
        assert worker.status() == {}
        assert peer.status() == {"calculator": "connected"}
        await peer.shutdown()
        assert peer.status() == {}
        # Execution-scoped managers never start the process-wide idle reaper.
        assert worker._reaper_task is None  # noqa: SLF001
        assert peer._reaper_task is None  # noqa: SLF001
    finally:
        await manager.shutdown()
        await engine.dispose()


def test_mcp_control_plane_probes_release_live_sessions(tmp_path) -> None:
    """API test/discover/health must not keep long-lived execution sessions."""
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        mcp_connect_timeout_seconds=5,
    )
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        health = client.get("/api/mcp/servers/demo-calculator/health")
        assert health.status_code == 200, health.text
        body = health.json()
        assert body["state"] == "healthy"
        assert body["tool_count"] >= 2
        assert body["resilience"]["state"] == "closed"
        assert container.mcp_manager.status() == {}

        tested = client.post("/api/mcp/servers/demo-calculator/test")
        assert tested.status_code == 200, tested.text
        assert {tool["name"] for tool in tested.json()["tools"]} >= {
            "eval_expr",
            "convert_units",
        }
        assert container.mcp_manager.status() == {}

        refreshed = client.get(
            "/api/mcp/servers/demo-calculator/tools",
            params={"refresh": "true"},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert container.mcp_manager.status() == {}

        cached = client.get("/api/mcp/servers/demo-calculator/tools")
        assert cached.status_code == 200, cached.text
        assert container.mcp_manager.status() == {}


def test_mcp_health_route_reports_connection_and_circuit_state(tmp_path) -> None:
    """Health remains a connectivity probe; live sessions are released afterward."""
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        mcp_connect_timeout_seconds=5,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/mcp/servers/demo-calculator/health")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["state"] == "healthy"
        assert body["connected"] is True
        assert body["tool_count"] >= 2
        assert body["resilience"]["state"] == "closed"
        # Live session is released after the control-plane probe returns.
        assert client.app.state.container.mcp_manager.status() == {}  # type: ignore[attr-defined]
