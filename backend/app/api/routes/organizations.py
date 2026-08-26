"""Organization, project, and membership routes (D3 Phase 2 tenancy)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, EditorDep, ViewerDep, get_container, get_session
from app.api.tenant_deps import require_org_member
from app.core.auth import Role
from app.core.container import ServiceContainer
from app.db.models import Membership, Organization, Project
from app.db.repositories import MembershipRepo, OrganizationRepo, ProjectRepo, UserRepo
from app.db.repositories.tenant import slugify
from app.schemas.api import (
    MembershipCreate,
    MembershipOut,
    MembershipUpdate,
    OrganizationCreate,
    OrganizationOut,
    ProjectCreate,
    ProjectOut,
)
from app.schemas.tenancy import OrganizationDeletionOut, OrgDeletionStatus
from app.services.audit import record_audit
from app.services.org_deletion import (
    OrgDeletionConflict,
    cancel_org_deletion,
    request_org_deletion,
)
from app.services.org_export import build_org_export, serialize_org_export

router = APIRouter(prefix="/api/organizations", tags=["organizations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _deletion_out(row: Organization) -> OrganizationDeletionOut:
    return OrganizationDeletionOut(
        status=cast(OrgDeletionStatus, row.deletion_status),
        requested_at=row.deletion_requested_at,
        requested_by=row.deletion_requested_by,
        purge_due_at=row.purge_due_at,
    )


def _org_out(row: Organization) -> OrganizationOut:
    return OrganizationOut(
        id=row.id,
        name=row.name,
        slug=row.slug,
        deletion=_deletion_out(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _project_out(row: Project) -> ProjectOut:
    return ProjectOut(
        id=row.id,
        organization_id=row.organization_id,
        name=row.name,
        slug=row.slug,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _membership_out(row: Membership) -> MembershipOut:
    return MembershipOut(
        id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        role=row.role,
        created_at=row.created_at,
    )


async def _unique_org_slug(session: AsyncSession, slug: str) -> None:
    if await OrganizationRepo(session).get_by_slug(slug) is not None:
        raise HTTPException(status_code=409, detail="organization slug already in use")


@router.post("", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrganizationCreate, session: SessionDep, principal: AdminDep
) -> OrganizationOut:
    if principal.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="an admin user session is required to create an organization",
        )
    slug = body.slug or slugify(body.name)
    await _unique_org_slug(session, slug)
    try:
        org = await OrganizationRepo(session).create(
            body.name, slug, creator_user_id=principal.user_id
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="organization slug already in use") from exc
    await record_audit(
        session,
        principal,
        action="organization.created",
        resource_type="organization",
        resource_id=org.id,
        resource_name=org.name,
        organization_id=org.id,
        details={"creator_membership_role": "admin"},
    )
    return _org_out(org)


@router.get("", response_model=list[OrganizationOut])
async def list_organizations(session: SessionDep, principal: ViewerDep) -> list[OrganizationOut]:
    if principal.user_id is None:
        return []
    return [
        _org_out(org) for org in await OrganizationRepo(session).list_for_user(principal.user_id)
    ]


@router.get("/{org_id}", response_model=OrganizationOut)
async def get_organization(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> OrganizationOut:
    org = await require_org_member(session, principal, org_id, required=Role.VIEWER)
    return _org_out(org)


@router.get("/{org_id}/projects", response_model=list[ProjectOut])
async def list_projects(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> list[ProjectOut]:
    await require_org_member(session, principal, org_id, required=Role.VIEWER)
    projects = await ProjectRepo(session).list_for_org(org_id)
    return [_project_out(row) for row in projects]


@router.post("/{org_id}/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    org_id: str, body: ProjectCreate, session: SessionDep, principal: EditorDep
) -> ProjectOut:
    await require_org_member(session, principal, org_id, required=Role.EDITOR)
    slug = body.slug or slugify(body.name)
    if await ProjectRepo(session).get_by_slug(org_id, slug) is not None:
        raise HTTPException(status_code=409, detail="project slug already in use")
    try:
        project = await ProjectRepo(session).create(org_id, body.name, slug)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="project slug already in use") from exc
    await record_audit(
        session,
        principal,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
        resource_name=project.name,
        organization_id=org_id,
        project_id=project.id,
    )
    return _project_out(project)


@router.get("/{org_id}/members", response_model=list[MembershipOut])
async def list_members(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> list[MembershipOut]:
    await require_org_member(session, principal, org_id, required=Role.VIEWER)
    members = await MembershipRepo(session).list_for_org(org_id)
    return [_membership_out(row) for row in members]


@router.post("/{org_id}/members", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    org_id: str, body: MembershipCreate, session: SessionDep, principal: ViewerDep
) -> MembershipOut:
    await require_org_member(session, principal, org_id, required=Role.ADMIN)
    user = await UserRepo(session).get_by_email(body.email)
    if user is None or user.status != "active":
        raise HTTPException(status_code=404, detail="user not found")
    if await MembershipRepo(session).get(org_id, user.id) is not None:
        raise HTTPException(status_code=409, detail="user is already a member")
    membership = await MembershipRepo(session).create(org_id, user.id, body.role)
    await record_audit(
        session,
        principal,
        action="membership.added",
        resource_type="membership",
        resource_id=membership.id,
        organization_id=org_id,
        details={"target_user_id": user.id, "role": membership.role},
    )
    return _membership_out(membership)


@router.put("/{org_id}/members/{user_id}", response_model=MembershipOut)
async def update_member(
    org_id: str,
    user_id: str,
    body: MembershipUpdate,
    session: SessionDep,
    principal: ViewerDep,
) -> MembershipOut:
    await require_org_member(session, principal, org_id, required=Role.ADMIN)
    existing = await MembershipRepo(session).get(org_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="membership not found")
    if existing.role == "admin" and body.role != "admin":
        admins = await MembershipRepo(session).list_by_role(org_id, "admin")
        if len(admins) <= 1:
            raise HTTPException(
                status_code=409,
                detail="an organization must retain at least one admin member",
            )
    previous_role = existing.role
    membership = await MembershipRepo(session).set_role(org_id, user_id, body.role)
    if previous_role != membership.role:
        await record_audit(
            session,
            principal,
            action="membership.role_changed",
            resource_type="membership",
            resource_id=membership.id,
            organization_id=org_id,
            details={
                "target_user_id": user_id,
                "previous_role": previous_role,
                "new_role": membership.role,
            },
        )
    return _membership_out(membership)


@router.delete("/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    org_id: str, user_id: str, session: SessionDep, principal: ViewerDep
) -> None:
    await require_org_member(session, principal, org_id, required=Role.ADMIN)
    existing = await MembershipRepo(session).get(org_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="membership not found")
    if existing.role == "admin":
        admins = await MembershipRepo(session).list_by_role(org_id, "admin")
        if len(admins) <= 1:
            raise HTTPException(
                status_code=409,
                detail="an organization must retain at least one admin member",
            )
    membership_id = existing.id
    previous_role = existing.role
    await MembershipRepo(session).remove(org_id, user_id)
    await record_audit(
        session,
        principal,
        action="membership.removed",
        resource_type="membership",
        resource_id=membership_id,
        organization_id=org_id,
        details={"target_user_id": user_id, "previous_role": previous_role},
    )


@router.post("/{org_id}/deletion-request", response_model=OrganizationOut)
async def request_deletion(
    org_id: str,
    session: SessionDep,
    principal: AdminDep,
    container: ContainerDep,
) -> OrganizationOut:
    """Freeze the organization and schedule its purge (C7-5).

    Only an org admin (or a platform admin via the global-admin bypass) may
    request deletion. The organization becomes read-only to its members for
    the grace window; a platform admin can cancel before the purge is due.
    """
    org = await require_org_member(session, principal, org_id, required=Role.ADMIN)
    try:
        await request_org_deletion(
            session,
            principal,
            org,
            grace_days=container.settings.org_deletion_grace_days,
        )
    except OrgDeletionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _org_out(org)


@router.delete("/{org_id}/deletion-request", response_model=OrganizationOut)
async def cancel_deletion(
    org_id: str, session: SessionDep, principal: AdminDep
) -> OrganizationOut:
    """Cancel a pending deletion within the grace window (C7-5)."""
    org = await require_org_member(session, principal, org_id, required=Role.ADMIN)
    try:
        await cancel_org_deletion(session, principal, org)
    except OrgDeletionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _org_out(org)


@router.get("/{org_id}/export")
async def export_organization(
    org_id: str, session: SessionDep, principal: ViewerDep
) -> StreamingResponse:
    """Stream a JSON compliance export of the organization's data (C7-5)."""
    org = await require_org_member(session, principal, org_id, required=Role.VIEWER)
    slug = org.slug or org.id
    payload = await build_org_export(session, org)
    await session.rollback()  # read-only export; release the snapshot
    content = serialize_org_export(payload)
    filename = f"{slug}-export.json"
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
