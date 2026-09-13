"""Type definitions for AgentCanvas SDK."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """Status of a workflow execution."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowNode(BaseModel):
    """A node in a workflow graph."""

    id: str
    type: str
    position: dict[str, float]
    data: dict[str, Any]


class WorkflowEdge(BaseModel):
    """An edge connecting two nodes in a workflow graph."""

    id: str
    source: str
    target: str
    sourceHandle: str | None = None
    targetHandle: str | None = None


class Workflow(BaseModel):
    """A workflow definition."""

    id: str
    name: str
    description: str | None = None
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    created_at: datetime
    updated_at: datetime
    version: int = 1


class CreateWorkflowInput(BaseModel):
    """Input for creating a new workflow."""

    name: str
    description: str | None = None
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class UpdateWorkflowInput(BaseModel):
    """Input for updating an existing workflow."""

    name: str | None = None
    description: str | None = None
    nodes: list[WorkflowNode] | None = None
    edges: list[WorkflowEdge] | None = None


class Execution(BaseModel):
    """A workflow execution record."""

    id: str
    workflow_id: str
    status: ExecutionStatus
    trigger_source: str = "manual"
    version: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration: float | None = None
    error: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)


class StartExecutionInput(BaseModel):
    """Input for starting a workflow execution."""

    workflow_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class Provider(BaseModel):
    """An LLM provider configuration."""

    id: str
    name: str
    type: str
    api_key: str | None = None
    api_base: str | None = None
    models: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateProviderInput(BaseModel):
    """Input for creating a new provider."""

    name: str
    type: str
    api_key: str | None = None
    api_base: str | None = None
    models: list[str] = Field(default_factory=list)


class UpdateProviderInput(BaseModel):
    """Input for updating an existing provider."""

    name: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    models: list[str] | None = None


class PaginationParams(BaseModel):
    """Pagination parameters for list operations."""

    skip: int = 0
    limit: int = 100
