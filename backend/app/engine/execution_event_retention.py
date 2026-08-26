"""Durable scheduler that prunes aged-out execution event detail rows (C6-1).

``execution_events`` grows unbounded because nothing deletes old detail rows.
This scheduler scans for events past the configured retention window that
belong to executions which have already reached a terminal state, and deletes
them in bounded batches so the write lock is held only for short windows and
concurrent execution writes are not blocked.

On PostgreSQL the table is intended to become partition-aware in C6-1b: once
the table is converted to a partitioned relation, ``prune_once`` can be
switched to ``DETACH PARTITION`` for O(1) drops. The current batched-DELETE
implementation works on both SQLite and un-partitioned PostgreSQL and is the
correct fallback for small/medium deployments that never reach partition
scale.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import ExecutionRepo

logger = logging.getLogger(__name__)

_SQLITE_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2)


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ExecutionEventRetentionScheduler:
    """Periodically delete terminal-execution events past the retention window."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        retention_days: int,
        grace_days: int,
        batch_size: int,
        poll_seconds: float,
        owner_id: str,
    ) -> None:
        self.session_factory = session_factory
        self.retention_days = max(0, int(retention_days))
        self.grace_days = max(0, int(grace_days))
        self.batch_size = max(1, int(batch_size))
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.owner_id = owner_id
        bind = session_factory.kw.get("bind")
        self._database_backend = str(getattr(getattr(bind, "dialect", None), "name", ""))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopped = False

    @property
    def enabled(self) -> bool:
        return self.retention_days > 0

    def start(self) -> None:
        if not self.enabled:
            logger.info(
                "execution event retention disabled (retention_days=0); "
                "prune scheduler will not start"
            )
            return
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(
            self._loop(), name=f"execution-event-retention:{self.owner_id}"
        )

    def kick(self) -> None:
        self._wake.set()

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

    async def prune_once(self, *, now: datetime | None = None) -> int:
        """Delete one full sweep of aged-out events, in bounded batches.

        Each batch commits in its own short transaction and yields to the
        event loop between batches, so a large backlog cannot monopolise the
        write lock or starve concurrent execution writes. Returns the total
        number of detail rows deleted across all batches in this sweep.
        """
        if not self.enabled:
            return 0
        current = _aware_utc(now or datetime.now(UTC))
        cutoff = current - timedelta(days=self.retention_days + self.grace_days)
        total = 0
        while not self._stopped:
            deleted = await self._prune_batch(cutoff)
            if deleted == 0:
                break
            total += deleted
            # Yield between batches so concurrent writers can make progress
            # even when a large backlog is being drained.
            await asyncio.sleep(0)
        return total

    async def _prune_batch(self, cutoff: datetime) -> int:
        for attempt in range(len(_SQLITE_LOCK_RETRY_DELAYS) + 1):
            try:
                async with self.session_factory() as session:
                    deleted = await ExecutionRepo(session).prune_events_before(
                        cutoff, batch_size=self.batch_size
                    )
                    await session.commit()
                    return deleted
            except OperationalError as exc:
                if not self._is_retryable_sqlite_lock(exc) or attempt >= len(
                    _SQLITE_LOCK_RETRY_DELAYS
                ):
                    logger.exception(
                        "execution event retention prune batch failed (cutoff=%s)",
                        cutoff.isoformat(),
                    )
                    return 0
                await asyncio.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])
            except Exception:
                logger.exception(
                    "execution event retention prune batch failed (cutoff=%s)",
                    cutoff.isoformat(),
                )
                return 0
        return 0

    def _is_retryable_sqlite_lock(self, exc: OperationalError) -> bool:
        return self._database_backend == "sqlite" and "locked" in str(exc.orig).lower()

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self.prune_once()
            except Exception:
                logger.exception("execution event retention sweep failed")
            if self._stopped:
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue


__all__ = ["ExecutionEventRetentionScheduler"]
