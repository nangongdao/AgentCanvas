"""Named quota plan schemas (C7-2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.model_costs import (
    COST_QUANTUM_USD,
    format_cost_usd,
    parse_nonnegative_decimal,
    usd_to_cost_units,
)

MAX_QUOTA_VALUE = 9_223_372_036_854_775_807
OveragePolicy = Literal["hard", "soft"]


def _validate_model_cost_usd(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = parse_nonnegative_decimal(value)
    if parsed is None:
        raise ValueError("model cost limit must be a nonnegative decimal string")
    units = usd_to_cost_units(parsed)
    if Decimal(units) * COST_QUANTUM_USD != parsed:
        raise ValueError("model cost limit supports at most 12 decimal places")
    if units > MAX_QUOTA_VALUE:
        raise ValueError("model cost limit is too large")
    return format_cost_usd(parsed)


class OrgPlanCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    concurrent_execution_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    storage_bytes_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    monthly_embedding_input_bytes_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    monthly_model_cost_usd_limit: str | None = None
    stdio_mcp_process_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    embedding_overage_policy: OveragePolicy = "hard"
    model_cost_overage_policy: OveragePolicy = "hard"

    @field_validator("monthly_model_cost_usd_limit")
    @classmethod
    def validate_model_cost_limit(cls, value: str | None) -> str | None:
        return _validate_model_cost_usd(value)


class OrgPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    concurrent_execution_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    storage_bytes_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    monthly_embedding_input_bytes_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    monthly_model_cost_usd_limit: str | None = None
    stdio_mcp_process_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    embedding_overage_policy: OveragePolicy | None = None
    model_cost_overage_policy: OveragePolicy | None = None

    @field_validator("monthly_model_cost_usd_limit")
    @classmethod
    def validate_model_cost_limit(cls, value: str | None) -> str | None:
        return _validate_model_cost_usd(value)


class OrgPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str
    concurrent_execution_limit: int | None
    storage_bytes_limit: int | None
    monthly_embedding_input_bytes_limit: int | None
    monthly_model_cost_usd_limit: str | None
    stdio_mcp_process_limit: int | None
    embedding_overage_policy: str
    model_cost_overage_policy: str
    is_system: bool
    organization_count: int = 0
    created_at: datetime
    updated_at: datetime


class OrgPlanAssignmentOut(BaseModel):
    plan: OrgPlanOut | None
    assigned_at: datetime | None = None
    assigned_by: str | None = None


class OrgPlanSet(BaseModel):
    plan_id: str | None = Field(default=None, max_length=32)


__all__ = [
    "OrgPlanAssignmentOut",
    "OrgPlanCreate",
    "OrgPlanOut",
    "OrgPlanSet",
    "OrgPlanUpdate",
]
