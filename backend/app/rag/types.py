"""Public value objects and errors shared by RAG service modules."""

from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Document
from app.rag.store import VectorHit


class KnowledgeNotFoundError(KeyError):
    """Raised when a knowledge-base resource is not visible in its scope."""


class KnowledgeConflictError(RuntimeError):
    """Raised when an operation conflicts with an active ingestion job."""


class KnowledgeValidationError(ValueError):
    """Raised when a knowledge-base input cannot be processed safely."""


@dataclass(frozen=True)
class IngestResult:
    document: Document
    cache_hits: int = 0
    cache_misses: int = 0
    job_id: str | None = None
    job_status: str | None = None


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    hits: list[VectorHit]
    # C4-1: how the hits were produced and per-hit score components for the
    # retrieval debug view. ``score_detail`` stays None for plain vector mode,
    # keeping legacy consumers unchanged.
    retrieval_mode: str = "vector"
    rerank_applied: bool = False
    score_detail: list[dict[str, float | str | bool | None]] | None = None


__all__ = [
    "IngestResult",
    "KnowledgeConflictError",
    "KnowledgeNotFoundError",
    "KnowledgeValidationError",
    "RetrievalResult",
]
