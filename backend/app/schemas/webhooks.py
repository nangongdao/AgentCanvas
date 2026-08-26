"""HTTP schemas for inbound workflow webhook triggers."""

from __future__ import annotations

from datetime import datetime
from ipaddress import ip_network
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _validate_ip_allowlist(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip()
        if not candidate:
            raise ValueError("IP allowlist entries must not be blank")
        try:
            network = ip_network(candidate, strict=False)
        except ValueError as exc:
            raise ValueError(f"invalid IP allowlist entry: {candidate}") from exc
        normalized.append(str(network))
    if len(set(normalized)) != len(normalized):
        raise ValueError("IP allowlist entries must be unique")
    return normalized


class WebhookTriggerCreate(BaseModel):
    secret: str | None = Field(default=None, min_length=16, max_length=256)
    ip_allowlist: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("ip_allowlist")
    @classmethod
    def validate_ips(cls, values: list[str]) -> list[str]:
        return _validate_ip_allowlist(values)


class WebhookTriggerUpdate(BaseModel):
    ip_allowlist: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("ip_allowlist")
    @classmethod
    def validate_ips(cls, values: list[str]) -> list[str]:
        return _validate_ip_allowlist(values)


class WebhookTriggerOut(BaseModel):
    id: str
    workflow_id: str
    published_version_id: str
    published_version_number: int
    token_prefix: str
    has_signing_secret: bool
    status: str
    ip_allowlist: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_triggered_at: datetime | None = None


class WebhookTriggerIssueOut(BaseModel):
    trigger: WebhookTriggerOut
    token: str
    secret: str


class WebhookInvokeOut(BaseModel):
    execution_id: str
    status: str
    accepted_version_id: str
    input_json: dict[str, Any] = Field(default_factory=dict)
    # Populated only in ``sync`` mode when the run reached a terminal state
    # before the bounded timeout; absent (None) for async acceptance and for
    # sync runs that timed out before finishing.
    output_json: dict[str, Any] | None = None
    error: str | None = None
