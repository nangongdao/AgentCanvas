"""Workflow template API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class TemplateParameter(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$", max_length=64)
    label: str = Field(min_length=1, max_length=120)
    type: Literal["string", "number", "boolean"] = "string"
    required: bool = False
    default: Any = None
    description: str = Field(default="", max_length=500)


class WorkflowTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    category: str = Field(default="general", min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=20)
    parameters: list[TemplateParameter] = Field(default_factory=list, max_length=20)
    workflow_id: str = Field(min_length=1, max_length=32)
    version_id: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in tags:
            value = tag.strip()
            if not value or len(value) > 40:
                raise ValueError("tags must contain 1 to 40 characters")
            if value not in normalized:
                normalized.append(value)
        return normalized


class WorkflowTemplateInstantiate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    parameters: dict[str, Any] = Field(default_factory=dict)


class WorkflowTemplateOut(BaseModel):
    id: str
    name: str
    description: str
    category: str
    tags: list[str]
    parameters: list[TemplateParameter]
    dsl: dict[str, Any]
    is_official: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "TemplateParameter",
    "WorkflowTemplateCreate",
    "WorkflowTemplateInstantiate",
    "WorkflowTemplateOut",
]
