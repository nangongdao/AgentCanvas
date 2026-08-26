"""Organization plan binding: batch quota adjustment on upgrade/downgrade."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, OrgPlan, Project
from app.db.repositories.org_plans import PLAN_COLUMNS
from app.db.repositories.quota import ProjectQuotaRepo


class OrgPlanService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ProjectQuotaRepo(session)

    async def apply_to_org(
        self,
        org: Organization,
        plan: OrgPlan,
        *,
        assigned_by: str,
        now: datetime | None = None,
    ) -> int:
        """Bind ``plan`` to ``org`` and copy its limits/policies to every project.

        The copy is the batch quota adjustment: an upgrade or downgrade
        rewrites all five limits plus both overage policies on each project
        quota row in this transaction. Returns the number of projects
        updated. Usage counters and reservations are untouched — only the
        configuration changes.
        """
        moment = now or datetime.now(UTC)
        project_ids = list(
            (
                await self.session.scalars(
                    select(Project.id).where(Project.organization_id == org.id).order_by(Project.id)
                )
            ).all()
        )
        for project_id in project_ids:
            quota, _counter = await self.repo.ensure_state(project_id)
            for column in PLAN_COLUMNS:
                setattr(quota, column, getattr(plan, column))
            quota.updated_at = moment
        org.plan_id = plan.id
        org.plan_assigned_at = moment
        org.plan_assigned_by = assigned_by
        await self.session.flush()
        return len(project_ids)

    async def unbind_from_org(self, org: Organization) -> None:
        """Clear the plan binding but keep the last applied limits in place."""
        org.plan_id = None
        org.plan_assigned_at = None
        org.plan_assigned_by = None
        await self.session.flush()


__all__ = ["OrgPlanService"]
