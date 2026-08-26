"""ORM models for organization tenancy: orgs, projects, and memberships."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organizations_slug"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_organizations_status"),
        CheckConstraint(
            "deletion_status IN ('none', 'requested', 'purging', 'purged')",
            name="ck_organizations_deletion_status",
        ),
        Index(
            "ix_organizations_deletion_due",
            "deletion_status",
            "purge_due_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    # C7-2: the quota plan currently bound to this organization. Binding and
    # rebinding copy the plan's limits/policies onto every project quota row.
    plan_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("org_plans.id", ondelete="SET NULL"), nullable=True
    )
    plan_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_assigned_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # C7-3: a disabled organization is invisible to its members' authorization
    # checks; global admins keep access and can re-enable it.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # C7-5 two-phase deletion state machine. While ``requested`` the org is
    # frozen (same authorization semantics as ``status='disabled'``) but data
    # is retained for the grace window so an admin can cancel. Once the purge
    # becomes due, the scheduler sweeps every related row and removes the org.
    deletion_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deletion_requested_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purge_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    projects: Mapped[list[Project]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_projects_org_slug"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    organization: Mapped[Organization] = relationship(back_populates="projects")
    workflows: Mapped[list[Any]] = relationship("Workflow", back_populates="project")
    apps: Mapped[list[Any]] = relationship("App", back_populates="project")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_memberships_org_user"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[Any] = relationship("User", back_populates="memberships")
