"""Data-access repositories for organizations, projects, and memberships."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import literal, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression

from app.db.models import (
    Membership,
    Organization,
    Project,
    ProjectQuota,
    ProjectQuotaCounter,
)

if TYPE_CHECKING:
    from app.core.auth import Principal


def _uuid() -> str:
    return uuid4().hex


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "org"


def project_access_predicate(
    project_column: SQLColumnExpression[str | None],
    principal: Principal,
    *,
    include_global: bool,
) -> ColumnElement[bool]:
    """Build the shared project-visibility predicate for bulk resource queries."""
    if principal.role.value == "admin" and principal.project_id is None:
        return true()

    allowed: ColumnElement[bool]
    if principal.project_id is not None:
        allowed = project_column == principal.project_id
    elif principal.user_id is not None:
        project_ids = (
            select(Project.id)
            .join(Membership, Membership.organization_id == Project.organization_id)
            .where(Membership.user_id == principal.user_id)
        )
        allowed = project_column.in_(project_ids)
    else:
        allowed = literal(False)
    return or_(project_column.is_(None), allowed) if include_global else allowed


class OrganizationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: str) -> Organization | None:
        return await self.session.get(Organization, org_id)

    async def get_by_slug(self, slug: str) -> Organization | None:
        stmt = select(Organization).where(Organization.slug == slug)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_for_user(self, user_id: str) -> list[Organization]:
        stmt = (
            select(Organization)
            .join(Membership, Membership.organization_id == Organization.id)
            .where(Membership.user_id == user_id)
            .order_by(Organization.name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, name: str, slug: str, *, creator_user_id: str) -> Organization:
        """Create an organization and grant its creator an admin membership."""
        org = Organization(name=name, slug=slug)
        self.session.add(org)
        await self.session.flush()
        membership = Membership(organization_id=org.id, user_id=creator_user_id, role="admin")
        self.session.add(membership)
        await self.session.flush()
        return org


class ProjectRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, project_id: str) -> Project | None:
        return await self.session.get(Project, project_id)

    async def get_by_slug(self, org_id: str, slug: str) -> Project | None:
        stmt = select(Project).where(Project.organization_id == org_id, Project.slug == slug)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_for_org(self, org_id: str) -> list[Project]:
        stmt = select(Project).where(Project.organization_id == org_id).order_by(Project.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self) -> list[Project]:
        result = await self.session.execute(select(Project).order_by(Project.name, Project.id))
        return list(result.scalars().all())

    async def list_for_user(self, user_id: str) -> list[Project]:
        result = await self.session.execute(
            select(Project)
            .join(Membership, Membership.organization_id == Project.organization_id)
            .where(Membership.user_id == user_id)
            .order_by(Project.name, Project.id)
        )
        return list(result.scalars().all())

    async def create(self, org_id: str, name: str, slug: str) -> Project:
        row = Project(organization_id=org_id, name=name, slug=slug)
        self.session.add(row)
        await self.session.flush()
        self.session.add_all(
            [ProjectQuota(project_id=row.id), ProjectQuotaCounter(project_id=row.id)]
        )
        await self.session.flush()
        return row


class MembershipRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, org_id: str, user_id: str) -> Membership | None:
        stmt = select(Membership).where(
            Membership.organization_id == org_id,
            Membership.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def role_for(self, org_id: str, user_id: str) -> str | None:
        row = await self.get(org_id, user_id)
        return row.role if row is not None else None

    async def create(self, org_id: str, user_id: str, role: str) -> Membership:
        row = Membership(organization_id=org_id, user_id=user_id, role=role)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_org(self, org_id: str) -> list[Membership]:
        stmt = (
            select(Membership)
            .where(Membership.organization_id == org_id)
            .order_by(Membership.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_role(self, org_id: str, role: str) -> list[Membership]:
        stmt = (
            select(Membership)
            .where(Membership.organization_id == org_id, Membership.role == role)
            .order_by(Membership.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def set_role(self, org_id: str, user_id: str, role: str) -> Membership:
        row = await self.get(org_id, user_id)
        assert row is not None
        row.role = role
        await self.session.flush()
        return row

    async def remove(self, org_id: str, user_id: str) -> None:
        row = await self.get(org_id, user_id)
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()
