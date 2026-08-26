"""Authenticated global search for the platform command palette."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_session
from app.schemas.search import GlobalSearchOut
from app.services.global_search import search_resources

router = APIRouter(prefix="/api/search", tags=["search"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=GlobalSearchOut)
async def global_search(
    session: SessionDep,
    principal: ViewerDep,
    q: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=30)] = 12,
) -> GlobalSearchOut:
    normalized = q.strip()
    if not normalized:
        raise HTTPException(status_code=422, detail="search query cannot be blank")
    return GlobalSearchOut(
        query=normalized,
        items=await search_resources(session, principal, normalized, limit=limit),
    )


__all__ = ["router"]
