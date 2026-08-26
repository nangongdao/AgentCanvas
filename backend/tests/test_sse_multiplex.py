"""Multiplexed execution SSE tail tests (C6-3)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.repositories import ExecutionRepo
from app.engine.events import EventBus
from app.engine.sse_multiplex import (
    composite_event_id,
    iter_multi_execution_sse,
    parse_after_map,
)
from app.schemas.events import EventType

TERMINAL_SET = {"workflow_finished", "workflow_failed", "workflow_cancelled"}


async def _session_factory(tmp_path: Path):
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    return settings, engine, create_session_factory(engine)


async def _seed_execution(session_factory, execution_id: str) -> None:
    from app.db.models import Workflow

    async with session_factory() as session:
        session.add(
            Workflow(
                id=f"wf-{execution_id}",
                name="multiplex probe",
                dsl_json={"version": "1.0", "name": "probe", "nodes": [], "edges": []},
            )
        )
        await session.flush()
        await ExecutionRepo(session).create(f"wf-{execution_id}", {}, execution_id=execution_id)
        await ExecutionRepo(session).append_events(
            [
                {
                    "execution_id": execution_id,
                    "seq": 1,
                    "event_type": "workflow_started",
                    "node_id": None,
                    "payload_json": {},
                    "ts": datetime.now(UTC),
                }
            ]
        )
        await session.commit()


async def test_multiplex_merges_two_streams_with_composite_ids(tmp_path: Path) -> None:
    _, engine, session_factory = await _session_factory(tmp_path)
    bus = EventBus()
    try:
        await _seed_execution(session_factory, "exec-a")
        await _seed_execution(session_factory, "exec-b")

        async def not_disconnected() -> bool:
            return False

        messages: list[dict[str, str]] = []

        async def collect() -> None:
            async for message in iter_multi_execution_sse(
                execution_ids=["exec-a", "exec-b"],
                after_map={},
                bus=bus,
                session_factory=session_factory,
                request_is_disconnected=not_disconnected,
                heartbeat_seconds=5,
                event_stream=None,
                pg_poll_seconds=0.2,
            ):
                messages.append(message)
                seen = {
                    m.get("event") for m in messages if m.get("event") in TERMINAL_SET
                }
                ids = {m["id"].split(":")[0] for m in messages if m.get("event") in TERMINAL_SET}
                if len(seen) >= 2 and ids >= {"exec-a", "exec-b"}:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        finished_a = await bus.publish("exec-a", EventType.WORKFLOW_FINISHED, payload={"a": 1})
        finished_b = await bus.publish("exec-b", EventType.WORKFLOW_FAILED, payload={"b": 2})
        await asyncio.wait_for(task, timeout=3)

        started = [m for m in messages if m.get("event") == "workflow_started"]
        assert {json.loads(m["data"])["execution_id"] for m in started} == {"exec-a", "exec-b"}
        # Composite SSE ids carry the per-execution seq space.
        for message in messages:
            if not message.get("event"):
                continue
            execution_id, _, seq_text = message["id"].rpartition(":")
            data = json.loads(message["data"])
            assert message["id"] == composite_event_id(data["execution_id"], data["seq"])
            assert execution_id == data["execution_id"]
            assert int(seq_text) == data["seq"]

        terminals = [m for m in messages if m.get("event") in TERMINAL_SET]
        assert {json.loads(m["data"])["execution_id"] for m in terminals} == {"exec-a", "exec-b"}
        by_exec = {json.loads(m["data"])["execution_id"]: m for m in terminals}
        assert json.loads(by_exec["exec-a"]["data"])["payload"] == {"a": 1}
        assert by_exec["exec-b"]["event"] == "workflow_failed"
        assert finished_a.seq >= 1 and finished_b.seq >= 1
    finally:
        await engine.dispose()


async def test_after_map_skips_replayed_sequences(tmp_path: Path) -> None:
    _, engine, session_factory = await _session_factory(tmp_path)
    bus = EventBus()
    try:
        await _seed_execution(session_factory, "exec-c")

        async def not_disconnected() -> bool:
            return False

        messages: list[dict[str, str]] = []

        async def collect() -> None:
            async for message in iter_multi_execution_sse(
                execution_ids=["exec-c"],
                after_map={"exec-c": 1},
                bus=bus,
                session_factory=session_factory,
                request_is_disconnected=not_disconnected,
                heartbeat_seconds=5,
                event_stream=None,
                pg_poll_seconds=0.2,
            ):
                messages.append(message)
                if any(m.get("event") in TERMINAL_SET for m in messages):
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        live = await bus.publish("exec-c", EventType.WORKFLOW_FINISHED, payload={"done": True})
        await asyncio.wait_for(task, timeout=3)

        events = [m for m in messages if m.get("event")]
        # seq 1 was already acknowledged via the reconnect cursor.
        assert all(json.loads(m["data"])["seq"] > 1 for m in events)
        terminal = [m for m in events if m.get("event") == "workflow_finished"]
        assert len(terminal) == 1
        assert json.loads(terminal[0]["data"])["seq"] == live.seq
    finally:
        await engine.dispose()


async def test_terminal_execution_channel_closes_but_stream_continues(tmp_path: Path) -> None:
    _, engine, session_factory = await _session_factory(tmp_path)
    bus = EventBus()
    try:
        await _seed_execution(session_factory, "exec-done")
        await _seed_execution(session_factory, "exec-live")

        async def not_disconnected() -> bool:
            return False

        messages: list[dict[str, str]] = []
        phase = {"finished_live": False}

        async def collect() -> None:
            async for message in iter_multi_execution_sse(
                execution_ids=["exec-done", "exec-live"],
                after_map={},
                bus=bus,
                session_factory=session_factory,
                request_is_disconnected=not_disconnected,
                heartbeat_seconds=5,
                event_stream=None,
                pg_poll_seconds=0.2,
            ):
                messages.append(message)
                if phase["finished_live"]:
                    return

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        # exec-done terminates first; its channel closes but exec-live continues.
        await bus.publish("exec-done", EventType.WORKFLOW_CANCELLED, payload={})
        await asyncio.sleep(0.15)
        late = await bus.publish("exec-live", EventType.NODE_STARTED, node_id="agent")
        phase["finished_live"] = True
        await bus.publish("exec-live", EventType.WORKFLOW_FINISHED, payload={})
        await asyncio.wait_for(task, timeout=3)

        events = [m for m in messages if m.get("event")]
        cancelled = [m for m in events if m.get("event") == "workflow_cancelled"]
        assert len(cancelled) == 1
        assert json.loads(cancelled[0]["data"])["execution_id"] == "exec-done"
        # The late exec-live event still arrived after exec-done's channel closed.
        node_started = [
            m
            for m in events
            if m.get("event") == "node_started"
            and json.loads(m["data"])["seq"] == late.seq
            and json.loads(m["data"])["execution_id"] == "exec-live"
        ]
        assert len(node_started) == 1
    finally:
        await engine.dispose()


def test_parse_after_map_handles_malformed_pairs() -> None:
    assert parse_after_map(None) == {}
    assert parse_after_map("") == {}
    assert parse_after_map("a:1,b:x,c:,:5,d:-2,e:7") == {"a": 1, "e": 7}
    assert parse_after_map("dup:3,dup:9") == {"dup": 9}


def test_composite_event_id_format() -> None:
    assert composite_event_id("abc", 12) == "abc:12"


# --- Route-level validation (the streaming path itself is covered above) ---


def _route_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        log_level="WARNING",
    )


def test_multi_route_rejects_empty_and_oversized_ids(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(_route_settings(tmp_path))) as client:
        empty = client.get("/api/executions/events/multi", params={"ids": " , ,"})
        assert empty.status_code == 422
        oversized = client.get(
            "/api/executions/events/multi",
            params={"ids": ",".join(f"exec-{i}" for i in range(11))},
        )
        assert oversized.status_code == 422


def test_multi_route_unknown_execution_is_404(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(_route_settings(tmp_path))) as client:
        missing = client.get(
            "/api/executions/events/multi",
            params={"ids": f"does-not-exist,{uuid4().hex}"},
        )
        assert missing.status_code == 404
