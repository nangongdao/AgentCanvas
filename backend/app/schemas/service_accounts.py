"""Service-account and one-time API-token HTTP schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ServiceAccountStatus = Literal["active", "disabled"]


class ServiceAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    project_id: str | None = Field(default=None, min_length=1, max_length=32)
    role: Literal["viewer", "editor", "admin"] = "editor"


class ServiceAccountUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    role: Literal["viewer", "editor", "admin"] | None = None
    status: ServiceAccountStatus | None = None


class ApiTokenIssue(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=120)


class ServiceAccountOut(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None = None
    role: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ApiTokenOut(BaseModel):
    id: str
    service_account_id: str
    name: str
    prefix: str
    scope: str = "management"
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime | None = None


class ApiTokenIssueOut(BaseModel):
    token: ApiTokenOut
    plaintext: str
