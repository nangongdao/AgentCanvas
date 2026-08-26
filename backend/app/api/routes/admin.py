"""Platform admin console endpoints (C7-3).

Everything under ``/api/admin`` requires the global platform admin role —
the same principal that administers MCP catalog and audit logs. Org-scoped
admin memberships are deliberately not sufficient. The announcement read
endpoint lives outside the prefix because every authenticated user needs
it to render the banner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_container, get_session
from app.api.tenant_deps import _is_global_admin
from app.core.auth import Principal
from app.core.container import ServiceContainer
from app.db.models import Membership, Organization, PlatformAnnouncement, Project
from app.db.repositories.execution_queue import ExecutionQueueRepo
from app.db.repositories.identity import RefreshTokenRepo, SessionRepo, UserRepo
from app.db.repositories.org_plans import OrgPlanRepo
from app.schemas.admin import (
    AdminOrganizationDeletionOut,
    AdminOrganizationOut,
    AdminOrganizationStatusUpdate,
    AdminQueueItemOut,
    AdminQueueOut,
    AdminUserOut,
    AdminUserStatusUpdate,
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUpdate,
)
from app.schemas.tenancy import OrgDeletionStatus
from app.services.audit import record_audit
from app.services.org_deletion import (
    OrgDeletionConflict,
    cancel_org_deletion,
    purge_organization_data,
    request_org_deletion,
)

router = APIRouter(tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _require_platform_admin(principal: Principal) -> None:
    if not _is_global_admin(principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform admin role required",
        )


def _admin_deletion_out(org: Organization) -> AdminOrganizationDeletionOut:
    return AdminOrganizationDeletionOut(
        status=cast(OrgDeletionStatus, org.deletion_status),
        requested_at=org.deletion_requested_at,
        requested_by=org.deletion_requested_by,
        purge_due_at=org.purge_due_at,
    )


def _admin_org_out(
    org: Organization,
    *,
    project_count: int,
    member_count: int,
    plan_slug: str | None,
    plan_name: str | None,
) -> AdminOrganizationOut:
    return AdminOrganizationOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        status=org.status,
        plan_id=org.plan_id,
        plan_slug=plan_slug,
        plan_name=plan_name,
        project_count=project_count,
        member_count=member_count,
        deletion=_admin_deletion_out(org),
        created_at=org.created_at,
    )


@router.get("/api/admin/organizations", response_model=list[AdminOrganizationOut])
async def list_organizations_for_admin(
    session: SessionDep, principal: ViewerDep
) -> list[AdminOrganizationOut]:
    _require_platform_admin(principal)
    rows = (
        await session.execute(
            select(
                Organization,
                func.count(func.distinct(Project.id)),
                func.count(func.distinct(Membership.user_id)),
            )
            .outerjoin(Project, Project.organization_id == Organization.id)
            .outerjoin(Membership, Membership.organization_id == Organization.id)
            .group_by(Organization.id)
            .order_by(Organization.created_at.asc(), Organization.id.asc())
        )
    ).all()
    plans = {plan.id: plan for plan in await OrgPlanRepo(session).list()}
    return [
        _admin_org_out(
            org,
            project_count=int(project_count),
            member_count=int(member_count),
            plan_slug=plans[org.plan_id].slug if org.plan_id in plans else None,
            plan_name=plans[org.plan_id].name if org.plan_id in plans else None,
        )
        for org, project_count, member_count in rows
    ]


@router.put("/api/admin/organizations/{org_id}/status", response_model=AdminOrganizationOut)
async def set_organization_status(
    org_id: str,
    body: AdminOrganizationStatusUpdate,
    session: SessionDep,
    principal: ViewerDep,
) -> AdminOrganizationOut:
    _require_platform_admin(principal)
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    previous = org.status
    if previous != body.status:
        org.status = body.status
        await session.flush()
        await record_audit(
            session,
            principal,
            action="organization.status.changed",
            resource_type="organization",
            resource_id=org.id,
            resource_name=org.name,
            organization_id=org.id,
            details={"from": previous, "to": body.status},
        )
    project_count = int(
        await session.scalar(
            select(func.count()).select_from(Project).where(Project.organization_id == org.id)
        )
        or 0
    )
    member_count = int(
        await session.scalar(
            select(func.count()).select_from(Membership).where(Membership.organization_id == org.id)
        )
        or 0
    )
    plan = await OrgPlanRepo(session).get(org.plan_id) if org.plan_id else None
    return _admin_org_out(
        org,
        project_count=project_count,
        member_count=member_count,
        plan_slug=plan.slug if plan else None,
        plan_name=plan.name if plan else None,
    )


@router.get(
    "/api/admin/organizations/{org_id}/deletion",
    response_model=AdminOrganizationDeletionOut,
)
async def get_org_deletion_status(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> AdminOrganizationDeletionOut:
    _require_platform_admin(principal)
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return _admin_deletion_out(org)


@router.post(
    "/api/admin/organizations/{org_id}/deletion/request",
    response_model=AdminOrganizationDeletionOut,
)
async def admin_request_org_deletion(
    org_id: str,
    session: SessionDep,
    principal: ViewerDep,
    container: ContainerDep,
) -> AdminOrganizationDeletionOut:
    """Platform-admin entry point to schedule a deletion (C7-5)."""
    _require_platform_admin(principal)
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    try:
        await request_org_deletion(
            session, principal, org, grace_days=container.settings.org_deletion_grace_days
        )
    except OrgDeletionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _admin_deletion_out(org)


@router.delete(
    "/api/admin/organizations/{org_id}/deletion",
    response_model=AdminOrganizationDeletionOut,
)
async def admin_cancel_org_deletion(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> AdminOrganizationDeletionOut:
    """Platform-admin cancellation of a pending deletion (C7-5)."""
    _require_platform_admin(principal)
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    try:
        await cancel_org_deletion(session, principal, org)
    except OrgDeletionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _admin_deletion_out(org)


@router.post(
    "/api/admin/organizations/{org_id}/deletion/purge",
    response_model=dict,
)
async def admin_purge_org_now(
    org_id: str,
    session: SessionDep,
    principal: ViewerDep,
    container: ContainerDep,
) -> dict[str, int]:
    """Platform-admin immediate purge, bypassing the grace window (C7-5).

    Sweeps every tenant-owned row and drops the organization. Intended for
    compliance scenarios where the grace window must be shortened.
    """
    _require_platform_admin(principal)
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    if org.deletion_status == "purged":
        raise HTTPException(status_code=409, detail="organization already purged")
    # Roll back the read session before the purge opens its own sessions.
    await session.rollback()
    settings = container.settings
    try:
        return await purge_organization_data(
            settings,
            container.session_factory,
            org_id,
            rag_service=container.rag_service,
            batch_size=settings.org_deletion_purge_batch_size,
            audit_principal=principal,
        )
    except OrgDeletionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/admin/users", response_model=list[AdminUserOut])
async def list_users_for_admin(session: SessionDep, principal: ViewerDep) -> list[AdminUserOut]:
    _require_platform_admin(principal)
    return [
        AdminUserOut(
            id=row.id,
            email=row.email,
            display_name=row.display_name or "",
            role=row.role,
            status=row.status,
            created_at=row.created_at,
            last_login_at=row.last_login_at,
        )
        for row in await UserRepo(session).list_all()
    ]


@router.put("/api/admin/users/{user_id}/status", response_model=AdminUserOut)
async def set_user_status(
    user_id: str,
    body: AdminUserStatusUpdate,
    session: SessionDep,
    principal: ViewerDep,
) -> AdminUserOut:
    _require_platform_admin(principal)
    user = await UserRepo(session).get(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if body.status == "disabled":
        if principal.user_id == user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot deactivate your own account",
            )
        now = datetime.now(UTC)
        sessions_revoked = await SessionRepo(session).revoke_all_for_user(user.id, now)
        tokens_revoked = await RefreshTokenRepo(session).revoke_all_for_user(user.id, now)
    else:
        sessions_revoked = tokens_revoked = 0
    previous = user.status
    if previous != body.status:
        await UserRepo(session).set_status(user, body.status)
        await record_audit(
            session,
            principal,
            action="user.status.changed",
            resource_type="user",
            resource_id=user.id,
            resource_name=user.email,
            details={
                "from": previous,
                "to": body.status,
                "sessions_revoked": sessions_revoked,
                "refresh_tokens_revoked": tokens_revoked,
            },
        )
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name or "",
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.get("/api/admin/queue", response_model=AdminQueueOut)
async def get_queue_overview(
    session: SessionDep, principal: ViewerDep, limit: int = 20
) -> AdminQueueOut:
    _require_platform_admin(principal)
    repo = ExecutionQueueRepo(session)
    depth = await repo.depth_by_status()
    dead_letters = await repo.list_dead_letters(limit=limit)
    return AdminQueueOut(
        depth=depth,
        dead_letters=[AdminQueueItemOut.model_validate(row) for row in dead_letters],
    )


@router.post("/api/admin/queue/{item_id}/replay", response_model=AdminQueueItemOut)
async def replay_dead_letter(
    item_id: str, session: SessionDep, principal: ViewerDep
) -> AdminQueueItemOut:
    _require_platform_admin(principal)
    repo = ExecutionQueueRepo(session)
    item = await repo.get(item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="queue item not found")
    if item.status != "dead_letter":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="queue item is not dead-lettered",
        )
    replayed = await repo.requeue_dead_letter(item_id)
    if not replayed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="queue item was modified concurrently; retry",
        )
    # The CAS update bypasses the identity map; expire so the re-read below
    # reflects the replayed row instead of the stale pre-replay snapshot.
    session.expire(item)
    item = await repo.get(item_id)
    assert item is not None
    await record_audit(
        session,
        principal,
        action="queue.dead_letter.replayed",
        resource_type="execution_queue_item",
        resource_id=item.id,
        details={
            "execution_id": item.execution_id,
            "kind": item.kind,
            "lease_generation": item.lease_generation,
        },
    )
    return AdminQueueItemOut.model_validate(item)


@router.get("/api/admin/announcements", response_model=list[AnnouncementOut])
async def list_announcements(session: SessionDep, principal: ViewerDep) -> list[AnnouncementOut]:
    _require_platform_admin(principal)
    rows = (
        (
            await session.execute(
                select(PlatformAnnouncement).order_by(
                    PlatformAnnouncement.created_at.desc(),
                    PlatformAnnouncement.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [AnnouncementOut.model_validate(row) for row in rows]


@router.post(
    "/api/admin/announcements",
    response_model=AnnouncementOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_announcement(
    body: AnnouncementCreate, session: SessionDep, principal: ViewerDep
) -> AnnouncementOut:
    _require_platform_admin(principal)
    row = PlatformAnnouncement(
        message=body.message,
        level=body.level,
        is_active=True,
        created_by=principal.subject,
    )
    session.add(row)
    await session.flush()
    await record_audit(
        session,
        principal,
        action="announcement.created",
        resource_type="platform_announcement",
        resource_id=row.id,
        details={"level": row.level, "is_active": True},
    )
    return AnnouncementOut.model_validate(row)


@router.put("/api/admin/announcements/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(
    announcement_id: str,
    body: AnnouncementUpdate,
    session: SessionDep,
    principal: ViewerDep,
) -> AnnouncementOut:
    _require_platform_admin(principal)
    row = await session.get(PlatformAnnouncement, announcement_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="announcement not found")
    if not body.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="at least one field is required",
        )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await session.flush()
    await record_audit(
        session,
        principal,
        action="announcement.updated",
        resource_type="platform_announcement",
        resource_id=row.id,
        details={"changed_fields": sorted(body.model_fields_set)},
    )
    return AnnouncementOut.model_validate(row)


@router.delete("/api/admin/announcements/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(
    announcement_id: str, session: SessionDep, principal: ViewerDep
) -> None:
    _require_platform_admin(principal)
    row = await session.get(PlatformAnnouncement, announcement_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="announcement not found")
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        principal,
        action="announcement.deleted",
        resource_type="platform_announcement",
        resource_id=announcement_id,
        details={},
    )


@router.get("/api/announcements/active", response_model=list[AnnouncementOut])
async def list_active_announcements(
    session: SessionDep, _principal: ViewerDep
) -> list[AnnouncementOut]:
    """Banner payload for every authenticated user (no admin gate)."""
    rows = (
        (
            await session.execute(
                select(PlatformAnnouncement)
                .where(PlatformAnnouncement.is_active.is_(True))
                .order_by(
                    PlatformAnnouncement.created_at.desc(),
                    PlatformAnnouncement.id.asc(),
                )
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return [AnnouncementOut.model_validate(row) for row in rows]


__all__ = ["router"]
