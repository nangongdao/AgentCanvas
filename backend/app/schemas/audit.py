"""HTTP contracts for administrative audit history."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditLogOut(BaseModel):
    id: str
    organization_id: str | None = None
    project_id: str | None = None
    actor_user_id: str | None = None
    actor_key: str
    actor_subject: str
    auth_method: str
    action: str
    resource_type: str
    resource_id: str
    resource_name: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


__all__ = ["AuditLogOut"]
