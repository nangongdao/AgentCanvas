"""SSE multi-source tail helpers for execution events (I1 Phase 5)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import ExecutionRepo
from app.engine.event_stream import ExecutionEventStream, StreamEvent
from app.engine.events import EventBus
from app.schemas.events import TERMINAL_EVENTS, ExecutionEvent

logger = logging.getLogger(__name__)

_TERMINAL_VALUES = frozenset(event.value for event in TERMINAL_EVENTS)
_TIMEOUT = object()


def _row_to_sse(execution_id: str, row: Any) -> dict[str, str]:
    data = {
        "execution_id": execution_id,
        "seq": row.seq,
        "event_type": row.event_type,
        "node_id": row.node_id,
        "ts": row.ts.isoformat() if isinstance(row.ts, datetime) else str(row.ts),
        "payload": row.payload_json or {},
    }
    return {
        "id": str(row.seq),
        "event": row.event_type,
        "data": json.dumps(data, ensure_ascii=False),
    }


def _stream_event_to_sse(event: StreamEvent) -> dict[str, str]:
    return {
        "id": str(event.seq),
        "event": event.event_type,
        "data": json.dumps(event.sse_payload(), ensure_ascii=False),
    }


def _bus_event_to_sse(event: ExecutionEvent) -> dict[str, str]:
    return {
        "id": str(event.seq),
        "event": event.event_type.value,
        "data": json.dumps(event.sse_data(), ensure_ascii=False),
    }


def _is_terminal_type(event_type: str) -> bool:
    return event_type in _TERMINAL_VALUES


async def iter_execution_sse(
    *,
    execution_id: str,
    after: int,
    bus: EventBus,
    session_factory: async_sessionmaker[AsyncSession],
    request_is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_seconds: float,
    event_stream: ExecutionEventStream | None = None,
    pg_poll_seconds: float = 1.0,
    on_opened: Callable[[], None] | None = None,
    on_replay: Callable[[int], None] | None = None,
    on_closed: Callable[[], None] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Replay durable history, then tail local bus / Redis / bounded PG poll.

    Protocol (ADR 0001):
    1. Subscribe local bus first, flush pending persist batch.
    2. Replay committed PostgreSQL events after ``after``.
    3. Catch up Redis stream backlog and re-check PostgreSQL for the gap.
    4. Live-tail with de-duplication by ``seq``; if Redis is unavailable, poll PG.
    """
    if on_opened is not None:
        on_opened()
    queue = bus.subscribe(execution_id)
    last_seq = after
    active_stream = event_stream
    try:
        await bus.flush()

        async with session_factory() as session:
            rows = await ExecutionRepo(session).list_events_after(execution_id, after)
        replayed = 0
        terminal_seen = False
        for row in rows:
            replayed += 1
            if int(row.seq) <= last_seq:
                continue
            last_seq = int(row.seq)
            yield _row_to_sse(execution_id, row)
            if _is_terminal_type(row.event_type):
                terminal_seen = True
        if on_replay is not None:
            on_replay(replayed)
        if terminal_seen:
            return

        bus.seed_seq(execution_id, last_seq)

        if active_stream is not None:
            try:
                backlog = await active_stream.read_after_seq(
                    execution_id,
                    last_seq,
                    count=500,
                )
            except Exception:
                logger.exception("redis execution stream catch-up failed; continuing with PG")
                backlog = []
                active_stream = None
            for backlog_event in backlog:
                if backlog_event.seq <= last_seq:
                    continue
                last_seq = backlog_event.seq
                yield _stream_event_to_sse(backlog_event)
                if _is_terminal_type(backlog_event.event_type):
                    return

        async with session_factory() as session:
            gap_rows = await ExecutionRepo(session).list_events_after(execution_id, last_seq)
        for row in gap_rows:
            if int(row.seq) <= last_seq:
                continue
            last_seq = int(row.seq)
            yield _row_to_sse(execution_id, row)
            if _is_terminal_type(row.event_type):
                return

        redis_id = "$"
        poll_seconds = max(0.1, float(pg_poll_seconds))
        heartbeat = max(1.0, float(heartbeat_seconds))
        wait_timeout = min(heartbeat, poll_seconds)
        while True:
            if await request_is_disconnected():
                break
            try:
                bus_item: ExecutionEvent | None | object = await asyncio.wait_for(
                    queue.get(), timeout=wait_timeout
                )
            except TimeoutError:
                bus_item = _TIMEOUT

            if isinstance(bus_item, ExecutionEvent):
                if bus_item.seq <= last_seq:
                    continue
                last_seq = bus_item.seq
                yield _bus_event_to_sse(bus_item)
                if bus_item.event_type in TERMINAL_EVENTS:
                    break
                continue

            if bus_item is None:
                # Local bus closed this execution.
                break

            # Timed out on the local bus: try shared Redis/PG tails.
            progressed = False
            if active_stream is not None:
                try:
                    redis_id, remote = await active_stream.tail(
                        execution_id,
                        redis_id,
                        count=100,
                        block_ms=int(wait_timeout * 1000),
                    )
                except Exception:
                    logger.exception("redis execution stream tail failed; falling back to PG poll")
                    remote = []
                    active_stream = None
                for remote_event in remote:
                    if remote_event.seq <= last_seq:
                        continue
                    last_seq = remote_event.seq
                    progressed = True
                    yield _stream_event_to_sse(remote_event)
                    if _is_terminal_type(remote_event.event_type):
                        return

            if not progressed:
                async with session_factory() as session:
                    polled = await ExecutionRepo(session).list_events_after(execution_id, last_seq)
                for row in polled:
                    if int(row.seq) <= last_seq:
                        continue
                    last_seq = int(row.seq)
                    progressed = True
                    yield _row_to_sse(execution_id, row)
                    if _is_terminal_type(row.event_type):
                        return

            closed = False
            while True:
                try:
                    pending = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if pending is None:
                    closed = True
                    break
                if pending.seq <= last_seq:
                    continue
                last_seq = pending.seq
                progressed = True
                yield _bus_event_to_sse(pending)
                if pending.event_type in TERMINAL_EVENTS:
                    return
            if closed:
                break

            if not progressed:
                yield {"comment": "ping"}
    finally:
        bus.unsubscribe(execution_id, queue)
        if on_closed is not None:
            on_closed()


__all__ = ["iter_execution_sse"]
