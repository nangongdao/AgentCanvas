"""Org plan persistence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, OrgPlan

PLAN_COLUMNS: tuple[str, ...] = (
    "concurrent_execution_limit",
    "storage_bytes_limit",
    "monthly_embedding_input_bytes_limit",
    "monthly_model_cost_units_limit",
    "stdio_mcp_process_limit",
    "embedding_overage_policy",
    "model_cost_overage_policy",
)


class OrgPlanRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, plan_id: str) -> OrgPlan | None:
        return await self.session.get(OrgPlan, plan_id)

    async def get_by_slug(self, slug: str) -> OrgPlan | None:
        result = await self.session.execute(select(OrgPlan).where(OrgPlan.slug == slug))
        return result.scalars().first()

    async def list(self) -> Sequence[OrgPlan]:
        result = await self.session.execute(
            select(OrgPlan).order_by(OrgPlan.is_system.desc(), OrgPlan.slug.asc())
        )
        return result.scalars().all()

    async def create(self, **values: Any) -> OrgPlan:
        plan = OrgPlan(**values)
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def apply_updates(self, plan: OrgPlan, updates: dict[str, Any]) -> OrgPlan:
        for name, value in updates.items():
            setattr(plan, name, value)
        await self.session.flush()
        await self.session.refresh(plan)
        return plan

    async def delete(self, plan: OrgPlan) -> None:
        await self.session.delete(plan)
        await self.session.flush()

    async def bound_organization_count(self, plan_id: str) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Organization).where(Organization.plan_id == plan_id)
        )
        return int(result.scalar_one())


__all__ = ["PLAN_COLUMNS", "OrgPlanRepo"]
