"""Redacted execution-inspection API contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class NodeAttemptOut(BaseModel):
    node_id: str
    node_type: str
    node_label: str
    attempt: int
    status: Literal["running", "succeeded", "failed", "interrupted", "cancelled"]
    started_seq: int
    finished_seq: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    input: Any = None
    output: Any = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: str | None = None
    price_version: str | None = None


class ExecutionInspectionOut(BaseModel):
    execution_id: str
    workflow_id: str
    workflow_version_id: str
    workflow_version_number: int
    status: str
    parent_execution_id: str | None = None
    rerun_from_node_id: str | None = None
    redacted: Literal[True] = True
    attempts: list[NodeAttemptOut]
    total_duration_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: str | None = None


__all__ = ["ExecutionInspectionOut", "NodeAttemptOut"]
