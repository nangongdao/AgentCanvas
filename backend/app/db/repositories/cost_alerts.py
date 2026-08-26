"""Data access for durable cost/budget alerts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CostAlert
from app.db.pagination import PageSlice, PageSpec, paginate_select


class CostAlertRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        execution_id: str | None,
        workflow_id: str | None,
        kind: str,
        severity: str,
        limit_value: str,
        actual_value: str,
        message: str,
    ) -> CostAlert:
        row = CostAlert(
            execution_id=execution_id,
            workflow_id=workflow_id,
            kind=kind,
            severity=severity,
            limit_value=limit_value,
            actual_value=actual_value,
            message=message,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, alert_id: str) -> CostAlert | None:
        return await self.session.get(CostAlert, alert_id)

    async def list_page(self, spec: PageSpec) -> PageSlice[CostAlert]:
        stmt = select(CostAlert)
        return await paginate_select(
            self.session,
            stmt,
            id_column=CostAlert.id,
            columns={
                "created_at": CostAlert.created_at,
                "severity": CostAlert.severity,
                "kind": CostAlert.kind,
                "status": CostAlert.status,
                "id": CostAlert.id,
            },
            spec=spec,
            search_columns=(CostAlert.message, CostAlert.kind, CostAlert.execution_id),
        )

    async def acknowledge(self, alert: CostAlert) -> None:
        alert.status = "acknowledged"
        await self.session.flush()

    async def summary(self) -> dict[str, Any]:
        """Aggregate alert counts by status, severity, and kind."""
        result = await self.session.execute(
            select(CostAlert.status, func.count(CostAlert.id)).group_by(CostAlert.status)
        )
        by_status = {status: int(count) for status, count in result.all()}
        result = await self.session.execute(
            select(CostAlert.severity, func.count(CostAlert.id)).group_by(CostAlert.severity)
        )
        by_severity = {severity: int(count) for severity, count in result.all()}
        result = await self.session.execute(
            select(CostAlert.kind, CostAlert.status, func.count(CostAlert.id))
            .where(CostAlert.status == "open")
            .group_by(CostAlert.kind, CostAlert.status)
        )
        open_by_kind: dict[str, int] = Counter()
        for kind, _status, count in result.all():
            open_by_kind[kind] += int(count)
        return {
            "total": int(sum(by_status.values())),
            "open": int(by_status.get("open", 0)),
            "acknowledged": int(by_status.get("acknowledged", 0)),
            "critical": int(by_severity.get("critical", 0)),
            "warning": int(by_severity.get("warning", 0)),
            "open_by_kind": dict(sorted(open_by_kind.items())),
        }


__all__ = ["CostAlertRepo"]
