"""Project quota configuration and usage response schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.core.model_costs import (
    COST_QUANTUM_USD,
    format_cost_usd,
    parse_nonnegative_decimal,
    usd_to_cost_units,
)

MAX_QUOTA_VALUE = 9_223_372_036_854_775_807


class ProjectQuotaUpdate(BaseModel):
    concurrent_execution_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    storage_bytes_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    monthly_embedding_input_bytes_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)
    monthly_model_cost_usd_limit: str | None = None
    stdio_mcp_process_limit: int | None = Field(default=None, ge=0, le=MAX_QUOTA_VALUE)

    @field_validator("monthly_model_cost_usd_limit")
    @classmethod
    def validate_model_cost_limit(cls, value: str | None) -> str | None:
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

    def to_internal_limits(self) -> dict[str, int | None]:
        values = self.model_dump(exclude_unset=True)
        if "monthly_model_cost_usd_limit" in values:
            usd = values.pop("monthly_model_cost_usd_limit")
            values["monthly_model_cost_units_limit"] = (
                None if usd is None else usd_to_cost_units(Decimal(str(usd)))
            )
        return values


class ProjectQuotaOut(BaseModel):
    project_id: str
    period_start: date
    can_update: bool
    concurrent_execution_limit: int | None
    storage_bytes_limit: int | None
    monthly_embedding_input_bytes_limit: int | None
    monthly_model_cost_usd_limit: str | None
    stdio_mcp_process_limit: int | None
    # C7-2: overage behavior copied from the bound org plan; realtime limits
    # always reject regardless of policy.
    embedding_overage_policy: str = "hard"
    model_cost_overage_policy: str = "hard"
    concurrent_executions: int
    storage_bytes: int
    embedding_input_bytes: int
    model_cost_usd: str
    stdio_mcp_processes: int
    concurrent_executions_remaining: int | None
    storage_bytes_remaining: int | None
    embedding_input_bytes_remaining: int | None
    model_cost_usd_remaining: str | None
    stdio_mcp_processes_remaining: int | None


__all__ = ["MAX_QUOTA_VALUE", "ProjectQuotaOut", "ProjectQuotaUpdate"]
