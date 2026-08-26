"""Preflight and charge project monthly model-cost quotas."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.model_costs import (
    has_complete_pricing,
    priced_usage_cost,
    usd_to_cost_units,
)
from app.db.repositories import ExecutionQueueRepo
from app.engine.execution_lease import LeaseLost, WorkerLease
from app.providers.base import BaseChatProvider, Usage
from app.services.project_quotas import ProjectQuotaExceeded, ProjectQuotaService


class ProjectModelCostUnavailable(RuntimeError):
    """A finite project quota cannot safely account for this provider call."""


class ProjectModelCostMeter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        project_id: str,
        *,
        execution_id: str | None = None,
        lease: WorkerLease | None = None,
        callback_dispatcher=None,
    ) -> None:
        self.session_factory = session_factory
        self.project_id = project_id
        self.execution_id = execution_id
        self.lease = lease
        self.callback_dispatcher = callback_dispatcher

    async def _lock_lease(self, session: AsyncSession) -> None:
        lease = self.lease
        if lease is None:
            return
        if not await ExecutionQueueRepo(session).owns_lease(
            item_id=lease.item_id,
            owner_id=lease.owner_id,
            lease_generation=lease.generation,
            lock=True,
        ):
            raise LeaseLost(f"project cost write lost lease {lease.item_id}:{lease.generation}")

    async def preflight(self, provider: BaseChatProvider) -> None:
        async with self.session_factory() as session:
            await self._lock_lease(session)
            snapshot = await ProjectQuotaService(session).snapshot(self.project_id)
        limit = snapshot.monthly_model_cost_units_limit
        if limit is None:
            return
        if snapshot.model_cost_units >= limit:
            # Soft plans let calls continue past the ceiling; the crossing
            # charge in ``record`` writes the one-shot durable alert.
            if snapshot.model_cost_overage_policy == "soft":
                pass
            else:
                error = ProjectQuotaExceeded(
                    "model_cost_units", limit, snapshot.model_cost_units, 1
                )
                if self.callback_dispatcher is not None:
                    await self.callback_dispatcher.enqueue_quota_alert_safely(
                        workflow_id=await self._workflow_id(session),
                        project_id=self.project_id,
                        metric=error.metric,
                        limit=error.limit,
                        usage=error.usage,
                        requested=error.requested,
                    )
                raise error
        if not has_complete_pricing(provider):
            raise ProjectModelCostUnavailable(
                "finite project model-cost quota requires prompt and completion pricing"
            )

    async def record(self, provider: BaseChatProvider, usage: Usage | None) -> None:
        async with self.session_factory() as session:
            await self._lock_lease(session)
            service = ProjectQuotaService(session)
            snapshot = await service.snapshot(self.project_id)
            finite = snapshot.monthly_model_cost_units_limit is not None
            if finite and not has_complete_pricing(provider):
                raise ProjectModelCostUnavailable(
                    "finite project model-cost quota requires prompt and completion pricing"
                )
            if usage is None:
                if finite:
                    raise ProjectModelCostUnavailable(
                        "finite project model-cost quota requires provider usage"
                    )
                return
            exact_cost = priced_usage_cost(provider, usage)
            if exact_cost is None:
                if finite:
                    raise ProjectModelCostUnavailable(
                        "finite project model-cost quota could not price provider usage"
                    )
                return
            amount = usd_to_cost_units(exact_cost)
            try:
                await service.charge_monthly(
                    self.project_id,
                    "model_cost_units",
                    amount,
                    execution_id=self.execution_id,
                )
            except ProjectQuotaExceeded as exc:
                await service.charge_monthly(
                    self.project_id,
                    "model_cost_units",
                    amount,
                    allow_overage=True,
                )
                await session.commit()
                if self.callback_dispatcher is not None:
                    await self.callback_dispatcher.enqueue_quota_alert_safely(
                        workflow_id=await self._workflow_id(session),
                        project_id=self.project_id,
                        metric=exc.metric,
                        limit=exc.limit,
                        usage=exc.usage,
                        requested=exc.requested,
                    )
                raise exc
            await session.commit()

    async def _workflow_id(self, session: AsyncSession) -> str:
        from sqlalchemy import select

        from app.db.models import Execution

        if self.execution_id is None:
            return ""
        result = await session.execute(
            select(Execution.workflow_id).where(Execution.id == self.execution_id)
        )
        workflow_id = result.scalar_one_or_none()
        return str(workflow_id or "")


__all__ = ["ProjectModelCostMeter", "ProjectModelCostUnavailable"]
