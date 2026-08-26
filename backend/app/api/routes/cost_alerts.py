"""Durable cost/budget alert and governance endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.pagination import PageParams, PageResult, page_result
from app.core.container import ServiceContainer
from app.db.repositories import CostAlertRepo
from app.schemas.api import CostAlertOut, CostGovernanceOut

router = APIRouter(prefix="/api", tags=["cost-alerts"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _alert_out(row) -> CostAlertOut:
    return CostAlertOut(
        id=row.id,
        execution_id=row.execution_id,
        workflow_id=row.workflow_id,
        kind=row.kind,
        severity=row.severity,
        status=row.status,
        limit_value=row.limit_value,
        actual_value=row.actual_value,
        message=row.message or "",
        created_at=row.created_at,
    )


@router.get("/cost-alerts", response_model=PageResult[CostAlertOut])
async def list_cost_alerts(
    session: SessionDep,
    _principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
) -> PageResult[CostAlertOut]:
    spec = params.to_spec(
        allowed_sorts={"created_at", "severity", "kind", "status", "id"},
        default_sort="created_at",
    )
    return page_result(await CostAlertRepo(session).list_page(spec), _alert_out)


@router.get("/cost-alerts/governance", response_model=CostGovernanceOut)
async def cost_governance(
    session: SessionDep, container: ContainerDep, _principal: ViewerDep
) -> CostGovernanceOut:
    settings = container.settings
    summary = await CostAlertRepo(session).summary()
    return CostGovernanceOut(
        max_tokens_per_execution=settings.model_max_tokens_per_execution,
        max_cost_usd_per_execution=settings.model_max_cost_usd_per_execution,
        max_concurrent_per_execution=settings.model_max_concurrent_per_execution,
        max_calls_per_execution=settings.model_max_calls_per_execution,
        summary=summary,
    )


@router.post("/cost-alerts/{alert_id}/ack", response_model=CostAlertOut)
async def acknowledge_cost_alert(
    alert_id: str,
    session: SessionDep,
    _principal: EditorDep,
) -> CostAlertOut:
    repo = CostAlertRepo(session)
    row = await repo.get(alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="cost alert not found")
    if row.status != "acknowledged":
        await repo.acknowledge(row)
    return _alert_out(row)


__all__ = ["router"]
