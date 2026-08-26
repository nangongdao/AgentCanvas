"""Knowledge-base orchestration: upload, ingest, retrieve, and cleanup."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.observability import Observability
from app.core.secret_providers import SecretResolver
from app.db.models import Document, KnowledgeBase, ModelConfig
from app.db.repositories import (
    DocumentRepo,
    IngestJobRepo,
    KnowledgeBaseRepo,
    ModelConfigRepo,
)
from app.rag.embedder import EmbeddingCoordinator
from app.rag.hybrid import HitScoreDetail, component_threshold, rrf_fuse
from app.rag.ingestion import RagIngestionMixin
from app.rag.reranker import NoopReranker, OpenAICompatReranker, Reranker, apply_rerank
from app.rag.storage import DocumentStorage
from app.rag.store import VectorHit, VectorStore
from app.rag.types import (
    IngestResult,
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    KnowledgeValidationError,
    RetrievalResult,
)
from app.services.project_quotas import ProjectQuotaService

logger = logging.getLogger(__name__)

RETRIEVAL_MODES = frozenset({"vector", "hybrid"})
SPLIT_STRATEGIES = frozenset({"window", "recursive", "heading"})


def _kb_hybrid_values(kb: KnowledgeBase) -> dict[str, Any]:
    """Current hybrid/rerank configuration of a knowledge base row."""
    return {
        "retrieval_mode": kb.retrieval_mode,
        "rerank_enabled": kb.rerank_enabled,
        "rerank_model_id": kb.rerank_model_id,
    }


__all__ = [
    "IngestResult",
    "KnowledgeConflictError",
    "KnowledgeNotFoundError",
    "KnowledgeValidationError",
    "RagService",
    "RetrievalResult",
]


class RagService(RagIngestionMixin):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        storage: DocumentStorage,
        embeddings: EmbeddingCoordinator,
        vector_store: VectorStore,
        ingest_timeout_seconds: float = 300.0,
        observability: Observability | None = None,
        secret_resolver: SecretResolver | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.ingest_timeout_seconds = ingest_timeout_seconds
        self.observability = observability
        self.secret_resolver = secret_resolver
        self.settings = settings
        self._ingest_tasks: dict[str, asyncio.Task[None]] = {}

    async def validate_embedding_model(self, model_id: str) -> ModelConfig:
        async with self.session_factory() as session:
            row = await ModelConfigRepo(session).get(model_id)
            if row is None or row.kind != "embedding":
                raise KnowledgeValidationError(f"embedding model config not found: {model_id}")
            return row

    async def _validate_rerank_model(self, model_id: str) -> None:
        async with self.session_factory() as session:
            row = await ModelConfigRepo(session).get(model_id)
            if row is None or row.kind != "rerank":
                raise KnowledgeValidationError(
                    f"rerank model config not found or not a rerank model: {model_id}"
                )

    async def _resolve_reranker(self, kb: KnowledgeBase) -> Reranker:
        """Build the KB's reranker; identity while reranking is disabled."""
        if not kb.rerank_enabled:
            return NoopReranker()
        if not kb.rerank_model_id:
            raise KnowledgeValidationError(
                "rerank_enabled requires rerank_model_id on the knowledge base"
            )
        async with self.session_factory() as session:
            row = await ModelConfigRepo(session).get(kb.rerank_model_id)
        if row is None or row.kind != "rerank":
            raise KnowledgeValidationError(
                f"rerank model config not found or not a rerank model: {kb.rerank_model_id}"
            )
        api_key = ""
        if self.secret_resolver is not None:
            api_key = self.secret_resolver.decrypt(row.api_key_encrypted or "")
        base_url = row.base_url
        if not base_url and self.settings is not None:
            base_url = self.settings.openai_base_url
        if not base_url:
            raise KnowledgeValidationError(f"rerank model '{row.id}' has no base_url configured")
        return OpenAICompatReranker(
            model=row.model_name,
            api_key=api_key,
            base_url=base_url,
        )

    async def create_knowledge_base(self, values: dict[str, Any]) -> KnowledgeBase:
        await self.validate_embedding_model(str(values["embedding_model_id"]))
        await self._validate_hybrid_values(values)
        async with self.session_factory() as session:
            row = await KnowledgeBaseRepo(session).create(**values)
            await session.commit()
            return row

    async def _validate_hybrid_values(self, values: dict[str, Any]) -> None:
        """Validate C4-1 retrieval/rerank fields from create/update payloads."""
        mode = values.get("retrieval_mode")
        if mode is not None and mode not in RETRIEVAL_MODES:
            raise KnowledgeValidationError(
                f"retrieval_mode must be one of {sorted(RETRIEVAL_MODES)}"
            )
        rerank_model = values.get("rerank_model_id")
        rerank_enabled = values.get("rerank_enabled")
        if rerank_model:
            await self._validate_rerank_model(str(rerank_model))
        if rerank_enabled and not rerank_model:
            raise KnowledgeValidationError("rerank_enabled requires rerank_model_id")
        strategy = values.get("split_strategy")
        if strategy is not None and strategy not in SPLIT_STRATEGIES:
            raise KnowledgeValidationError(
                f"split_strategy must be one of {sorted(SPLIT_STRATEGIES)}"
            )
        parent_chunk = values.get("parent_chunk")
        if parent_chunk and strategy is not None and strategy != "heading":
            raise KnowledgeValidationError(
                "parent_chunk is only supported with split_strategy='heading'"
            )

    async def update_knowledge_base(self, kb_id: str, values: dict[str, Any]) -> KnowledgeBase:
        async with self.session_factory() as session:
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if kb is None:
                raise KnowledgeNotFoundError(kb_id)
            # Merge the update over current values so hybrid/rerank validation
            # sees the effective configuration, not just the delta.
            await self._validate_hybrid_values({**_kb_hybrid_values(kb), **values})
            next_chunk_size = int(values.get("chunk_size", kb.chunk_size))
            next_overlap = int(values.get("chunk_overlap", kb.chunk_overlap))
            if next_overlap >= next_chunk_size:
                raise KnowledgeValidationError("chunk_overlap must be smaller than chunk_size")
            next_model = str(values.get("embedding_model_id", kb.embedding_model_id))

        if next_model != kb.embedding_model_id:
            await self.validate_embedding_model(next_model)

        invalidates_vectors = any(
            key in values and values[key] != getattr(kb, key)
            for key in (
                "embedding_model_id",
                "chunk_size",
                "chunk_overlap",
                "split_strategy",
                "parent_chunk",
            )
        )
        async with self.session_factory() as session:
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if kb is None:
                raise KnowledgeNotFoundError(kb_id)
            await KnowledgeBaseRepo(session).update(kb, **values)
            if invalidates_vectors:
                for document in await DocumentRepo(session).list_for_kb(kb_id):
                    await DocumentRepo(session).set_status(
                        document, "pending", chunk_count=0, error=None
                    )
            await session.commit()

        if invalidates_vectors:
            await self.vector_store.delete_knowledge_base(kb_id)
        return kb

    async def retrieve(
        self,
        kb_id: str,
        query: str,
        *,
        top_k: int,
        score_threshold: float,
        project_id: str | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        status = "failed"
        try:
            result = await self._retrieve(
                kb_id,
                query,
                top_k=top_k,
                score_threshold=score_threshold,
                project_id=project_id,
            )
            status = "succeeded"
            return result
        finally:
            if self.observability is not None:
                self.observability.record_retrieval(
                    status=status,
                    duration=time.perf_counter() - started,
                )

    async def _retrieve(
        self,
        kb_id: str,
        query: str,
        *,
        top_k: int,
        score_threshold: float,
        project_id: str | None = None,
    ) -> RetrievalResult:
        async with self.session_factory() as session:
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            documents = await DocumentRepo(session).list_for_kb(kb_id)
        if kb is None:
            raise KnowledgeNotFoundError(kb_id)
        if kb.project_id is not None and kb.project_id != project_id:
            raise KnowledgeNotFoundError(kb_id)
        ready_ids = {document.id for document in documents if document.status == "ready"}
        if not ready_ids:
            return RetrievalResult(query=query, hits=[])
        if kb.retrieval_mode not in RETRIEVAL_MODES:
            raise KnowledgeValidationError(f"unknown retrieval mode '{kb.retrieval_mode}'")

        embedded = await self.embeddings.embed_texts(kb, [query])
        candidate_k = min(50, max(top_k, top_k * 3))
        vector_hits = [
            hit
            for hit in await self.vector_store.query(
                kb_id=kb_id,
                vector=embedded.vectors[0],
                top_k=candidate_k,
                document_ids=ready_ids,
            )
            if hit.document_id in ready_ids
        ]

        retrieval_mode = kb.retrieval_mode
        details: dict[str, HitScoreDetail] = {}
        if retrieval_mode == "hybrid":
            keyword_hits = [
                hit
                for hit in await self.vector_store.query_text(
                    kb_id=kb_id,
                    query_text=query,
                    top_k=candidate_k,
                    document_ids=ready_ids,
                )
                if hit.document_id in ready_ids
            ]
            fused = rrf_fuse(vector_hits, keyword_hits)
            ordered: list[VectorHit] = []
            for hit, detail in fused:
                if component_threshold(detail, score_threshold):
                    ordered.append(hit)
                    details[hit.id] = detail
            ordered = ordered[:top_k]
        else:
            ordered = [hit for hit in vector_hits if hit.score >= score_threshold][:top_k]
            details = {
                hit.id: HitScoreDetail(
                    hit_id=hit.id, retrieval_mode="vector", vector_score=hit.score
                )
                for hit in ordered
            }

        rerank_applied = False
        if kb.rerank_enabled:
            reranker = await self._resolve_reranker(kb)
            reranked = await apply_rerank(reranker, query, ordered, top_k=top_k)
            if reranked is not None:
                rerank_applied = True
                rerank_scores = {hit.id: score for hit, score in reranked}
                ordered = [replace(hit, score=rerank_scores[hit.id]) for hit in ordered]
                ordered.sort(key=lambda hit: (-hit.score, hit.id))
                ordered = ordered[:top_k]
                for hit in ordered:
                    existing = details.get(hit.id)
                    if existing is not None:
                        details[hit.id] = replace(
                            existing,
                            rerank_score=rerank_scores[hit.id],
                            rerank_applied=True,
                        )

        return RetrievalResult(
            query=query,
            hits=ordered,
            retrieval_mode=retrieval_mode,
            rerank_applied=rerank_applied,
            score_detail=[details[hit.id].as_dict() for hit in ordered if hit.id in details],
        )

    async def delete_document(self, kb_id: str, document_id: str) -> None:
        async with self.session_factory() as session:
            document = await DocumentRepo(session).get(document_id)
            if document is None or document.kb_id != kb_id:
                raise KnowledgeNotFoundError(document_id)
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if kb is None:
                raise KnowledgeNotFoundError(kb_id)
            active = await IngestJobRepo(session).active_for_document(document_id)
        if active is not None:
            task = self._ingest_tasks.get(active.id)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await self.vector_store.delete_document(document_id)
        await self.storage.delete(document.file_path)
        async with self.session_factory() as session:
            document = await DocumentRepo(session).get(document_id)
            if document is not None:
                if kb.project_id is not None:
                    await ProjectQuotaService(session).release(
                        kb.project_id, "document_storage", document.id
                    )
                await DocumentRepo(session).delete(document)
                await session.commit()

    async def delete_knowledge_base(self, kb_id: str) -> None:
        async with self.session_factory() as session:
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if kb is None:
                raise KnowledgeNotFoundError(kb_id)
            documents = await DocumentRepo(session).list_for_kb(kb_id)
            active_jobs = [
                job
                for document in documents
                for job in [await IngestJobRepo(session).active_for_document(document.id)]
                if job is not None
            ]
        tasks = [self._ingest_tasks.get(job.id) for job in active_jobs]
        live_tasks = [task for task in tasks if task is not None and not task.done()]
        for task in live_tasks:
            task.cancel()
        if live_tasks:
            await asyncio.gather(*live_tasks, return_exceptions=True)
        await self.vector_store.delete_knowledge_base(kb_id)
        for document in documents:
            await self.storage.delete(document.file_path)
        async with self.session_factory() as session:
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if kb is not None:
                for document in await DocumentRepo(session).list_for_kb(kb_id):
                    if kb.project_id is not None:
                        await ProjectQuotaService(session).release(
                            kb.project_id, "document_storage", document.id
                        )
                    await DocumentRepo(session).delete(document)
                await KnowledgeBaseRepo(session).delete(kb)
                await session.commit()

    async def recover_processing_documents(self) -> int:
        async with self.session_factory() as session:
            result = await session.execute(select(Document).where(Document.status == "processing"))
            documents = list(result.scalars().all())
            for document in documents:
                await DocumentRepo(session).set_status(
                    document,
                    "failed",
                    chunk_count=0,
                    error="backend restarted during document ingestion",
                )
                jobs = await IngestJobRepo(session).latest_for_document(document.id)
                if jobs is not None and jobs.status in {"queued", "running"}:
                    await IngestJobRepo(session).update(
                        jobs,
                        status="failed",
                        error="backend restarted during document ingestion",
                    )
            await session.commit()
        return len(documents)

    async def shutdown(self) -> None:
        """Cancel queued workers and wait for their cleanup before DB close."""
        tasks = [task for task in self._ingest_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.vector_store.close()
