"""Durable, multi-instance-safe cron workflow dispatcher."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import CostAlert, WorkflowSchedule
from app.db.repositories import (
    ExecutionQueueRepo,
    ExecutionRepo,
    WorkflowRepo,
    WorkflowScheduleRepo,
    WorkflowVersionRepo,
)
from app.engine.execution_launch import _execution_id_for_key
from app.engine.input_validation import validate_execution_inputs
from app.schemas.dsl import WorkflowDSL
from app.services.project_quotas import ProjectQuotaService
from app.services.schedule_time import next_cron_run

logger = logging.getLogger(__name__)

_SQLITE_LOCK_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08)


def _aware_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


class WorkflowScheduleScheduler:
    """Claim due cron slots and create their executions in one transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_seconds: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        self.session_factory = session_factory
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.batch_size = max(1, int(batch_size))
        bind = session_factory.kw.get("bind")
        self._database_backend = str(getattr(getattr(bind, "dialect", None), "name", ""))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopped = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="workflow-schedule-scheduler")

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

    async def dispatch_once(self, *, now: datetime | None = None) -> int:
        current = _aware_utc(now or datetime.now(UTC))
        async with self.session_factory() as session:
            due = await WorkflowScheduleRepo(session).list_due(current, limit=self.batch_size)
            slots = [(row.id, _aware_utc(row.next_run_at)) for row in due]
            await session.rollback()

        dispatched = 0
        for schedule_id, scheduled_for in slots:
            dispatched += int(
                await self._dispatch_slot(schedule_id, scheduled_for=scheduled_for, now=current)
            )
        return dispatched

    async def _dispatch_slot(
        self,
        schedule_id: str,
        *,
        scheduled_for: datetime,
        now: datetime,
    ) -> bool:
        for attempt in range(len(_SQLITE_LOCK_RETRY_DELAYS) + 1):
            try:
                return await self._dispatch_slot_once(
                    schedule_id,
                    scheduled_for=scheduled_for,
                    now=now,
                )
            except OperationalError as exc:
                if not self._is_retryable_sqlite_lock(exc) or attempt >= len(
                    _SQLITE_LOCK_RETRY_DELAYS
                ):
                    raise
                await asyncio.sleep(_SQLITE_LOCK_RETRY_DELAYS[attempt])
        raise RuntimeError("unreachable schedule dispatch retry state")

    async def _dispatch_slot_once(
        self,
        schedule_id: str,
        *,
        scheduled_for: datetime,
        now: datetime,
    ) -> bool:
        async with self.session_factory() as session:
            repo = WorkflowScheduleRepo(session)
            if not await repo.claim_due(schedule_id, scheduled_for):
                await session.rollback()
                return False
            row = await repo.get(schedule_id)
            if row is None:
                await session.rollback()
                return False
            try:
                async with session.begin_nested():
                    await self._enqueue(session, row, scheduled_for=scheduled_for)
            except Exception as exc:  # noqa: BLE001 - policy persists bounded failure state
                if isinstance(exc, OperationalError) and self._is_retryable_sqlite_lock(exc):
                    raise
                await self._advance_failure(
                    session, row, scheduled_for=scheduled_for, now=now, exc=exc
                )
                await session.commit()
                logger.warning("workflow schedule %s dispatch failed: %s", schedule_id, exc)
                return False
            self._advance_success(row, scheduled_for=scheduled_for, now=now)
            await session.commit()
            return True

    def _is_retryable_sqlite_lock(self, exc: OperationalError) -> bool:
        return self._database_backend == "sqlite" and "locked" in str(exc.orig).lower()

    async def _enqueue(
        self,
        session: AsyncSession,
        row: WorkflowSchedule,
        *,
        scheduled_for: datetime,
    ) -> str:
        version = await WorkflowVersionRepo(session).get(row.published_version_id)
        workflow = await WorkflowRepo(session).get(row.workflow_id)
        if (
            version is None
            or version.workflow_id != row.workflow_id
            or version.status != "published"
        ):
            raise ValueError("schedule published version is unavailable")
        if workflow is None or workflow.is_archived:
            raise ValueError("scheduled workflow is unavailable")
        inputs = validate_execution_inputs(
            WorkflowDSL.model_validate(version.dsl_json), dict(row.input_json or {})
        )
        key = f"schedule:{row.id}:{scheduled_for.isoformat()}"
        execution_id = _execution_id_for_key(row.workflow_id, key)
        assert execution_id is not None
        execution = await ExecutionRepo(session).get(execution_id)
        if execution is None:
            execution = await ExecutionRepo(session).create(
                workflow_id=row.workflow_id,
                inputs=inputs,
                execution_id=execution_id,
                workflow_version_id=version.id,
                trigger_source="schedule",
            )
            if workflow.project_id is not None:
                await ProjectQuotaService(session).reserve(
                    workflow.project_id, "execution", execution_id
                )
            await ExecutionQueueRepo(session).enqueue(execution_id, kind="start")
        elif (
            execution.workflow_version_id != version.id
            or execution.input_json != inputs
            or execution.workflow_id != row.workflow_id
        ):
            raise ValueError("schedule idempotency collision")
        row.last_execution_id = execution_id
        return execution_id

    @staticmethod
    def _advance_success(row: WorkflowSchedule, *, scheduled_for: datetime, now: datetime) -> None:
        base = scheduled_for if row.misfire_policy == "catch_up" else now
        row.last_run_at = scheduled_for
        row.next_run_at = next_cron_run(row.cron_expression, row.timezone, base)
        row.pending_run_at = None
        row.last_error = None
        row.failure_count = 0
        row.updated_at = now

    async def _advance_failure(
        self,
        session: AsyncSession,
        row: WorkflowSchedule,
        *,
        scheduled_for: datetime,
        now: datetime,
        exc: Exception,
    ) -> None:
        row.failure_count += 1
        last_error = str(exc)[:2000]
        row.last_error = last_error
        row.pending_run_at = None
        if row.failure_policy == "retry":
            row.next_run_at = now + timedelta(seconds=row.retry_delay_seconds)
        elif row.failure_policy == "alert":
            # The schedule itself is marked errored, and a durable operational
            # alert is recorded so operators can see the failure without
            # scraping logs. execution_id stays None because the dispatch
            # never produced a row (validation failed before enqueue).
            row.status = "error"
            session.add(
                CostAlert(
                    workflow_id=row.workflow_id,
                    execution_id=None,
                    kind="schedule",
                    severity="critical",
                    status="open",
                    limit_value="0",
                    actual_value=str(row.failure_count),
                    message=f"schedule {row.id} dispatch failed: {last_error}",
                )
            )
        else:
            base = scheduled_for if row.misfire_policy == "catch_up" else now
            row.next_run_at = next_cron_run(row.cron_expression, row.timezone, base)
        row.updated_at = now

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                dispatched = await self.dispatch_once()
            except Exception:
                logger.exception("workflow schedule batch failed")
                dispatched = 0
            if self._stopped:
                break
            if dispatched >= self.batch_size:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue


__all__ = ["WorkflowScheduleScheduler"]
