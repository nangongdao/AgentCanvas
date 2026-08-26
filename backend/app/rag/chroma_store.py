"""Embedded Chroma adapter for local single-instance deployments."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from app.rag.keyword import InProcessBm25Index
from app.rag.splitter import TextChunk
from app.rag.vector_store import (
    ExportedDocumentVectors,
    VectorHit,
    valid_query_vector,
    validate_document_vectors,
)


class ChromaVectorStore:
    """Embedded Chroma client for single-host / SQLite deployments."""

    COLLECTION_NAME = "agentcanvas_rag"
    backend_name = "chroma"

    def __init__(self, path: Path, *, max_concurrent: int = 4) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self._client: Any = None
        self._lock = threading.RLock()
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def _to_thread(self, operation: Callable[[], Any]) -> Any:
        async with self._semaphore:
            return await asyncio.to_thread(operation)

    def _collection(self):
        import chromadb

        if self._client is None:
            self._client = chromadb.PersistentClient(path=str(self.path))
        return self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _where(kb_id: str, document_ids: Collection[str] | None) -> dict[str, Any]:
        if document_ids is None:
            return {"kb_id": kb_id}
        return {
            "$and": [
                {"kb_id": kb_id},
                {"document_id": {"$in": sorted(document_ids)}},
            ]
        }

    async def upsert_document(
        self,
        *,
        kb_id: str,
        document_id: str,
        filename: str,
        chunks: list[TextChunk],
        embeddings: list[list[float]],
    ) -> None:
        validate_document_vectors(chunks, embeddings)

        def operation() -> None:
            with self._lock:
                collection = self._collection()
                collection.delete(where={"document_id": document_id})
                if not chunks:
                    return
                metadatas: list[dict[str, Any]] = []
                for chunk in chunks:
                    metadata: dict[str, Any] = {
                        "kb_id": kb_id,
                        "document_id": document_id,
                        "filename": filename,
                        "chunk_index": chunk.index,
                        "start_char": chunk.start_char,
                        "end_char": chunk.end_char,
                    }
                    if chunk.page is not None:
                        metadata["page"] = chunk.page
                    if chunk.parent_id is not None:
                        metadata["parent_id"] = chunk.parent_id
                    if chunk.parent_text is not None:
                        metadata["parent_text"] = chunk.parent_text
                    metadatas.append(metadata)
                collection.upsert(
                    ids=[f"{document_id}:{chunk.index}" for chunk in chunks],
                    embeddings=embeddings,
                    documents=[chunk.text for chunk in chunks],
                    metadatas=metadatas,
                )

        await self._to_thread(operation)

    async def query(
        self,
        *,
        kb_id: str,
        vector: list[float],
        top_k: int,
        document_ids: Collection[str] | None = None,
    ) -> list[VectorHit]:
        if (
            top_k < 1
            or not valid_query_vector(vector)
            or (document_ids is not None and not document_ids)
        ):
            return []

        def operation() -> list[VectorHit]:
            with self._lock:
                collection = self._collection()
                count = int(collection.count())
                if count == 0:
                    return []
                payload = collection.query(
                    query_embeddings=[vector],
                    n_results=min(top_k, count),
                    where=self._where(kb_id, document_ids),
                    include=["documents", "metadatas", "distances"],
                )
            ids = (payload.get("ids") or [[]])[0]
            documents = (payload.get("documents") or [[]])[0]
            metadatas = (payload.get("metadatas") or [[]])[0]
            distances = (payload.get("distances") or [[]])[0]
            hits: list[VectorHit] = []
            for item_id, document_text, metadata, distance in zip(
                ids, documents, metadatas, distances, strict=True
            ):
                meta = metadata or {}
                page_value = meta.get("page")
                hits.append(
                    VectorHit(
                        id=str(item_id),
                        document_id=str(meta.get("document_id") or ""),
                        filename=str(meta.get("filename") or ""),
                        chunk_index=int(meta.get("chunk_index") or 0),
                        page=int(page_value) if page_value is not None else None,
                        text=str(document_text or ""),
                        score=max(0.0, min(1.0, 1.0 - float(distance))),
                        kb_id=str(meta.get("kb_id") or ""),
                        parent_text=(
                            str(meta["parent_text"])
                            if meta.get("parent_text") is not None
                            else None
                        ),
                    )
                )
            return hits

        return await self._to_thread(operation)

    async def query_text(
        self,
        *,
        kb_id: str,
        query_text: str,
        top_k: int,
        document_ids: Collection[str] | None = None,
    ) -> list[VectorHit]:
        """Keyword-rank a knowledge base's chunks (C4-1 hybrid retrieval).

        Embedded Chroma has no server-side text search, so the KB's chunk
        documents are fetched and ranked in process with the shared BM25
        scorer; the local single-instance scale keeps this bounded.
        """
        if (
            top_k < 1
            or not (query_text or "").strip()
            or (document_ids is not None and not document_ids)
        ):
            return []

        def operation() -> list[VectorHit]:
            with self._lock:
                payload = self._collection().get(
                    where=self._where(kb_id, document_ids),
                    include=["documents", "metadatas"],
                )
            item_ids = [str(value) for value in payload.get("ids") or []]
            documents = payload.get("documents") or []
            metadatas = payload.get("metadatas") or []
            index = InProcessBm25Index()
            for item_id, document_text in zip(item_ids, documents, strict=True):
                index.add(item_id, str(document_text or ""))
            scores = index.score(query_text)
            ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:top_k]
            hits: list[VectorHit] = []
            for item_id, score in ordered:
                position = item_ids.index(item_id)
                meta = dict(metadatas[position] or {})
                page_value = meta.get("page")
                hits.append(
                    VectorHit(
                        id=item_id,
                        document_id=str(meta.get("document_id") or ""),
                        filename=str(meta.get("filename") or ""),
                        chunk_index=int(meta.get("chunk_index") or 0),
                        page=int(page_value) if page_value is not None else None,
                        text=str(documents[position] or ""),
                        score=float(score),
                        kb_id=str(meta.get("kb_id") or ""),
                        parent_text=(
                            str(meta["parent_text"])
                            if meta.get("parent_text") is not None
                            else None
                        ),
                    )
                )
            return hits

        return await self._to_thread(operation)

    async def export_document(self, document_id: str) -> ExportedDocumentVectors | None:
        """Read one complete legacy document index for an operator migration."""

        def operation() -> ExportedDocumentVectors | None:
            with self._lock:
                payload = self._collection().get(
                    where={"document_id": document_id},
                    include=["documents", "metadatas", "embeddings"],
                )
            documents = payload.get("documents")
            metadatas = payload.get("metadatas")
            embeddings = payload.get("embeddings")
            if not documents:
                return None
            if metadatas is None or embeddings is None:
                raise ValueError(f"Chroma vectors are incomplete for document {document_id}")
            if not (len(documents) == len(metadatas) == len(embeddings)):
                raise ValueError(f"Chroma vector counts do not match for document {document_id}")

            exported: list[tuple[TextChunk, list[float], dict[str, Any]]] = []
            for content, raw_metadata, raw_embedding in zip(
                documents, metadatas, embeddings, strict=True
            ):
                metadata = dict(raw_metadata or {})
                text_value = str(content or "")
                page_value = metadata.get("page")
                chunk = TextChunk(
                    index=int(metadata.get("chunk_index", 0)),
                    text=text_value,
                    start_char=int(metadata.get("start_char", 0)),
                    end_char=int(metadata.get("end_char", len(text_value))),
                    page=int(page_value) if page_value is not None else None,
                    parent_id=(
                        int(metadata["parent_id"])
                        if metadata.get("parent_id") is not None
                        else None
                    ),
                    parent_text=(
                        str(metadata["parent_text"])
                        if metadata.get("parent_text") is not None
                        else None
                    ),
                )
                exported.append((chunk, [float(value) for value in raw_embedding], metadata))
            exported.sort(key=lambda item: item[0].index)
            chunks = [item[0] for item in exported]
            vectors = [item[1] for item in exported]
            validate_document_vectors(chunks, vectors)

            kb_ids = {str(item[2].get("kb_id") or "") for item in exported}
            filenames = {str(item[2].get("filename") or "") for item in exported}
            if len(kb_ids) != 1 or "" in kb_ids or len(filenames) != 1:
                raise ValueError(f"Chroma metadata is inconsistent for document {document_id}")
            return ExportedDocumentVectors(
                kb_id=kb_ids.pop(),
                document_id=document_id,
                filename=filenames.pop(),
                chunks=chunks,
                embeddings=vectors,
            )

        return await self._to_thread(operation)

    async def delete_document(self, document_id: str) -> None:
        def operation() -> None:
            with self._lock:
                self._collection().delete(where={"document_id": document_id})

        await self._to_thread(operation)

    async def delete_knowledge_base(self, kb_id: str) -> None:
        def operation() -> None:
            with self._lock:
                self._collection().delete(where={"kb_id": kb_id})

        await self._to_thread(operation)

    async def health(self) -> dict[str, Any]:
        """Report readiness without leaking private collection handles."""

        def operation() -> int:
            with self._lock:
                return int(self._collection().count())

        try:
            count = await self._to_thread(operation)
        except Exception as exc:  # noqa: BLE001
            return {
                "state": "unavailable",
                "backend": self.backend_name,
                "error": str(exc)[:200],
            }
        return {"state": "ready", "backend": self.backend_name, "chunk_count": count}

    async def close(self) -> None:
        """Release Chroma/HNSW file handles before volumes or temp dirs move."""

        def operation() -> None:
            with self._lock:
                if self._client is None:
                    return
                self._client.close()
                self._client = None

        await self._to_thread(operation)


__all__ = ["ChromaVectorStore"]
