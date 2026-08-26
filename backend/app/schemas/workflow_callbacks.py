"""Schemas for outbound workflow callback configuration and delivery history."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

CallbackEventType = Literal[
    "workflow_finished",
    "workflow_failed",
    "workflow_cancelled",
    "dead_letter",
    "cost_alert",
    "quota_alert",
]

DEFAULT_CALLBACK_EVENTS: list[CallbackEventType] = [
    "workflow_finished",
    "workflow_failed",
    "workflow_cancelled",
    "dead_letter",
    "cost_alert",
    "quota_alert",
]


def _validate_url(value: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("callback URL must use http or https and include a hostname")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("callback URL must not contain credentials or a fragment")
    if len(candidate) > 2048:
        raise ValueError("callback URL is too long")
    return candidate


def _validate_events(values: list[CallbackEventType]) -> list[CallbackEventType]:
    if not values:
        raise ValueError("event_types must not be empty")
    if len(set(values)) != len(values):
        raise ValueError("event_types must be unique")
    return list(values)


class WorkflowCallbackCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    secret: str | None = Field(default=None, min_length=16, max_length=256)
    event_types: list[CallbackEventType] = Field(
        default_factory=lambda: list(DEFAULT_CALLBACK_EVENTS), max_length=6
    )
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    max_attempts: int = Field(default=5, ge=1, le=20)
    retry_delay_seconds: int = Field(default=10, ge=1, le=3600)

    @field_validator("url")
    @classmethod
    def validate_callback_url(cls, value: str) -> str:
        return _validate_url(value)

    @field_validator("event_types")
    @classmethod
    def validate_callback_events(cls, value: list[CallbackEventType]) -> list[CallbackEventType]:
        return _validate_events(value)


class WorkflowCallbackUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    secret: str | None = Field(default=None, min_length=16, max_length=256)
    rotate_secret: bool = False
    event_types: list[CallbackEventType] | None = Field(default=None, max_length=6)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    retry_delay_seconds: int | None = Field(default=None, ge=1, le=3600)
    status: Literal["active", "disabled"] | None = None

    @field_validator("url")
    @classmethod
    def validate_callback_url(cls, value: str | None) -> str | None:
        return _validate_url(value) if value is not None else None

    @field_validator("event_types")
    @classmethod
    def validate_callback_events(
        cls, value: list[CallbackEventType] | None
    ) -> list[CallbackEventType] | None:
        return _validate_events(value) if value is not None else None


class WorkflowCallbackOut(BaseModel):
    id: str
    workflow_id: str
    project_id: str | None = None
    url: str
    event_types: list[CallbackEventType]
    status: Literal["active", "disabled"]
    has_secret: bool
    timeout_seconds: int
    max_attempts: int
    retry_delay_seconds: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_delivered_at: datetime | None = None


class WorkflowCallbackIssueOut(BaseModel):
    callback: WorkflowCallbackOut
    secret: str | None = None


class WorkflowCallbackDeliveryOut(BaseModel):
    id: str
    callback_id: str
    workflow_id: str
    source_type: str
    source_id: str
    event_type: str
    status: str
    attempt_count: int
    last_status_code: int | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    delivered_at: datetime | None = None


__all__ = [
    "CallbackEventType",
    "WorkflowCallbackCreate",
    "WorkflowCallbackDeliveryOut",
    "WorkflowCallbackIssueOut",
    "WorkflowCallbackOut",
    "WorkflowCallbackUpdate",
]
