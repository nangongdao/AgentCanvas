"""Type definitions for AgentCanvas SDK."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

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
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None


class Workflow(BaseModel):
    """A workflow definition."""

    id: str
    name: str
    description: Optional[str] = None
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    created_at: datetime
    updated_at: datetime
    version: int = 1


class CreateWorkflowInput(BaseModel):
    """Input for creating a new workflow."""

    name: str
    description: Optional[str] = None
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class UpdateWorkflowInput(BaseModel):
    """Input for updating an existing workflow."""

    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[list[WorkflowNode]] = None
    edges: Optional[list[WorkflowEdge]] = None


class Execution(BaseModel):
    """A workflow execution record."""

    id: str
    workflow_id: str
    status: ExecutionStatus
    trigger_source: str = "manual"
    version: Optional[int] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration: Optional[float] = None
    error: Optional[str] = None
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
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateProviderInput(BaseModel):
    """Input for creating a new provider."""

    name: str
    type: str
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: list[str] = Field(default_factory=list)


class UpdateProviderInput(BaseModel):
    """Input for updating an existing provider."""

    name: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    models: Optional[list[str]] = None


class PaginationParams(BaseModel):
    """Pagination parameters for list operations."""

    skip: int = 0
    limit: int = 100
