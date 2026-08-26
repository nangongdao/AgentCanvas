"""Data access for knowledge bases, documents, and cached embeddings."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, EmbeddingCache, IngestJob, KnowledgeBase
from app.db.pagination import PageSlice, PageSpec, paginate_select

__all__ = ["DocumentRepo", "EmbeddingCacheRepo", "IngestJobRepo", "KnowledgeBaseRepo"]


class KnowledgeBaseRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **values: Any) -> KnowledgeBase:
        row = KnowledgeBase(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, kb_id: str) -> KnowledgeBase | None:
        return await self.session.get(KnowledgeBase, kb_id)

    async def list(self, *, project_id: str | None = None) -> list[KnowledgeBase]:
        statement = select(KnowledgeBase)
        if project_id is None:
            statement = statement.where(KnowledgeBase.project_id.is_(None))
        else:
            statement = statement.where(KnowledgeBase.project_id == project_id)
        result = await self.session.execute(statement.order_by(KnowledgeBase.updated_at.desc()))
        return list(result.scalars().all())

    async def list_page(
        self, spec: PageSpec, *, project_id: str | None = None
    ) -> PageSlice[KnowledgeBase]:
        statement = select(KnowledgeBase)
        if project_id is None:
            statement = statement.where(KnowledgeBase.project_id.is_(None))
        else:
            statement = statement.where(KnowledgeBase.project_id == project_id)
        return await paginate_select(
            self.session,
            statement,
            id_column=KnowledgeBase.id,
            columns={
                "created_at": KnowledgeBase.created_at,
                "updated_at": KnowledgeBase.updated_at,
                "name": KnowledgeBase.name,
                "id": KnowledgeBase.id,
            },
            spec=spec,
            search_columns=(
                KnowledgeBase.name,
                KnowledgeBase.description,
                KnowledgeBase.id,
            ),
        )

    async def document_counts(
        self, knowledge_base_ids: Sequence[str] | None = None
    ) -> dict[str, int]:
        if knowledge_base_ids is not None and not knowledge_base_ids:
            return {}
        statement = select(Document.kb_id, func.count(Document.id)).group_by(Document.kb_id)
        if knowledge_base_ids is not None:
            statement = statement.where(Document.kb_id.in_(knowledge_base_ids))
        result = await self.session.execute(statement)
        return {str(kb_id): int(count) for kb_id, count in result.all()}

    async def update(self, row: KnowledgeBase, **values: Any) -> KnowledgeBase:
        for key, value in values.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def delete(self, row: KnowledgeBase) -> None:
        await self.session.delete(row)
        await self.session.flush()


class DocumentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **values: Any) -> Document:
        row = Document(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, document_id: str) -> Document | None:
        return await self.session.get(Document, document_id)

    async def list_for_kb(self, kb_id: str) -> list[Document]:
        result = await self.session.execute(
            select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_for_kb_page(self, kb_id: str, spec: PageSpec) -> PageSlice[Document]:
        statement = select(Document).where(Document.kb_id == kb_id)
        return await paginate_select(
            self.session,
            statement,
            id_column=Document.id,
            columns={
                "created_at": Document.created_at,
                "updated_at": Document.updated_at,
                "filename": Document.filename,
                "status": Document.status,
                "id": Document.id,
            },
            spec=spec,
            search_columns=(Document.filename, Document.status, Document.id),
        )

    async def set_status(
        self,
        row: Document,
        status: str,
        *,
        chunk_count: int | None = None,
        error: str | None = None,
    ) -> Document:
        row.status = status
        row.error = error
        if chunk_count is not None:
            row.chunk_count = chunk_count
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return row

    async def claim_for_ingest(self, kb_id: str, document_id: str) -> Document | None:
        """Atomically move a document into processing for one worker."""
        result = await self.session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.kb_id == kb_id,
                Document.status.in_(("pending", "failed", "ready")),
            )
            .values(status="processing", chunk_count=0, error=None)
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            return None
        return await self.get(document_id)

    async def delete(self, row: Document) -> None:
        await self.session.delete(row)
        await self.session.flush()


class EmbeddingCacheRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_many(self, cache_keys: list[str]) -> dict[str, EmbeddingCache]:
        if not cache_keys:
            return {}
        result = await self.session.execute(
            select(EmbeddingCache).where(EmbeddingCache.cache_key.in_(cache_keys))
        )
        return {row.cache_key: row for row in result.scalars().all()}

    async def upsert_many(self, rows: list[EmbeddingCache]) -> None:
        for row in rows:
            await self.session.merge(row)
        await self.session.flush()


class IngestJobRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **values: Any) -> IngestJob:
        row = IngestJob(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, job_id: str) -> IngestJob | None:
        return await self.session.get(IngestJob, job_id)

    async def latest_for_document(self, document_id: str) -> IngestJob | None:
        result = await self.session.execute(
            select(IngestJob)
            .where(IngestJob.document_id == document_id)
            .order_by(IngestJob.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def active_for_document(self, document_id: str) -> IngestJob | None:
        result = await self.session.execute(
            select(IngestJob)
            .where(
                IngestJob.document_id == document_id,
                IngestJob.status.in_(("queued", "running")),
            )
            .order_by(IngestJob.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def update(
        self,
        row: IngestJob,
        *,
        status: str | None = None,
        cache_hits: int | None = None,
        cache_misses: int | None = None,
        error: str | None = None,
    ) -> IngestJob:
        if status is not None:
            row.status = status
        if cache_hits is not None:
            row.cache_hits = cache_hits
        if cache_misses is not None:
            row.cache_misses = cache_misses
        row.error = error
        row.updated_at = datetime.now(UTC)
        await self.session.flush()
        return row
