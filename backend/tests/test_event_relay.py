"""Durable event relay and multi-source SSE tail (I1 Phase 5)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import inspect

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import CURRENT_REVISION, CURRENT_TABLES, upgrade_database
from app.db.models import Workflow
from app.db.repositories import ExecutionEventRelayRepo, ExecutionRepo
from app.engine.event_relay import EventRelay
from app.engine.event_stream import (
    GLOBAL_RELAY_CURSOR,
    ExecutionEventStream,
    StreamEvent,
    decode_stream_fields,
    encode_stream_fields,
    execution_stream_key,
)
from app.engine.events import EventBus
from app.engine.sse_tail import iter_execution_sse
from app.schemas.events import EventType


class FakeRedis:
    """Minimal in-memory Redis Streams stand-in for relay/SSE tests."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._counter = 0

    async def xadd(
        self,
        name: str,
        fields: dict[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        _ = approximate
        self._counter += 1
        entry_id = f"{self._counter}-0"
        entries = self.streams.setdefault(name, [])
        entries.append((entry_id, dict(fields)))
        if maxlen is not None and len(entries) > maxlen:
            del entries[0 : len(entries) - maxlen]
        return entry_id

    async def xread(
        self,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        _ = block
        out: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for name, last_id in streams.items():
            selected: list[tuple[str, dict[str, str]]] = []
            for entry_id, fields in self.streams.get(name, []):
                if last_id == "$":
                    break
                if last_id in {"0-0", "0"} or _id_greater(entry_id, last_id):
                    selected.append((entry_id, fields))
                    if count is not None and len(selected) >= count:
                        break
            if selected:
                out.append((name, selected))
        return out


def _id_greater(left: str, right: str) -> bool:
    if right in {"0-0", "0"}:
        return True
    try:
        l_ms, l_seq = (int(part) for part in left.split("-", 1))
        r_ms, r_seq = (int(part) for part in right.split("-", 1))
    except ValueError:
        return left > right
    return (l_ms, l_seq) > (r_ms, r_seq)


async def _session_factory(tmp_path):
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    return settings, engine, create_session_factory(engine)


async def test_upgrade_creates_event_relay_cursor_table(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            version = await connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar_one() == CURRENT_REVISION
        assert "execution_event_relay_cursors" in tables
        assert tables >= CURRENT_TABLES
        assert CURRENT_REVISION == "0047_evaluation_policy"
    finally:
        await engine.dispose()


def test_stream_field_roundtrip() -> None:
    fields = encode_stream_fields(
        execution_id="exec-1",
        seq=3,
        event_type="node_started",
        node_id="n1",
        ts=datetime(2026, 8, 10, tzinfo=UTC),
        payload={"ok": True},
        event_row_id=9,
    )
    decoded = decode_stream_fields(fields)
    assert decoded is not None
    assert decoded.execution_id == "exec-1"
    assert decoded.seq == 3
    assert decoded.event_type == "node_started"
    assert decoded.node_id == "n1"
    assert decoded.payload == {"ok": True}
    assert decoded.event_row_id == 9
    assert execution_stream_key("exec-1") == "agentcanvas:exec-events:exec-1"


async def test_event_relay_publishes_and_advances_cursor(tmp_path) -> None:
    _settings, engine, session_factory = await _session_factory(tmp_path)
    redis = FakeRedis()
    stream = ExecutionEventStream(redis, maxlen=100)
    relay = EventRelay(
        session_factory,
        redis_client=redis,
        event_stream=stream,
        batch_size=10,
    )
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-relay",
                    name="Relay",
                    dsl_json={"version": "1.0", "name": "Relay", "nodes": [], "edges": []},
                )
            )
            await session.flush()
            execution = await ExecutionRepo(session).create("wf-relay", {})
            await ExecutionRepo(session).append_events(
                [
                    {
                        "execution_id": execution.id,
                        "seq": 1,
                        "event_type": "workflow_started",
                        "node_id": None,
                        "payload_json": {},
                        "ts": datetime.now(UTC),
                    },
                    {
                        "execution_id": execution.id,
                        "seq": 2,
                        "event_type": "workflow_finished",
                        "node_id": None,
                        "payload_json": {"ok": True},
                        "ts": datetime.now(UTC),
                    },
                ]
            )
            await session.commit()
            execution_id = execution.id

        published = await relay.publish_pending()
        assert published == 2
        key = execution_stream_key(execution_id)
        assert key in redis.streams
        assert len(redis.streams[key]) == 2

        async with session_factory() as session:
            cursor = await ExecutionEventRelayRepo(session).get_or_create(GLOBAL_RELAY_CURSOR)
            assert cursor.last_event_id >= 2

        # Second pass is a no-op once the cursor catches up.
        assert await relay.publish_pending() == 0
    finally:
        await relay.stop()
        await engine.dispose()


async def test_sse_tail_replays_db_and_dedupes_bus(tmp_path) -> None:
    _settings, engine, session_factory = await _session_factory(tmp_path)
    bus = EventBus()
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-sse",
                    name="SSE",
                    dsl_json={"version": "1.0", "name": "SSE", "nodes": [], "edges": []},
                )
            )
            await session.flush()
            execution = await ExecutionRepo(session).create("wf-sse", {})
            await ExecutionRepo(session).append_events(
                [
                    {
                        "execution_id": execution.id,
                        "seq": 1,
                        "event_type": "workflow_started",
                        "node_id": None,
                        "payload_json": {},
                        "ts": datetime.now(UTC),
                    }
                ]
            )
            await session.commit()
            execution_id = execution.id

        async def not_disconnected() -> bool:
            return False

        messages: list[dict[str, str]] = []

        async def collect() -> None:
            async for message in iter_execution_sse(
                execution_id=execution_id,
                after=0,
                bus=bus,
                session_factory=session_factory,
                request_is_disconnected=not_disconnected,
                heartbeat_seconds=5,
                event_stream=None,
                pg_poll_seconds=0.2,
            ):
                messages.append(message)
                if message.get("event") in {
                    "workflow_finished",
                    "workflow_failed",
                    "workflow_cancelled",
                }:
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        live = await bus.publish(execution_id, EventType.WORKFLOW_FINISHED, payload={"done": True})
        # Persist the live event so PG poll also sees it if bus delivery races.
        async with session_factory() as session:
            await ExecutionRepo(session).append_events(
                [
                    {
                        "execution_id": execution_id,
                        "seq": live.seq,
                        "event_type": live.event_type.value,
                        "node_id": None,
                        "payload_json": live.payload,
                        "ts": datetime.now(UTC),
                    }
                ]
            )
            await session.commit()
        await asyncio.wait_for(task, timeout=2)

        events = [m for m in messages if m.get("event")]
        assert events[0]["event"] == "workflow_started"
        assert events[0]["id"] == "1"
        finished = [m for m in events if m["event"] == "workflow_finished"]
        assert len(finished) == 1
        payload = json.loads(finished[0]["data"])
        assert payload["seq"] == live.seq
        assert payload["payload"] == {"done": True}
    finally:
        await engine.dispose()


async def test_event_stream_filters_by_seq() -> None:
    redis = FakeRedis()
    stream = ExecutionEventStream(redis)
    await stream.publish(
        StreamEvent(
            execution_id="e1",
            seq=1,
            event_type="workflow_started",
            node_id=None,
            ts="t1",
            payload={},
            event_row_id=1,
        )
    )
    await stream.publish(
        StreamEvent(
            execution_id="e1",
            seq=2,
            event_type="workflow_finished",
            node_id=None,
            ts="t2",
            payload={"ok": True},
            event_row_id=2,
        )
    )
    only_second = await stream.read_after_seq("e1", after_seq=1)
    assert [item.seq for item in only_second] == [2]
