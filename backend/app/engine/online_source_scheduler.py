"""Durable periodic dispatcher for online knowledge sources (C4-3)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import OnlineSourceRepo
from app.rag.service import KnowledgeNotFoundError
from app.services.online_source_sync import OnlineSourceBusyError, OnlineSourceSyncService

logger = logging.getLogger(__name__)

_SQLITE_LOCK_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08)


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


class OnlineSourceScheduler:
    """Scan due sources; the sync service performs the atomic fenced claim."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sync_service: OnlineSourceSyncService,
        *,
        owner_id: str,
        poll_seconds: float = 5.0,
        batch_size: int = 10,
        lease_seconds: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.sync_service = sync_service
        self.owner_id = owner_id
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.batch_size = max(1, int(batch_size))
        self.lease_seconds = max(
            1, int(lease_seconds or sync_service.lease_seconds)
        )
        bind = session_factory.kw.get("bind")
        self._database_backend = str(getattr(getattr(bind, "dialect", None), "name", ""))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopped = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(
            self._loop(), name=f"online-source-scheduler:{self.owner_id}"
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

    async def sync_once(self, *, now: datetime | None = None) -> int:
        current = _aware_utc(now or datetime.now(UTC))
        async with self.session_factory() as session:
            due = await OnlineSourceRepo(session).list_due(
                current,
                stale_before=current - timedelta(seconds=self.lease_seconds),
                limit=self.batch_size,
            )
            source_ids = [row.id for row in due]
            await session.rollback()

        completed = 0
        for source_id in source_ids:
            claim_time = current if now is not None else _aware_utc(datetime.now(UTC))
            completed += int(await self._sync_candidate(source_id, now=claim_time))
        return completed

    async def _sync_candidate(self, source_id: str, *, now: datetime) -> bool:
        for attempt in range(len(_SQLITE_LOCK_RETRY_DELAYS) + 1):
            try:
                await self.sync_service.sync_source(source_id, scheduled=True, now=now)
                return True
            except (KnowledgeNotFoundError, OnlineSourceBusyError):
                return False
            except OperationalError as exc:
                if not self._is_retryable_sqlite_lock(exc) or attempt >= len(
                    _SQLITE_LOCK_RETRY_DELAYS
                ):
                    logger.exception("online source %s database sync failed", source_id)
                    return True
                await asyncio.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])
            except Exception:
                logger.exception("online source %s scheduled sync failed", source_id)
                return True
        raise RuntimeError("unreachable online-source retry state")

    def _is_retryable_sqlite_lock(self, exc: OperationalError) -> bool:
        return self._database_backend == "sqlite" and "locked" in str(exc.orig).lower()

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                completed = await self.sync_once()
            except Exception:
                logger.exception("online source sync batch failed")
                completed = 0
            if self._stopped:
                break
            if completed >= self.batch_size:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue


__all__ = ["OnlineSourceScheduler"]
