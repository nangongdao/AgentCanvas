"""Platform admin console schemas (C7-3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tenancy import OrgDeletionStatus

OrgStatus = Literal["active", "disabled"]
UserStatus = Literal["active", "disabled"]
AnnouncementLevel = Literal["info", "warning", "critical"]


class AdminOrganizationDeletionOut(BaseModel):
    """Deletion state surfaced to platform admins (C7-5)."""

    status: OrgDeletionStatus = "none"
    requested_at: datetime | None = None
    requested_by: str | None = None
    purge_due_at: datetime | None = None


class AdminOrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    status: str
    plan_id: str | None = None
    plan_slug: str | None = None
    plan_name: str | None = None
    project_count: int = 0
    member_count: int = 0
    deletion: AdminOrganizationDeletionOut = Field(default_factory=AdminOrganizationDeletionOut)
    created_at: datetime


class AdminOrganizationStatusUpdate(BaseModel):
    status: OrgStatus


class AdminUserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    status: str
    created_at: datetime
    last_login_at: datetime | None = None


class AdminUserStatusUpdate(BaseModel):
    status: UserStatus


class AdminQueueItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    execution_id: str
    kind: str
    status: str
    attempt: int
    lease_generation: int
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminQueueOut(BaseModel):
    depth: dict[str, int]
    dead_letters: list[AdminQueueItemOut]


class AnnouncementCreate(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    level: AnnouncementLevel = "info"


class AnnouncementUpdate(BaseModel):
    message: str | None = Field(default=None, min_length=1, max_length=1000)
    level: AnnouncementLevel | None = None
    is_active: bool | None = None


class AnnouncementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message: str
    level: str
    is_active: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


__all__ = [
    "AdminOrganizationDeletionOut",
    "AdminOrganizationOut",
    "AdminOrganizationStatusUpdate",
    "AdminQueueItemOut",
    "AdminQueueOut",
    "AdminUserOut",
    "AdminUserStatusUpdate",
    "AnnouncementCreate",
    "AnnouncementOut",
    "AnnouncementUpdate",
]
