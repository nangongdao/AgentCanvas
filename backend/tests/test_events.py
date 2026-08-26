"""Event bus persistence and stream shutdown ordering."""

from __future__ import annotations

import asyncio

from app.engine.events import EventBus
from app.schemas.events import EventType, ExecutionEvent


async def test_close_execution_waits_for_persistence() -> None:
    persisted: list[ExecutionEvent] = []

    async def persist(events: list[ExecutionEvent]) -> None:
        await asyncio.sleep(0.01)
        persisted.extend(events)

    bus = EventBus(persist)
    queue = bus.subscribe("execution-1")
    event = await bus.publish("execution-1", EventType.WORKFLOW_FINISHED)

    await bus.close_execution("execution-1")

    assert persisted == [event]
    assert await queue.get() == event
    assert await queue.get() is None


async def test_flush_bridges_late_subscriber_replay_and_live_events() -> None:
    persisted: list[ExecutionEvent] = []

    async def persist(events: list[ExecutionEvent]) -> None:
        await asyncio.sleep(0.01)
        persisted.extend(events)

    bus = EventBus(persist)
    replayed_event = await bus.publish("execution-1", EventType.NODE_STREAMING)

    queue = bus.subscribe("execution-1")
    await bus.flush()
    live_event = await bus.publish("execution-1", EventType.WORKFLOW_FINISHED)

    assert persisted == [replayed_event]
    assert await queue.get() == live_event
    assert [replayed_event.seq, live_event.seq] == [1, 2]


async def test_persistence_is_one_batch_and_retries_without_loss() -> None:
    persisted: list[ExecutionEvent] = []
    attempts: list[list[int]] = []

    async def persist(events: list[ExecutionEvent]) -> None:
        attempts.append([event.seq for event in events])
        if len(attempts) == 1:
            raise RuntimeError("transient write failure")
        persisted.extend(events)

    bus = EventBus(persist, flush_interval=0.001, retry_interval=0.001)
    expected = [
        await bus.publish("execution-1", EventType.NODE_STARTED),
        await bus.publish("execution-1", EventType.NODE_FINISHED),
        await bus.publish("execution-1", EventType.WORKFLOW_FINISHED),
    ]
    await bus.flush()

    assert attempts == [[1, 2, 3], [1, 2, 3]]
    assert persisted == expected


async def test_event_bus_without_persistence_and_full_subscriber_queue() -> None:
    bus = EventBus()
    queue = bus.subscribe("execution")
    for _ in range(queue.maxsize):
        queue.put_nowait(None)

    event = await bus.publish("execution", EventType.NODE_STARTED)

    assert event.seq == 1
    assert bus.last_seq("execution") == 1
    assert bus._flush_task is None  # noqa: SLF001
    bus.unsubscribe("missing", queue)
    bus.unsubscribe("execution", queue)
    assert "execution" not in bus._subs  # noqa: SLF001


async def test_event_bus_flush_exits_if_persistence_is_removed() -> None:
    async def persist(_events: list[ExecutionEvent]) -> None:
        raise AssertionError("removed persistence callback should not run")

    bus = EventBus(persist, flush_interval=0.01)
    await bus.publish("execution", EventType.NODE_STARTED)
    bus.set_persist(None)
    await bus.flush()

    assert bus._pending  # noqa: SLF001
