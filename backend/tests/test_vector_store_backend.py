"""I1 Phase 7 vector backend selection, health, and meta contracts."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.config import Settings, validate_runtime_settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Document, DocumentChunk, KnowledgeBase
from app.main import create_app
from app.rag.splitter import TextChunk
from app.rag.store import (
    ChromaVectorStore,
    SqlVectorStore,
    build_vector_store,
    resolve_vector_backend,
)
from app.services.migrate_vectors import migrate_chroma_vectors

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="


def test_resolve_vector_backend_auto_and_overrides(tmp_path) -> None:
    assert resolve_vector_backend(Settings(data_dir=tmp_path)) == "chroma"
    assert (
        resolve_vector_backend(
            Settings(
                data_dir=tmp_path,
                database_url="postgresql+asyncpg://agent:agent@localhost:5432/agentcanvas",
            )
        )
        == "pgvector"
    )
    assert resolve_vector_backend(Settings(data_dir=tmp_path, vector_backend="sql")) == "sql"
    assert resolve_vector_backend(Settings(data_dir=tmp_path, vector_backend="chroma")) == "chroma"
    with pytest.raises(ValueError, match="requires PostgreSQL"):
        resolve_vector_backend(Settings(data_dir=tmp_path, vector_backend="pgvector"))
    with pytest.raises(ValueError, match="VECTOR_BACKEND"):
        resolve_vector_backend(Settings(data_dir=tmp_path, vector_backend="qdrant"))


def test_validate_runtime_settings_rejects_unknown_vector_backend(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="VECTOR_BACKEND"):
        validate_runtime_settings(Settings(data_dir=tmp_path, vector_backend="pinecone"))
    with pytest.raises(RuntimeError, match="requires postgresql"):
        validate_runtime_settings(Settings(data_dir=tmp_path, vector_backend="pgvector"))


def test_build_vector_store_defaults_to_chroma(tmp_path) -> None:
    store = build_vector_store(Settings(data_dir=tmp_path, chroma_max_concurrent=2))
    assert isinstance(store, ChromaVectorStore)
    assert store.backend_name == "chroma"


def test_build_vector_store_sql_requires_session_factory(tmp_path) -> None:
    with pytest.raises(ValueError, match="session_factory"):
        build_vector_store(Settings(data_dir=tmp_path, vector_backend="sql"))


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_meta_and_readyz_expose_vector_backend(client: TestClient) -> None:
    meta = client.get("/api/meta")
    assert meta.status_code == 200, meta.text
    body = meta.json()
    assert body["vector_backend"] == "chroma"
    assert body["memory_backend"] in {"sqlite", "none", "redis"}

    ready = client.get("/readyz")
    assert ready.status_code == 200, ready.text
    vector = ready.json()["checks"]["vector_store"]
    assert vector["state"] == "ready"
    assert vector["backend"] == "chroma"


def test_sql_vector_backend_wires_through_app(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key=FERNET_KEY,
        vector_backend="sql",
    )
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container  # type: ignore[attr-defined]
        assert isinstance(container.rag_service.vector_store, SqlVectorStore)
        meta = client.get("/api/meta")
        assert meta.status_code == 200, meta.text
        assert meta.json()["vector_backend"] == "sql"
        ready = client.get("/readyz")
        assert ready.status_code == 200, ready.text
        vector = ready.json()["checks"]["vector_store"]
        assert vector["state"] == "ready"
        assert vector["backend"] == "sql"


async def test_chroma_to_sql_migration_is_strict_and_idempotent(tmp_path) -> None:
    source_path = tmp_path / "legacy-chroma"
    settings = Settings(data_dir=tmp_path / "target", vector_backend="sql")
    await upgrade_database(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                KnowledgeBase(
                    id="kb-migrate",
                    name="Migration",
                    embedding_model_id="embedding-migrate",
                )
            )
            session.add(
                Document(
                    id="doc-migrate",
                    kb_id="kb-migrate",
                    filename="legacy.txt",
                    file_path="kb-migrate/legacy.txt",
                    mime_type="text/plain",
                    size_bytes=20,
                    content_sha256="a" * 64,
                    status="ready",
                    chunk_count=2,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()

    chunks = [
        TextChunk(index=0, text="alpha", start_char=0, end_char=5, page=1),
        TextChunk(index=1, text="beta", start_char=6, end_char=10, page=2),
    ]
    source = ChromaVectorStore(source_path)
    await source.upsert_document(
        kb_id="kb-migrate",
        document_id="doc-migrate",
        filename="legacy.txt",
        chunks=chunks,
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
    )
    await source.close()

    first = await migrate_chroma_vectors(settings, source_path)
    second = await migrate_chroma_vectors(settings, source_path)
    assert first == second
    assert first.documents == 1
    assert first.chunks == 2
    assert first.backend == "sql"

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        peer = SqlVectorStore(factory)
        hits = await peer.query(kb_id="kb-migrate", vector=[0.0, 1.0], top_k=1)
        assert hits and hits[0].text == "beta"
        async with factory() as session:
            session.add(
                Document(
                    id="doc-missing-vectors",
                    kb_id="kb-migrate",
                    filename="missing.txt",
                    file_path="kb-migrate/missing.txt",
                    mime_type="text/plain",
                    size_bytes=7,
                    content_sha256="b" * 64,
                    status="ready",
                    chunk_count=1,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()

    with pytest.raises(RuntimeError, match="has no Chroma vectors"):
        await migrate_chroma_vectors(settings, source_path)

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            count = int(
                (
                    await session.execute(select(func.count()).select_from(DocumentChunk))
                ).scalar_one()
            )
        assert count == 2
    finally:
        await engine.dispose()
