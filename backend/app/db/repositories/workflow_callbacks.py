"""Repositories for callback configuration and durable deliveries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkflowCallback, WorkflowCallbackCursor, WorkflowCallbackDelivery


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowCallbackRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_workflow(self, workflow_id: str) -> WorkflowCallback | None:
        result = await self.session.execute(
            select(WorkflowCallback).where(WorkflowCallback.workflow_id == workflow_id)
        )
        return result.scalars().first()

    async def get(self, callback_id: str) -> WorkflowCallback | None:
        return await self.session.get(WorkflowCallback, callback_id)

    async def has_active(self) -> bool:
        result = await self.session.execute(
            select(WorkflowCallback.id).where(WorkflowCallback.status == "active").limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        workflow_id: str,
        project_id: str | None,
        *,
        url: str,
        secret_encrypted: str,
        event_types: list[str],
        timeout_seconds: int,
        max_attempts: int,
        retry_delay_seconds: int,
    ) -> WorkflowCallback:
        now = _utcnow()
        row = WorkflowCallback(
            workflow_id=workflow_id,
            project_id=project_id,
            url=url,
            secret_encrypted=secret_encrypted,
            event_types_json=list(event_types),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
            created_at=now,
            updated_at=now,
            activated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, row: WorkflowCallback, **values: object) -> WorkflowCallback:
        was_active = row.status == "active"
        for key, value in values.items():
            setattr(row, key, value)
        now = _utcnow()
        row.updated_at = now
        if not was_active and row.status == "active":
            row.activated_at = now
        await self.session.flush()
        return row

    async def disable(self, row: WorkflowCallback) -> None:
        row.status = "disabled"
        row.updated_at = _utcnow()
        await self.session.flush()


class WorkflowCallbackDeliveryRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, delivery_id: str) -> WorkflowCallbackDelivery | None:
        return await self.session.get(WorkflowCallbackDelivery, delivery_id)

    async def enqueue(
        self,
        *,
        callback_id: str,
        workflow_id: str,
        project_id: str | None,
        source_type: str,
        source_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> WorkflowCallbackDelivery | None:
        now = _utcnow()
        row = WorkflowCallbackDelivery(
            id=_uuid(),
            callback_id=callback_id,
            workflow_id=workflow_id,
            project_id=project_id,
            source_type=source_type,
            source_id=source_id,
            event_type=event_type,
            payload_json=dict(payload),
            status="pending",
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        insert = (
            postgresql_insert(WorkflowCallbackDelivery)
            if self.session.get_bind().dialect.name == "postgresql"
            else sqlite_insert(WorkflowCallbackDelivery)
        )
        result = await self.session.execute(
            insert.values(
                id=row.id,
                callback_id=row.callback_id,
                workflow_id=row.workflow_id,
                project_id=row.project_id,
                source_type=row.source_type,
                source_id=row.source_id,
                event_type=row.event_type,
                payload_json=row.payload_json,
                status=row.status,
                attempt_count=0,
                next_attempt_at=row.next_attempt_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            ).on_conflict_do_nothing(index_elements=("callback_id", "source_type", "source_id"))
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            return None
        await self.session.flush()
        return row

    async def claim_due(
        self, *, owner: str, limit: int, lease_seconds: int
    ) -> list[WorkflowCallbackDelivery]:
        now = _utcnow()
        eligible = and_(
            WorkflowCallbackDelivery.status.in_(("pending", "retry_wait", "leased")),
            WorkflowCallbackDelivery.next_attempt_at <= now,
            or_(
                WorkflowCallbackDelivery.lease_expires_at.is_(None),
                WorkflowCallbackDelivery.lease_expires_at <= now,
            ),
        )
        stmt = (
            select(WorkflowCallbackDelivery)
            .where(eligible)
            .order_by(WorkflowCallbackDelivery.next_attempt_at, WorkflowCallbackDelivery.created_at)
            .limit(max(1, limit))
        )
        if self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
            claimed_rows = list((await self.session.execute(stmt)).scalars().all())
            for row in claimed_rows:
                row.status = "leased"
                row.lease_owner = owner
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                row.updated_at = now
            await self.session.flush()
            return claimed_rows
        ids = list(
            (await self.session.execute(stmt.with_only_columns(WorkflowCallbackDelivery.id)))
            .scalars()
            .all()
        )
        sqlite_claimed_rows: list[WorkflowCallbackDelivery] = []
        for delivery_id in ids:
            claimed = await self.session.execute(
                update(WorkflowCallbackDelivery)
                .where(WorkflowCallbackDelivery.id == delivery_id, eligible)
                .values(
                    status="leased",
                    lease_owner=owner,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if int(getattr(claimed, "rowcount", 0) or 0) == 1:
                claimed_row = await self.session.get(WorkflowCallbackDelivery, delivery_id)
                if claimed_row is not None:
                    sqlite_claimed_rows.append(claimed_row)
        return sqlite_claimed_rows

    async def mark_delivered(
        self, row: WorkflowCallbackDelivery, *, owner: str, status_code: int
    ) -> bool:
        now = _utcnow()
        result = await self.session.execute(
            update(WorkflowCallbackDelivery)
            .where(
                WorkflowCallbackDelivery.id == row.id,
                WorkflowCallbackDelivery.status == "leased",
                WorkflowCallbackDelivery.lease_owner == owner,
            )
            .values(
                status="delivered",
                attempt_count=WorkflowCallbackDelivery.attempt_count + 1,
                lease_owner=None,
                lease_expires_at=None,
                last_status_code=status_code,
                last_error=None,
                delivered_at=now,
                updated_at=now,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def renew_lease(
        self, row: WorkflowCallbackDelivery, *, owner: str, lease_seconds: int
    ) -> bool:
        now = _utcnow()
        result = await self.session.execute(
            update(WorkflowCallbackDelivery)
            .where(
                WorkflowCallbackDelivery.id == row.id,
                WorkflowCallbackDelivery.status == "leased",
                WorkflowCallbackDelivery.lease_owner == owner,
            )
            .values(
                lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
                updated_at=now,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def mark_failed(
        self,
        row: WorkflowCallbackDelivery,
        *,
        owner: str,
        retry: bool,
        next_attempt_at: datetime,
        status_code: int | None,
        error: str,
    ) -> bool:
        now = _utcnow()
        new_status = "retry_wait" if retry else "dead_letter"
        result = await self.session.execute(
            update(WorkflowCallbackDelivery)
            .where(
                WorkflowCallbackDelivery.id == row.id,
                WorkflowCallbackDelivery.status == "leased",
                WorkflowCallbackDelivery.lease_owner == owner,
            )
            .values(
                status=new_status,
                attempt_count=WorkflowCallbackDelivery.attempt_count + 1,
                next_attempt_at=next_attempt_at,
                lease_owner=None,
                lease_expires_at=None,
                last_status_code=status_code,
                last_error=error[:4000],
                updated_at=now,
            )
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1


class WorkflowCallbackCursorRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self, source_type: str, *, lock: bool = False
    ) -> WorkflowCallbackCursor:
        insert = (
            postgresql_insert(WorkflowCallbackCursor)
            if self.session.get_bind().dialect.name == "postgresql"
            else sqlite_insert(WorkflowCallbackCursor)
        )
        await self.session.execute(
            insert.values(
                source_type=source_type, last_event_id=0, updated_at=_utcnow()
            ).on_conflict_do_nothing(index_elements=("source_type",))
        )
        stmt = select(WorkflowCallbackCursor).where(
            WorkflowCallbackCursor.source_type == source_type
        )
        if lock:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one()

    async def advance_events(self, source_type: str, event_id: int) -> None:
        row = await self.get_or_create(source_type)
        if event_id > row.last_event_id:
            row.last_event_id = event_id
            row.updated_at = _utcnow()
            await self.session.flush()

    async def advance_alerts(self, source_type: str, created_at: datetime, source_id: str) -> None:
        row = await self.get_or_create(source_type)
        if row.last_created_at is None or (
            created_at,
            source_id,
        ) > (row.last_created_at, row.last_source_id or ""):
            row.last_created_at = created_at
            row.last_source_id = source_id
            row.updated_at = _utcnow()
            await self.session.flush()


__all__ = [
    "WorkflowCallbackCursorRepo",
    "WorkflowCallbackDeliveryRepo",
    "WorkflowCallbackRepo",
]
