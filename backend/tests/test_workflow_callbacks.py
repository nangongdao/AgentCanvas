"""Outbound callback persistence, signing, and retry coverage."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import sqlite3
import ssl
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import httpcore
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.outbound_http import (
    OutboundDestinationError,
    ResolvedDestination,
    _PinnedAsyncHTTPTransport,
    _PinnedNetworkBackend,
    resolve_public_destination,
)
from app.core.secret_providers import SecretResolver
from app.core.security import SecretBox
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Execution, ExecutionEventRow, Workflow, WorkflowCallbackDelivery
from app.db.repositories import WorkflowCallbackRepo, WorkflowVersionRepo
from app.engine.workflow_callback_dispatcher import WorkflowCallbackDispatcher
from app.main import create_app

SECRET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="
EDITOR_TOKEN = "callback-editor-token-with-more-than-16-characters"
VIEWER_TOKEN = "callback-viewer-token-with-more-than-16-characters"
SocketOption = (
    tuple[int, int, int] | tuple[int, int, bytes | bytearray] | tuple[int, int, None, int]
)


def _api_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        secret_key=SECRET_KEY,
        auth_mode="token",
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
        admin_api_token="callback-admin-token-with-more-than-16-characters",
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workflow_body() -> dict:
    return {
        "name": "Callback workflow",
        "dsl": {
            "version": "1.0",
            "name": "Callback workflow",
            "variables": [],
            "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


async def _fixture(tmp_path):
    settings = Settings(data_dir=tmp_path, environment="test", secret_key=SECRET_KEY)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    async with sessions() as session:
        workflow = Workflow(id="callback-workflow", name="Callback", description="", dsl_json={})
        session.add(workflow)
        await session.flush()
        version = await WorkflowVersionRepo(session).ensure_current(workflow)
        execution = Execution(
            id="callback-execution",
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            status="succeeded",
            input_json={"prompt": "hello"},
            output_json={"answer": "world"},
            thread_id="callback-execution",
            started_at=datetime.now(UTC),
        )
        session.add(execution)
        await session.flush()
        callback = await WorkflowCallbackRepo(session).create(
            workflow.id,
            None,
            url="https://callback.example.test/receive",
            secret_encrypted=SecretResolver(SecretBox(SECRET_KEY)).encrypt(
                "callback-secret-123456"
            ),
            event_types=["workflow_finished", "dead_letter"],
            timeout_seconds=5,
            max_attempts=2,
            retry_delay_seconds=1,
        )
        session.add(
            ExecutionEventRow(
                execution_id=execution.id,
                seq=1,
                event_type="workflow_finished",
                payload_json={"result": "ok"},
                ts=datetime.now(UTC),
            )
        )
        await session.commit()
    return settings, engine, sessions, execution.id, callback.id


@pytest.mark.asyncio
async def test_callback_dispatcher_coalesces_event_kicks_without_active_callbacks(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", secret_key=SECRET_KEY)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    active_queries = 0
    first_scan = asyncio.Event()
    original_has_active = WorkflowCallbackRepo.has_active

    async def counted_has_active(repo: WorkflowCallbackRepo) -> bool:
        nonlocal active_queries
        active_queries += 1
        result = await original_has_active(repo)
        first_scan.set()
        return result

    monkeypatch.setattr(WorkflowCallbackRepo, "has_active", counted_has_active)
    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        poll_seconds=1.0,
    )
    dispatcher.start()
    try:
        await asyncio.wait_for(first_scan.wait(), timeout=1)
        await asyncio.sleep(0.05)
        for _index in range(5):
            dispatcher.kick()
            await asyncio.sleep(0.05)
        assert active_queries == 1
    finally:
        await dispatcher.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_callback_dispatcher_scans_signs_and_deduplicates(tmp_path) -> None:
    settings, engine, sessions, execution_id, callback_id = await _fixture(tmp_path)
    received: list[tuple[httpx.Request, bytes]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received.append((request, await request.aread()))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        transport=transport,
        poll_seconds=0.01,
    )
    assert await dispatcher.dispatch_once() == 1
    assert await dispatcher.dispatch_once() == 0
    assert len(received) == 1
    request, body = received[0]
    timestamp = request.headers["X-AgentCanvas-Timestamp"]
    expected = hmac.new(
        b"callback-secret-123456",
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    assert hmac.compare_digest(request.headers["X-AgentCanvas-Signature"], f"sha256={expected}")
    payload = json.loads(body)
    assert payload["event_type"] == "workflow_finished"
    assert payload["execution_id"] == execution_id
    async with sessions() as session:
        rows = list((await session.execute(select(WorkflowCallbackDelivery))).scalars().all())
        assert len(rows) == 1
        assert rows[0].callback_id == callback_id
        assert rows[0].status == "delivered"
        assert rows[0].attempt_count == 1
        assert rows[0].last_status_code == 204
    await engine.dispose()


@pytest.mark.asyncio
async def test_callback_dispatcher_does_not_backfill_before_first_activation(tmp_path) -> None:
    _settings, engine, sessions, _execution_id, callback_id = await _fixture(tmp_path)
    async with sessions() as session:
        callback = await WorkflowCallbackRepo(session).get(callback_id)
        event = (await session.execute(select(ExecutionEventRow))).scalar_one()
        assert callback is not None
        activation = event.ts.replace(tzinfo=UTC) + timedelta(seconds=1)
        callback.created_at = activation
        callback.updated_at = activation
        callback.activated_at = activation
        await session.commit()

    received: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        transport=httpx.MockTransport(handler),
        poll_seconds=0.01,
    )
    assert await dispatcher.dispatch_once() == 0
    assert received == []
    async with sessions() as session:
        assert (await session.execute(select(WorkflowCallbackDelivery))).scalars().all() == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_callback_dispatcher_does_not_backfill_while_disabled(tmp_path) -> None:
    _settings, engine, sessions, execution_id, callback_id = await _fixture(tmp_path)
    received: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        transport=httpx.MockTransport(handler),
        poll_seconds=0.01,
    )
    assert await dispatcher.dispatch_once() == 1
    async with sessions() as session:
        callback = await WorkflowCallbackRepo(session).get(callback_id)
        assert callback is not None
        await WorkflowCallbackRepo(session).disable(callback)
        session.add(
            ExecutionEventRow(
                execution_id=execution_id,
                seq=2,
                event_type="workflow_finished",
                payload_json={"result": "while-disabled"},
                ts=datetime.now(UTC),
            )
        )
        await session.commit()
    assert await dispatcher.dispatch_once() == 0

    async with sessions() as session:
        callback = await WorkflowCallbackRepo(session).get(callback_id)
        assert callback is not None
        await WorkflowCallbackRepo(session).update(callback, status="active")
        await session.commit()
    assert await dispatcher.dispatch_once() == 0
    assert len(received) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_callback_dispatcher_delivers_events_after_reactivation(tmp_path) -> None:
    _settings, engine, sessions, execution_id, callback_id = await _fixture(tmp_path)
    received: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        transport=httpx.MockTransport(handler),
        poll_seconds=0.01,
    )
    assert await dispatcher.dispatch_once() == 1
    async with sessions() as session:
        callback = await WorkflowCallbackRepo(session).get(callback_id)
        assert callback is not None
        await WorkflowCallbackRepo(session).disable(callback)
        await session.commit()
    assert await dispatcher.dispatch_once() == 0

    async with sessions() as session:
        callback = await WorkflowCallbackRepo(session).get(callback_id)
        assert callback is not None
        await WorkflowCallbackRepo(session).update(callback, status="active")
        await session.flush()
        activated_at = callback.activated_at.replace(tzinfo=UTC)
        session.add(
            ExecutionEventRow(
                execution_id=execution_id,
                seq=2,
                event_type="workflow_finished",
                payload_json={"result": "after-reactivation"},
                ts=activated_at + timedelta(seconds=1),
            )
        )
        await session.commit()

    assert await dispatcher.dispatch_once() == 1
    assert len(received) == 2
    assert json.loads(await received[-1].aread())["event"]["result"] == "after-reactivation"
    await engine.dispose()


@pytest.mark.asyncio
async def test_callback_dispatcher_retries_then_dead_letters(tmp_path) -> None:
    _settings, engine, sessions, _execution_id, _callback_id = await _fixture(tmp_path)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        transport=httpx.MockTransport(handler),
        poll_seconds=0.01,
    )
    assert await dispatcher.dispatch_once() == 0
    async with sessions() as session:
        row = (await session.execute(select(WorkflowCallbackDelivery))).scalar_one()
        assert row.status == "retry_wait"
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    assert await dispatcher.dispatch_once() == 0
    async with sessions() as session:
        row = (await session.execute(select(WorkflowCallbackDelivery))).scalar_one()
        assert row.status == "dead_letter"
        assert row.attempt_count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_callback_dispatcher_claims_and_extends_only_the_active_delivery(tmp_path) -> None:
    _settings, engine, sessions, execution_id, _callback_id = await _fixture(tmp_path)
    async with sessions() as session:
        session.add(
            ExecutionEventRow(
                execution_id=execution_id,
                seq=2,
                event_type="workflow_finished",
                payload_json={"result": "second"},
                ts=datetime.now(UTC),
            )
        )
        await session.commit()

    started = asyncio.Event()
    release = asyncio.Event()
    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            started.set()
            await release.wait()
        return httpx.Response(204)

    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        transport=httpx.MockTransport(handler),
        batch_size=100,
        lease_seconds=1,
        poll_seconds=0.01,
    )
    dispatch = asyncio.create_task(dispatcher.dispatch_once())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        async with sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(WorkflowCallbackDelivery).order_by(
                            WorkflowCallbackDelivery.created_at,
                            WorkflowCallbackDelivery.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert [row.status for row in rows].count("leased") == 1
        assert [row.status for row in rows].count("pending") == 1
        leased = next(row for row in rows if row.status == "leased")
        assert leased.lease_expires_at is not None
        assert leased.lease_expires_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(
            seconds=4
        )
    finally:
        release.set()
    assert await asyncio.wait_for(dispatch, timeout=1) == 2
    assert request_count == 2
    await engine.dispose()


class _RecordingNetworkStream(httpcore.AsyncMockStream):
    def __init__(self) -> None:
        super().__init__([b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"])
        self.server_hostnames: list[str | None] = []
        self.writes: list[bytes] = []

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del ssl_context, timeout
        self.server_hostnames.append(server_hostname)
        return self

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del timeout
        self.writes.append(buffer)


class _RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.stream = _RecordingNetworkStream()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del port, timeout, local_address, socket_options
        self.hosts.append(host)
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("callback transport must not use Unix sockets")

    async def sleep(self, seconds: float) -> None:
        del seconds


@pytest.mark.asyncio
async def test_callback_destination_resolution_is_public_and_connection_is_pinned(
    monkeypatch,
) -> None:
    def mixed_addresses(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", mixed_addresses)
    with pytest.raises(OutboundDestinationError, match="non-public"):
        resolve_public_destination("https://callback.example.test/receive")

    recorder = _RecordingNetworkBackend()
    destination = ResolvedDestination("callback.example.test", ("93.184.216.34",))
    backend = _PinnedNetworkBackend(destination, backend=recorder)
    async with httpx.AsyncClient(
        transport=_PinnedAsyncHTTPTransport(backend=backend),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = await client.post("https://callback.example.test/receive", content=b"{}")
    assert response.status_code == 204
    assert recorder.hosts == ["93.184.216.34"]
    assert recorder.stream.server_hostnames == ["callback.example.test"]
    assert b"Host: callback.example.test\r\n" in b"".join(recorder.stream.writes)


@pytest.mark.asyncio
async def test_callback_dispatcher_stop_cancels_a_stuck_delivery(tmp_path) -> None:
    _settings, engine, sessions, _execution_id, _callback_id = await _fixture(tmp_path)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return httpx.Response(204)

    dispatcher = WorkflowCallbackDispatcher(
        sessions,
        SecretResolver(SecretBox(SECRET_KEY)),
        transport=httpx.MockTransport(handler),
        poll_seconds=0.01,
        shutdown_timeout_seconds=0.01,
    )
    dispatcher.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.wait_for(dispatcher.stop(), timeout=0.5)
    assert cancelled.is_set()
    await engine.dispose()


def test_callback_management_encrypts_secret_and_manages_lifecycle(tmp_path) -> None:
    with TestClient(create_app(_api_settings(tmp_path))) as client:
        editor = _headers(EDITOR_TOKEN)
        viewer = _headers(VIEWER_TOKEN)
        workflow = client.post("/api/workflows", headers=editor, json=_workflow_body()).json()
        url = f"/api/workflows/{workflow['id']}/callback"
        assert client.get(url, headers=viewer).status_code == 404
        assert (
            client.post(
                url,
                headers=editor,
                json={"url": "http://127.0.0.1/private"},
            ).status_code
            == 422
        )

        created = client.post(
            url,
            headers=editor,
            json={
                "url": "https://callback.example.test/receive",
                "secret": "callback-secret-123456",
                "event_types": ["workflow_finished", "quota_alert"],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["secret"] == "callback-secret-123456"
        assert (
            client.post(
                url,
                headers=editor,
                json={"url": "https://callback.example.test/other"},
            ).status_code
            == 409
        )
        visible = client.get(url, headers=viewer)
        assert visible.status_code == 200
        assert "secret" not in visible.json()
        assert visible.json()["event_types"] == ["workflow_finished", "quota_alert"]

        updated = client.put(
            url,
            headers=editor,
            json={"event_types": ["workflow_failed"], "max_attempts": 3},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["secret"] is None
        assert updated.json()["callback"]["event_types"] == ["workflow_failed"]
        assert updated.json()["callback"]["max_attempts"] == 3
        assert client.get(f"{url}/deliveries", headers=viewer).json() == []
        assert client.delete(url, headers=viewer).status_code == 403
        assert client.delete(url, headers=editor).status_code == 204
        assert client.get(url, headers=viewer).json()["status"] == "disabled"

    with sqlite3.connect(tmp_path / "app.db") as database:
        encrypted = database.execute("SELECT secret_encrypted FROM workflow_callbacks").fetchone()[
            0
        ]
    assert encrypted != "callback-secret-123456"
    assert "callback-secret-123456" not in encrypted
