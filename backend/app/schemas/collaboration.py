"""Workflow collaboration API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

CLIENT_ID_PATTERN = r"^[A-Za-z0-9._:-]+$"


class CollaborationClient(BaseModel):
    client_id: str = Field(min_length=8, max_length=128, pattern=CLIENT_ID_PATTERN)


class CollaborationHeartbeat(CollaborationClient):
    lease_id: str | None = Field(default=None, min_length=32, max_length=128)


class CollaborationLockRequest(CollaborationClient):
    takeover: bool = False
    lease_id: str | None = Field(default=None, min_length=32, max_length=128)


class CollaborationLockRelease(CollaborationClient):
    lease_id: str = Field(min_length=32, max_length=128)


class CollaborationPresenceOut(BaseModel):
    client_id: str
    subject: str
    last_seen_at: datetime
    expires_at: datetime
    is_self: bool


class CollaborationLockOut(BaseModel):
    client_id: str
    subject: str
    acquired_at: datetime
    expires_at: datetime
    owned_by_self: bool


class CollaborationSnapshotOut(BaseModel):
    workflow_id: str
    revision: int
    can_edit: bool
    participants: list[CollaborationPresenceOut] = Field(default_factory=list)
    lock: CollaborationLockOut | None = None
    lease_id: str | None = None
    backend: str = "memory"
    read_only: bool = False
