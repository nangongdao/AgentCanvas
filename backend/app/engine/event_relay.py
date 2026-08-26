"""Durable execution event relay (I1 Phase 5).

Reads committed ``execution_events`` rows after a durable cursor, publishes
them to execution-specific Redis Streams, then advances the cursor. When Redis
is unavailable the relay stays idle; SSE falls back to bounded PostgreSQL
polling so history is never lost.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import ExecutionEventRelayRepo, ExecutionRepo
from app.engine.event_stream import (
    GLOBAL_RELAY_CURSOR,
    ExecutionEventStream,
    StreamEvent,
    close_redis_client,
    connect_event_stream_client,
)

logger = logging.getLogger(__name__)


class EventRelay:
    """Publish committed execution events to Redis Streams with a durable cursor."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        redis_url: str = "",
        poll_seconds: float = 0.5,
        batch_size: int = 100,
        stream_maxlen: int = 10_000,
        stream_key: str = GLOBAL_RELAY_CURSOR,
        redis_client: Any | None = None,
        event_stream: ExecutionEventStream | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.redis_url = redis_url
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.batch_size = max(1, int(batch_size))
        self.stream_maxlen = max(1, int(stream_maxlen))
        self.stream_key = stream_key
        self._external_client = redis_client is not None
        self._client = redis_client
        self._stream = event_stream
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopped = False
        self._owns_client = False
        self._publish_enabled = True

    @property
    def enabled(self) -> bool:
        return self._stream is not None

    @property
    def event_stream(self) -> ExecutionEventStream | None:
        """Shared stream adapter used by SSE tails on this process."""
        return self._stream

    def kick(self) -> None:
        """Wake the relay after a local persistence batch commits."""
        self._wake.set()

    async def start(self, *, run_loop: bool = True) -> None:
        """Connect the stream client and optionally own the relay loop."""
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._publish_enabled = run_loop
        await self._connect_stream()
        if self._stream is None and not self.redis_url:
            logger.info("event relay idle; REDIS_URL unavailable (SSE will poll PostgreSQL)")
            return
        task_name = "execution-event-relay" if run_loop else "execution-event-connector"
        self._task = asyncio.create_task(self._loop(), name=task_name)
        logger.info(
            "execution event %s started",
            "relay" if run_loop else "connection supervisor",
        )

    async def _connect_stream(self) -> None:
        if self._stream is not None:
            return
        if self._client is None and self.redis_url:
            self._client = await connect_event_stream_client(self.redis_url)
            self._owns_client = self._client is not None
        if self._client is not None:
            self._stream = ExecutionEventStream(self._client, maxlen=self.stream_maxlen)

    async def _drop_owned_connection(self) -> None:
        if not self._owns_client:
            return
        await close_redis_client(self._client)
        self._client = None
        self._stream = None
        self._owns_client = False

    async def _check_connection(self) -> None:
        if not self._owns_client or self._client is None:
            return
        await self._client.ping()

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._drop_owned_connection()
        self._stream = None

    async def publish_pending(self) -> int:
        """Publish one batch of committed events. Returns published count."""
        if self._stream is None:
            return 0
        async with self.session_factory() as session:
            cursor_repo = ExecutionEventRelayRepo(session)
            cursor = await cursor_repo.get_or_create(self.stream_key, lock=True)
            after_id = int(cursor.last_event_id)
            rows = await ExecutionRepo(session).list_events_after_id(
                after_id,
                limit=self.batch_size,
            )
            if not rows:
                await session.rollback()
                return 0

            last_id = after_id
            published = 0
            for row in rows:
                event = StreamEvent(
                    execution_id=row.execution_id,
                    seq=int(row.seq),
                    event_type=row.event_type,
                    node_id=row.node_id,
                    ts=row.ts.isoformat() if hasattr(row.ts, "isoformat") else str(row.ts),
                    payload=dict(row.payload_json or {}),
                    event_row_id=int(row.id),
                )
                await self._stream.publish(event)
                last_id = int(row.id)
                published += 1
            await cursor_repo.advance(self.stream_key, last_id)
            await session.commit()
            return published

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self._connect_stream()
                if self._stream is None:
                    published = 0
                elif self._publish_enabled:
                    published = await self.publish_pending()
                else:
                    await self._check_connection()
                    published = 0
            except Exception:
                logger.exception("execution event Redis operation failed; reconnecting")
                await self._drop_owned_connection()
                published = 0
            if self._stopped:
                break
            if self._publish_enabled and published >= self.batch_size:
                # More rows may be waiting; keep draining without sleeping.
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue


__all__ = ["EventRelay"]
