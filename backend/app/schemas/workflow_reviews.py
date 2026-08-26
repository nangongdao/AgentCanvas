"""API contracts for durable workflow comments and version reviews."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.workflow_reviews import ReviewDecision, ReviewStatus


class WorkflowCommentCreate(BaseModel):
    version_id: str = Field(min_length=1, max_length=32)
    body: str = Field(min_length=1, max_length=8000)
    parent_comment_id: str | None = Field(default=None, min_length=1, max_length=32)
    node_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("body")
    @classmethod
    def body_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("comment body cannot be blank")
        return value


class WorkflowCommentResolution(BaseModel):
    resolved: bool


class WorkflowCommentOut(BaseModel):
    id: str
    workflow_id: str
    version_id: str
    parent_comment_id: str | None = None
    node_id: str | None = None
    author_subject: str
    body: str
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by_subject: str | None = None


class WorkflowReviewCreate(BaseModel):
    version_id: str = Field(min_length=1, max_length=32)
    summary: str = Field(default="", max_length=4000)


class WorkflowReviewDecision(BaseModel):
    decision: ReviewDecision
    summary: str = Field(default="", max_length=4000)


class WorkflowReviewOut(BaseModel):
    id: str
    workflow_id: str
    version_id: str
    status: ReviewStatus
    summary: str = ""
    requested_by_subject: str
    requester_is_current_actor: bool = False
    created_at: datetime | None = None
    decision_summary: str = ""
    decided_by_subject: str | None = None
    decided_at: datetime | None = None


__all__ = [
    "WorkflowCommentCreate",
    "WorkflowCommentOut",
    "WorkflowCommentResolution",
    "WorkflowReviewCreate",
    "WorkflowReviewDecision",
    "WorkflowReviewOut",
]
