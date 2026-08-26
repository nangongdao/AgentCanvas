"""RAG component contracts and failure branches."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from dataclasses import replace
from io import BytesIO
from typing import Any, cast

import httpx
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.config import Settings
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Document, IngestJob, KnowledgeBase, ModelConfig
from app.rag.embedder import (
    EmbeddingCoordinator,
    EmbeddingError,
    LocalHashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    _decode_vector,
)
from app.rag.loaders import DocumentLoadError, load_document
from app.rag.service import (
    KnowledgeConflictError,
    KnowledgeNotFoundError,
    KnowledgeValidationError,
    RagService,
)
from app.rag.splitter import LoadedSection, TextChunk, split_sections
from app.rag.storage import DocumentStorage, UploadValidationError
from app.rag.store import (
    ChromaVectorStore,
    SqlVectorStore,
    build_vector_store,
    cosine_similarity,
    resolve_vector_backend,
)
from app.rag.vector_store import decode_embedding, validate_document_vectors


def _upload(filename: str, content: bytes, mime: str) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": mime}),
    )


def test_vector_validation_rejects_malformed_embeddings() -> None:
    with pytest.raises(ValueError, match="invalid dimensions"):
        decode_embedding(b"", 0)
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([0.0], [1.0]) == 0.0

    chunk = TextChunk(index=0, text="alpha", start_char=0, end_char=5)
    with pytest.raises(ValueError, match="dimensions must be"):
        validate_document_vectors([chunk], [[]])

    duplicate_chunks = [chunk, replace(chunk, text="beta")]
    with pytest.raises(ValueError, match="indexes must be unique"):
        validate_document_vectors(duplicate_chunks, [[1.0], [1.0]])


async def test_local_hash_embedding_validation_and_empty_features() -> None:
    with pytest.raises(EmbeddingError, match="at least 32"):
        LocalHashEmbeddingProvider(31)

    provider = LocalHashEmbeddingProvider(32)
    vectors = await provider.embed(["", "知识库检索"])
    assert len(vectors) == 2
    assert all(len(vector) == 32 for vector in vectors)
    assert math.isclose(sum(value * value for value in vectors[1]), 1.0)


async def test_openai_embedding_provider_validates_http_and_payload(
    monkeypatch,
) -> None:
    original_client = httpx.AsyncClient
    seen: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if payload["model"] == "http-error":
            return httpx.Response(503, json={"error": "unavailable"})
        if payload["model"] == "invalid":
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.rag.embedder.httpx.AsyncClient",
        lambda **_kwargs: original_client(transport=transport),
    )

    provider = OpenAIEmbeddingProvider(
        model="valid",
        api_key="test-key",
        base_url="https://example.invalid/v1/",
        timeout_seconds=3,
    )
    assert provider.model_key.endswith(":valid")
    assert await provider.embed(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert seen[-1] == {"model": "valid", "input": ["first", "second"]}

    invalid = OpenAIEmbeddingProvider(
        model="invalid",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        timeout_seconds=3,
    )
    with pytest.raises(EmbeddingError, match="invalid response"):
        await invalid.embed(["first", "second"])

    failed = OpenAIEmbeddingProvider(
        model="http-error",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        timeout_seconds=3,
    )
    with pytest.raises(EmbeddingError, match="request failed"):
        await failed.embed(["first"])


async def test_embedding_coordinator_provider_and_vector_failures(tmp_path, monkeypatch) -> None:
    settings = Settings(
        data_dir=tmp_path,
        local_embedding_dimensions=32,
        embedding_timeout_seconds=1,
    )
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    secret_box = create_secret_box(settings)
    coordinator = EmbeddingCoordinator(session_factory, settings, secret_box)
    try:
        async with session_factory() as session:
            session.add_all(
                [
                    ModelConfig(
                        id="chat-only",
                        name="Chat only",
                        provider="openai_compat",
                        model_name="chat-model",
                        kind="chat",
                    ),
                    ModelConfig(
                        id="unsupported",
                        name="Unsupported",
                        provider="other",
                        model_name="embed-model",
                        api_key_encrypted=secret_box.encrypt("secret"),
                        kind="embedding",
                    ),
                    ModelConfig(
                        id="remote",
                        name="Remote",
                        provider="openai_compat",
                        model_name="embed-model",
                        base_url="https://example.invalid/v1",
                        api_key_encrypted=secret_box.encrypt("secret"),
                        kind="embedding",
                    ),
                ]
            )
            await session.commit()

        with pytest.raises(EmbeddingError, match="not found"):
            await coordinator._provider(  # noqa: SLF001
                KnowledgeBase(embedding_model_id="missing")
            )
        with pytest.raises(EmbeddingError, match="not an embedding"):
            await coordinator._provider(  # noqa: SLF001
                KnowledgeBase(embedding_model_id="chat-only")
            )
        with pytest.raises(EmbeddingError, match="not supported"):
            await coordinator._provider(  # noqa: SLF001
                KnowledgeBase(embedding_model_id="unsupported")
            )
        assert isinstance(
            await coordinator._provider(  # noqa: SLF001
                KnowledgeBase(embedding_model_id="remote")
            ),
            OpenAIEmbeddingProvider,
        )
        assert (await coordinator.embed_texts(KnowledgeBase(), [])).model_key == "none"

        class FakeProvider:
            model_key = "fake"

            def __init__(self, mode: str) -> None:
                self.mode = mode

            async def embed(self, texts: Sequence[str]) -> list[list[float]]:
                if self.mode == "short":
                    return []
                if self.mode == "nonfinite":
                    return [[float("nan")]] * len(texts)
                await asyncio.sleep(1)
                return [[1.0]] * len(texts)

        for mode, message in (
            ("short", "wrong vector count"),
            ("nonfinite", "non-finite"),
        ):

            async def provider(_kb: KnowledgeBase, selected: str = mode) -> FakeProvider:
                return FakeProvider(selected)

            monkeypatch.setattr(coordinator, "_provider", provider)
            with pytest.raises(EmbeddingError, match=message):
                await coordinator.embed_texts(
                    KnowledgeBase(embedding_model_id="remote"), [f"value-{mode}"]
                )

        timeout_coordinator = EmbeddingCoordinator(
            session_factory,
            replace(settings, embedding_timeout_seconds=cast(int, 0.01)),
            secret_box,
        )

        async def timeout_provider(_kb: KnowledgeBase) -> FakeProvider:
            return FakeProvider("timeout")

        monkeypatch.setattr(timeout_coordinator, "_provider", timeout_provider)
        with pytest.raises(EmbeddingError, match="timed out"):
            await timeout_coordinator.embed_texts(
                KnowledgeBase(embedding_model_id="remote"), ["value-timeout"]
            )

        with pytest.raises(EmbeddingError, match="invalid dimensions"):
            _decode_vector(b"bad", 2)
    finally:
        await engine.dispose()


def test_loader_splitter_and_storage_reject_edge_cases(tmp_path, monkeypatch) -> None:
    invalid_utf8 = tmp_path / "invalid.txt"
    invalid_utf8.write_bytes(b"\xff\xfe\xfd")
    with pytest.raises(DocumentLoadError, match="UTF-8"):
        load_document(invalid_utf8, "invalid.txt")

    blank = tmp_path / "blank.md"
    blank.write_text("   \n", encoding="utf-8")
    with pytest.raises(DocumentLoadError, match="no searchable"):
        load_document(blank, "blank.md")
    with pytest.raises(DocumentLoadError, match="unsupported"):
        load_document(blank, "blank.docx")

    class EncryptedPdf:
        is_encrypted = True
        pages: list[Any] = []

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def decrypt(self, _password: str) -> int:
            return 0

    monkeypatch.setattr("app.rag.loaders.PdfReader", EncryptedPdf)
    with pytest.raises(DocumentLoadError, match="encrypted PDF"):
        load_document(blank, "encrypted.pdf")

    with pytest.raises(ValueError, match="positive"):
        split_sections([], chunk_size=0, chunk_overlap=0)
    with pytest.raises(ValueError, match="non-negative"):
        split_sections([], chunk_size=10, chunk_overlap=10)
    chunks = split_sections(
        [LoadedSection("   First paragraph. Second paragraph.   ", page=2)],
        chunk_size=18,
        chunk_overlap=4,
    )
    assert chunks and all(chunk.page == 2 for chunk in chunks)

    storage = DocumentStorage(tmp_path / "uploads", max_bytes=100)
    with pytest.raises(UploadValidationError, match="valid filename"):
        asyncio.run(storage.save("kb", _upload("..", b"x", "text/plain")))
    with pytest.raises(UploadValidationError, match="content type"):
        asyncio.run(storage.save("kb", _upload("file.txt", b"x", "image/png")))
    with pytest.raises(UploadValidationError, match="escapes"):
        storage.resolve("../escape.txt")
    with pytest.raises(FileNotFoundError, match="missing"):
        storage.resolve("kb/missing.txt")
    asyncio.run(storage.delete("kb/missing.txt"))


async def test_vector_store_empty_and_shape_contracts(tmp_path) -> None:
    with pytest.raises(ValueError, match="positive"):
        ChromaVectorStore(tmp_path / "invalid", max_concurrent=0)
    store = ChromaVectorStore(tmp_path / "chroma")
    assert store.backend_name == "chroma"
    chunk = TextChunk(
        index=0,
        text="hello",
        start_char=0,
        end_char=5,
        page=None,
        parent_id=7,
        parent_text="Heading\n\nhello from the wider source section",
    )
    with pytest.raises(ValueError, match="counts do not match"):
        await store.upsert_document(
            kb_id="kb",
            document_id="doc",
            filename="doc.txt",
            chunks=[chunk],
            embeddings=[],
        )
    await store.upsert_document(
        kb_id="kb",
        document_id="doc",
        filename="doc.txt",
        chunks=[],
        embeddings=[],
    )
    assert await store.query(kb_id="kb", vector=[1.0, 0.0], top_k=3) == []
    await store.upsert_document(
        kb_id="kb",
        document_id="doc",
        filename="doc.txt",
        chunks=[chunk],
        embeddings=[[1.0, 0.0]],
    )
    hits = await store.query(kb_id="kb", vector=[1.0, 0.0], top_k=3)
    assert hits[0].kb_id == "kb"
    assert hits[0].parent_text == "Heading\n\nhello from the wider source section"
    health = await store.health()
    assert health["state"] == "ready"
    assert health["backend"] == "chroma"
    await store.delete_document("doc")
    await store.delete_knowledge_base("kb")
    await store.close()
    await store.close()


async def test_sql_vector_store_round_trip_and_factory(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, vector_backend="sql")
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        assert resolve_vector_backend(Settings(data_dir=tmp_path)) == "chroma"
        assert (
            resolve_vector_backend(
                Settings(
                    data_dir=tmp_path,
                    database_url="postgresql+asyncpg://user:pass@localhost/db",
                )
            )
            == "pgvector"
        )
        assert resolve_vector_backend(Settings(data_dir=tmp_path, vector_backend="sql")) == "sql"
        store = build_vector_store(settings, session_factory)
        assert isinstance(store, SqlVectorStore)
        assert store.backend_name == "sql"

        async with session_factory() as session:
            session.add(
                KnowledgeBase(
                    id="kb-sql",
                    name="SQL vectors",
                    embedding_model_id="embedding-sql",
                )
            )
            session.add_all(
                [
                    Document(
                        id="doc-sql",
                        kb_id="kb-sql",
                        filename="animals.txt",
                        file_path="kb-sql/animals.txt",
                        mime_type="text/plain",
                        size_bytes=20,
                        content_sha256="a" * 64,
                        status="ready",
                        chunk_count=2,
                    ),
                    Document(
                        id="doc-sql-stale",
                        kb_id="kb-sql",
                        filename="legacy.txt",
                        file_path="kb-sql/legacy.txt",
                        mime_type="text/plain",
                        size_bytes=24,
                        content_sha256="b" * 64,
                        status="pending",
                        chunk_count=0,
                    ),
                ]
            )
            await session.commit()

        chunks = [
            TextChunk(
                index=0,
                text="alpha cats",
                start_char=0,
                end_char=10,
                page=1,
                parent_id=3,
                parent_text="Animals\n\nalpha cats and their wider habitat",
            ),
            TextChunk(index=1, text="beta dogs", start_char=11, end_char=20, page=2),
        ]
        embeddings = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        await store.upsert_document(
            kb_id="kb-sql",
            document_id="doc-sql",
            filename="animals.txt",
            chunks=chunks,
            embeddings=embeddings,
        )
        hits = await store.query(kb_id="kb-sql", vector=[1.0, 0.0, 0.0], top_k=2)
        assert hits and hits[0].text == "alpha cats"
        assert hits[0].score == pytest.approx(1.0)
        assert hits[0].document_id == "doc-sql"
        assert hits[0].kb_id == "kb-sql"
        assert hits[0].parent_text == "Animals\n\nalpha cats and their wider habitat"
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

        await store.upsert_document(
            kb_id="kb-sql",
            document_id="doc-sql-stale",
            filename="legacy.txt",
            chunks=[
                TextChunk(
                    index=0,
                    text="alpha cats legacy stale vector",
                    start_char=0,
                    end_char=30,
                )
            ],
            embeddings=[[0.99, 0.1, 0.0]],
        )
        filtered_vector = await store.query(
            kb_id="kb-sql",
            vector=[0.99, 0.1, 0.0],
            top_k=1,
            document_ids={"doc-sql"},
        )
        filtered_text = await store.query_text(
            kb_id="kb-sql",
            query_text="alpha cats",
            top_k=1,
            document_ids={"doc-sql"},
        )
        assert [hit.document_id for hit in filtered_vector] == ["doc-sql"]
        assert [hit.document_id for hit in filtered_text] == ["doc-sql"]
        assert (
            await store.query(kb_id="kb-sql", vector=[1.0, 0.0, 0.0], top_k=1, document_ids=set())
            == []
        )
        assert (
            await store.query_text(kb_id="kb-sql", query_text="alpha", top_k=1, document_ids=set())
            == []
        )

        with pytest.raises(ValueError, match="one dimension"):
            await store.upsert_document(
                kb_id="kb-sql",
                document_id="doc-sql",
                filename="animals.txt",
                chunks=chunks,
                embeddings=[[1.0, 0.0], [0.0, 1.0, 0.0]],
            )
        with pytest.raises(ValueError, match="finite"):
            await store.upsert_document(
                kb_id="kb-sql",
                document_id="doc-sql",
                filename="animals.txt",
                chunks=chunks,
                embeddings=[[float("nan"), 1.0], [1.0, 0.0]],
            )
        with pytest.raises(ValueError, match="non-zero norm"):
            await store.upsert_document(
                kb_id="kb-sql",
                document_id="doc-sql",
                filename="animals.txt",
                chunks=chunks,
                embeddings=[[0.0, 0.0], [1.0, 0.0]],
            )
        assert (await store.query(kb_id="kb-sql", vector=[1.0, 0.0, 0.0], top_k=1))[
            0
        ].text == "alpha cats"

        # Second store instance must observe the shared SQL rows (multi-instance safe).
        peer = SqlVectorStore(session_factory)
        peer_hits = await peer.query(kb_id="kb-sql", vector=[0.0, 1.0, 0.0], top_k=1)
        assert peer_hits and peer_hits[0].text == "beta dogs"

        health = await store.health()
        assert health["state"] == "ready"
        assert health["backend"] == "sql"
        assert health["chunk_count"] >= 2

        await store.delete_document("doc-sql")
        assert (
            await store.query(
                kb_id="kb-sql",
                vector=[1.0, 0.0, 0.0],
                top_k=2,
                document_ids={"doc-sql"},
            )
            == []
        )
        await store.upsert_document(
            kb_id="kb-sql",
            document_id="doc-sql",
            filename="animals.txt",
            chunks=chunks,
            embeddings=embeddings,
        )
        await store.delete_knowledge_base("kb-sql")
        assert await peer.query(kb_id="kb-sql", vector=[1.0, 0.0, 0.0], top_k=2) == []
        await store.close()
    finally:
        await engine.dispose()


async def test_rag_service_direct_state_machine_branches(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, local_embedding_dimensions=32)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    secret_box = create_secret_box(settings)
    service = RagService(
        session_factory,
        DocumentStorage(settings.uploads_dir, settings.rag_max_upload_bytes),
        EmbeddingCoordinator(session_factory, settings, secret_box),
        ChromaVectorStore(settings.chroma_dir),
        ingest_timeout_seconds=1,
    )
    try:
        async with session_factory() as session:
            session.add_all(
                [
                    ModelConfig(
                        id="embedding-a",
                        name="Embedding A",
                        provider="openai_compat",
                        model_name="local-a",
                        kind="embedding",
                    ),
                    ModelConfig(
                        id="embedding-b",
                        name="Embedding B",
                        provider="openai_compat",
                        model_name="local-b",
                        kind="embedding",
                    ),
                    ModelConfig(
                        id="chat-model",
                        name="Chat",
                        provider="openai_compat",
                        model_name="chat",
                        kind="chat",
                    ),
                ]
            )
            await session.commit()

        with pytest.raises(KnowledgeValidationError, match="not found"):
            await service.validate_embedding_model("missing")
        with pytest.raises(KnowledgeValidationError, match="not found"):
            await service.validate_embedding_model("chat-model")
        assert (await service.validate_embedding_model("embedding-a")).id == "embedding-a"

        kb = await service.create_knowledge_base(
            {
                "name": "Direct service",
                "description": "",
                "embedding_model_id": "embedding-a",
                "chunk_size": 128,
                "chunk_overlap": 16,
            }
        )
        with pytest.raises(KnowledgeNotFoundError):
            await service.update_knowledge_base("missing", {"name": "Nope"})
        with pytest.raises(KnowledgeValidationError, match="chunk_overlap"):
            await service.update_knowledge_base(kb.id, {"chunk_size": 128, "chunk_overlap": 128})
        renamed = await service.update_knowledge_base(kb.id, {"name": "Renamed"})
        assert renamed.name == "Renamed"
        changed = await service.update_knowledge_base(kb.id, {"embedding_model_id": "embedding-b"})
        assert changed.embedding_model_id == "embedding-b"

        with pytest.raises(KnowledgeNotFoundError):
            await service.upload_document(
                "missing", _upload("missing.txt", b"content", "text/plain")
            )
        document = await service.upload_document(
            kb.id, _upload("direct.txt", b"direct service content", "text/plain")
        )
        no_job = await service.get_ingest_job(kb.id, document.id)
        assert no_job.job_id is None
        with pytest.raises(KnowledgeNotFoundError):
            await service.get_ingest_job(kb.id, "missing")
        with pytest.raises(KnowledgeNotFoundError):
            await service.start_ingest(kb.id, "missing")
        with pytest.raises(KnowledgeNotFoundError):
            await service.ingest_document(kb.id, "missing")
        with pytest.raises(KnowledgeNotFoundError):
            await service._finish_ingest(  # noqa: SLF001
                "missing", status="failed", chunk_count=0, error="missing"
            )
        await service._update_job("missing", status="failed")  # noqa: SLF001

        async with session_factory() as session:
            row = await session.get(Document, document.id)
            assert row is not None
            row.status = "processing"
            job = IngestJob(
                id="direct-job",
                document_id=document.id,
                kb_id=kb.id,
                status="running",
                cache_hits=2,
                cache_misses=3,
            )
            session.add(job)
            await session.commit()

        active = await service.start_ingest(kb.id, document.id)
        assert active.job_status == "running"
        async with session_factory() as session:
            loaded_job = await session.get(IngestJob, "direct-job")
            assert loaded_job is not None
            loaded_job.status = "failed"
            await session.commit()

        latest = await service.start_ingest(kb.id, document.id)
        assert latest.job_status == "failed"
        async with session_factory() as session:
            loaded_job = await session.get(IngestJob, "direct-job")
            assert loaded_job is not None
            await session.delete(loaded_job)
            await session.commit()
        with pytest.raises(KnowledgeConflictError, match="already running"):
            await service.start_ingest(kb.id, document.id)

        assert (await service.retrieve(kb.id, "nothing", top_k=3, score_threshold=0)).hits == []
        with pytest.raises(KnowledgeNotFoundError):
            await service.retrieve("missing", "nothing", top_k=3, score_threshold=0)
        with pytest.raises(KnowledgeNotFoundError):
            await service.delete_document(kb.id, "missing")
        with pytest.raises(KnowledgeNotFoundError):
            await service.delete_knowledge_base("missing")
        assert await service.recover_processing_documents() == 1
        await service.delete_document(kb.id, document.id)
        await service.delete_knowledge_base(kb.id)
        await service.shutdown()
    finally:
        await engine.dispose()
