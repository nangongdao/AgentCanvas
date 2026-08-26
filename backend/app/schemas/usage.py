"""Usage metering fact schemas (C7-1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_USAGE_FACT_WINDOW_DAYS = 366


class UsageFactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    day: date
    organization_id: str
    project_id: str
    app_id: str | None = None
    model_config_id: str | None = None
    executions: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_unknown_executions: int
    storage_bytes_delta: int
    retrievals: int
    estimated_cost_usd: str | None = None


class UsageDailyOut(BaseModel):
    from_day: date
    to_day: date
    facts: list[UsageFactOut]


class UsageReconciliationDayOut(BaseModel):
    day: date
    rows: int
    digest: str


class UsageReconciliationTotalsOut(BaseModel):
    executions: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_unknown_executions: int = 0
    storage_bytes_delta: int = 0
    retrievals: int = 0
    estimated_cost_usd: str | None = None


class UsageReconciliationOut(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    digest: str
    days: list[UsageReconciliationDayOut]
    totals: UsageReconciliationTotalsOut
    scope: dict[str, Any]


__all__ = [
    "MAX_USAGE_FACT_WINDOW_DAYS",
    "UsageDailyOut",
    "UsageFactOut",
    "UsageReconciliationDayOut",
    "UsageReconciliationOut",
    "UsageReconciliationTotalsOut",
]
