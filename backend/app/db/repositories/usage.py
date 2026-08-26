"""Usage fact persistence: idempotent day rewrites and deterministic reads."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageDailyFact


class UsageFactRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_day(self, facts: list[dict[str, Any]], *, day: date) -> int:
        """Replace all fact rows for ``day`` in one transaction.

        A delete-then-insert of the full day keeps the rewrite idempotent
        without dialect-specific upsert syntax: re-aggregating the same day
        yields byte-identical contents regardless of how many times it runs.
        Returns the number of rows written.

        An empty day with no existing rows stays read-only: the scheduler
        sweeps trailing days on every process start, and unconditionally
        opening a write transaction there contends with the first concurrent
        request on SQLite ("database is locked").
        """
        existing = int(
            await self.session.scalar(
                select(func.count()).select_from(UsageDailyFact).where(UsageDailyFact.day == day)
            )
            or 0
        )
        if not facts and existing == 0:
            return 0
        await self.session.execute(delete(UsageDailyFact).where(UsageDailyFact.day == day))
        for fact in facts:
            self.session.add(UsageDailyFact(**fact))
        return len(facts)

    async def latest_day(self) -> date | None:
        result = await self.session.execute(select(func.max(UsageDailyFact.day)))
        return result.scalar_one_or_none()

    async def list_range(
        self,
        *,
        from_day: date,
        to_day: date,
        organization_id: str | None = None,
        project_id: str | None = None,
    ) -> Sequence[UsageDailyFact]:
        """Deterministically ordered facts for the reporting window."""
        stmt = select(UsageDailyFact).where(
            UsageDailyFact.day >= from_day,
            UsageDailyFact.day <= to_day,
        )
        if organization_id is not None:
            stmt = stmt.where(UsageDailyFact.organization_id == organization_id)
        if project_id is not None:
            stmt = stmt.where(UsageDailyFact.project_id == project_id)
        stmt = stmt.order_by(
            UsageDailyFact.day.asc(),
            UsageDailyFact.organization_id.asc(),
            UsageDailyFact.project_id.asc(),
            # SQLite sorts NULL first ascending; the composite ordering stays
            # deterministic for export purposes either way.
            UsageDailyFact.app_id.asc(),
            UsageDailyFact.model_config_id.asc(),
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_month(
        self,
        month_first_day: date,
        *,
        organization_id: str | None = None,
        project_id: str | None = None,
    ) -> Sequence[UsageDailyFact]:
        from calendar import monthrange

        last_day = monthrange(month_first_day.year, month_first_day.month)[1]
        to_day = month_first_day.replace(day=last_day)
        return await self.list_range(
            from_day=month_first_day,
            to_day=to_day,
            organization_id=organization_id,
            project_id=project_id,
        )


__all__ = ["UsageFactRepo"]
