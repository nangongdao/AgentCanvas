"""Shared HTTP contract for cursor-paginated list endpoints."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.db.pagination import PageSlice, PageSpec, PaginationError, decode_cursor

type RowT = object
type ItemT = object


class PageResult[ItemT](BaseModel):
    """Uniform response envelope for every bounded list endpoint."""

    items: list[ItemT] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


class PageParams(BaseModel):
    """Uniform query parameters accepted by paginated list endpoints."""

    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    limit: int = Field(default=50, ge=1, le=200)
    search: str | None = Field(default=None, max_length=200)
    sort: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    order: Literal["asc", "desc"] = "desc"

    def to_spec(
        self,
        *,
        allowed_sorts: Collection[str],
        default_sort: str,
        scope: str | None = None,
    ) -> PageSpec:
        sort = self.sort or default_sort
        if sort not in allowed_sorts:
            choices = ", ".join(sorted(allowed_sorts))
            raise HTTPException(
                status_code=422,
                detail=f"invalid sort field '{sort}'; expected one of: {choices}",
            )
        normalized_search = self.search.strip() if self.search else ""
        search = normalized_search or None
        try:
            position = decode_cursor(
                self.cursor,
                sort=sort,
                order=self.order,
                search=search,
                scope=scope,
            )
        except PaginationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return PageSpec(
            limit=self.limit,
            sort=sort,
            order=self.order,
            search=search,
            scope=scope,
            position=position,
        )


def page_result[RowT, ItemT](
    page: PageSlice[RowT], mapper: Callable[[RowT], ItemT]
) -> PageResult[ItemT]:
    return PageResult(
        items=[mapper(row) for row in page.rows],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )
