"""In-memory event bus with optional async persistence callback."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from app.schemas.events import EventType, ExecutionEvent

logger = logging.getLogger(__name__)

PersistFn = Callable[[list[ExecutionEvent]], Awaitable[None]]
ObserverFn = Callable[[ExecutionEvent], None]
QueueObserverFn = Callable[[int], None]


class EventBus:
    """Per-execution fan-out queues + monotonic seq assignment."""

    def __init__(
        self,
        persist: PersistFn | None = None,
        *,
        flush_interval: float = 0.05,
        retry_interval: float = 0.1,
        observer: ObserverFn | None = None,
        queue_observer: QueueObserverFn | None = None,
    ) -> None:
        self._subs: dict[str, set[asyncio.Queue[ExecutionEvent | None]]] = defaultdict(set)
        self._seq: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._persist = persist
        self._pending: list[ExecutionEvent] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._flush_interval = flush_interval
        self._retry_interval = retry_interval
        self._observer = observer
        self._queue_observer = queue_observer

    def set_persist(self, persist: PersistFn | None) -> None:
        self._persist = persist

    async def publish(
        self,
        execution_id: str,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ExecutionEvent:
        async with self._lock:
            self._seq[execution_id] += 1
            seq = self._seq[execution_id]
            event = ExecutionEvent(
                execution_id=execution_id,
                event_type=event_type,
                seq=seq,
                node_id=node_id,
                payload=payload or {},
            )
            if self._persist is not None:
                self._pending.append(event)
                self._observe_queue()
        self._deliver(event)
        # Persist asynchronously (batched)
        if self._persist is not None and (
            self._flush_task is None or self._flush_task.done()
        ):
            self._flush_task = asyncio.create_task(self._flush_loop())
        return event

    def publish_committed(self, event: ExecutionEvent) -> None:
        """Fan out an event that was inserted by the caller's DB transaction."""
        self.seed_seq(event.execution_id, event.seq)
        self._deliver(event)

    def _deliver(self, event: ExecutionEvent) -> None:
        if self._observer is not None:
            try:
                self._observer(event)
            except Exception:
                logger.exception("execution event observer failed")
        # Fan-out to live subscribers
        for q in list(self._subs.get(event.execution_id, set())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "event queue full for %s seq=%s",
                    event.execution_id,
                    event.seq,
                )

    async def _flush_loop(self) -> None:
        """Drain pending events until empty; reschedules itself via publish()."""
        while True:
            await asyncio.sleep(self._flush_interval)
            async with self._lock:
                if not self._pending:
                    return
                batch = self._pending
                self._pending = []
                self._observe_queue()
                persist = self._persist
            if persist is None:
                async with self._lock:
                    self._pending = batch + self._pending
                    self._observe_queue()
                return
            try:
                await persist(batch)
            except asyncio.CancelledError:
                async with self._lock:
                    self._pending = batch + self._pending
                    self._observe_queue()
                raise
            except Exception:
                logger.exception(
                    "failed to persist event batch (%d event(s)); retrying",
                    len(batch),
                )
                async with self._lock:
                    self._pending = batch + self._pending
                    self._observe_queue()
                await asyncio.sleep(self._retry_interval)

    def _observe_queue(self) -> None:
        if self._queue_observer is None:
            return
        try:
            self._queue_observer(len(self._pending))
        except Exception:
            logger.exception("event queue observer failed")

    def subscribe(self, execution_id: str) -> asyncio.Queue[ExecutionEvent | None]:
        q: asyncio.Queue[ExecutionEvent | None] = asyncio.Queue(maxsize=1024)
        self._subs[execution_id].add(q)
        return q

    def unsubscribe(self, execution_id: str, q: asyncio.Queue[ExecutionEvent | None]) -> None:
        subs = self._subs.get(execution_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(execution_id, None)

    async def close_execution(self, execution_id: str) -> None:
        """Persist queued events, then wake subscribers so streams can exit."""
        await self.flush()
        for q in list(self._subs.get(execution_id, set())):
            with suppress(asyncio.QueueFull):
                q.put_nowait(None)

    async def flush(self) -> None:
        """Wait until the current persistence batch has drained."""
        task = self._flush_task
        if task is not None and not task.done():
            await asyncio.shield(task)

    def last_seq(self, execution_id: str) -> int:
        return self._seq.get(execution_id, 0)

    def seed_seq(self, execution_id: str, seq: int) -> None:
        """Ensure in-memory seq continues after process restart (loaded from DB)."""
        self._seq[execution_id] = max(self._seq.get(execution_id, 0), seq)


class EventEmitter:
    """Bound to a single execution_id for node instrumentation."""

    def __init__(self, bus: EventBus, execution_id: str) -> None:
        self.bus = bus
        self.execution_id = execution_id

    async def emit(
        self,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ExecutionEvent:
        return await self.bus.publish(
            self.execution_id, event_type, node_id=node_id, payload=payload
        )
