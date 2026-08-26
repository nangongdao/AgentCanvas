"""Rolling daily usage fact aggregation scheduler (C7-1).

Each sweep re-aggregates the trailing ``lookback_days`` complete UTC days.
Re-aggregation is a full-day rewrite, so overlapping sweeps are idempotent
while still absorbing executions that finish after midnight and DSL/price
corrections inside the window. Days older than the window are frozen: their
exported billing history cannot drift after the fact.

Runs in the ``all`` and ``worker`` process roles alongside the other
DB-owning schedulers; concurrent sweeps from multiple workers are safe
because each day rewrite is one transaction and last-writer-wins with
byte-identical contents.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, date, datetime

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.usage_facts import aggregate_day, complete_days_before

logger = logging.getLogger(__name__)

_SQLITE_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2)


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


class UsageFactScheduler:
    """Periodically rewrite usage facts for the trailing complete days."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lookback_days: int,
        poll_seconds: float,
        owner_id: str,
    ) -> None:
        self.session_factory = session_factory
        self.lookback_days = max(1, int(lookback_days))
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.owner_id = owner_id
        bind = session_factory.kw.get("bind")
        self._database_backend = str(getattr(getattr(bind, "dialect", None), "name", ""))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopped = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name=f"usage-fact-scheduler:{self.owner_id}")

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

    async def aggregate_window(self, *, now: datetime | None = None) -> int:
        """Rewrite every day in the lookback window; returns rows written."""
        current = _aware_utc(now or datetime.now(UTC))
        total = 0
        for day in complete_days_before(current, count=self.lookback_days):
            total += await self._aggregate_one_day(day)
        return total

    async def _aggregate_one_day(self, day: date) -> int:
        for attempt in range(len(_SQLITE_LOCK_RETRY_DELAYS) + 1):
            try:
                async with self.session_factory() as session:
                    written = await aggregate_day(session, day=day)
                    await session.commit()
                    return written
            except OperationalError as exc:
                if not self._is_retryable_sqlite_lock(exc) or attempt >= len(
                    _SQLITE_LOCK_RETRY_DELAYS
                ):
                    logger.exception("usage fact aggregation failed (day=%s)", day.isoformat())
                    return 0
                await asyncio.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])
            except Exception:
                logger.exception("usage fact aggregation failed (day=%s)", day.isoformat())
                return 0
        return 0

    def _is_retryable_sqlite_lock(self, exc: OperationalError) -> bool:
        return self._database_backend == "sqlite" and "locked" in str(exc.orig).lower()

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self.aggregate_window()
            except Exception:
                logger.exception("usage fact aggregation sweep failed")
            if self._stopped:
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue


__all__ = ["UsageFactScheduler"]
