"""Persistence operations for durable workflow schedules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkflowSchedule


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowScheduleRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, schedule_id: str) -> WorkflowSchedule | None:
        return await self.session.get(WorkflowSchedule, schedule_id)

    async def get_for_workflow(
        self, workflow_id: str, schedule_id: str
    ) -> WorkflowSchedule | None:
        result = await self.session.execute(
            select(WorkflowSchedule).where(
                WorkflowSchedule.id == schedule_id,
                WorkflowSchedule.workflow_id == workflow_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: str) -> list[WorkflowSchedule]:
        result = await self.session.execute(
            select(WorkflowSchedule)
            .where(WorkflowSchedule.workflow_id == workflow_id)
            .order_by(WorkflowSchedule.created_at.asc(), WorkflowSchedule.id.asc())
        )
        return list(result.scalars())

    async def create(
        self,
        *,
        workflow_id: str,
        published_version_id: str,
        name: str,
        cron_expression: str,
        timezone: str,
        input_json: dict[str, Any],
        misfire_policy: str,
        failure_policy: str,
        retry_delay_seconds: int,
        next_run_at: datetime,
        enabled: bool,
    ) -> WorkflowSchedule:
        row = WorkflowSchedule(
            id=uuid4().hex,
            workflow_id=workflow_id,
            published_version_id=published_version_id,
            name=name,
            cron_expression=cron_expression,
            timezone=timezone,
            status="active" if enabled else "disabled",
            input_json=dict(input_json),
            misfire_policy=misfire_policy,
            failure_policy=failure_policy,
            retry_delay_seconds=retry_delay_seconds,
            next_run_at=next_run_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_due(self, now: datetime, *, limit: int) -> list[WorkflowSchedule]:
        stmt = (
            select(WorkflowSchedule)
            .where(
                WorkflowSchedule.status == "active",
                WorkflowSchedule.next_run_at <= now,
            )
            .order_by(WorkflowSchedule.next_run_at.asc(), WorkflowSchedule.id.asc())
            .limit(limit)
        )
        if self.session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def claim_due(self, schedule_id: str, scheduled_for: datetime) -> bool:
        """Atomically claim one exact due slot within the caller's transaction."""
        result = await self.session.execute(
            update(WorkflowSchedule)
            .where(
                WorkflowSchedule.id == schedule_id,
                WorkflowSchedule.status == "active",
                WorkflowSchedule.next_run_at == scheduled_for,
                WorkflowSchedule.pending_run_at.is_(None),
            )
            .values(pending_run_at=scheduled_for, updated_at=_utcnow())
            .execution_options(synchronize_session=False)
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            return False
        self.session.expire_all()
        return True

    async def bind_version_for_workflow(
        self, workflow_id: str, published_version_id: str
    ) -> int:
        rows = await self.list_for_workflow(workflow_id)
        now = _utcnow()
        for row in rows:
            row.published_version_id = published_version_id
            row.updated_at = now
        if rows:
            await self.session.flush()
        return len(rows)

    async def disable(self, row: WorkflowSchedule) -> None:
        row.status = "disabled"
        row.pending_run_at = None
        row.updated_at = _utcnow()
        await self.session.flush()


__all__ = ["WorkflowScheduleRepo"]
