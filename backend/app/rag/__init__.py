"""Retrieval-augmented generation services."""

from app.rag.embedder import EmbeddingCoordinator, EmbeddingError
from app.rag.service import (
    IngestResult,
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    KnowledgeValidationError,
    RagService,
    RetrievalResult,
)
from app.rag.storage import DocumentStorage, UploadValidationError
from app.rag.store import (
    ChromaVectorStore,
    ExportedDocumentVectors,
    SqlVectorStore,
    VectorHit,
    VectorStore,
    build_vector_store,
    resolve_vector_backend,
)

__all__ = [
    "ChromaVectorStore",
    "ExportedDocumentVectors",
    "DocumentStorage",
    "EmbeddingCoordinator",
    "EmbeddingError",
    "IngestResult",
    "KnowledgeConflictError",
    "KnowledgeNotFoundError",
    "KnowledgeValidationError",
    "RagService",
    "RetrievalResult",
    "SqlVectorStore",
    "UploadValidationError",
    "VectorHit",
    "VectorStore",
    "build_vector_store",
    "resolve_vector_backend",
]
