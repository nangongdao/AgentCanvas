"""Remote MCP transport contract tests against the installed SDK server."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

from app.mcphub.connection import McpConnection, McpConnectionError, McpServerSpec

RemoteTransport = Literal["sse", "streamable_http"]


@dataclass
class LiveMcpServer:
    mcp: MCPServer[None]
    url: str
    server: uvicorn.Server
    task: asyncio.Task[None]

    async def stop(self) -> None:
        if self.task.done():
            await self.task
            return
        self.server.should_exit = True
        await asyncio.wait_for(self.task, timeout=5)


@asynccontextmanager
async def _running_mcp_server(
    transport: RemoteTransport,
) -> AsyncIterator[LiveMcpServer]:
    mcp: MCPServer[None] = MCPServer("transport-contract", log_level="ERROR")

    @mcp.tool()
    async def echo(value: str) -> str:
        """Return the supplied value."""
        return value

    @mcp.tool()
    async def slow_echo(value: str, delay: float = 1.0) -> str:
        """Return a value after a configurable delay."""
        await asyncio.sleep(delay)
        return value

    @mcp.tool()
    async def fail(message: str) -> str:
        """Raise a tool error with a caller-provided message."""
        raise ValueError(message)

    app = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
    path = "/sse" if transport == "sse" else "/mcp"
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind(("127.0.0.1", 0))
    listen_socket.listen()
    port = int(listen_socket.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            lifespan="on",
            access_log=False,
            timeout_graceful_shutdown=1,
        )
    )
    task = asyncio.create_task(
        server.serve(sockets=[listen_socket]), name=f"mcp-{transport}-server"
    )
    try:
        for _ in range(100):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError(f"{transport} MCP server did not start")
        yield LiveMcpServer(mcp, f"http://127.0.0.1:{port}{path}", server, task)
    finally:
        if not task.done():
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)
        listen_socket.close()


def _connection(transport: RemoteTransport, url: str) -> McpConnection:
    return McpConnection(
        McpServerSpec(
            id=f"{transport}-contract",
            name=f"{transport} contract",
            transport=transport,
            url=url,
        ),
        connect_timeout=5,
    )


async def _exercise_remote_transport(
    transport: RemoteTransport,
) -> None:
    async with _running_mcp_server(transport) as live:
        connection = _connection(transport, live.url)
        await connection.start()
        try:
            assert connection.session is not None
            assert connection.session.protocol_version == "2026-07-28"
            tools = await connection.list_tools()
            assert {tool["name"] for tool in tools} == {"echo", "fail", "slow_echo"}
            assert await connection.call_tool("echo", {"value": "hello"}) == {
                "content": "hello",
                "is_error": False,
            }
            failed = await connection.call_tool("fail", {"message": "contract failure"})
            assert failed["is_error"] is True
            assert "contract failure" in failed["content"]

            async def upper(value: str) -> str:
                """Uppercase the supplied value."""
                return value.upper()

            live.mcp.add_tool(upper)
            refreshed = await connection.list_tools()
            upper_schema = next(tool for tool in refreshed if tool["name"] == "upper")
            assert upper_schema["input_schema"]["required"] == ["value"]

            with pytest.raises(McpConnectionError, match="request failed"):
                await connection.call_tool(
                    "slow_echo", {"value": "late", "delay": 0.2}, timeout=0.01
                )
            assert connection.alive
        finally:
            await connection.stop()

        assert not connection.alive
        reconnected = _connection(transport, live.url)
        await reconnected.start()
        try:
            assert await reconnected.call_tool("upper", {"value": "again"}) == {
                "content": "AGAIN",
                "is_error": False,
            }
            await live.stop()
            with pytest.raises(McpConnectionError, match="(closed|request failed|not connected)"):
                await asyncio.wait_for(reconnected.list_tools(), timeout=5)
            if transport == "streamable_http":
                # MCP v2 isolates HTTP/JSON-RPC request failures and keeps the
                # transport usable; the caller decides whether to reconnect.
                assert reconnected.alive
            else:
                for _ in range(200):
                    if not reconnected.alive:
                        break
                    await asyncio.sleep(0.01)
                assert not reconnected.alive
        finally:
            await reconnected.stop()
        assert not reconnected.alive


@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
def test_remote_transport_round_trip_timeout_schema_change_and_reconnect(
    transport: RemoteTransport,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--contract", transport],
        cwd=backend_dir,
        env={**os.environ, "PYTHONPATH": str(backend_dir)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


if __name__ == "__main__" and sys.argv[1:2] == ["--contract"]:
    asyncio.run(_exercise_remote_transport(sys.argv[2]))  # type: ignore[arg-type]
