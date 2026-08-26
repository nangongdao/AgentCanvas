"""Project quota policy and reconciliation service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    CostAlert,
    Document,
    Execution,
    KnowledgeBase,
    Project,
    Workflow,
)
from app.db.repositories.quota import PeriodKind, ProjectQuotaRepo, ReservationKind


def utc_month_start(now: datetime | None = None) -> date:
    current = now or datetime.now(UTC)
    return date(current.year, current.month, 1)


class ProjectQuotaExceeded(RuntimeError):
    def __init__(self, metric: str, limit: int, usage: int, requested: int) -> None:
        self.metric = metric
        self.limit = limit
        self.usage = usage
        self.requested = requested
        super().__init__(
            f"project quota exceeded for {metric}: "
            f"usage {usage}, requested {requested}, limit {limit}"
        )


class ProjectQuotaReservationConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectQuotaSnapshot:
    project_id: str
    period_start: date
    concurrent_execution_limit: int | None
    storage_bytes_limit: int | None
    monthly_embedding_input_bytes_limit: int | None
    monthly_model_cost_units_limit: int | None
    stdio_mcp_process_limit: int | None
    embedding_overage_policy: str
    model_cost_overage_policy: str
    concurrent_executions: int
    storage_bytes: int
    embedding_input_bytes: int
    model_cost_units: int
    stdio_mcp_processes: int


class ProjectQuotaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProjectQuotaRepo(session)

    async def snapshot(
        self, project_id: str, *, period_start: date | None = None
    ) -> ProjectQuotaSnapshot:
        period = period_start or utc_month_start()
        quota, counter = await self.repo.ensure_state(project_id)
        usage = await self.repo.ensure_period(project_id, period)
        return ProjectQuotaSnapshot(
            project_id=project_id,
            period_start=period,
            concurrent_execution_limit=quota.concurrent_execution_limit,
            storage_bytes_limit=quota.storage_bytes_limit,
            monthly_embedding_input_bytes_limit=quota.monthly_embedding_input_bytes_limit,
            monthly_model_cost_units_limit=quota.monthly_model_cost_units_limit,
            stdio_mcp_process_limit=quota.stdio_mcp_process_limit,
            embedding_overage_policy=quota.embedding_overage_policy,
            model_cost_overage_policy=quota.model_cost_overage_policy,
            concurrent_executions=counter.concurrent_executions,
            storage_bytes=counter.storage_bytes,
            embedding_input_bytes=usage.embedding_input_bytes,
            model_cost_units=usage.model_cost_units,
            stdio_mcp_processes=counter.stdio_mcp_processes,
        )

    async def configure(self, project_id: str, **limits: int | None) -> ProjectQuotaSnapshot:
        allowed = {
            "concurrent_execution_limit",
            "storage_bytes_limit",
            "monthly_embedding_input_bytes_limit",
            "monthly_model_cost_units_limit",
            "stdio_mcp_process_limit",
        }
        unknown = set(limits) - allowed
        if unknown:
            raise ValueError(f"unknown project quota limits: {', '.join(sorted(unknown))}")
        if any(value is not None and value < 0 for value in limits.values()):
            raise ValueError("project quota limits must be nonnegative or null")
        quota, _counter = await self.repo.ensure_state(project_id)
        for name, value in limits.items():
            setattr(quota, name, value)
        quota.updated_at = datetime.now(UTC)
        await self.session.flush()
        return await self.snapshot(project_id)

    async def reserve(
        self,
        project_id: str,
        kind: ReservationKind,
        resource_id: str,
        amount: int = 1,
    ) -> bool:
        if amount < 0:
            raise ValueError("quota reservation amount must be nonnegative")
        attempt = await self.repo.try_reserve(project_id, kind, resource_id, amount)
        if attempt.created:
            return True
        if attempt.existing_amount is not None:
            if attempt.existing_amount != amount:
                raise ProjectQuotaReservationConflict(
                    f"quota reservation {kind}/{resource_id} already has amount "
                    f"{attempt.existing_amount}, not {amount}"
                )
            return False
        if attempt.exceeded:
            snapshot = await self.snapshot(project_id)
            limit, usage = _realtime_values(snapshot, kind)
            assert limit is not None
            raise ProjectQuotaExceeded(kind, limit, usage, amount)
        raise RuntimeError("unexpected quota reservation result")

    async def release(self, project_id: str, kind: ReservationKind, resource_id: str) -> bool:
        return await self.repo.release(project_id, kind, resource_id) is not None

    async def charge_monthly(
        self,
        project_id: str,
        kind: PeriodKind,
        amount: int,
        *,
        period_start: date | None = None,
        allow_overage: bool = False,
        execution_id: str | None = None,
    ) -> None:
        """Charge a monthly metered metric under the project's overage policy.

        ``hard`` (default) rejects the crossing charge. ``soft`` records the
        overage, writes one durable ``quota`` cost alert at the crossing
        point, and lets the charge through — the alert fires only on the
        first charge that crosses the limit, not on every subsequent
        over-limit charge. ``allow_overage=True`` bypasses the policy check
        entirely for callers that enforce their own semantics.
        """
        if amount < 0:
            raise ValueError("monthly quota charge must be nonnegative")
        period = period_start or utc_month_start()
        charged = await self.repo.try_charge_period(
            project_id,
            period,
            kind,
            amount,
            allow_overage=allow_overage,
        )
        if charged:
            return
        snapshot = await self.snapshot(project_id, period_start=period)
        limit, usage = _period_values(snapshot, kind)
        assert limit is not None
        if not allow_overage and _period_policy(snapshot, kind) == "soft":
            await self.repo.try_charge_period(project_id, period, kind, amount, allow_overage=True)
            # The crossing charge is the first one whose pre-charge usage was
            # still within (or exactly at) the limit.
            if usage <= limit:
                self.session.add(
                    CostAlert(
                        execution_id=execution_id,
                        workflow_id=None,
                        kind="quota",
                        severity="warning",
                        status="open",
                        limit_value=str(limit),
                        actual_value=str(usage + amount),
                        message=(
                            f"soft quota overage for {kind}: charged {amount} over "
                            f"limit {limit} (usage was {usage}); request allowed by plan policy"
                        ),
                    )
                )
                await self.session.flush()
            return
        raise ProjectQuotaExceeded(kind, limit, usage, amount)

    async def reconcile(self, project_id: str | None = None) -> int:
        project_stmt = select(Project.id).order_by(Project.id)
        if project_id is not None:
            project_stmt = project_stmt.where(Project.id == project_id)
        project_ids = list((await self.session.scalars(project_stmt)).all())
        if project_id is not None and not project_ids:
            raise KeyError(f"project not found: {project_id}")

        for current_project_id in project_ids:
            # Queue-backed work holds a concurrent slot from enqueue until a worker
            # reaches a non-running terminal/wait state. Reconcile must therefore
            # count both queued and running rows after process restart.
            executions = list(
                (
                    await self.session.scalars(
                        select(Execution.id)
                        .join(Workflow, Workflow.id == Execution.workflow_id)
                        .where(
                            Workflow.project_id == current_project_id,
                            Execution.status.in_(("queued", "running")),
                        )
                        .order_by(Execution.id)
                    )
                ).all()
            )
            document_rows = (
                await self.session.execute(
                    select(Document.id, Document.size_bytes)
                    .join(KnowledgeBase, KnowledgeBase.id == Document.kb_id)
                    .where(KnowledgeBase.project_id == current_project_id)
                    .order_by(Document.id)
                )
            ).all()
            await self.repo.replace_realtime_state(
                current_project_id,
                execution_ids=executions,
                documents=[(row.id, row.size_bytes) for row in document_rows],
            )
        return len(project_ids)


async def reconcile_project_quota_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Rebuild realtime reservations after process-local resources stop."""
    async with session_factory() as session:
        reconciled = await ProjectQuotaService(session).reconcile()
        await session.commit()
    return reconciled


def _realtime_values(
    snapshot: ProjectQuotaSnapshot, kind: ReservationKind
) -> tuple[int | None, int]:
    if kind == "execution":
        return snapshot.concurrent_execution_limit, snapshot.concurrent_executions
    if kind == "document_storage":
        return snapshot.storage_bytes_limit, snapshot.storage_bytes
    return snapshot.stdio_mcp_process_limit, snapshot.stdio_mcp_processes


def _period_values(snapshot: ProjectQuotaSnapshot, kind: PeriodKind) -> tuple[int | None, int]:
    if kind == "embedding_input_bytes":
        return snapshot.monthly_embedding_input_bytes_limit, snapshot.embedding_input_bytes
    return snapshot.monthly_model_cost_units_limit, snapshot.model_cost_units


def _period_policy(snapshot: ProjectQuotaSnapshot, kind: PeriodKind) -> str:
    if kind == "embedding_input_bytes":
        return snapshot.embedding_overage_policy
    return snapshot.model_cost_overage_policy
