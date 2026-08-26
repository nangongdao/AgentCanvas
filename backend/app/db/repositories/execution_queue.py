"""Claim, fence, and lifecycle operations for the durable execution queue."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Execution, ExecutionQueueItem


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


def _lease_lock_key(item_id: str) -> int:
    """Map a queue id to a stable signed PostgreSQL advisory-lock key."""
    return int.from_bytes(hashlib.sha256(item_id.encode("utf-8")).digest()[:8], "big", signed=True)


class ExecutionQueueRepo:
    """PostgreSQL SKIP LOCKED / SQLite CAS claim surface for worker leases."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, item_id: str) -> ExecutionQueueItem | None:
        return await self.session.get(ExecutionQueueItem, item_id)

    async def get_for_execution(self, execution_id: str) -> ExecutionQueueItem | None:
        result = await self.session.execute(
            select(ExecutionQueueItem).where(ExecutionQueueItem.execution_id == execution_id)
        )
        return result.scalars().first()

    async def depth_by_status(self) -> dict[str, int]:
        """Item counts grouped by status for the platform queue view (C7-3)."""
        rows = (
            await self.session.execute(
                select(ExecutionQueueItem.status, func.count())
                .group_by(ExecutionQueueItem.status)
                .order_by(ExecutionQueueItem.status.asc())
            )
        ).all()
        return {status: int(count) for status, count in rows}

    async def list_dead_letters(self, *, limit: int = 20) -> list[ExecutionQueueItem]:
        result = await self.session.execute(
            select(ExecutionQueueItem)
            .where(ExecutionQueueItem.status == "dead_letter")
            .order_by(ExecutionQueueItem.updated_at.desc(), ExecutionQueueItem.id.asc())
            .limit(max(1, min(limit, 100)))
        )
        return list(result.scalars().all())

    async def requeue_dead_letter(self, item_id: str) -> bool:
        """Move one dead-lettered item back to the queue (C7-3 replay).

        A single CAS guarded on ``status == 'dead_letter'``: attempts reset so
        the next claim gets a full retry budget, the lease owner/expires are
        cleared, and ``lease_generation`` is deliberately untouched — the next
        claim increments it, which fences any stale pre-replay owner writes.
        """
        now = _utcnow()
        replayed = await self.session.execute(
            update(ExecutionQueueItem)
            .where(ExecutionQueueItem.id == item_id, ExecutionQueueItem.status == "dead_letter")
            .values(
                status="queued",
                owner_id=None,
                lease_expires_at=None,
                attempt=0,
                cancel_requested=False,
                available_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return int(getattr(replayed, "rowcount", 0) or 0) == 1

    async def enqueue(
        self,
        execution_id: str,
        *,
        kind: str = "start",
        payload: dict[str, Any] | None = None,
        available_at: datetime | None = None,
    ) -> ExecutionQueueItem:
        """Create or reopen a queue row and mark the execution as queued."""
        now = _utcnow()
        existing = await self.get_for_execution(execution_id)
        if existing is not None:
            if existing.status in {"queued", "leased", "retry_wait"}:
                raise ValueError(f"execution {execution_id} already has active queue work")
            existing.kind = kind
            existing.status = "queued"
            existing.owner_id = None
            existing.lease_expires_at = None
            existing.available_at = available_at or now
            existing.cancel_requested = False
            existing.last_error = None
            existing.payload_json = dict(payload or {})
            existing.updated_at = now
            row = existing
        else:
            row = ExecutionQueueItem(
                id=_new_id(),
                execution_id=execution_id,
                kind=kind,
                status="queued",
                attempt=0,
                lease_generation=0,
                available_at=available_at or now,
                cancel_requested=False,
                payload_json=dict(payload or {}),
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)

        execution = await self.session.get(Execution, execution_id)
        if execution is not None and execution.status not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            execution.status = "queued"
            execution.error = None
            execution.finished_at = None
        await self.session.flush()
        return row

    async def enqueue_new(
        self,
        execution_id: str,
        *,
        kind: str = "start",
        payload: dict[str, Any] | None = None,
        available_at: datetime | None = None,
    ) -> ExecutionQueueItem:
        """Enqueue a brand-new execution without the pre-insert lookup.

        The launch path creates the ``Execution`` row moments earlier with a
        fresh unique id, so no queue row can exist yet — skipping the
        ``get_for_execution`` SELECT removes one query from every creation
        (C6-4). A conflict would mean the id was reused, which is an
        integrity error rather than a reopenable state.
        """
        now = _utcnow()
        row = ExecutionQueueItem(
            id=_new_id(),
            execution_id=execution_id,
            kind=kind,
            status="queued",
            attempt=0,
            lease_generation=0,
            available_at=available_at or now,
            cancel_requested=False,
            payload_json=dict(payload or {}),
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def claim_next(
        self,
        *,
        owner_id: str,
        lease_seconds: int,
    ) -> ExecutionQueueItem | None:
        """Atomically lease one eligible queue item and mark its execution running."""
        now = _utcnow()
        expires = now + timedelta(seconds=lease_seconds)
        bind = self.session.get_bind()
        eligible = and_(
            ExecutionQueueItem.available_at <= now,
            ExecutionQueueItem.status.in_(("queued", "retry_wait")),
        )

        if bind.dialect.name == "postgresql":
            result = await self.session.execute(
                select(ExecutionQueueItem.id)
                .where(eligible)
                .order_by(
                    ExecutionQueueItem.available_at.asc(), ExecutionQueueItem.created_at.asc()
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            item_id = result.scalar_one_or_none()
            if item_id is None:
                return None
            return await self._activate_lease(item_id, owner_id=owner_id, expires=expires, now=now)

        result = await self.session.execute(
            select(ExecutionQueueItem.id)
            .where(eligible)
            .order_by(ExecutionQueueItem.available_at.asc(), ExecutionQueueItem.created_at.asc())
            .limit(1)
        )
        item_id = result.scalar_one_or_none()
        if item_id is None:
            return None
        # synchronize_session=False keeps CAS evaluation in SQL only. SQLite may
        # round-trip datetimes as naive values, which breaks ORM evaluators.
        claimed = await self.session.execute(
            update(ExecutionQueueItem)
            .where(
                ExecutionQueueItem.id == item_id,
                eligible,
            )
            .values(
                status="leased",
                owner_id=owner_id,
                lease_generation=ExecutionQueueItem.lease_generation + 1,
                lease_expires_at=expires,
                attempt=ExecutionQueueItem.attempt + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if int(getattr(claimed, "rowcount", 0) or 0) != 1:
            return None
        # Drop stale ORM state so the fenced lease values are reloaded from SQL.
        self.session.expire_all()
        return await self._activate_lease(
            item_id, owner_id=owner_id, expires=expires, now=now, already_leased=True
        )

    async def _activate_lease(
        self,
        item_id: str,
        *,
        owner_id: str,
        expires: datetime,
        now: datetime,
        already_leased: bool = False,
    ) -> ExecutionQueueItem | None:
        row = await self.get(item_id)
        if row is None:
            return None
        if not already_leased:
            row.status = "leased"
            row.owner_id = owner_id
            row.lease_generation = int(row.lease_generation) + 1
            row.lease_expires_at = expires
            row.attempt = int(row.attempt) + 1
            row.updated_at = now
        execution = await self.session.get(Execution, row.execution_id)
        if execution is not None and execution.status in {
            "queued",
            "waiting_approval",
            "interrupted",
            "running",
        }:
            execution.status = "running"
            execution.error = None
            execution.finished_at = None
        await self.session.flush()
        return row

    async def heartbeat(
        self,
        *,
        item_id: str,
        owner_id: str,
        lease_generation: int,
        lease_seconds: int,
    ) -> bool:
        now = _utcnow()
        result = await self.session.execute(
            update(ExecutionQueueItem)
            .where(
                ExecutionQueueItem.id == item_id,
                ExecutionQueueItem.status == "leased",
                ExecutionQueueItem.owner_id == owner_id,
                ExecutionQueueItem.lease_generation == lease_generation,
                ExecutionQueueItem.lease_expires_at.is_not(None),
                ExecutionQueueItem.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def lock_lease(self, item_id: str) -> None:
        """Serialize checkpoint writes with scheduler lease reassignment."""
        if self.session.get_bind().dialect.name == "postgresql":
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lease_lock_key)"),
                {"lease_lock_key": _lease_lock_key(item_id)},
            )

    async def owns_lease(
        self,
        *,
        item_id: str,
        owner_id: str,
        lease_generation: int,
        require_unexpired: bool = True,
        lock: bool = False,
    ) -> bool:
        """Validate a fencing token, optionally locking it for a dependent write."""
        conditions = [
            ExecutionQueueItem.id == item_id,
            ExecutionQueueItem.status == "leased",
            ExecutionQueueItem.owner_id == owner_id,
            ExecutionQueueItem.lease_generation == lease_generation,
        ]
        if require_unexpired:
            conditions.extend(
                [
                    ExecutionQueueItem.lease_expires_at.is_not(None),
                    ExecutionQueueItem.lease_expires_at > _utcnow(),
                ]
            )
        stmt = select(ExecutionQueueItem.id).where(*conditions)
        if lock:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def list_expired(self, *, limit: int = 100) -> list[ExecutionQueueItem]:
        """Return a bounded batch of expired lease candidates.

        The scheduler takes the per-item advisory lock and re-reads each row
        before mutation. Keeping this query unlocked avoids an advisory/row
        lock inversion with fenced checkpoint writes.
        """
        stmt = (
            select(ExecutionQueueItem)
            .where(
                ExecutionQueueItem.status == "leased",
                ExecutionQueueItem.lease_expires_at.is_not(None),
                ExecutionQueueItem.lease_expires_at < _utcnow(),
            )
            .order_by(ExecutionQueueItem.lease_expires_at.asc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def complete(
        self,
        *,
        item_id: str,
        owner_id: str,
        lease_generation: int,
    ) -> bool:
        now = _utcnow()
        result = await self.session.execute(
            update(ExecutionQueueItem)
            .where(
                ExecutionQueueItem.id == item_id,
                ExecutionQueueItem.status == "leased",
                ExecutionQueueItem.owner_id == owner_id,
                ExecutionQueueItem.lease_generation == lease_generation,
            )
            .values(
                status="done",
                owner_id=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def release_for_approval(
        self,
        *,
        item_id: str,
        owner_id: str,
        lease_generation: int,
    ) -> bool:
        """Drop the worker lease after a durable waiting_approval checkpoint."""
        now = _utcnow()
        result = await self.session.execute(
            update(ExecutionQueueItem)
            .where(
                ExecutionQueueItem.id == item_id,
                ExecutionQueueItem.status == "leased",
                ExecutionQueueItem.owner_id == owner_id,
                ExecutionQueueItem.lease_generation == lease_generation,
            )
            .values(
                status="done",
                owner_id=None,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def fail(
        self,
        *,
        item_id: str,
        owner_id: str,
        lease_generation: int,
        error: str,
        retryable: bool = False,
        retry_delay_seconds: int = 5,
        max_attempts: int = 3,
    ) -> bool:
        now = _utcnow()
        fence = (
            ExecutionQueueItem.id == item_id,
            ExecutionQueueItem.status == "leased",
            ExecutionQueueItem.owner_id == owner_id,
            ExecutionQueueItem.lease_generation == lease_generation,
        )
        if retryable:
            retry = await self.session.execute(
                update(ExecutionQueueItem)
                .where(
                    *fence,
                    ExecutionQueueItem.attempt < max_attempts,
                    ExecutionQueueItem.cancel_requested.is_(False),
                )
                .values(
                    status="retry_wait",
                    owner_id=None,
                    lease_expires_at=None,
                    available_at=now + timedelta(seconds=retry_delay_seconds),
                    last_error=error,
                    updated_at=now,
                )
            )
            if int(getattr(retry, "rowcount", 0) or 0) == 1:
                return True
        dead_letter = await self.session.execute(
            update(ExecutionQueueItem)
            .where(*fence)
            .values(
                status="dead_letter",
                owner_id=None,
                lease_expires_at=None,
                last_error=error,
                updated_at=now,
            )
        )
        return int(getattr(dead_letter, "rowcount", 0) or 0) == 1

    async def request_cancel(self, execution_id: str) -> ExecutionQueueItem | None:
        row = await self.get_for_execution(execution_id)
        if row is None:
            return None
        if row.status in {"done", "dead_letter"}:
            return row
        row.cancel_requested = True
        row.updated_at = _utcnow()
        await self.session.flush()
        return row


__all__ = ["ExecutionQueueRepo"]
