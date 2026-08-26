"""Shared vector-store contracts and embedding validation."""

from __future__ import annotations

import math
import struct
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.rag.splitter import TextChunk


@dataclass(frozen=True)
class VectorHit:
    id: str
    document_id: str
    filename: str
    chunk_index: int
    page: int | None
    text: str
    score: float
    kb_id: str = ""
    parent_text: str | None = None


@dataclass(frozen=True)
class ExportedDocumentVectors:
    """One complete document index exported from embedded Chroma."""

    kb_id: str
    document_id: str
    filename: str
    chunks: list[TextChunk]
    embeddings: list[list[float]]


def encode_embedding(vector: Sequence[float]) -> bytes:
    """Pack a float embedding into little-endian binary for SQLite storage."""
    return struct.pack(f"<{len(vector)}f", *vector)


def decode_embedding(payload: bytes, dimensions: int) -> list[float]:
    """Unpack a little-endian float embedding from SQLite storage."""
    expected = dimensions * 4
    if dimensions < 1 or len(payload) != expected:
        raise ValueError("embedding payload has invalid dimensions")
    return list(struct.unpack(f"<{dimensions}f", payload))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return cosine similarity clamped to ``[0, 1]`` for ranking."""
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    score = dot / (math.sqrt(left_norm) * math.sqrt(right_norm))
    return max(0.0, min(1.0, float(score)))


def validate_document_vectors(
    chunks: Sequence[TextChunk], embeddings: Sequence[Sequence[float]]
) -> int | None:
    if len(chunks) != len(embeddings):
        raise ValueError("chunk and embedding counts do not match")
    if not chunks:
        return None

    dimensions = len(embeddings[0])
    if dimensions < 1 or dimensions > 16_000:
        raise ValueError("embedding dimensions must be between 1 and 16000")
    indexes = [chunk.index for chunk in chunks]
    if any(index < 0 for index in indexes) or len(indexes) != len(set(indexes)):
        raise ValueError("chunk indexes must be unique and non-negative")

    for embedding in embeddings:
        if len(embedding) != dimensions:
            raise ValueError("document embeddings must use one dimension")
        values = [float(value) for value in embedding]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embeddings must contain only finite values")
        if not any(value != 0.0 for value in values):
            raise ValueError("embeddings must have a non-zero norm")
    return dimensions


def valid_query_vector(vector: Sequence[float]) -> bool:
    return (
        bool(vector)
        and all(math.isfinite(float(value)) for value in vector)
        and any(float(value) != 0.0 for value in vector)
    )


@runtime_checkable
class VectorStore(Protocol):
    """Shared async surface for RAG chunk indexing and retrieval."""

    backend_name: str

    async def upsert_document(
        self,
        *,
        kb_id: str,
        document_id: str,
        filename: str,
        chunks: list[TextChunk],
        embeddings: list[list[float]],
    ) -> None: ...

    async def query(
        self,
        *,
        kb_id: str,
        vector: list[float],
        top_k: int,
        document_ids: Collection[str] | None = None,
    ) -> list[VectorHit]: ...

    async def query_text(
        self,
        *,
        kb_id: str,
        query_text: str,
        top_k: int,
        document_ids: Collection[str] | None = None,
    ) -> list[VectorHit]: ...

    async def delete_document(self, document_id: str) -> None: ...

    async def delete_knowledge_base(self, kb_id: str) -> None: ...

    async def health(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


__all__ = [
    "ExportedDocumentVectors",
    "VectorHit",
    "VectorStore",
    "cosine_similarity",
    "decode_embedding",
    "encode_embedding",
    "valid_query_vector",
    "validate_document_vectors",
]
