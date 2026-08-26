"""Atomic persistence operations for project quota accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.db.models import (
    ProjectQuota,
    ProjectQuotaCounter,
    ProjectQuotaPeriodUsage,
    ProjectQuotaReservation,
)

ReservationKind = Literal["execution", "document_storage", "stdio_mcp_process"]
PeriodKind = Literal["embedding_input_bytes", "model_cost_units"]


@dataclass(frozen=True)
class ReservationAttempt:
    created: bool
    exceeded: bool = False
    existing_amount: int | None = None


def _realtime_columns(
    kind: ReservationKind,
) -> tuple[InstrumentedAttribute[int], InstrumentedAttribute[int | None]]:
    if kind == "execution":
        return (
            ProjectQuotaCounter.concurrent_executions,
            ProjectQuota.concurrent_execution_limit,
        )
    if kind == "document_storage":
        return ProjectQuotaCounter.storage_bytes, ProjectQuota.storage_bytes_limit
    return (
        ProjectQuotaCounter.stdio_mcp_processes,
        ProjectQuota.stdio_mcp_process_limit,
    )


def _period_columns(
    kind: PeriodKind,
) -> tuple[InstrumentedAttribute[int], InstrumentedAttribute[int | None]]:
    if kind == "embedding_input_bytes":
        return (
            ProjectQuotaPeriodUsage.embedding_input_bytes,
            ProjectQuota.monthly_embedding_input_bytes_limit,
        )
    return (
        ProjectQuotaPeriodUsage.model_cost_units,
        ProjectQuota.monthly_model_cost_units_limit,
    )


class ProjectQuotaRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _insert_if_missing(
        self,
        model: type[Any],
        values: dict[str, Any],
        *,
        conflict_columns: tuple[str, ...],
    ) -> bool:
        bind = self.session.get_bind()
        if bind.dialect.name == "sqlite":
            result = await self.session.execute(
                sqlite_insert(model)
                .values(**values)
                .on_conflict_do_nothing(index_elements=conflict_columns)
            )
            return int(getattr(result, "rowcount", 0) or 0) == 1
        try:
            async with self.session.begin_nested():
                self.session.add(model(**values))
                await self.session.flush()
            return True
        except IntegrityError:
            return False

    async def ensure_state(self, project_id: str) -> tuple[ProjectQuota, ProjectQuotaCounter]:
        # Fast path (C6-4): in the steady state both rows already exist, so two
        # primary-key reads replace the insert-if-missing probes that used to
        # run on every execution launch. The insert path below remains the
        # bootstrap fallback for a project's first reservation.
        quota = await self.session.get(ProjectQuota, project_id)
        counter = await self.session.get(ProjectQuotaCounter, project_id)
        if quota is not None and counter is not None:
            return quota, counter
        await self._insert_if_missing(
            ProjectQuota,
            {"project_id": project_id},
            conflict_columns=("project_id",),
        )
        await self._insert_if_missing(
            ProjectQuotaCounter,
            {"project_id": project_id},
            conflict_columns=("project_id",),
        )
        if quota is None:
            quota = await self.session.get(ProjectQuota, project_id)
        if counter is None:
            counter = await self.session.get(ProjectQuotaCounter, project_id)
        if quota is None or counter is None:
            raise KeyError(f"project not found: {project_id}")
        return quota, counter

    async def ensure_period(self, project_id: str, period_start: date) -> ProjectQuotaPeriodUsage:
        key = (project_id, period_start)
        await self._insert_if_missing(
            ProjectQuotaPeriodUsage,
            {"project_id": project_id, "period_start": period_start},
            conflict_columns=("project_id", "period_start"),
        )
        usage = await self.session.get(ProjectQuotaPeriodUsage, key)
        if usage is None:
            raise KeyError(f"project not found: {project_id}")
        return usage

    async def get_reservation(
        self, project_id: str, kind: ReservationKind, resource_id: str
    ) -> ProjectQuotaReservation | None:
        result = await self.session.execute(
            select(ProjectQuotaReservation).where(
                ProjectQuotaReservation.project_id == project_id,
                ProjectQuotaReservation.kind == kind,
                ProjectQuotaReservation.resource_id == resource_id,
            )
        )
        return result.scalars().first()

    async def try_reserve(
        self,
        project_id: str,
        kind: ReservationKind,
        resource_id: str,
        amount: int,
    ) -> ReservationAttempt:
        await self.ensure_state(project_id)
        reservation_id = uuid4().hex
        inserted = await self._insert_if_missing(
            ProjectQuotaReservation,
            {
                "id": reservation_id,
                "project_id": project_id,
                "kind": kind,
                "resource_id": resource_id,
                "amount": amount,
            },
            conflict_columns=("project_id", "kind", "resource_id"),
        )
        if not inserted:
            existing = await self.get_reservation(project_id, kind, resource_id)
            if existing is None:
                raise RuntimeError("quota reservation conflict could not be resolved")
            return ReservationAttempt(False, existing_amount=existing.amount)

        counter_column, limit_column = _realtime_columns(kind)
        limit_value = (
            select(limit_column).where(ProjectQuota.project_id == project_id).scalar_subquery()
        )
        result = await self.session.execute(
            update(ProjectQuotaCounter)
            .where(
                ProjectQuotaCounter.project_id == project_id,
                or_(
                    limit_value.is_(None),
                    counter_column + amount <= limit_value,
                ),
            )
            .values(
                {
                    counter_column.key: counter_column + amount,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        if int(getattr(result, "rowcount", 0) or 0) == 1:
            return ReservationAttempt(True)

        await self.session.execute(
            delete(ProjectQuotaReservation).where(ProjectQuotaReservation.id == reservation_id)
        )
        return ReservationAttempt(False, exceeded=True)

    async def release(self, project_id: str, kind: ReservationKind, resource_id: str) -> int | None:
        result = await self.session.execute(
            delete(ProjectQuotaReservation)
            .where(
                ProjectQuotaReservation.project_id == project_id,
                ProjectQuotaReservation.kind == kind,
                ProjectQuotaReservation.resource_id == resource_id,
            )
            .returning(ProjectQuotaReservation.amount)
        )
        amount = result.scalar_one_or_none()
        if amount is None:
            return None

        counter_column, _limit_column = _realtime_columns(kind)
        counter_result = await self.session.execute(
            update(ProjectQuotaCounter)
            .where(
                ProjectQuotaCounter.project_id == project_id,
                counter_column >= amount,
            )
            .values(
                {
                    counter_column.key: counter_column - amount,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        if int(getattr(counter_result, "rowcount", 0) or 0) != 1:
            raise RuntimeError("quota counter drift detected while releasing reservation")
        return int(amount)

    async def try_charge_period(
        self,
        project_id: str,
        period_start: date,
        kind: PeriodKind,
        amount: int,
        *,
        allow_overage: bool = False,
    ) -> bool:
        await self.ensure_state(project_id)
        await self.ensure_period(project_id, period_start)
        usage_column, limit_column = _period_columns(kind)
        conditions = [
            ProjectQuotaPeriodUsage.project_id == project_id,
            ProjectQuotaPeriodUsage.period_start == period_start,
        ]
        if not allow_overage:
            limit_value = (
                select(limit_column).where(ProjectQuota.project_id == project_id).scalar_subquery()
            )
            conditions.append(or_(limit_value.is_(None), usage_column + amount <= limit_value))
        result = await self.session.execute(
            update(ProjectQuotaPeriodUsage)
            .where(*conditions)
            .values(
                {
                    usage_column.key: usage_column + amount,
                    "updated_at": datetime.now(UTC),
                }
            )
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def replace_realtime_state(
        self,
        project_id: str,
        *,
        execution_ids: list[str],
        documents: list[tuple[str, int]],
    ) -> None:
        _quota, counter = await self.ensure_state(project_id)
        await self.session.execute(
            delete(ProjectQuotaReservation).where(ProjectQuotaReservation.project_id == project_id)
        )
        self.session.add_all(
            [
                ProjectQuotaReservation(
                    project_id=project_id,
                    kind="execution",
                    resource_id=execution_id,
                    amount=1,
                )
                for execution_id in execution_ids
            ]
            + [
                ProjectQuotaReservation(
                    project_id=project_id,
                    kind="document_storage",
                    resource_id=document_id,
                    amount=size_bytes,
                )
                for document_id, size_bytes in documents
            ]
        )
        counter.concurrent_executions = len(execution_ids)
        counter.storage_bytes = sum(size for _document_id, size in documents)
        counter.stdio_mcp_processes = 0
        counter.updated_at = datetime.now(UTC)
        await self.session.flush()
