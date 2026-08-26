"""Knowledge-index safety rules for mutable model configurations."""

from __future__ import annotations

from collections.abc import Set

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, KnowledgeBase

EMBEDDING_BEHAVIOR_FIELDS = frozenset({"provider", "model_name", "base_url", "api_key", "params"})


async def dependent_knowledge_base_ids(session: AsyncSession, model_id: str) -> list[str]:
    result = await session.execute(
        select(KnowledgeBase.id)
        .where(KnowledgeBase.embedding_model_id == model_id)
        .order_by(KnowledgeBase.id.asc())
    )
    return list(result.scalars())


def changes_embedding_behavior(
    *,
    current_kind: str,
    next_kind: str,
    changed_fields: Set[str],
) -> bool:
    return (current_kind == "embedding" or next_kind == "embedding") and bool(
        EMBEDDING_BEHAVIOR_FIELDS.intersection(changed_fields)
    )


async def invalidate_knowledge_indexes(
    session: AsyncSession, knowledge_base_ids: list[str]
) -> None:
    if not knowledge_base_ids:
        return
    await session.execute(
        update(Document)
        .where(Document.kb_id.in_(knowledge_base_ids))
        .values(status="pending", chunk_count=0, error=None)
    )


__all__ = [
    "changes_embedding_behavior",
    "dependent_knowledge_base_ids",
    "invalidate_knowledge_indexes",
]
