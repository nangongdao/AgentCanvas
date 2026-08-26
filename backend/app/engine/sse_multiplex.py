"""Multiplexed SSE tail across several executions over one connection (C6-3).

Each execution owns an independent monotonic ``seq`` space, so the multiplexer
keeps a per-execution ``last_seq`` map, replays each stream's durable history
individually, and fans per-execution EventBus queues into one aggregate
queue. The SSE ``id`` field carries ``{execution_id}:{seq}`` so clients can
echo progress back per execution on reconnect via ``?after=id:seq,...``.

Terminal semantics: one execution reaching a terminal event closes only that
execution's channel; the stream stays open until every requested execution
has terminated (or the client disconnects).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import ExecutionRepo
from app.engine.event_stream import ExecutionEventStream, StreamEvent
from app.engine.events import EventBus
from app.schemas.events import TERMINAL_EVENTS, ExecutionEvent

logger = logging.getLogger(__name__)

_TIMEOUT = object()


def composite_event_id(execution_id: str, seq: int) -> str:
    return f"{execution_id}:{seq}"


def parse_after_map(raw: str | None) -> dict[str, int]:
    """Parse an ``id:seq,id:seq`` reconnect cursor into a per-execution map.

    Malformed pairs are skipped rather than failing the request: the client
    simply receives an idempotent replay it already deduplicates by seq.
    """
    result: dict[str, int] = {}
    if not raw:
        return result
    for pair in raw.split(","):
        pair = pair.strip()
        execution_id, _, seq_text = pair.rpartition(":")
        if not execution_id or not seq_text:
            continue
        try:
            seq = int(seq_text)
        except ValueError:
            continue
        if seq < 0:
            continue
        result[execution_id] = max(result.get(execution_id, 0), seq)
    return result


def _row_to_sse(execution_id: str, row: Any) -> dict[str, str]:
    from datetime import datetime

    data = {
        "execution_id": execution_id,
        "seq": row.seq,
        "event_type": row.event_type,
        "node_id": row.node_id,
        "ts": row.ts.isoformat() if isinstance(row.ts, datetime) else str(row.ts),
        "payload": row.payload_json or {},
    }
    return {
        "id": composite_event_id(execution_id, int(row.seq)),
        "event": row.event_type,
        "data": _dumps(data),
    }


def _event_to_sse(event: ExecutionEvent | StreamEvent) -> dict[str, str]:
    payload = (
        event.sse_data()
        if isinstance(event, ExecutionEvent)
        else event.sse_payload()
    )
    return {
        "id": composite_event_id(event.execution_id, event.seq),
        "event": (
            event.event_type.value
            if isinstance(event, ExecutionEvent)
            else event.event_type
        ),
        "data": _dumps(payload),
    }


def _dumps(data: dict[str, Any]) -> str:
    from json import dumps

    return dumps(data, ensure_ascii=False)


def _is_terminal_value(event_type: str) -> bool:
    return event_type in {terminal.value for terminal in TERMINAL_EVENTS}


class _Channel:
    """Per-execution tail state inside one multiplexed connection."""

    def __init__(self, execution_id: str, queue: asyncio.Queue[ExecutionEvent | None]) -> None:
        self.execution_id = execution_id
        self.queue = queue
        self.last_seq = 0
        self.terminal = False
        self.redis_id = "$"
        self.forward_task: asyncio.Task[None] | None = None


async def iter_multi_execution_sse(
    *,
    execution_ids: list[str],
    after_map: dict[str, int],
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
    """Replay each execution's durable history, then multiplex live tails."""
    if on_opened is not None:
        on_opened()

    channels: dict[str, _Channel] = {}
    aggregate: asyncio.Queue[ExecutionEvent | tuple[str, StreamEvent]] = asyncio.Queue()
    active_stream = event_stream
    replayed_total = 0

    try:
        await bus.flush()
        poll_seconds = max(0.1, float(pg_poll_seconds))
        heartbeat = max(1.0, float(heartbeat_seconds))
        wait_timeout = min(heartbeat, poll_seconds)

        # Phase 1 — durable replay per execution.
        for execution_id in execution_ids:
            channel = _Channel(execution_id, bus.subscribe(execution_id))
            channels[execution_id] = channel
            channel.last_seq = int(after_map.get(execution_id, 0))
            async with session_factory() as session:
                rows = await ExecutionRepo(session).list_events_after(
                    execution_id, channel.last_seq
                )
            for row in rows:
                replayed_total += 1
                if int(row.seq) <= channel.last_seq:
                    continue
                channel.last_seq = int(row.seq)
                yield _row_to_sse(execution_id, row)
                if _is_terminal_value(row.event_type):
                    channel.terminal = True
                    break
            if not channel.terminal:
                bus.seed_seq(execution_id, channel.last_seq)
        if on_replay is not None:
            on_replay(replayed_total)

        # Phase 2 — Redis backlog catch-up for still-open executions.
        for channel in channels.values():
            if channel.terminal or active_stream is None:
                continue
            try:
                backlog = await active_stream.read_after_seq(
                    channel.execution_id,
                    channel.last_seq,
                    count=500,
                )
            except Exception:
                logger.exception("redis multiplex catch-up failed; continuing with PG")
                active_stream = None
                break
            for backlog_event in backlog:
                if backlog_event.seq <= channel.last_seq:
                    continue
                channel.last_seq = backlog_event.seq
                yield _event_to_sse(backlog_event)
                if _is_terminal_value(backlog_event.event_type):
                    channel.terminal = True

        if all(channel.terminal for channel in channels.values()):
            return

        # Phase 3 — forward tasks: per-execution bus queue → aggregate queue.
        def make_forwarder(channel: _Channel) -> asyncio.Task[None]:
            async def forward() -> None:
                while True:
                    item = await channel.queue.get()
                    if item is None:
                        return
                    await aggregate.put(item)

            return asyncio.create_task(forward())

        for channel in channels.values():
            if not channel.terminal:
                channel.forward_task = make_forwarder(channel)

        # Phase 4 — multiplexed live tail with bounded PG polling fallback.
        while True:
            if await request_is_disconnected():
                break
            open_channels = [c for c in channels.values() if not c.terminal]
            if not open_channels:
                break
            try:
                item: ExecutionEvent | tuple[str, StreamEvent] | object = (
                    await asyncio.wait_for(aggregate.get(), timeout=wait_timeout)
                )
            except TimeoutError:
                item = _TIMEOUT

            if isinstance(item, ExecutionEvent):
                target = channels.get(item.execution_id)
                if target is not None and item.seq > target.last_seq:
                    target.last_seq = item.seq
                    yield _event_to_sse(item)
                    if item.event_type in TERMINAL_EVENTS:
                        target.terminal = True
                        _cancel_forwarder(target)
                continue
            if isinstance(item, tuple):
                execution_id, remote_event = item
                target = channels.get(execution_id)
                if target is not None and remote_event.seq > target.last_seq:
                    target.last_seq = remote_event.seq
                    yield _event_to_sse(remote_event)
                    if _is_terminal_value(remote_event.event_type):
                        target.terminal = True
                        _cancel_forwarder(target)
                continue

            # Timed out on the aggregate queue: drain forwarded items without
            # blocking, then poll PG for anything the local bus missed.
            drained = False
            while True:
                try:
                    pending = aggregate.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(pending, ExecutionEvent):
                    target = channels.get(pending.execution_id)
                    if target is not None and pending.seq > target.last_seq:
                        target.last_seq = pending.seq
                        drained = True
                        yield _event_to_sse(pending)
                        if pending.event_type in TERMINAL_EVENTS:
                            target.terminal = True
                            _cancel_forwarder(target)
                elif isinstance(pending, tuple):
                    execution_id, remote_event = pending
                    target = channels.get(execution_id)
                    if target is not None and remote_event.seq > target.last_seq:
                        target.last_seq = remote_event.seq
                        drained = True
                        yield _event_to_sse(remote_event)
                        if _is_terminal_value(remote_event.event_type):
                            target.terminal = True
                            _cancel_forwarder(target)
                elif pending is None:
                    pass  # Channel close markers carry no data.
            if [c for c in channels.values() if not c.terminal]:
                progressed = drained
                for channel in channels.values():
                    if channel.terminal:
                        continue
                    async with session_factory() as session:
                        polled = await ExecutionRepo(session).list_events_after(
                            channel.execution_id, channel.last_seq
                        )
                    for row in polled:
                        if int(row.seq) <= channel.last_seq:
                            continue
                        channel.last_seq = int(row.seq)
                        progressed = True
                        yield _row_to_sse(channel.execution_id, row)
                        if _is_terminal_value(row.event_type):
                            channel.terminal = True
                            _cancel_forwarder(channel)
                if not progressed:
                    yield {"comment": "ping"}
            else:
                if not drained:
                    yield {"comment": "ping"}
    finally:
        for channel in channels.values():
            _cancel_forwarder(channel)
            bus.unsubscribe(channel.execution_id, channel.queue)
        if on_closed is not None:
            on_closed()


def _cancel_forwarder(channel: _Channel) -> None:
    task = channel.forward_task
    channel.forward_task = None
    if task is not None and not task.done():
        task.cancel()


__all__ = [
    "composite_event_id",
    "iter_multi_execution_sse",
    "parse_after_map",
]
