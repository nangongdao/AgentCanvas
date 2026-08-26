"""Project-scoped authorization helpers for tenant-aware routes."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ROLE_LEVEL, Principal, Role
from app.db.models import Organization, Project
from app.db.repositories.tenant import MembershipRepo, OrganizationRepo, ProjectRepo


def _sufficient(actual: Role, required: Role) -> bool:
    return ROLE_LEVEL[actual] >= ROLE_LEVEL[required]


def _is_global_admin(principal: Principal) -> bool:
    return principal.role == Role.ADMIN and principal.project_id is None


def _ensure_org_active(org: Organization, principal: Principal) -> None:
    """A platform-disabled or deletion-frozen organization is invisible to
    its members (C7-3 / C7-5).

    Global admins keep full access so they can investigate, re-enable, or
    cancel the pending deletion.
    """
    if org.status == "disabled" and not _is_global_admin(principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organization is disabled",
        )
    if org.deletion_status in ("requested", "purging") and not _is_global_admin(principal):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organization is frozen pending deletion",
        )


async def membership_role(session: AsyncSession, principal: Principal, org_id: str) -> Role | None:
    """Resolve the principal's effective role in an organization.

    Global admins are treated as superusers; other principals need a stored
    membership (token principals without a ``user_id`` can never be members).
    """
    if principal.role == Role.ADMIN and principal.project_id is None:
        return Role.ADMIN
    if principal.user_id is None:
        return None
    role_name = await MembershipRepo(session).role_for(org_id, principal.user_id)
    if role_name is None:
        return None
    try:
        return Role(role_name)
    except ValueError:
        return None


async def require_org_member(
    session: AsyncSession,
    principal: Principal,
    org_id: str,
    *,
    required: Role,
) -> Organization:
    """Load an organization and assert the principal meets the membership level."""
    org = await OrganizationRepo(session).get(org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="organization not found")
    _ensure_org_active(org, principal)
    role = await membership_role(session, principal, org_id)
    if role is None or not _sufficient(role, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{required.value} membership in the organization required",
        )
    return org


async def authorize_project(
    session: AsyncSession,
    principal: Principal,
    project_id: str,
    *,
    required: Role,
) -> Project:
    """Load a project and assert the principal meets the project's access level."""
    project = await ProjectRepo(session).get(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    org = await OrganizationRepo(session).get(project.organization_id)
    if org is not None:
        _ensure_org_active(org, principal)
    if principal.service_account_id is not None and principal.project_id is not None:
        if principal.project_id != project.id or not _sufficient(principal.role, required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required.value} access to the project required",
            )
        return project
    role = await membership_role(session, principal, project.organization_id)
    if role is None or not _sufficient(role, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{required.value} membership in the project's organization required",
        )
    return project


async def authorize_workflow_project(
    session: AsyncSession,
    principal: Principal,
    project_id: str | None,
    *,
    required: Role,
) -> None:
    """Enforce project RBAC only when the workflow is tenant-scoped."""
    if project_id is not None:
        await authorize_project(session, principal, project_id, required=required)


async def authorize_project_scope(
    session: AsyncSession,
    principal: Principal,
    project_id: str | None,
    *,
    required: Role,
) -> None:
    """Enforce project RBAC for any optionally project-owned resource."""
    if project_id is not None:
        await authorize_project(session, principal, project_id, required=required)
