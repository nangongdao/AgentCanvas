"""Cost-governance HTTP response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CostAlertOut(BaseModel):
    id: str
    execution_id: str | None = None
    workflow_id: str | None = None
    kind: str
    severity: str
    status: str
    limit_value: str
    actual_value: str
    message: str
    created_at: datetime | None = None


class CostGovernanceOut(BaseModel):
    """Configured per-execution ceilings plus durable alert counts."""

    max_tokens_per_execution: int = 0
    max_cost_usd_per_execution: str | None = None
    max_concurrent_per_execution: int = 0
    max_calls_per_execution: int = 0
    summary: dict[str, Any] = Field(default_factory=dict)
