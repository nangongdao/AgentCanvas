"""Operational overview route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_session
from app.api.tenant_deps import authorize_workflow_project
from app.core.auth import Role
from app.schemas.overview import OverviewOut
from app.services.overview import collect_overview

router = APIRouter(prefix="/api/overview", tags=["overview"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=OverviewOut)
async def get_overview(
    session: SessionDep,
    principal: ViewerDep,
    project_id: Annotated[str | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
) -> OverviewOut:
    await authorize_workflow_project(session, principal, project_id, required=Role.VIEWER)
    return await collect_overview(session, project_id=project_id, days=days)


__all__ = ["router"]
