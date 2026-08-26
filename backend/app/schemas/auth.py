"""HTTP schemas for local, token, refresh, and federated authentication."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AuthLogin(BaseModel):
    """Token credentials OR email/password credentials (exactly one form)."""

    token: str | None = Field(default=None, min_length=1, max_length=4096)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_credentials(self) -> AuthLogin:
        has_token = bool(self.token)
        has_user = bool(self.email) and bool(self.password)
        if has_token and has_user:
            raise ValueError("provide either token or email+password, not both")
        if not has_token and not has_user:
            raise ValueError("provide token or email+password")
        return self


class AuthRegister(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=120)
    role: Literal["viewer", "editor", "admin"] = "editor"


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    status: str
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class AuthSessionOut(BaseModel):
    authenticated: bool = True
    role: Literal["viewer", "editor", "admin"]
    auth_enabled: bool = True
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    subject: str | None = None
    language: Literal["zh", "en"] | None = None


class AuthPreferencesUpdate(BaseModel):
    """Account-level preferences writable by the session owner (C5-9)."""

    language: Literal["zh", "en"]
