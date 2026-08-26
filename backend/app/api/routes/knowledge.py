"""Knowledge-base CRUD, document ingestion, and retrieval probe routes."""

from __future__ import annotations

import contextlib
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.pagination import PageParams, PageResult, page_result
from app.api.tenant_deps import authorize_project_scope
from app.core.auth import Principal, Role
from app.core.container import ServiceContainer
from app.db.models import Document, KnowledgeBase
from app.db.repositories import DocumentRepo, KnowledgeBaseRepo, OnlineSourceRepo
from app.rag import (
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    KnowledgeValidationError,
    UploadValidationError,
)
from app.rag.splitter import preview_chunks as split_text
from app.schemas.api import (
    ChunkPreviewOut,
    ChunkPreviewRequest,
    DocumentIngestOut,
    DocumentStatus,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    KnowledgeDocumentOut,
    OnlineSourceCreate,
    OnlineSourceOut,
    OnlineSourceStatus,
    OnlineSourceUpdate,
    RetrievalChunkPreviewOut,
    RetrievalHitOut,
    RetrievalMode,
    RetrievalOut,
    RetrievalRequest,
    RetrievalScoreDetailOut,
    SplitStrategy,
)
from app.services.online_source_sync import OnlineSourceBusyError
from app.services.project_quotas import ProjectQuotaExceeded

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _kb_out(row: KnowledgeBase, document_count: int = 0) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        embedding_model_id=row.embedding_model_id,
        chunk_size=row.chunk_size,
        chunk_overlap=row.chunk_overlap,
        split_strategy=cast(SplitStrategy, row.split_strategy),
        parent_chunk=row.parent_chunk,
        retrieval_mode=cast(RetrievalMode, row.retrieval_mode),
        rerank_enabled=row.rerank_enabled,
        rerank_model_id=row.rerank_model_id,
        document_count=document_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _document_out(row: Document) -> KnowledgeDocumentOut:
    return KnowledgeDocumentOut(
        id=row.id,
        kb_id=row.kb_id,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        content_sha256=row.content_sha256,
        status=cast(DocumentStatus, row.status),
        chunk_count=row.chunk_count,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _kb_or_404(
    session: AsyncSession,
    principal: Principal,
    kb_id: str,
    *,
    required: Role,
) -> KnowledgeBase:
    row = await KnowledgeBaseRepo(session).get(kb_id)
    if row is None:
        raise HTTPException(status_code=404, detail="knowledge base not found")
    await authorize_project_scope(session, principal, row.project_id, required=required)
    return row


async def _release_read_transaction(session: AsyncSession) -> None:
    if session.in_transaction():
        await session.rollback()


@router.get("", response_model=PageResult[KnowledgeBaseOut])
async def list_knowledge_bases(
    session: SessionDep,
    principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
    project_id: Annotated[str | None, Query()] = None,
) -> PageResult[KnowledgeBaseOut]:
    await authorize_project_scope(session, principal, project_id, required=Role.VIEWER)
    repo = KnowledgeBaseRepo(session)
    spec = params.to_spec(
        allowed_sorts={"created_at", "updated_at", "name", "id"},
        default_sort="updated_at",
        scope=f"knowledge-bases:{project_id or 'global'}",
    )
    page = await repo.list_page(spec, project_id=project_id)
    counts = await repo.document_counts([row.id for row in page.rows])
    return page_result(page, lambda row: _kb_out(row, counts.get(row.id, 0)))


@router.post("", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    body: KnowledgeBaseCreate,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> KnowledgeBaseOut:
    await authorize_project_scope(session, principal, body.project_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    try:
        row = await container.rag_service.create_knowledge_base(body.model_dump())
    except KnowledgeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _kb_out(row)


@router.get("/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(
    kb_id: str, session: SessionDep, principal: ViewerDep
) -> KnowledgeBaseOut:
    row = await _kb_or_404(session, principal, kb_id, required=Role.VIEWER)
    documents = await DocumentRepo(session).list_for_kb(kb_id)
    return _kb_out(row, len(documents))


@router.put("/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    kb_id: str,
    body: KnowledgeBaseUpdate,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> KnowledgeBaseOut:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    try:
        row = await container.rag_service.update_knowledge_base(
            kb_id, body.model_dump(exclude_unset=True)
        )
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge base not found") from exc
    except KnowledgeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _kb_out(row)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> None:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    try:
        await container.rag_service.delete_knowledge_base(kb_id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge base not found") from exc


@router.get(
    "/{kb_id}/documents",
    response_model=PageResult[KnowledgeDocumentOut],
)
async def list_documents(
    kb_id: str,
    session: SessionDep,
    principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
) -> PageResult[KnowledgeDocumentOut]:
    await _kb_or_404(session, principal, kb_id, required=Role.VIEWER)
    spec = params.to_spec(
        allowed_sorts={"created_at", "updated_at", "filename", "status", "id"},
        default_sort="created_at",
        scope=f"documents:{kb_id}",
    )
    page = await DocumentRepo(session).list_for_kb_page(kb_id, spec)
    return page_result(page, _document_out)


@router.post(
    "/{kb_id}/documents",
    response_model=KnowledgeDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    kb_id: str,
    file: Annotated[UploadFile, File()],
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> KnowledgeDocumentOut:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    try:
        row = await container.rag_service.upload_document(kb_id, file)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge base not found") from exc
    except UploadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProjectQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    return _document_out(row)


@router.get(
    "/{kb_id}/documents/{document_id}",
    response_model=KnowledgeDocumentOut,
)
async def get_document(
    kb_id: str,
    document_id: str,
    session: SessionDep,
    principal: ViewerDep,
) -> KnowledgeDocumentOut:
    await _kb_or_404(session, principal, kb_id, required=Role.VIEWER)
    row = await DocumentRepo(session).get(document_id)
    if row is None or row.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="document not found")
    return _document_out(row)


@router.post(
    "/{kb_id}/documents/{document_id}/ingest",
    response_model=DocumentIngestOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_document(
    kb_id: str,
    document_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> DocumentIngestOut:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    try:
        result = await container.rag_service.start_ingest(kb_id, document_id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="document not found") from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DocumentIngestOut(
        document=_document_out(result.document),
        job_id=result.job_id,
        job_status=cast(JobStatus | None, result.job_status),
        cache_hits=result.cache_hits,
        cache_misses=result.cache_misses,
    )


@router.get(
    "/{kb_id}/documents/{document_id}/ingest",
    response_model=DocumentIngestOut,
)
async def get_ingest_job(
    kb_id: str,
    document_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
) -> DocumentIngestOut:
    await _kb_or_404(session, principal, kb_id, required=Role.VIEWER)
    await _release_read_transaction(session)
    try:
        result = await container.rag_service.get_ingest_job(kb_id, document_id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="document not found") from exc
    return DocumentIngestOut(
        document=_document_out(result.document),
        job_id=result.job_id,
        job_status=cast(JobStatus | None, result.job_status),
        cache_hits=result.cache_hits,
        cache_misses=result.cache_misses,
    )


@router.delete("/{kb_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    kb_id: str,
    document_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> None:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    try:
        await container.rag_service.delete_document(kb_id, document_id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="document not found") from exc


@router.post("/preview-chunks", response_model=ChunkPreviewOut)
async def preview_chunks(
    body: ChunkPreviewRequest,
    _principal: ViewerDep,
) -> ChunkPreviewOut:
    """Preview how a sample would chunk under a given strategy (C4-2).

    No side effects: the splitter runs in-process against the supplied text so
    editors can tune parameters before committing to a full re-index.
    """
    chunks = split_text(
        body.text,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
        strategy=body.split_strategy,
        parent_chunk=body.parent_chunk,
        limit=50,
    )
    return ChunkPreviewOut(
        chunks=[
            RetrievalChunkPreviewOut(
                index=chunk.index,
                text=chunk.text,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                page=chunk.page,
                parent_id=chunk.parent_id,
                parent_text=chunk.parent_text,
            )
            for chunk in chunks
        ],
        chunk_count=len(chunks),
    )


@router.post("/{kb_id}/retrieve", response_model=RetrievalOut)
async def retrieve(
    kb_id: str,
    body: RetrievalRequest,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
) -> RetrievalOut:
    kb = await _kb_or_404(session, principal, kb_id, required=Role.VIEWER)
    project_id = kb.project_id
    await _release_read_transaction(session)
    try:
        result = await container.rag_service.retrieve(
            kb_id,
            body.query,
            top_k=body.top_k,
            score_threshold=body.score_threshold,
            project_id=project_id,
        )
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge base not found") from exc
    except ProjectQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    return RetrievalOut(
        query=result.query,
        hits=[
            RetrievalHitOut(
                id=hit.id,
                document_id=hit.document_id,
                filename=hit.filename,
                chunk_index=hit.chunk_index,
                page=hit.page,
                text=hit.text,
                score=hit.score,
                citation=(f"{hit.filename}, page {hit.page}" if hit.page else hit.filename),
            )
            for hit in result.hits
        ],
        retrieval_mode=result.retrieval_mode,
        rerank_applied=result.rerank_applied,
        score_detail=(
            [
                RetrievalScoreDetailOut(
                    hit_id=str(detail["hit_id"]),
                    retrieval_mode=str(detail["retrieval_mode"]),
                    vector_score=_optional_float(detail.get("vector_score")),
                    keyword_score=_optional_float(detail.get("keyword_score")),
                    fused_score=_optional_float(detail.get("fused_score")),
                    rerank_score=_optional_float(detail.get("rerank_score")),
                    rerank_applied=bool(detail.get("rerank_applied", False)),
                )
                for detail in (result.score_detail or [])
            ]
            if body.include_scores
            else None
        ),
    )


# ---- C4-3: online (URL) knowledge sources ----


def _online_source_out(row) -> OnlineSourceOut:
    return OnlineSourceOut(
        id=row.id,
        kb_id=row.kb_id,
        url=row.url,
        document_id=row.document_id,
        max_pages=row.max_pages,
        depth=row.depth,
        content_sha256=row.content_sha256,
        status=cast(OnlineSourceStatus, row.status),
        error=row.error,
        last_synced_at=row.last_synced_at,
        sync_interval_minutes=row.sync_interval_minutes,
        next_sync_at=row.next_sync_at,
        sync_started_at=row.sync_started_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/{kb_id}/online-sources", response_model=list[OnlineSourceOut]
)
async def list_online_sources(
    kb_id: str, session: SessionDep, principal: ViewerDep
) -> list[OnlineSourceOut]:
    await _kb_or_404(session, principal, kb_id, required=Role.VIEWER)
    rows = await OnlineSourceRepo(session).list_for_kb(kb_id)
    return [_online_source_out(row) for row in rows]


@router.post(
    "/{kb_id}/online-sources",
    response_model=OnlineSourceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_online_source(
    kb_id: str,
    body: OnlineSourceCreate,
    session: SessionDep,
    principal: EditorDep,
) -> OnlineSourceOut:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    row = await OnlineSourceRepo(session).create(
        kb_id=kb_id,
        url=body.url,
        max_pages=body.max_pages,
        depth=body.depth,
        sync_interval_minutes=body.sync_interval_minutes,
    )
    await session.commit()
    return _online_source_out(row)


@router.put(
    "/{kb_id}/online-sources/{source_id}", response_model=OnlineSourceOut
)
async def update_online_source(
    kb_id: str,
    source_id: str,
    body: OnlineSourceUpdate,
    session: SessionDep,
    principal: EditorDep,
) -> OnlineSourceOut:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    repo = OnlineSourceRepo(session)
    row = await repo.get(source_id)
    if row is None or row.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="online source not found")
    if row.status == "syncing":
        raise HTTPException(status_code=409, detail="online source sync is running")
    updated = await repo.configure(
        row,
        max_pages=body.max_pages if body.max_pages is not None else row.max_pages,
        depth=body.depth if body.depth is not None else row.depth,
        sync_interval_minutes=body.sync_interval_minutes,
        schedule_changed="sync_interval_minutes" in body.model_fields_set,
    )
    if updated is None:
        await session.rollback()
        raise HTTPException(status_code=409, detail="online source sync is running")
    await session.commit()
    return _online_source_out(updated)


@router.delete(
    "/{kb_id}/online-sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_online_source(
    kb_id: str,
    source_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> None:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    repo = OnlineSourceRepo(session)
    row = await repo.get(source_id)
    if row is None or row.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="online source not found")
    if row.status == "syncing":
        raise HTTPException(status_code=409, detail="online source sync is running")
    document_id = row.document_id
    if document_id is not None:
        document = await DocumentRepo(session).get(document_id)
        if document is not None:
            await DocumentRepo(session).set_status(document, "pending")
    await repo.delete(row)
    await session.commit()
    if document_id is not None:
        with contextlib.suppress(KnowledgeNotFoundError):
            await container.rag_service.delete_document(kb_id, document_id)


@router.post(
    "/{kb_id}/online-sources/{source_id}/sync", response_model=OnlineSourceOut
)
async def sync_online_source(
    kb_id: str,
    source_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> OnlineSourceOut:
    await _kb_or_404(session, principal, kb_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    row = await OnlineSourceRepo(session).get(source_id)
    if row is None or row.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="online source not found")
    try:
        updated = await container.online_source_sync.sync_source(source_id)
    except KnowledgeNotFoundError as exc:
        raise HTTPException(status_code=404, detail="online source not found") from exc
    except OnlineSourceBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface fetch/ingest failures
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"online source sync failed: {exc}"[:500],
        ) from exc
    return _online_source_out(updated)
