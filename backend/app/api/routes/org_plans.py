"""Named quota plan administration and organization binding (C7-2).

Plan definitions are platform configuration: only global admins create,
update, or delete them. Updating a definition does not silently rewrite
bound organizations — rebinding (or binding for the first time) copies the
plan's five limits and both overage policies onto every project quota row
of the organization in one transaction, which is exactly the batch quota
adjustment of an upgrade or downgrade. Unbinding keeps the last applied
limits in place.

Organization admins may bind or unbind their own organization; members may
view the current assignment.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_session
from app.api.tenant_deps import require_org_member
from app.core.auth import Principal, Role
from app.core.model_costs import cost_units_to_usd, usd_to_cost_units
from app.db.models import OrgPlan
from app.db.repositories.org_plans import OrgPlanRepo
from app.schemas.org_plans import (
    OrgPlanAssignmentOut,
    OrgPlanCreate,
    OrgPlanOut,
    OrgPlanSet,
    OrgPlanUpdate,
)
from app.services.audit import record_audit
from app.services.org_plans import OrgPlanService

router = APIRouter(tags=["org-plans"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _is_global_admin(principal: Principal) -> bool:
    return principal.role == Role.ADMIN and principal.project_id is None


def _require_global_admin(principal: Principal) -> None:
    if not _is_global_admin(principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform admin role required to manage quota plans",
        )


async def _plan_out(
    session: AsyncSession, plan: OrgPlan, *, with_org_count: bool = True
) -> OrgPlanOut:
    units = plan.monthly_model_cost_units_limit
    return OrgPlanOut(
        id=plan.id,
        slug=plan.slug,
        name=plan.name,
        description=plan.description or "",
        concurrent_execution_limit=plan.concurrent_execution_limit,
        storage_bytes_limit=plan.storage_bytes_limit,
        monthly_embedding_input_bytes_limit=plan.monthly_embedding_input_bytes_limit,
        monthly_model_cost_usd_limit=None if units is None else cost_units_to_usd(units),
        stdio_mcp_process_limit=plan.stdio_mcp_process_limit,
        embedding_overage_policy=plan.embedding_overage_policy,
        model_cost_overage_policy=plan.model_cost_overage_policy,
        is_system=plan.is_system,
        organization_count=(
            await OrgPlanRepo(session).bound_organization_count(plan.id) if with_org_count else 0
        ),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _create_values(body: OrgPlanCreate) -> dict[str, object]:
    values = body.model_dump()
    usd = values.pop("monthly_model_cost_usd_limit")
    values["monthly_model_cost_units_limit"] = (
        None if usd is None else usd_to_cost_units(Decimal(str(usd)))
    )
    return values


def _update_values(body: OrgPlanUpdate) -> dict[str, object]:
    values = body.model_dump(exclude_unset=True)
    usd = values.pop("monthly_model_cost_usd_limit", None)
    if "monthly_model_cost_usd_limit" in body.model_fields_set:
        values["monthly_model_cost_units_limit"] = (
            None if usd is None else usd_to_cost_units(Decimal(str(usd)))
        )
    return values


@router.get("/api/org-plans", response_model=list[OrgPlanOut])
async def list_org_plans(session: SessionDep, principal: ViewerDep) -> list[OrgPlanOut]:
    _require_global_admin(principal)
    plans = await OrgPlanRepo(session).list()
    return [await _plan_out(session, plan) for plan in plans]


@router.post("/api/org-plans", response_model=OrgPlanOut, status_code=status.HTTP_201_CREATED)
async def create_org_plan(
    body: OrgPlanCreate, session: SessionDep, principal: ViewerDep
) -> OrgPlanOut:
    _require_global_admin(principal)
    repo = OrgPlanRepo(session)
    if await repo.get_by_slug(body.slug) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="plan slug already exists")
    plan = await repo.create(**_create_values(body))
    await record_audit(
        session,
        principal,
        action="org_plan.created",
        resource_type="org_plan",
        resource_id=plan.id,
        resource_name=plan.name,
        details={
            "slug": plan.slug,
            "embedding_overage_policy": plan.embedding_overage_policy,
            "model_cost_overage_policy": plan.model_cost_overage_policy,
        },
    )
    return await _plan_out(session, plan)


@router.get("/api/org-plans/{plan_id}", response_model=OrgPlanOut)
async def get_org_plan(plan_id: str, session: SessionDep, principal: ViewerDep) -> OrgPlanOut:
    _require_global_admin(principal)
    plan = await OrgPlanRepo(session).get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    return await _plan_out(session, plan)


@router.put("/api/org-plans/{plan_id}", response_model=OrgPlanOut)
async def update_org_plan(
    plan_id: str,
    body: OrgPlanUpdate,
    session: SessionDep,
    principal: ViewerDep,
) -> OrgPlanOut:
    _require_global_admin(principal)
    repo = OrgPlanRepo(session)
    plan = await repo.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    if not body.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="at least one field is required",
        )
    updates = _update_values(body)
    plan = await repo.apply_updates(plan, updates)
    await record_audit(
        session,
        principal,
        action="org_plan.updated",
        resource_type="org_plan",
        resource_id=plan.id,
        resource_name=plan.name,
        details={"changed_fields": sorted(body.model_fields_set)},
    )
    return await _plan_out(session, plan)


@router.delete("/api/org-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org_plan(plan_id: str, session: SessionDep, principal: ViewerDep) -> None:
    _require_global_admin(principal)
    repo = OrgPlanRepo(session)
    plan = await repo.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    if plan.is_system:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="system plans cannot be deleted",
        )
    if await repo.bound_organization_count(plan.id) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="plan is bound to organizations; unbind them first",
        )
    plan_name = plan.name
    await repo.delete(plan)
    await record_audit(
        session,
        principal,
        action="org_plan.deleted",
        resource_type="org_plan",
        resource_id=plan_id,
        resource_name=plan_name,
        details={},
    )


@router.get("/api/organizations/{org_id}/plan", response_model=OrgPlanAssignmentOut)
async def get_org_plan_assignment(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> OrgPlanAssignmentOut:
    org = await require_org_member(session, principal, org_id, required=Role.VIEWER)
    plan: OrgPlan | None = None
    if org.plan_id is not None:
        plan = await OrgPlanRepo(session).get(org.plan_id)
    return OrgPlanAssignmentOut(
        plan=None if plan is None else await _plan_out(session, plan),
        assigned_at=org.plan_assigned_at if plan is not None else None,
        assigned_by=org.plan_assigned_by if plan is not None else None,
    )


@router.put("/api/organizations/{org_id}/plan", response_model=OrgPlanAssignmentOut)
async def set_org_plan_assignment(
    org_id: str,
    body: OrgPlanSet,
    session: SessionDep,
    principal: ViewerDep,
) -> OrgPlanAssignmentOut:
    org = await require_org_member(session, principal, org_id, required=Role.ADMIN)
    service = OrgPlanService(session)
    repo = OrgPlanRepo(session)
    if body.plan_id is None:
        await service.unbind_from_org(org)
        await record_audit(
            session,
            principal,
            action="organization.plan.unbound",
            resource_type="organization",
            resource_id=org.id,
            resource_name=org.name,
            organization_id=org.id,
            details={},
        )
        return OrgPlanAssignmentOut(plan=None, assigned_at=None, assigned_by=None)

    plan = await repo.get(body.plan_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    projects_updated = await service.apply_to_org(org, plan, assigned_by=principal.subject)
    await record_audit(
        session,
        principal,
        action="organization.plan.bound",
        resource_type="organization",
        resource_id=org.id,
        resource_name=org.name,
        organization_id=org.id,
        details={
            "plan_id": plan.id,
            "plan_slug": plan.slug,
            "projects_updated": projects_updated,
        },
    )
    return OrgPlanAssignmentOut(
        plan=await _plan_out(session, plan),
        assigned_at=org.plan_assigned_at,
        assigned_by=org.plan_assigned_by,
    )


__all__ = ["router"]
