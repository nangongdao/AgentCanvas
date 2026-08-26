"""MCP owner-task failure-path tests."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import anyio
import pytest
from mcp import Client

from app.mcphub.connection import (
    McpConnection,
    McpConnectionError,
    McpServerSpec,
    _ObservedReceiveStream,
)


def _connection(
    transport: str,
    *,
    command: str | None = None,
    url: str | None = None,
    request_timeout: float = 30.0,
) -> McpConnection:
    return McpConnection(
        McpServerSpec(
            id=f"invalid-{transport}",
            name=f"Invalid {transport}",
            transport=transport,
            command=command,
            url=url,
        ),
        connect_timeout=0.1,
        request_timeout=request_timeout,
    )


@pytest.mark.parametrize(
    ("transport", "message"),
    [
        ("stdio", "requires a command"),
        ("sse", "requires a url"),
        ("streamable_http", "requires a url"),
        ("unsupported", "unknown transport"),
    ],
)
async def test_connection_reports_invalid_transport_configuration(
    transport: str, message: str
) -> None:
    connection = _connection(transport)
    with pytest.raises(McpConnectionError, match=message):
        await connection.start()
    assert not connection.alive
    await connection.stop()


async def test_connection_start_timeout_stops_owner(monkeypatch) -> None:
    connection = _connection("sse", url="http://example.invalid/sse")
    connection.connect_timeout = 0.01

    async def never_ready() -> None:
        await connection._shutdown.wait()  # noqa: SLF001

    monkeypatch.setattr(connection, "_owner_task", never_ready)
    with pytest.raises(McpConnectionError, match="connect timeout"):
        await connection.start()
    assert connection._owner is not None and connection._owner.done()  # noqa: SLF001


async def test_observed_stream_marks_python_error_and_eof() -> None:
    sender, receiver = anyio.create_memory_object_stream[Any](1)
    closed = asyncio.Event()
    observed = _ObservedReceiveStream(receiver, closed)
    transport_error = OSError("transport closed")
    await sender.send(transport_error)
    assert await observed.receive() is transport_error
    assert closed.is_set()
    await observed.aclose()

    sender, receiver = anyio.create_memory_object_stream[Any](1)
    eof = asyncio.Event()
    observed = _ObservedReceiveStream(receiver, eof)
    await sender.aclose()
    with pytest.raises(anyio.EndOfStream):
        await observed.receive()
    assert eof.is_set()


async def test_disconnected_and_owner_exit_requests_fail_promptly() -> None:
    connection = _connection("sse", url="http://example.invalid/sse")
    with pytest.raises(McpConnectionError, match="not connected"):
        await connection.list_tools()

    connection.session = cast(Client, object())
    connection._owner = asyncio.create_task(asyncio.sleep(0))  # noqa: SLF001
    await connection._owner  # noqa: SLF001
    with pytest.raises(McpConnectionError, match="not connected"):
        await connection.call_tool("echo", {})

    owner_exit = asyncio.Event()

    async def owner() -> None:
        await owner_exit.wait()

    connection._owner = asyncio.create_task(owner())  # noqa: SLF001
    request = asyncio.create_task(connection.list_tools())
    while connection._requests.empty():  # noqa: SLF001
        await asyncio.sleep(0)
    owner_exit.set()
    with pytest.raises(McpConnectionError, match="closed during request"):
        await request
    await connection.stop()


async def test_cancelled_submit_cancels_its_response_future() -> None:
    connection = _connection("sse", url="http://example.invalid/sse")
    connection.session = cast(Client, object())

    async def owner() -> None:
        await connection._shutdown.wait()  # noqa: SLF001

    connection._owner = asyncio.create_task(owner())  # noqa: SLF001
    request = asyncio.create_task(connection.list_tools())
    while connection._requests.empty():  # noqa: SLF001
        await asyncio.sleep(0)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    queued = connection._requests.get_nowait()  # noqa: SLF001
    assert queued.future.cancelled()
    await connection.stop()


async def test_list_tools_times_out_without_stranding_owner_task() -> None:
    connection = _connection(
        "sse",
        url="http://example.invalid/sse",
        request_timeout=0.01,
    )

    class SlowClient:
        async def list_tools(self) -> None:
            await asyncio.Event().wait()

    connection.session = cast(Client, SlowClient())
    transport_closed = asyncio.Event()
    connection._owner = asyncio.create_task(  # noqa: SLF001
        connection._serve(connection.session, transport_closed)  # noqa: SLF001
    )

    with pytest.raises(McpConnectionError, match="request failed"):
        await connection.list_tools()

    assert connection.alive
    await connection.stop()
    assert not connection.alive
