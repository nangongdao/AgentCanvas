"""MCP connection manager: lazy connect, idle reaper, tools cache."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.mcp_policy import McpStdioPolicy, StdioCommandDenied
from app.core.observability import Observability
from app.core.resilience import (
    CircuitOpenError,
    ResilienceBackendUnavailable,
    ResilienceConfig,
    ResilienceRegistry,
    RetryBudgetExceeded,
    is_transient_error,
)
from app.core.secret_providers import SecretResolver
from app.core.security import SecretBox
from app.db.models import McpCatalogVersion, McpServer
from app.db.repositories.mcp import McpServerRepo
from app.mcphub.connection import McpConnection, McpConnectionError, McpServerSpec
from app.services.mcp_catalog import (
    McpCatalogPolicyError,
    catalog_version_is_usable,
    validate_catalog_binding,
)
from app.services.project_quotas import ProjectQuotaService

logger = logging.getLogger(__name__)


class McpServerScopeError(McpConnectionError):
    """Raised when a workflow attempts to use another project's server."""


def spec_from_row(
    row: McpServer,
    secret_box: SecretBox,
    secret_resolver: SecretResolver | None = None,
) -> McpServerSpec:
    resolver = secret_resolver or SecretResolver(secret_box)
    env = (
        resolver.decrypt_mapping(row.env_encrypted)
        if row.env_encrypted
        else {str(key): str(value) for key, value in (row.env_json or {}).items()}
    )
    headers = (
        resolver.decrypt_mapping(row.headers_encrypted)
        if row.headers_encrypted
        else {str(key): str(value) for key, value in (row.headers_json or {}).items()}
    )
    return McpServerSpec(
        id=row.id,
        name=row.name,
        transport=row.transport,
        command=row.command,
        args=tuple(str(a) for a in (row.args_json or [])),
        env=env,
        url=row.url,
        headers=headers,
    )


class McpManager:
    """Lazily connects to registered MCP servers and reuses sessions.

    Control-plane instances may keep short-lived discovery sessions with an idle
    reaper. Execution workers must call :meth:`for_execution` so live sessions
    stay bound to the leased attempt and are released when the attempt ends.
    Sessions are never transferred between workers; resume reconnects from
    durable server configuration.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        secret_box: SecretBox,
        stdio_policy: McpStdioPolicy,
        *,
        secret_resolver: SecretResolver | None = None,
        connect_timeout: float = 20.0,
        request_timeout: float = 30.0,
        idle_timeout: float = 600.0,
        reaper_interval: float = 60.0,
        observability: Observability | None = None,
        resilience: ResilienceRegistry | None = None,
        resilience_config: ResilienceConfig | None = None,
        owner_scope: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.secret_box = secret_box
        self.secret_resolver = secret_resolver or SecretResolver(secret_box)
        self.stdio_policy = stdio_policy
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.idle_timeout = idle_timeout
        self.reaper_interval = reaper_interval
        self.observability = observability
        self.resilience = resilience or ResilienceRegistry()
        self.resilience_config = resilience_config or ResilienceConfig()
        self.owner_scope = owner_scope
        self._connections: dict[str, McpConnection] = {}
        self._quota_projects: dict[str, str] = {}
        self._connect_locks: dict[str, asyncio.Lock] = {}
        self._tool_refreshes: dict[str, asyncio.Task[list[dict[str, Any]]]] = {}
        self._reaper_task: asyncio.Task[None] | None = None

    def for_execution(self, execution_id: str) -> McpManager:
        """Create a worker-owned manager that does not share live MCP sessions.

        The child reuses policy, secrets, resilience and DB access but keeps an
        isolated connection table. Call :meth:`shutdown` when the lease ends so
        stdio processes and project quotas are released promptly.
        """
        if not execution_id:
            raise ValueError("execution_id is required for worker-owned MCP sessions")
        return McpManager(
            self.session_factory,
            self.secret_box,
            self.stdio_policy,
            secret_resolver=self.secret_resolver,
            connect_timeout=self.connect_timeout,
            request_timeout=self.request_timeout,
            idle_timeout=self.idle_timeout,
            reaper_interval=self.reaper_interval,
            observability=self.observability,
            resilience=self.resilience,
            resilience_config=self.resilience_config,
            owner_scope=f"execution:{execution_id}",
        )

    def start_reaper(self) -> None:
        if self.owner_scope is not None:
            # Execution-scoped managers are short-lived; the owner releases them.
            return
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reaper_loop(), name="mcp-reaper")

    async def shutdown(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        refreshes = list(self._tool_refreshes.values())
        self._tool_refreshes.clear()
        for task in refreshes:
            task.cancel()
        await asyncio.gather(*refreshes, return_exceptions=True)
        conns = list(self._connections.items())
        self._connections.clear()
        for server_id, conn in conns:
            try:
                await conn.stop()
            finally:
                await self._release_process(server_id)
        for server_id in list(self._quota_projects):
            await self._release_process(server_id)

    # ------------------------------------------------------------------

    async def get_connection(
        self, server_id: str, *, project_id: str | None = None
    ) -> McpConnection:
        """Return a live connection, lazily connecting on first use."""
        row = await self._load_scoped_row(server_id, project_id)
        conn = self._connections.get(server_id)
        if conn is not None and conn.alive:
            return conn

        lock = self._connect_locks.setdefault(server_id, asyncio.Lock())
        async with lock:
            conn = self._connections.get(server_id)
            if conn is not None and conn.alive:
                return conn
            stale = self._connections.pop(server_id, None)
            if stale is not None:
                try:
                    await stale.stop()
                finally:
                    await self._release_process(server_id)
            if not row.enabled:
                raise McpConnectionError(f"MCP server disabled: {row.name}")
            if row.transport == "stdio":
                try:
                    self.stdio_policy.validate(
                        row.command,
                        tuple(str(value) for value in (row.args_json or [])),
                    )
                except StdioCommandDenied as exc:
                    raise McpConnectionError(str(exc)) from exc

            conn = McpConnection(
                spec_from_row(row, self.secret_box, self.secret_resolver),
                connect_timeout=self.connect_timeout,
                request_timeout=self.request_timeout,
            )
            quota_project_id = await self._reserve_process(row)
            try:
                await conn.start()
                await self._set_status(server_id, "connected")
            except BaseException as exc:
                with suppress(Exception):
                    await conn.stop()
                if quota_project_id is not None:
                    await self._release_process(server_id, quota_project_id)
                if isinstance(exc, McpConnectionError):
                    await self._set_status(server_id, f"error: {exc}")
                raise
            self._connections[server_id] = conn
            if quota_project_id is not None:
                self._quota_projects[server_id] = quota_project_id
            logger.info("mcp connected: %s (%s)", row.name, row.transport)
            return conn

    async def list_tools(
        self,
        server_id: str,
        *,
        refresh: bool = False,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return tools, from DB cache unless refresh or cache empty."""
        row = await self._load_scoped_row(server_id, project_id)
        if not refresh and row.tools_cache_json:
            return list(row.tools_cache_json)
        task = self._tool_refreshes.get(server_id)
        if task is None or task.done():
            task = asyncio.create_task(
                self._refresh_tools(server_id, project_id),
                name=f"mcp-tools-{server_id}",
            )
            self._tool_refreshes[server_id] = task

            def finish_tools_refresh(
                completed: asyncio.Task[list[dict[str, Any]]],
            ) -> None:
                self._finish_tools_refresh(server_id, completed)

            task.add_done_callback(finish_tools_refresh)
        return list(await asyncio.shield(task))

    async def call_tool(
        self,
        server_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 30.0,
        max_attempts: int | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Call a tool with retry budget and circuit protection."""
        started = time.perf_counter()
        status = "failed"
        attempts = max_attempts or self.resilience_config.max_attempts

        async def call_once() -> dict[str, Any]:
            try:
                if project_id is None:
                    conn = await self.get_connection(server_id)
                else:
                    conn = await self.get_connection(server_id, project_id=project_id)
                return await conn.call_tool(tool_name, arguments, timeout=timeout)
            except McpServerScopeError:
                raise
            except (McpConnectionError, TimeoutError, OSError):
                # Drop dead connection so the resilience retry reconnects.
                stale = self._connections.pop(server_id, None)
                if stale is not None:
                    try:
                        await stale.stop()
                    finally:
                        await self._release_process(server_id)
                raise

        try:
            result = await self.resilience.execute(
                f"mcp:{server_id}:tool:{tool_name}",
                call_once,
                config=self.resilience_config,
                retryable=lambda exc: (
                    isinstance(exc, (McpConnectionError, TimeoutError, OSError))
                    or is_transient_error(exc)
                ),
                max_attempts=max_attempts,
            )
            status = "succeeded"
            return result
        except McpServerScopeError:
            raise
        except (CircuitOpenError, RetryBudgetExceeded) as exc:
            raise McpConnectionError(str(exc)) from exc
        except Exception as exc:
            if isinstance(exc, (McpConnectionError, TimeoutError, OSError)) or is_transient_error(
                exc
            ):
                raise McpConnectionError(
                    f"tool call failed after {attempts} attempts: {exc}"
                ) from exc
            raise
        finally:
            if self.observability is not None:
                self.observability.record_mcp(
                    status=status,
                    duration=time.perf_counter() - started,
                )

    async def disconnect(self, server_id: str) -> None:
        refresh = self._tool_refreshes.pop(server_id, None)
        if refresh is not None:
            refresh.cancel()
            await asyncio.gather(refresh, return_exceptions=True)
        conn = self._connections.pop(server_id, None)
        if conn is not None:
            try:
                await conn.stop()
            finally:
                await self._release_process(server_id)
            await self._set_status(server_id, "disconnected")
        else:
            await self._release_process(server_id)

    def status(self) -> dict[str, str]:
        return {sid: ("connected" if c.alive else "dead") for sid, c in self._connections.items()}

    async def health_check(
        self, server_id: str, *, project_id: str | None = None
    ) -> dict[str, Any]:
        """Probe connection and tool discovery without exposing secrets."""
        started = time.perf_counter()
        try:
            tools = await self.list_tools(server_id, refresh=True, project_id=project_id)
        except Exception as exc:  # noqa: BLE001 - health is a report, not a control path
            return {
                "server_id": server_id,
                "state": "degraded",
                "error": str(exc)[:200],
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "resilience": await self._resilience_health_snapshot(server_id),
            }
        resilience = await self._resilience_health_snapshot(server_id)
        return {
            "server_id": server_id,
            "state": "degraded" if resilience.get("state") == "unavailable" else "healthy",
            "connected": server_id in self._connections and self._connections[server_id].alive,
            "tool_count": len(tools),
            "tools": tools,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "resilience": resilience,
        }

    async def _resilience_health_snapshot(self, server_id: str) -> dict[str, Any]:
        key = f"mcp:{server_id}:list_tools"
        try:
            return await self.resilience.snapshot_async(key) or {"key": key, "state": "unknown"}
        except ResilienceBackendUnavailable as exc:
            return {"key": key, "state": "unavailable", "error": str(exc)}

    # ------------------------------------------------------------------

    async def _refresh_tools(self, server_id: str, project_id: str | None) -> list[dict[str, Any]]:
        async def list_once() -> list[dict[str, Any]]:
            try:
                if project_id is None:
                    conn = await self.get_connection(server_id)
                else:
                    conn = await self.get_connection(server_id, project_id=project_id)
                return await conn.list_tools()
            except McpServerScopeError:
                raise
            except (McpConnectionError, TimeoutError, OSError):
                stale = self._connections.pop(server_id, None)
                if stale is not None:
                    try:
                        await stale.stop()
                    finally:
                        await self._release_process(server_id)
                raise

        tools = await self.resilience.execute(
            f"mcp:{server_id}:list_tools",
            list_once,
            config=self.resilience_config,
            retryable=lambda exc: (
                isinstance(exc, (McpConnectionError, TimeoutError, OSError))
                or is_transient_error(exc)
            ),
        )
        await self._save_tools_cache(server_id, tools)
        return tools

    def _finish_tools_refresh(
        self,
        server_id: str,
        completed: asyncio.Task[list[dict[str, Any]]],
    ) -> None:
        if self._tool_refreshes.get(server_id) is completed:
            self._tool_refreshes.pop(server_id, None)
        if not completed.cancelled():
            completed.exception()

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(self.reaper_interval)
            now = time.monotonic()
            for sid, conn in list(self._connections.items()):
                if not conn.alive or now - conn.last_used > self.idle_timeout:
                    logger.info("mcp reaper: closing idle/dead connection %s", sid)
                    self._connections.pop(sid, None)
                    try:
                        await conn.stop()
                    finally:
                        await self._release_process(sid)

    async def _load_row(self, server_id: str) -> McpServer | None:
        async with self.session_factory() as session:
            return await McpServerRepo(session).get(server_id)

    async def _load_scoped_row(self, server_id: str, project_id: str | None) -> McpServer:
        row = await self._load_row(server_id)
        if row is None:
            raise McpConnectionError(f"MCP server not found: {server_id}")
        if row.project_id is not None and row.project_id != project_id:
            raise McpServerScopeError(f"MCP server not found: {server_id}")
        if row.catalog_version_id is not None:
            async with self.session_factory() as session:
                version = await session.get(McpCatalogVersion, row.catalog_version_id)
            if (
                version is None
                or version.entry_id != row.catalog_entry_id
                or not catalog_version_is_usable(version)
            ):
                raise McpConnectionError("MCP server catalog version is not approved")
            try:
                validate_catalog_binding(row, version)
            except McpCatalogPolicyError as exc:
                raise McpConnectionError(f"MCP catalog policy denied server: {exc}") from exc
        return row

    async def _reserve_process(self, row: McpServer) -> str | None:
        if row.project_id is None or row.transport != "stdio":
            return None
        async with self.session_factory() as session:
            created = await ProjectQuotaService(session).reserve(
                row.project_id,
                "stdio_mcp_process",
                row.id,
            )
            if not created:
                raise McpConnectionError(f"MCP server process is already active: {row.id}")
            await session.commit()
        return row.project_id

    async def _release_process(self, server_id: str, project_id: str | None = None) -> None:
        quota_project_id = project_id or self._quota_projects.pop(server_id, None)
        if quota_project_id is None:
            return
        self._quota_projects.pop(server_id, None)
        async with self.session_factory() as session:
            await ProjectQuotaService(session).release(
                quota_project_id,
                "stdio_mcp_process",
                server_id,
            )
            await session.commit()

    async def _set_status(self, server_id: str, status: str) -> None:
        async with self.session_factory() as session:
            repo = McpServerRepo(session)
            row = await repo.get(server_id)
            if row is not None:
                row.last_status = status[:300]
                await session.commit()

    async def _save_tools_cache(self, server_id: str, tools: list[dict[str, Any]]) -> None:
        async with self.session_factory() as session:
            repo = McpServerRepo(session)
            row = await repo.get(server_id)
            if row is not None:
                row.tools_cache_json = tools
                row.tools_cached_at = datetime.now(UTC)
                await session.commit()
