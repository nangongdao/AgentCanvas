"""Operational overview response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OverviewWorkflowOut(BaseModel):
    id: str
    name: str
    description: str
    project_id: str | None
    updated_at: datetime


class OverviewExecutionSummaryOut(BaseModel):
    total: int
    succeeded: int
    failed: int
    active: int
    cancelled: int
    success_rate: float | None


class OverviewDayOut(BaseModel):
    date: str
    executions: int
    succeeded: int
    failed: int
    estimated_cost_usd: str | None
    cost_known: bool


class OverviewOut(BaseModel):
    project_id: str | None
    days: int
    since: datetime
    recent_workflows: list[OverviewWorkflowOut]
    execution_summary: OverviewExecutionSummaryOut
    estimated_cost_usd: str | None
    cost_known: bool
    daily: list[OverviewDayOut]


__all__ = [
    "OverviewDayOut",
    "OverviewExecutionSummaryOut",
    "OverviewOut",
    "OverviewWorkflowOut",
]
