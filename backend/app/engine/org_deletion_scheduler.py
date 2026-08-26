"""Scheduler that hard-purges organizations whose deletion grace window has elapsed (C7-5).

Each sweep scans for organizations in the ``requested`` state whose
``purge_due_at`` is now in the past, transitions them to ``purging``, and
runs the full tenant data sweep via :func:`purge_organization_data`. A purge
that fails mid-way leaves the org in the ``purging`` state so the next sweep
retries; the idempotent delete-by-id shape makes a partial retry safe.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Organization
from app.rag import RagService
from app.services.org_deletion import purge_organization_data

logger = logging.getLogger(__name__)

_SQLITE_LOCK_RETRY_DELAYS = (0.05, 0.1, 0.2)


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


class OrgDeletionScheduler:
    """Periodically purge organizations whose grace window has elapsed."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        rag_service: RagService,
        batch_size: int,
        poll_seconds: float,
        owner_id: str,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.rag_service = rag_service
        self.batch_size = max(1, int(batch_size))
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
        self._task = asyncio.create_task(
            self._loop(), name=f"org-deletion-scheduler:{self.owner_id}"
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

    async def purge_once(self, *, now: datetime | None = None) -> int:
        """Purge every due organization; returns how many were swept."""
        current = _aware_utc(now or datetime.now(UTC))
        async with self.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(Organization)
                        .where(
                            Organization.deletion_status == "requested",
                            Organization.purge_due_at.is_not(None),
                            Organization.purge_due_at <= current,
                        )
                        .order_by(Organization.purge_due_at.asc())
                        .limit(max(1, self.batch_size))
                    )
                )
                .scalars()
                .all()
            )
            due_ids = [row.id for row in rows]
        swept = 0
        for org_id in due_ids:
            swept += await self._purge_one(org_id)
        return swept

    async def _purge_one(self, org_id: str) -> int:
        for attempt in range(len(_SQLITE_LOCK_RETRY_DELAYS) + 1):
            try:
                await purge_organization_data(
                    self.settings,
                    self.session_factory,
                    org_id,
                    rag_service=self.rag_service,
                    batch_size=self.batch_size,
                )
                logger.info("org deletion purge completed for %s", org_id)
                return 1
            except OperationalError as exc:
                if not self._is_retryable_sqlite_lock(exc) or attempt >= len(
                    _SQLITE_LOCK_RETRY_DELAYS
                ):
                    logger.exception("org deletion purge failed (org=%s)", org_id)
                    return 0
                await asyncio.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])
            except Exception:
                logger.exception("org deletion purge failed (org=%s)", org_id)
                return 0
        return 0

    def _is_retryable_sqlite_lock(self, exc: OperationalError) -> bool:
        return self._database_backend == "sqlite" and "locked" in str(exc.orig).lower()

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self.purge_once()
            except Exception:
                logger.exception("org deletion scheduler sweep failed")
            if self._stopped:
                break
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue


__all__ = ["OrgDeletionScheduler"]
