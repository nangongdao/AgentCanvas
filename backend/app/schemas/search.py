"""Global command-palette search response schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SearchResultKind = Literal["workflow", "app", "knowledge_base", "document"]


class GlobalSearchResultOut(BaseModel):
    kind: SearchResultKind
    id: str
    title: str
    subtitle: str
    project_id: str | None
    parent_id: str | None


class GlobalSearchOut(BaseModel):
    query: str
    items: list[GlobalSearchResultOut]


__all__ = ["GlobalSearchOut", "GlobalSearchResultOut", "SearchResultKind"]
