"""Database vector adapter with native pgvector ranking on PostgreSQL."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import cast, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.rag.keyword import InProcessBm25Index
from app.rag.splitter import TextChunk
from app.rag.vector_store import (
    VectorHit,
    cosine_similarity,
    decode_embedding,
    encode_embedding,
    valid_query_vector,
    validate_document_vectors,
)

logger = logging.getLogger(__name__)


def _normalize_ts_rank(rank: float) -> float:
    """Map a PostgreSQL ts_rank (unbounded, typically < 0.4) into [0, 1]."""
    if not math.isfinite(rank) or rank <= 0.0:
        return 0.0
    return rank / (rank + 0.1)


class SqlVectorStore:
    """Shared SQL store with server-side cosine ranking on PostgreSQL.

    SQLite stores portable float blobs and ranks them in process. That path is
    useful for migration tests and explicit local fallback only; PostgreSQL
    stores dimensionless ``vector`` values and performs cosine ranking in SQL.
    """

    backend_name = "sql"

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_concurrent: int = 4,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.session_factory = session_factory
        self._semaphore = asyncio.Semaphore(max_concurrent)
        bind = session_factory.kw.get("bind")
        self.database_backend = str(getattr(getattr(bind, "dialect", None), "name", ""))
        if self.database_backend not in {"sqlite", "postgresql"}:
            raise ValueError("SQL vector backend requires a SQLite or PostgreSQL engine")
        self.backend_name = "pgvector" if self.database_backend == "postgresql" else "sql"

    async def upsert_document(
        self,
        *,
        kb_id: str,
        document_id: str,
        filename: str,
        chunks: list[TextChunk],
        embeddings: list[list[float]],
    ) -> None:
        dimensions = validate_document_vectors(chunks, embeddings)
        from app.db.models import Document, DocumentChunk

        async with self._semaphore, self.session_factory() as session:
            locked_document = await session.scalar(
                select(Document.id)
                .where(Document.id == document_id, Document.kb_id == kb_id)
                .with_for_update()
            )
            if locked_document is None:
                raise ValueError(
                    f"document {document_id} does not belong to knowledge base {kb_id}"
                )
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            now = datetime.now(UTC)
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                stored_embedding: list[float] | bytes
                if self.database_backend == "postgresql":
                    stored_embedding = [float(value) for value in embedding]
                else:
                    stored_embedding = encode_embedding(embedding)
                session.add(
                    DocumentChunk(
                        id=uuid4().hex,
                        kb_id=kb_id,
                        document_id=document_id,
                        chunk_index=chunk.index,
                        filename=filename,
                        page=chunk.page,
                        start_char=chunk.start_char,
                        end_char=chunk.end_char,
                        text=chunk.text,
                        parent_id=chunk.parent_id,
                        parent_text=chunk.parent_text,
                        dimensions=dimensions or 0,
                        embedding=stored_embedding,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()

    async def query(
        self,
        *,
        kb_id: str,
        vector: list[float],
        top_k: int,
        document_ids: Collection[str] | None = None,
    ) -> list[VectorHit]:
        from app.db.models import DocumentChunk

        if (
            top_k < 1
            or not valid_query_vector(vector)
            or (document_ids is not None and not document_ids)
        ):
            return []
        filters = [
            DocumentChunk.kb_id == kb_id,
            DocumentChunk.dimensions == len(vector),
        ]
        if document_ids is not None:
            filters.append(DocumentChunk.document_id.in_(document_ids))
        async with self._semaphore, self.session_factory() as session:
            if self.database_backend == "postgresql":
                distance = cast(DocumentChunk.embedding, VECTOR(len(vector))).cosine_distance(
                    vector
                )
                ranked = await session.execute(
                    select(DocumentChunk, distance.label("distance"))
                    .where(*filters)
                    .order_by(distance.asc(), DocumentChunk.id.asc())
                    .limit(top_k)
                )
                return [
                    self._hit(row, max(0.0, min(1.0, 1.0 - float(distance_value))))
                    for row, distance_value in ranked.all()
                    if distance_value is not None and math.isfinite(float(distance_value))
                ]
            rows = (await session.execute(select(DocumentChunk).where(*filters))).scalars().all()
        scored: list[tuple[float, Any]] = []
        for row in rows:
            if not isinstance(row.embedding, (bytes, bytearray, memoryview)):
                logger.debug("skip non-binary SQLite vector chunk %s", row.id)
                continue
            try:
                embedding = decode_embedding(bytes(row.embedding), row.dimensions)
            except ValueError:
                logger.debug("skip malformed vector chunk %s", row.id)
                continue
            scored.append((cosine_similarity(vector, embedding), row))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [self._hit(row, score) for score, row in scored[:top_k]]

    async def query_text(
        self,
        *,
        kb_id: str,
        query_text: str,
        top_k: int,
        document_ids: Collection[str] | None = None,
    ) -> list[VectorHit]:
        """Keyword-rank a knowledge base's chunks (C4-1 hybrid retrieval).

        PostgreSQL ranks with ``ts_rank`` over an inline ``to_tsvector``; the
        GIN expression index from migration 0035 accelerates it. SQLite has no
        maintained FTS table on this fallback path, so chunk texts are scored
        in process with the shared BM25 scorer — behaviorally equivalent for
        local use.
        """
        from app.db.models import DocumentChunk

        if (
            top_k < 1
            or not (query_text or "").strip()
            or (document_ids is not None and not document_ids)
        ):
            return []
        filters = [DocumentChunk.kb_id == kb_id]
        if document_ids is not None:
            filters.append(DocumentChunk.document_id.in_(document_ids))
        async with self._semaphore, self.session_factory() as session:
            if self.database_backend == "postgresql":
                ts_vector = func.to_tsvector("simple", DocumentChunk.text)
                ts_query = func.plainto_tsquery("simple", query_text)
                rank = func.ts_rank(ts_vector, ts_query)
                ranked = await session.execute(
                    select(DocumentChunk, rank.label("rank"))
                    .where(*filters, rank > 0.0)
                    .order_by(rank.desc(), DocumentChunk.id.asc())
                    .limit(top_k)
                )
                return [
                    self._hit(row, _normalize_ts_rank(float(rank_value)))
                    for row, rank_value in ranked.all()
                ]
            rows = (await session.execute(select(DocumentChunk).where(*filters))).scalars().all()
        index = InProcessBm25Index()
        rows_by_id = {}
        for row in rows:
            rows_by_id[row.id] = row
            index.add(row.id, row.text or "")
        scores = index.score(query_text)
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:top_k]
        return [self._hit(rows_by_id[key], score) for key, score in ordered]

    @staticmethod
    def _hit(row: Any, score: float) -> VectorHit:
        return VectorHit(
            id=row.id,
            document_id=row.document_id,
            filename=row.filename,
            chunk_index=row.chunk_index,
            page=row.page,
            text=row.text,
            score=score,
            kb_id=row.kb_id,
            parent_text=row.parent_text,
        )

    async def delete_document(self, document_id: str) -> None:
        from app.db.models import DocumentChunk

        async with self._semaphore, self.session_factory() as session:
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            await session.commit()

    async def delete_knowledge_base(self, kb_id: str) -> None:
        from app.db.models import DocumentChunk

        async with self._semaphore, self.session_factory() as session:
            await session.execute(delete(DocumentChunk).where(DocumentChunk.kb_id == kb_id))
            await session.commit()

    async def health(self) -> dict[str, Any]:
        from app.db.models import DocumentChunk

        try:
            async with self.session_factory() as session:
                extension_version: str | None = None
                if self.database_backend == "postgresql":
                    extension_version = await session.scalar(
                        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                    )
                    if extension_version is None:
                        raise RuntimeError("pgvector extension is not installed")
                count = int(
                    (
                        await session.execute(select(func.count()).select_from(DocumentChunk))
                    ).scalar_one()
                )
        except Exception as exc:  # noqa: BLE001
            return {
                "state": "unavailable",
                "backend": self.backend_name,
                "error": str(exc)[:200],
            }
        result: dict[str, Any] = {
            "state": "ready",
            "backend": self.backend_name,
            "chunk_count": count,
        }
        if extension_version is not None:
            result["extension_version"] = extension_version
        return result

    async def close(self) -> None:
        return None


__all__ = ["SqlVectorStore"]
