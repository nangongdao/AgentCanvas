"""Durable online-source synchronization with change-aware ingestion (C4-3)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import OnlineSource
from app.db.repositories import KnowledgeBaseRepo, OnlineSourceRepo
from app.rag.online_sources import OnlineFetchError, crawl_source
from app.rag.service import KnowledgeNotFoundError, RagService
from app.rag.storage import DocumentStorage

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 5 * 1024 * 1024


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OnlineSourceBusyError(RuntimeError):
    """Raised when another worker owns the active source-sync generation."""


class OnlineSourceClaimLostError(OnlineSourceBusyError):
    """Raised when a stale worker attempts to finish a reclaimed generation."""


class OnlineSourceSyncService:
    """Fetch one URL source and atomically replace its searchable document."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        rag_service: RagService,
        storage: DocumentStorage,
        *,
        lease_seconds: int = 1_800,
    ) -> None:
        self.session_factory = session_factory
        self.rag_service = rag_service
        self.storage = storage
        self.lease_seconds = max(1, int(lease_seconds))

    async def sync_source(
        self,
        source_id: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        scheduled: bool = False,
        now: datetime | None = None,
    ) -> OnlineSource:
        """Claim, fetch, and finish one source generation.

        Manual calls may claim any idle source. Scheduled calls additionally
        require an enabled interval whose next run is due.
        """
        started_at = now or _utcnow()
        async with self.session_factory() as session:
            repo = OnlineSourceRepo(session)
            source = await repo.claim(
                source_id,
                now=started_at,
                stale_before=started_at - timedelta(seconds=self.lease_seconds),
                force=not scheduled,
            )
            if source is None:
                existing = await repo.get(source_id)
                await session.rollback()
                if existing is None:
                    raise KnowledgeNotFoundError(source_id)
                raise OnlineSourceBusyError("online source sync is already running or not due")
            generation = source.sync_generation
            kb_id = source.kb_id
            url = source.url
            max_pages = source.max_pages
            depth = source.depth
            prior_sha = source.content_sha256
            prior_document_id = source.document_id
            await session.commit()

        temporary_document_id: str | None = None
        try:
            await self._require_knowledge_base(kb_id)
            pages = await crawl_source(
                url,
                max_pages=max_pages,
                depth=depth,
                transport=transport,
            )
            if not pages:
                raise OnlineFetchError("online source produced no fetchable pages")

            body = "\n\n".join(f"# {page.url}\n{page.text}" for page in pages)
            body_bytes = body.encode("utf-8")
            if len(body_bytes) > MAX_BODY_BYTES:
                raise OnlineFetchError("online source body exceeds the 5 MiB limit")
            body_sha = hashlib.sha256(body_bytes).hexdigest()

            if body_sha == prior_sha and prior_document_id:
                return await self._finish_success(
                    source_id,
                    generation=generation,
                    document_id=None,
                    content_sha256=None,
                )

            document = await self._persist_body(
                source_id,
                kb_id,
                url,
                body_bytes,
                body_sha,
            )
            temporary_document_id = document["id"]
            try:
                result = await self.rag_service.ingest_document(
                    kb_id, temporary_document_id, publish=False
                )
            except Exception as exc:
                raise RuntimeError(f"ingestion failed: {exc}") from exc
            if result is not None and result.document.status != "pending":
                detail = result.document.error or result.document.status
                raise RuntimeError(f"ingestion failed: {detail}")

            row = await self._finish_success(
                source_id,
                generation=generation,
                document_id=temporary_document_id,
                content_sha256=body_sha,
                retired_document_id=(
                    str(prior_document_id) if prior_document_id is not None else None
                ),
            )
            temporary_document_id = None
            if prior_document_id and prior_document_id != row.document_id:
                await self._cleanup_document(kb_id, prior_document_id)
            return row
        except asyncio.CancelledError:
            published = (
                temporary_document_id is not None
                and await self._is_document_published(
                    source_id,
                    document_id=temporary_document_id,
                )
            )
            if temporary_document_id is not None and not published:
                await self._cleanup_document(kb_id, temporary_document_id)
            if not published:
                await self._mark_failed(
                    source_id,
                    "online source sync cancelled",
                    generation=generation,
                    retry_immediately=True,
                )
            raise
        except Exception as exc:
            if temporary_document_id is not None:
                await self._cleanup_document(kb_id, temporary_document_id)
            if not isinstance(exc, OnlineSourceClaimLostError):
                await self._mark_failed(source_id, str(exc), generation=generation)
            raise

    async def _require_knowledge_base(self, kb_id: str) -> None:
        async with self.session_factory() as session:
            if await KnowledgeBaseRepo(session).get(kb_id) is None:
                raise KnowledgeNotFoundError(kb_id)

    async def _is_document_published(
        self,
        source_id: str,
        *,
        document_id: str,
    ) -> bool:
        async with self.session_factory() as session:
            row = await OnlineSourceRepo(session).get(source_id)
            return bool(row is not None and row.document_id == document_id)

    async def _finish_success(
        self,
        source_id: str,
        *,
        generation: int,
        document_id: str | None,
        content_sha256: str | None,
        retired_document_id: str | None = None,
    ) -> OnlineSource:
        async with self.session_factory() as session:
            repo = OnlineSourceRepo(session)
            finished = await repo.finish_success(
                source_id,
                generation=generation,
                completed_at=_utcnow(),
                document_id=document_id,
                content_sha256=content_sha256,
                retired_document_id=retired_document_id,
            )
            if not finished:
                await session.rollback()
                raise OnlineSourceClaimLostError("online source sync claim was reclaimed")
            row = await repo.get(source_id)
            if row is None:
                await session.rollback()
                raise KnowledgeNotFoundError(source_id)
            await session.commit()
            return row

    async def _persist_body(
        self,
        source_id: str,
        kb_id: str,
        url: str,
        body: bytes,
        body_sha: str,
    ) -> dict[str, str]:
        """Write the fetched body as a markdown document for standard ingestion."""
        from uuid import uuid4

        document_id = uuid4().hex
        relative = Path(kb_id) / f"online-{source_id}-{document_id}.md"
        target = self.storage._safe_path(relative.as_posix())  # noqa: SLF001
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, body)

        try:
            async with self.session_factory() as session:
                from app.db.models import Document

                session.add(
                    Document(
                        id=document_id,
                        kb_id=kb_id,
                        filename=f"online-{url[:200]}.md",
                        file_path=relative.as_posix(),
                        mime_type="text/markdown",
                        size_bytes=len(body),
                        content_sha256=body_sha,
                        status="pending",
                    )
                )
                await session.commit()
        except Exception:
            await self.storage.delete(relative.as_posix())
            raise
        return {"id": document_id, "path": relative.as_posix()}

    async def _cleanup_document(self, kb_id: str, document_id: str) -> None:
        try:
            await self.rag_service.delete_document(kb_id, document_id)
        except KnowledgeNotFoundError:
            return
        except Exception:
            logger.exception("failed to clean online-source document %s", document_id)

    async def _mark_failed(
        self,
        source_id: str,
        error: str,
        *,
        generation: int | None = None,
        retry_immediately: bool = False,
    ) -> None:
        async with self.session_factory() as session:
            repo = OnlineSourceRepo(session)
            row = await repo.get(source_id)
            if row is None:
                await session.rollback()
                return
            if generation is not None:
                await repo.finish_failure(
                    source_id,
                    generation=generation,
                    completed_at=_utcnow(),
                    error=error,
                    retry_immediately=retry_immediately,
                )
            else:
                await repo.update(row, status="failed", error=error[:2000])
            await session.commit()


__all__ = [
    "OnlineSourceBusyError",
    "OnlineSourceClaimLostError",
    "OnlineSourceSyncService",
]
