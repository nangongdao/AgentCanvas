"""Published workflow API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowApiPublicationCreate(BaseModel):
    service_account_id: str | None = Field(default=None, min_length=1, max_length=32)


class WorkflowApiPublicationOut(BaseModel):
    id: str
    workflow_id: str
    published_version_id: str
    published_version_number: int
    service_account_id: str
    api_token_id: str
    token_prefix: str
    status: str
    endpoint: str
    last_triggered_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowApiIssueOut(BaseModel):
    publication: WorkflowApiPublicationOut
    token: str
    endpoint: str
    openapi: dict[str, Any]
    examples: dict[str, str]


class WorkflowApiInvokeOut(BaseModel):
    execution_id: str
    status: str = "queued"
    accepted_version_id: str
    input_json: dict[str, Any] = Field(default_factory=dict)
