"""Tenant-aware project quota configuration and usage endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_session
from app.api.tenant_deps import authorize_project, membership_role
from app.core.auth import Role
from app.core.model_costs import cost_units_to_usd
from app.db.models import Project
from app.db.repositories import ProjectRepo
from app.schemas.api import ProjectOut
from app.schemas.project_quotas import ProjectQuotaOut, ProjectQuotaUpdate
from app.services.audit import record_audit
from app.services.project_quotas import ProjectQuotaService, ProjectQuotaSnapshot

router = APIRouter(prefix="/api/projects", tags=["projects"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _remaining(limit: int | None, usage: int) -> int | None:
    return None if limit is None else max(0, limit - usage)


def _project_out(row: Project) -> ProjectOut:
    return ProjectOut(
        id=row.id,
        organization_id=row.organization_id,
        name=row.name,
        slug=row.slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_out(snapshot: ProjectQuotaSnapshot, *, can_update: bool) -> ProjectQuotaOut:
    model_limit = snapshot.monthly_model_cost_units_limit
    model_remaining = _remaining(model_limit, snapshot.model_cost_units)
    return ProjectQuotaOut(
        project_id=snapshot.project_id,
        period_start=snapshot.period_start,
        can_update=can_update,
        concurrent_execution_limit=snapshot.concurrent_execution_limit,
        storage_bytes_limit=snapshot.storage_bytes_limit,
        monthly_embedding_input_bytes_limit=(snapshot.monthly_embedding_input_bytes_limit),
        monthly_model_cost_usd_limit=(
            None if model_limit is None else cost_units_to_usd(model_limit)
        ),
        stdio_mcp_process_limit=snapshot.stdio_mcp_process_limit,
        embedding_overage_policy=snapshot.embedding_overage_policy,
        model_cost_overage_policy=snapshot.model_cost_overage_policy,
        concurrent_executions=snapshot.concurrent_executions,
        storage_bytes=snapshot.storage_bytes,
        embedding_input_bytes=snapshot.embedding_input_bytes,
        model_cost_usd=cost_units_to_usd(snapshot.model_cost_units),
        stdio_mcp_processes=snapshot.stdio_mcp_processes,
        concurrent_executions_remaining=_remaining(
            snapshot.concurrent_execution_limit, snapshot.concurrent_executions
        ),
        storage_bytes_remaining=_remaining(snapshot.storage_bytes_limit, snapshot.storage_bytes),
        embedding_input_bytes_remaining=_remaining(
            snapshot.monthly_embedding_input_bytes_limit,
            snapshot.embedding_input_bytes,
        ),
        model_cost_usd_remaining=(
            None if model_remaining is None else cost_units_to_usd(model_remaining)
        ),
        stdio_mcp_processes_remaining=_remaining(
            snapshot.stdio_mcp_process_limit, snapshot.stdio_mcp_processes
        ),
    )


@router.get("", response_model=list[ProjectOut])
async def list_accessible_projects(session: SessionDep, principal: ViewerDep) -> list[ProjectOut]:
    repo = ProjectRepo(session)
    if principal.role == Role.ADMIN:
        rows = await repo.list_all()
    elif principal.user_id is not None:
        rows = await repo.list_for_user(principal.user_id)
    else:
        rows = []
    return [_project_out(row) for row in rows]


@router.get("/{project_id}/quotas", response_model=ProjectQuotaOut)
async def get_project_quotas(
    project_id: str, session: SessionDep, principal: ViewerDep
) -> ProjectQuotaOut:
    project = await authorize_project(session, principal, project_id, required=Role.VIEWER)
    role = await membership_role(session, principal, project.organization_id)
    return _to_out(
        await ProjectQuotaService(session).snapshot(project_id),
        can_update=role == Role.ADMIN,
    )


@router.put("/{project_id}/quotas", response_model=ProjectQuotaOut)
async def update_project_quotas(
    project_id: str,
    body: ProjectQuotaUpdate,
    session: SessionDep,
    principal: ViewerDep,
) -> ProjectQuotaOut:
    project = await authorize_project(session, principal, project_id, required=Role.ADMIN)
    if not body.model_fields_set:
        raise HTTPException(status_code=422, detail="at least one quota limit is required")
    snapshot = await ProjectQuotaService(session).configure(project_id, **body.to_internal_limits())
    await record_audit(
        session,
        principal,
        action="project_quota.updated",
        resource_type="project_quota",
        resource_id=project_id,
        resource_name=project.name,
        project_id=project_id,
        details={"changed_fields": sorted(body.model_fields_set)},
    )
    return _to_out(snapshot, can_update=True)


__all__ = ["router"]
