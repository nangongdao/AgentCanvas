"""Durable document ingestion workflow for :mod:`app.rag.service`."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, cast
from uuid import uuid4

from app.db.repositories import DocumentRepo, IngestJobRepo, KnowledgeBaseRepo
from app.rag.loaders import load_document
from app.rag.splitter import SplitStrategy, split_sections
from app.rag.types import (
    IngestResult,
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    KnowledgeValidationError,
)
from app.services.project_quotas import ProjectQuotaService

logger = logging.getLogger(__name__)


class RagIngestionMixin:
    """Own ingestion state transitions while the service owns composition."""

    async def upload_document(self: Any, kb_id: str, upload: Any) -> Any:
        async with self.session_factory() as session:
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if kb is None:
                raise KnowledgeNotFoundError(kb_id)

        stored = await self.storage.save(kb_id, upload)
        try:
            async with self.session_factory() as session:
                row = await DocumentRepo(session).create(
                    id=stored.document_id,
                    kb_id=kb_id,
                    filename=stored.filename,
                    file_path=stored.relative_path,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    content_sha256=stored.sha256,
                    status="pending",
                )
                if kb.project_id is not None:
                    await ProjectQuotaService(session).reserve(
                        kb.project_id,
                        "document_storage",
                        row.id,
                        row.size_bytes,
                    )
                await session.commit()
                return row
        except Exception:
            await self.storage.delete(stored.relative_path)
            raise

    async def start_ingest(self: Any, kb_id: str, document_id: str) -> IngestResult:
        """Create one durable ingestion job and schedule its worker."""
        async with self.session_factory() as session:
            document = await DocumentRepo(session).get(document_id)
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if document is None or document.kb_id != kb_id or kb is None:
                raise KnowledgeNotFoundError(document_id)
            active = await IngestJobRepo(session).active_for_document(document_id)
            if active is not None:
                return IngestResult(
                    document,
                    active.cache_hits,
                    active.cache_misses,
                    active.id,
                    active.status,
                )
            claimed = await DocumentRepo(session).claim_for_ingest(kb_id, document_id)
            if claimed is None:
                latest = await IngestJobRepo(session).latest_for_document(document_id)
                if latest is None:
                    raise KnowledgeConflictError("document ingestion is already running")
                return IngestResult(
                    document,
                    latest.cache_hits,
                    latest.cache_misses,
                    latest.id,
                    latest.status,
                )
            job = await IngestJobRepo(session).create(
                id=uuid4().hex,
                document_id=document_id,
                kb_id=kb_id,
                status="queued",
                cache_hits=0,
                cache_misses=0,
            )
            await session.commit()

        task = asyncio.create_task(
            self._run_ingest_job(job.id, kb_id, document_id),
            name=f"ingest-{job.id}",
        )
        self._ingest_tasks[job.id] = task

        def on_done(done: asyncio.Task[None], job_id: str = job.id) -> None:
            self._forget_ingest_task(job_id, done)

        task.add_done_callback(on_done)
        return IngestResult(claimed, job_id=job.id, job_status="queued")

    def _forget_ingest_task(self: Any, job_id: str, task: asyncio.Task[None]) -> None:
        self._ingest_tasks.pop(job_id, None)
        if not task.cancelled() and task.exception() is not None:
            logger.exception(
                "ingest job %s failed outside the worker", job_id, exc_info=task.exception()
            )

    async def _run_ingest_job(self: Any, job_id: str, kb_id: str, document_id: str) -> None:
        async with self.session_factory() as session:
            job = await IngestJobRepo(session).get(job_id)
            if job is None:
                return
            await IngestJobRepo(session).update(job, status="running")
            await session.commit()
        started = time.perf_counter()
        status = "failed"
        size_bytes = 0
        try:
            async with asyncio.timeout(self.ingest_timeout_seconds):
                result = await self.ingest_document(kb_id, document_id, claimed=True)
                size_bytes = result.document.size_bytes
                status = "succeeded" if result.document.status == "ready" else "failed"
        except asyncio.CancelledError:
            status = "cancelled"
            await self._update_job(job_id, status="cancelled", error="document ingestion cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - persist worker failure
            await self._update_job(job_id, status="failed", error=str(exc)[:2000])
            return
        finally:
            if self.observability is not None:
                self.observability.record_ingest(
                    status=status,
                    duration=time.perf_counter() - started,
                    size_bytes=size_bytes,
                )
        await self._update_job(
            job_id,
            status=status,
            cache_hits=result.cache_hits,
            cache_misses=result.cache_misses,
            error=result.document.error,
        )

    async def _update_job(
        self: Any,
        job_id: str,
        *,
        status: str,
        cache_hits: int = 0,
        cache_misses: int = 0,
        error: str | None = None,
    ) -> None:
        async with self.session_factory() as session:
            job = await IngestJobRepo(session).get(job_id)
            if job is not None:
                await IngestJobRepo(session).update(
                    job,
                    status=status,
                    cache_hits=cache_hits,
                    cache_misses=cache_misses,
                    error=error,
                )
                await session.commit()

    async def get_ingest_job(self: Any, kb_id: str, document_id: str) -> IngestResult:
        async with self.session_factory() as session:
            document = await DocumentRepo(session).get(document_id)
            if document is None or document.kb_id != kb_id:
                raise KnowledgeNotFoundError(document_id)
            job = await IngestJobRepo(session).latest_for_document(document_id)
            if job is None:
                return IngestResult(document)
            return IngestResult(
                document,
                job.cache_hits,
                job.cache_misses,
                job.id,
                job.status,
            )

    async def ingest_document(
        self: Any,
        kb_id: str,
        document_id: str,
        *,
        claimed: bool = False,
        publish: bool = True,
    ) -> IngestResult:
        async with self.session_factory() as session:
            document = await DocumentRepo(session).get(document_id)
            kb = await KnowledgeBaseRepo(session).get(kb_id)
            if document is None or document.kb_id != kb_id or kb is None:
                raise KnowledgeNotFoundError(document_id)
            if not claimed:
                if document.status == "processing":
                    raise KnowledgeConflictError("document ingestion is already running")
                await DocumentRepo(session).set_status(
                    document, "processing", chunk_count=0, error=None
                )
                await session.commit()

        cache_hits = 0
        cache_misses = 0
        try:
            await self.vector_store.delete_document(document_id)
            path = self.storage.resolve(document.file_path)
            sections = await asyncio.to_thread(load_document, path, document.filename)
            chunks = split_sections(
                sections,
                chunk_size=kb.chunk_size,
                chunk_overlap=kb.chunk_overlap,
                strategy=cast(SplitStrategy, kb.split_strategy),
                parent_chunk=kb.parent_chunk,
            )
            if not chunks:
                raise KnowledgeValidationError("document produced no searchable chunks")
            embedded = await self.embeddings.embed_texts(kb, [chunk.text for chunk in chunks])
            cache_hits = embedded.cache_hits
            cache_misses = embedded.cache_misses
            await self.vector_store.upsert_document(
                kb_id=kb_id,
                document_id=document_id,
                filename=document.filename,
                chunks=chunks,
                embeddings=embedded.vectors,
            )
        except asyncio.CancelledError:
            await self._fail_cancelled_ingest(document_id)
            raise
        except Exception as exc:
            logger.warning("document %s ingestion failed: %s", document_id, exc)
            try:
                await self.vector_store.delete_document(document_id)
            except Exception:
                logger.exception("failed to clean vectors for document %s", document_id)
            failed = await self._finish_ingest(
                document_id,
                status="failed",
                chunk_count=0,
                error=str(exc)[:2000] or exc.__class__.__name__,
            )
            return IngestResult(failed, cache_hits, cache_misses)

        ready = await self._finish_ingest(
            document_id,
            status="ready" if publish else "pending",
            chunk_count=len(chunks),
            error=None,
        )
        return IngestResult(ready, cache_hits, cache_misses)

    async def _fail_cancelled_ingest(self: Any, document_id: str) -> None:
        logger.warning("document %s ingestion cancelled", document_id)
        try:
            await self.vector_store.delete_document(document_id)
        except Exception:
            logger.exception("failed to clean vectors for cancelled document %s", document_id)
        await self._finish_ingest(
            document_id,
            status="failed",
            chunk_count=0,
            error="document ingestion cancelled",
        )

    async def _finish_ingest(
        self: Any,
        document_id: str,
        *,
        status: str,
        chunk_count: int,
        error: str | None,
    ) -> Any:
        async with self.session_factory() as session:
            document = await DocumentRepo(session).get(document_id)
            if document is None:
                raise KnowledgeNotFoundError(document_id)
            await DocumentRepo(session).set_status(
                document,
                status,
                chunk_count=chunk_count,
                error=error,
            )
            await session.commit()
            return document
