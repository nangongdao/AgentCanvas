"""Workflow cron schedule API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.schedule_time import validate_cron_expression, validate_timezone

MisfirePolicy = Literal["skip", "catch_up"]
FailurePolicy = Literal["skip", "retry", "alert"]


class WorkflowScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    cron_expression: str = Field(min_length=1, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=120)
    inputs: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    misfire_policy: MisfirePolicy = "skip"
    failure_policy: FailurePolicy = "skip"
    retry_delay_seconds: int = Field(default=60, ge=5, le=86_400)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        return validate_cron_expression(value)

    @field_validator("timezone")
    @classmethod
    def validate_zone(cls, value: str) -> str:
        return validate_timezone(value)


class WorkflowScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=120)
    inputs: dict[str, Any] | None = None
    enabled: bool | None = None
    misfire_policy: MisfirePolicy | None = None
    failure_policy: FailurePolicy | None = None
    retry_delay_seconds: int | None = Field(default=None, ge=5, le=86_400)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        return validate_cron_expression(value) if value is not None else None

    @field_validator("timezone")
    @classmethod
    def validate_zone(cls, value: str | None) -> str | None:
        return validate_timezone(value) if value is not None else None


class WorkflowScheduleOut(BaseModel):
    id: str
    workflow_id: str
    published_version_id: str
    published_version_number: int
    name: str
    cron_expression: str
    timezone: str
    status: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    misfire_policy: MisfirePolicy
    failure_policy: FailurePolicy
    retry_delay_seconds: int
    next_run_at: datetime
    pending_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_execution_id: str | None = None
    last_error: str | None = None
    failure_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "FailurePolicy",
    "MisfirePolicy",
    "WorkflowScheduleCreate",
    "WorkflowScheduleOut",
    "WorkflowScheduleUpdate",
]
