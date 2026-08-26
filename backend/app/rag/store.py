"""Vector-store factory and stable public imports."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.rag.chroma_store import ChromaVectorStore
from app.rag.sql_store import SqlVectorStore
from app.rag.vector_store import (
    ExportedDocumentVectors,
    VectorHit,
    VectorStore,
    cosine_similarity,
    decode_embedding,
    encode_embedding,
)

logger = logging.getLogger(__name__)


def resolve_vector_backend(settings: Any) -> str:
    """Resolve the effective vector backend from settings and database kind."""
    configured = str(getattr(settings, "vector_backend", "auto") or "auto").strip().lower()
    database_url = str(getattr(settings, "effective_database_url", "") or "")
    is_postgresql = database_url.startswith("postgresql")
    if configured == "pgvector":
        if not is_postgresql:
            raise ValueError("VECTOR_BACKEND=pgvector requires PostgreSQL")
        return configured
    if configured in {"chroma", "sql"}:
        return configured
    if configured not in {"", "auto"}:
        raise ValueError(
            f"VECTOR_BACKEND must be auto, chroma, sql, or pgvector; got {configured!r}"
        )
    return "pgvector" if is_postgresql else "chroma"


def build_vector_store(
    settings: Any,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> VectorStore:
    """Construct the process vector store, mirroring ``build_memory_store``."""
    backend = resolve_vector_backend(settings)
    if backend in {"sql", "pgvector"}:
        if session_factory is None:
            raise ValueError(f"{backend} vector backend requires a session_factory")
        logger.info("using %s vector store backend", backend)
        return SqlVectorStore(
            session_factory,
            max_concurrent=int(getattr(settings, "vector_max_concurrent", 4) or 4),
        )
    chroma_dir = Path(settings.chroma_dir)
    logger.info("using Chroma vector store at %s", chroma_dir)
    return ChromaVectorStore(
        chroma_dir,
        max_concurrent=int(getattr(settings, "chroma_max_concurrent", 4) or 4),
    )


__all__ = [
    "ChromaVectorStore",
    "ExportedDocumentVectors",
    "SqlVectorStore",
    "VectorHit",
    "VectorStore",
    "build_vector_store",
    "cosine_similarity",
    "decode_embedding",
    "encode_embedding",
    "resolve_vector_backend",
]
