"""Owner-task MCP connection.

The mcp SDK's anyio-based context managers must be entered and exited in the
SAME asyncio task. This module wraps each server connection in a dedicated
owner task that opens the MCP v2 Client, then serves call requests from a
queue until shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Protocol, cast

import anyio
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client import Transport
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpServerSpec:
    """Connection parameters resolved from the DB row."""

    id: str
    name: str
    transport: str  # stdio | sse | streamable_http
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class McpConnectionError(RuntimeError):
    """Raised when a server cannot be connected or a call fails fatally."""


@dataclass
class _McpRequest:
    kind: Literal["list_tools", "call_tool"]
    future: asyncio.Future[Any]
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0


class _ReadableStream(Protocol):
    async def receive(self) -> Any: ...

    async def aclose(self) -> None: ...


class _ObservedReceiveStream:
    """Expose transport EOF to the owner task while the SDK consumes messages."""

    def __init__(self, source: _ReadableStream, closed: asyncio.Event) -> None:
        self._source = source
        self._closed = closed

    async def receive(self) -> Any:
        try:
            value = await self._source.receive()
        except (anyio.ClosedResourceError, anyio.EndOfStream):
            self._closed.set()
            raise
        if isinstance(value, Exception):
            self._closed.set()
        return value

    async def aclose(self) -> None:
        self._closed.set()
        await self._source.aclose()

    def __aiter__(self) -> _ObservedReceiveStream:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def __aenter__(self) -> _ObservedReceiveStream:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


@asynccontextmanager
async def _observed_transport(
    transport: Transport, closed: asyncio.Event
) -> AsyncIterator[tuple[Any, Any]]:
    async with transport as (read, write):
        yield _ObservedReceiveStream(read, closed), write


class McpConnection:
    """One live MCP server connection managed by an owner task."""

    def __init__(
        self,
        spec: McpServerSpec,
        *,
        connect_timeout: float = 20.0,
        request_timeout: float = 30.0,
    ) -> None:
        self.spec = spec
        self.session: Client | None = None
        self.last_used: float = time.monotonic()
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._owner: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._requests: asyncio.Queue[_McpRequest] = asyncio.Queue()

    @property
    def alive(self) -> bool:
        return self._owner is not None and not self._owner.done() and self.session is not None

    async def start(self) -> None:
        """Spawn the owner task and wait until the session is ready."""
        self._owner = asyncio.create_task(self._owner_task(), name=f"mcp-{self.spec.id}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.connect_timeout)
        except TimeoutError:
            await self.stop()
            raise McpConnectionError(
                f"MCP server '{self.spec.name}' connect timeout ({self.connect_timeout}s)"
            ) from None
        if self._error is not None:
            raise McpConnectionError(
                f"MCP server '{self.spec.name}' failed to connect: {self._error}"
            ) from self._error

    async def stop(self) -> None:
        """Signal the owner task to exit and wait for cleanup."""
        self._shutdown.set()
        if self._owner is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._owner), timeout=10.0)
            except (TimeoutError, asyncio.CancelledError):
                self._owner.cancel()
                with suppress(asyncio.CancelledError):
                    await self._owner
            except Exception:  # noqa: BLE001 - owner errors already recorded
                pass
        self.session = None

    async def _owner_task(self) -> None:
        """Enter transport + session context managers; exit on shutdown event."""
        try:
            if self.spec.transport == "stdio":
                await self._run_stdio()
            elif self.spec.transport == "sse":
                await self._run_sse()
            elif self.spec.transport == "streamable_http":
                await self._run_streamable_http()
            else:
                raise McpConnectionError(f"unknown transport: {self.spec.transport}")
        except BaseException as exc:  # noqa: BLE001 - report to waiter
            self._error = exc
            if not isinstance(exc, asyncio.CancelledError):
                logger.warning(
                    "mcp owner task for '%s' ended with error: %s",
                    self.spec.name,
                    exc,
                )
        finally:
            self.session = None
            error = McpConnectionError(f"MCP server '{self.spec.name}' connection closed")
            while not self._requests.empty():
                request = self._requests.get_nowait()
                if not request.future.done():
                    request.future.set_exception(error)
            self._ready.set()  # unblock start() waiter on failure

    def _stdio_params(self) -> StdioServerParameters:
        if not self.spec.command:
            raise McpConnectionError("stdio transport requires a command")
        env = {**os.environ, **self.spec.env}
        env.setdefault("PYTHONUTF8", "1")
        command = self.spec.command
        args = list(self.spec.args)
        # Windows: npx/npm-style commands need the cmd shell
        if sys.platform == "win32" and command in ("npx", "npm", "node.cmd"):
            args = ["/c", command, *args]
            command = "cmd"
        return StdioServerParameters(command=command, args=args, env=env)

    async def _run_stdio(self) -> None:
        await self._run_client(cast(Transport, stdio_client(self._stdio_params())))

    async def _run_sse(self) -> None:
        if not self.spec.url:
            raise McpConnectionError("sse transport requires a url")
        await self._run_client(
            cast(Transport, sse_client(self.spec.url, headers=self.spec.headers))
        )

    async def _run_streamable_http(self) -> None:
        if not self.spec.url:
            raise McpConnectionError("streamable_http transport requires a url")
        async with httpx2.AsyncClient(
            headers=self.spec.headers,
            follow_redirects=True,
            timeout=httpx2.Timeout(30.0, read=300.0),
        ) as http_client:
            await self._run_client(
                cast(
                    Transport,
                    streamable_http_client(self.spec.url, http_client=http_client),
                )
            )

    async def _run_client(self, transport: Transport) -> None:
        transport_closed = asyncio.Event()
        async with Client(
            cast(Transport, _observed_transport(transport, transport_closed)),
            mode="auto",
            cache=None,
        ) as client:
            self.session = client
            self._ready.set()
            await self._serve(client, transport_closed)

    async def _serve(self, session: Client, transport_closed: asyncio.Event) -> None:
        """Process all SDK calls inside the task that owns the MCP contexts."""
        while not self._shutdown.is_set():
            shutdown_waiter = asyncio.create_task(self._shutdown.wait())
            closed_waiter = asyncio.create_task(transport_closed.wait())
            request_waiter = asyncio.create_task(self._requests.get())
            done, pending = await asyncio.wait(
                {shutdown_waiter, closed_waiter, request_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if shutdown_waiter in done or closed_waiter in done:
                return

            request = request_waiter.result()
            if request.future.cancelled():
                continue
            try:
                if request.kind == "list_tools":
                    list_result = await asyncio.wait_for(
                        session.list_tools(),
                        timeout=request.timeout,
                    )
                    value: Any = [
                        {
                            "name": tool.name,
                            "description": tool.description or "",
                            "input_schema": tool.input_schema or {},
                        }
                        for tool in list_result.tools
                    ]
                else:
                    call_result = await asyncio.wait_for(
                        session.call_tool(request.tool_name, request.arguments),
                        timeout=request.timeout,
                    )
                    texts = [
                        text
                        for item in call_result.content
                        if (text := getattr(item, "text", None)) is not None
                    ]
                    value = {
                        "content": "\n".join(texts),
                        "is_error": bool(call_result.is_error),
                    }
            except Exception as exc:  # noqa: BLE001 - relay SDK failures to caller
                if not request.future.done():
                    request.future.set_exception(exc)
            else:
                if not request.future.done():
                    request.future.set_result(value)

    # ------------------------------------------------------------------

    async def list_tools(self, *, timeout: float | None = None) -> list[dict[str, Any]]:
        """Return tool schemas as plain dicts."""
        result = await self._submit(
            _McpRequest(
                kind="list_tools",
                future=self._future(),
                timeout=self.request_timeout if timeout is None else timeout,
            )
        )
        return list(result)

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Call a tool; returns {content, is_error} with text content joined."""
        result = await self._submit(
            _McpRequest(
                kind="call_tool",
                future=self._future(),
                tool_name=tool_name,
                arguments=arguments,
                timeout=timeout,
            )
        )
        return dict(result)

    def _future(self) -> asyncio.Future[Any]:
        return asyncio.get_running_loop().create_future()

    async def _submit(self, request: _McpRequest) -> Any:
        self._require_session()
        owner = self._owner
        if owner is None or owner.done():
            raise McpConnectionError(f"MCP server '{self.spec.name}' is not connected")
        self.last_used = time.monotonic()
        await self._requests.put(request)
        try:
            done, _pending = await asyncio.wait(
                {request.future, owner}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            request.future.cancel()
            raise
        if request.future in done:
            try:
                return request.future.result()
            except Exception as exc:
                raise McpConnectionError(
                    f"MCP server '{self.spec.name}' request failed: {exc}"
                ) from exc
        request.future.cancel()
        raise McpConnectionError(f"MCP server '{self.spec.name}' connection closed during request")

    def _require_session(self) -> Client:
        if self.session is None:
            raise McpConnectionError(f"MCP server '{self.spec.name}' is not connected")
        return self.session
