"""Workflow version lifecycle API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkflowVersionOut(BaseModel):
    id: str
    workflow_id: str
    number: int
    status: Literal["draft", "published", "archived"]
    name: str
    description: str = ""
    dsl: dict[str, Any]
    change_summary: str = ""
    created_at: datetime | None = None
    published_at: datetime | None = None
    archived_at: datetime | None = None


class WorkflowRollback(BaseModel):
    change_summary: str = Field(default="", max_length=2000)


class WorkflowClone(BaseModel):
    version_id: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)


class WorkflowDiffOut(BaseModel):
    base_id: str
    target_id: str
    added_nodes: list[str]
    removed_nodes: list[str]
    changed_nodes: list[str]
    added_edges: list[str]
    removed_edges: list[str]
    changed_edges: list[str]
    settings_changed: bool
    variables_changed: bool
    canvas_changed: bool = False


class WorkflowMergeValueOut(BaseModel):
    present: bool
    value: Any = None


class WorkflowMergeConflictOut(BaseModel):
    path: str
    base: WorkflowMergeValueOut
    local: WorkflowMergeValueOut
    remote: WorkflowMergeValueOut


class WorkflowMergeRequest(BaseModel):
    base_version_id: str = Field(min_length=1, max_length=32)
    remote_version: int = Field(ge=1)
    local_name: str = Field(min_length=1, max_length=200)
    local_dsl: dict[str, Any]
    change_summary: str = Field(default="Merge local changes", max_length=2000)


class WorkflowMergeOut(BaseModel):
    status: Literal["merged", "conflict"]
    workflow_id: str
    base_version: int
    remote_version: int
    saved_version: int | None = None
    name: str
    dsl: dict[str, Any]
    conflicts: list[WorkflowMergeConflictOut] = Field(default_factory=list)


class WorkflowExportOut(BaseModel):
    format: Literal["agentcanvas-workflow"] = "agentcanvas-workflow"
    format_version: Literal[1] = 1
    name: str
    description: str = ""
    source_workflow_id: str
    source_version_number: int
    source_version_status: Literal["draft", "published", "archived"]
    dsl: dict[str, Any]


class WorkflowImport(BaseModel):
    format: Literal["agentcanvas-workflow"] = "agentcanvas-workflow"
    format_version: Literal[1] = 1
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    dsl: dict[str, Any]


class WorkflowEvaluationPolicyIn(BaseModel):
    """Optional eval gate attached to a workflow (Backlog: publish gate)."""

    dataset_version_id: str = Field(min_length=1, max_length=64)
    threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class WorkflowEvaluationPolicyOut(BaseModel):
    dataset_version_id: str
    threshold: float


__all__ = [
    "WorkflowClone",
    "WorkflowDiffOut",
    "WorkflowEvaluationPolicyIn",
    "WorkflowEvaluationPolicyOut",
    "WorkflowExportOut",
    "WorkflowImport",
    "WorkflowMergeConflictOut",
    "WorkflowMergeOut",
    "WorkflowMergeRequest",
    "WorkflowMergeValueOut",
    "WorkflowRollback",
    "WorkflowVersionOut",
]
