"""ORM models for knowledge bases, documents, and embedding cache entries."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    embedding_model_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="default-embedding"
    )
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=150)
    split_strategy: Mapped[str] = mapped_column(
        String(16), nullable=False, default="window", server_default="window"
    )
    parent_chunk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # C4-1 hybrid retrieval: keyword candidates fused with vector ranking.
    retrieval_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="vector", server_default="vector"
    )
    rerank_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    rerank_model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    online_sources: Mapped[list[OnlineSource]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kb_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column("mime", String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"

    cache_key: Mapped[str] = mapped_column("hash", String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    dimensions: Mapped[int] = mapped_column("dim", Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DocumentChunk(Base):
    """Durable vector chunk row shared across multi-instance SQL backends."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index_nonnegative"),
        CheckConstraint(
            "dimensions > 0 AND dimensions <= 16000",
            name="ck_document_chunks_dimensions",
        ),
        Index("ix_document_chunks_kb_document", "kb_id", "document_id"),
        # GIN expression index for PostgreSQL hybrid retrieval (0035). Declared
        # here so autogenerate sees the migrated PG schema as in sync; SQLite
        # ranks keyword candidates in process and never creates this index.
        Index(
            "ix_document_chunks_text_tsv",
            text("to_tsvector('simple', text)"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kb_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | bytes] = mapped_column(
        LargeBinary().with_variant(VECTOR(), "postgresql"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


_TSV_INDEX_NAME = "ix_document_chunks_text_tsv"


@event.listens_for(DocumentChunk.__table__, "before_create")
def _detach_tsv_index_off_postgresql(target, connection, **_kw) -> None:
    """Skip the PostgreSQL GIN expression index when creating tables elsewhere.

    SQLite has no ``to_tsvector``, so ``create_all`` against a SQLite engine
    would fail on it (0035 creates it only behind a PostgreSQL guard). The
    index stays in the metadata — autogenerate on PostgreSQL still sees it —
    and is restored after the DDL so later connections are unaffected.
    """
    if connection.dialect.name == "postgresql":
        return
    tsv_index = next(index for index in target.indexes if index.name == _TSV_INDEX_NAME)
    target.indexes.discard(tsv_index)
    target.info.setdefault("_detached_tsv_index", []).append(tsv_index)


@event.listens_for(DocumentChunk.__table__, "after_create")
def _reattach_tsv_index_off_postgresql(target, connection, **_kw) -> None:
    if connection.dialect.name == "postgresql":
        return
    for tsv_index in target.info.get("_detached_tsv_index", []):
        target.indexes.add(tsv_index)
    target.info.pop("_detached_tsv_index", None)


class IngestJob(Base):
    """Durable state for one asynchronous document ingestion attempt."""

    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kb_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_misses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class OnlineSource(Base):
    """A URL-based knowledge source synced into a knowledge base (C4-3).

    Each source maps to a Document whose ``content_sha256`` records the last
    fetched body; re-sync only re-embeds when the hash changes.
    """

    __tablename__ = "online_sources"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'syncing', 'ready', 'failed')",
            name="ck_online_sources_status",
        ),
        CheckConstraint("max_pages > 0 AND max_pages <= 50", name="ck_online_sources_max_pages"),
        CheckConstraint("depth >= 0 AND depth <= 3", name="ck_online_sources_depth"),
        CheckConstraint(
            "sync_interval_minutes IS NULL OR "
            "(sync_interval_minutes >= 5 AND sync_interval_minutes <= 10080)",
            name="ck_online_sources_sync_interval",
        ),
        Index("ix_online_sources_schedule_due", "next_sync_at", "status"),
        UniqueConstraint("kb_id", "url", name="uq_online_sources_kb_url"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    kb_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    document_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    max_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="online_sources")
    document: Mapped[Document | None] = relationship()
