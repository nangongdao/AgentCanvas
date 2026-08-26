"""Organization, project, and membership HTTP schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )


OrgDeletionStatus = Literal["none", "requested", "purging", "purged"]


class OrganizationDeletionOut(BaseModel):
    """Current two-phase deletion state (C7-5)."""

    status: OrgDeletionStatus = "none"
    requested_at: datetime | None = None
    requested_by: str | None = None
    purge_due_at: datetime | None = None


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    deletion: OrganizationDeletionOut = Field(default_factory=OrganizationDeletionOut)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$"
    )


class ProjectOut(BaseModel):
    id: str
    organization_id: str
    name: str
    slug: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MembershipCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: Literal["viewer", "editor", "admin"] = "viewer"


class MembershipUpdate(BaseModel):
    role: Literal["viewer", "editor", "admin"]


class MembershipOut(BaseModel):
    id: str
    organization_id: str
    user_id: str
    role: str
    created_at: datetime | None = None

