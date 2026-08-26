"""Chunking strategy and preview tests (C4-2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from alembic import command
from app.core.config import BACKEND_DIR, Settings
from app.db.base import create_engine
from app.db.migrations import upgrade_database
from app.main import create_app
from app.rag.loaders import LoadedSection
from app.rag.splitter import preview_chunks, split_sections
from tests.test_rag import _wait_ingest

_MARKDOWN = """\
# Introduction

AgentCanvas compiles a visual canvas into LangGraph. The engine replays durable
SSE events after reconnect. Knowledge documents are chunked for retrieval.

# Operations

Deploy with a canary rollout. Monitor dashboards before promoting. Roll back
quickly when error budgets burn. Queue workers fence leases against split brain.
"""


def _alembic_config(settings: Settings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.effective_database_url)
    return config


def test_window_strategy_keeps_natural_boundaries() -> None:
    chunks = split_sections(
        [LoadedSection(text=_MARKDOWN)],
        chunk_size=160,
        chunk_overlap=20,
        strategy="window",
    )
    assert chunks, "window strategy must produce chunks"
    assert chunks[0].parent_id is None
    assert chunks[0].parent_text is None
    # Indices are dense 0..n-1 and offsets are monotonic.
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    offsets = [chunk.start_char for chunk in chunks]
    assert offsets == sorted(offsets)


def test_recursive_strategy_respects_headings_and_paragraphs() -> None:
    chunks = split_sections(
        [LoadedSection(text=_MARKDOWN)],
        chunk_size=120,
        chunk_overlap=10,
        strategy="recursive",
    )
    assert chunks
    # No chunk should straddle both top-level headings.
    intro_text = " ".join(chunk.text for chunk in chunks if "Introduction" in chunk.text)
    assert "Operations" not in intro_text


def test_heading_strategy_emits_per_section_chunks_with_parent_context() -> None:
    chunks = split_sections(
        [LoadedSection(text=_MARKDOWN)],
        chunk_size=120,
        chunk_overlap=10,
        strategy="heading",
        parent_chunk=True,
    )
    intro_chunks = [chunk for chunk in chunks if "Introduction" in chunk.text]
    ops_chunks = [chunk for chunk in chunks if "Operations" in chunk.text]
    assert intro_chunks and ops_chunks
    # Parent/child capture activates only when a section produces >1 chunk.
    parents = {chunk.parent_id for chunk in chunks if chunk.parent_id is not None}
    if len(intro_chunks) > 1:
        assert all(chunk.parent_id is not None for chunk in intro_chunks)
        assert all(chunk.parent_text for chunk in intro_chunks)
    assert parents, "at least one parent_id must be set when parent_chunk is enabled"


async def test_citation_context_migration_invalidates_legacy_parent_vectors(
    tmp_path: Path,
) -> None:
    settings = Settings(data_dir=tmp_path)
    await asyncio.to_thread(
        command.upgrade,
        _alembic_config(settings),
        "0037_online_sources",
    )
    engine = create_engine(settings)
    timestamp = "2026-08-17 00:00:00"
    try:
        async with engine.begin() as connection:
            for kb_id, strategy, parent_chunk in (
                ("kb-parent", "heading", True),
                ("kb-window", "window", False),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_bases "
                        "(id, name, description, embedding_model_id, chunk_size, "
                        "chunk_overlap, created_at, updated_at, split_strategy, parent_chunk) "
                        "VALUES (:id, :name, '', 'local', 128, 16, :ts, :ts, :strategy, :parent)"
                    ),
                    {
                        "id": kb_id,
                        "name": kb_id,
                        "ts": timestamp,
                        "strategy": strategy,
                        "parent": parent_chunk,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO documents "
                        "(id, kb_id, filename, file_path, mime, size_bytes, content_sha256, "
                        "status, chunk_count, error, created_at, updated_at) "
                        "VALUES (:id, :kb, :filename, :path, 'text/markdown', 10, :sha, "
                        "'ready', 1, NULL, :ts, :ts)"
                    ),
                    {
                        "id": f"doc-{kb_id}",
                        "kb": kb_id,
                        "filename": f"{kb_id}.md",
                        "path": f"uploads/{kb_id}.md",
                        "sha": kb_id.ljust(64, "0"),
                        "ts": timestamp,
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO document_chunks "
                        "(id, kb_id, document_id, chunk_index, filename, page, start_char, "
                        "end_char, text, dimensions, embedding, created_at, updated_at) "
                        "VALUES (:id, :kb, :document, 0, :filename, NULL, 0, 8, 'evidence', "
                        "1, :embedding, :ts, :ts)"
                    ),
                    {
                        "id": f"chunk-{kb_id}",
                        "kb": kb_id,
                        "document": f"doc-{kb_id}",
                        "filename": f"{kb_id}.md",
                        "embedding": b"\x00\x00\x00\x00",
                        "ts": timestamp,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO online_sources "
                    "(id, kb_id, url, document_id, content_sha256, status, created_at, updated_at) "
                    "VALUES ('source-parent', 'kb-parent', 'https://example.com/source', "
                    "'doc-kb-parent', :sha, 'ready', :ts, :ts)"
                ),
                {"sha": "f" * 64, "ts": timestamp},
            )
    finally:
        await engine.dispose()

    await upgrade_database(settings)
    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            documents = {
                row.id: (row.status, row.chunk_count)
                for row in (
                    await connection.execute(
                        text("SELECT id, status, chunk_count FROM documents")
                    )
                )
            }
            chunks = (
                await connection.execute(
                    text("SELECT id, parent_id, parent_text FROM document_chunks")
                )
            ).all()
            source = (
                await connection.execute(
                    text(
                        "SELECT status, content_sha256 FROM online_sources "
                        "WHERE id = 'source-parent'"
                    )
                )
            ).one()
    finally:
        await engine.dispose()

    assert documents["doc-kb-parent"] == ("pending", 0)
    assert documents["doc-kb-window"] == ("ready", 1)
    assert chunks == [("chunk-kb-window", None, None)]
    assert source == ("pending", None)


def test_parent_chunk_requires_heading_strategy() -> None:
    import pytest

    with pytest.raises(ValueError, match="parent_chunk is only supported"):
        split_sections(
            [LoadedSection(text=_MARKDOWN)],
            chunk_size=200,
            chunk_overlap=20,
            strategy="recursive",
            parent_chunk=True,
        )


def test_unknown_strategy_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown split strategy"):
        split_sections(
            [LoadedSection(text="x")],
            chunk_size=200,
            chunk_overlap=20,
            strategy="semantic",  # type: ignore[arg-type]
        )


def test_preview_chunks_is_bounded_and_side_effect_free() -> None:
    text = ("sentence one. " * 50).strip()
    chunks = preview_chunks(
        text,
        chunk_size=80,
        chunk_overlap=10,
        strategy="recursive",
        parent_chunk=False,
        limit=5,
    )
    assert len(chunks) == 5
    assert all(chunk.index < 5 for chunk in chunks)


def test_kb_create_validates_and_persists_chunk_strategy(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        local_embedding_dimensions=64,
    )
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/knowledge-bases",
            json={
                "name": "Recursive KB",
                "chunk_size": 200,
                "chunk_overlap": 20,
                "split_strategy": "recursive",
                "parent_chunk": False,
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["split_strategy"] == "recursive"
        assert body["parent_chunk"] is False

        invalid = client.post(
            "/api/knowledge-bases",
            json={"name": "Bad", "split_strategy": "semantic"},
        )
        assert invalid.status_code == 422

        bad_parent = client.post(
            "/api/knowledge-bases",
            json={
                "name": "Bad parent",
                "split_strategy": "recursive",
                "parent_chunk": True,
            },
        )
        assert bad_parent.status_code == 422

        # Switching to heading + parent_chunk marks documents pending.
        switched = client.put(
            f"/api/knowledge-bases/{body['id']}",
            json={"split_strategy": "heading", "parent_chunk": True},
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["split_strategy"] == "heading"
        assert switched.json()["parent_chunk"] is True


def test_kb_chunk_strategy_change_invalidates_vectors(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        local_embedding_dimensions=64,
        rate_limit_default_requests=1000,
        rate_limit_ingest_requests=1000,
        rate_limit_upload_requests=1000,
        rate_limit_retrieval_requests=1000,
    )
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/knowledge-bases",
            json={
                "name": "Invalidate KB",
                "chunk_size": 200,
                "chunk_overlap": 20,
                "split_strategy": "window",
            },
        )
        kb_id = created.json()["id"]
        upload = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": ("notes.md", _MARKDOWN.encode(), "text/markdown")},
        )
        ingest = _wait_ingest(client, kb_id, upload.json()["id"])
        assert ingest["document"]["status"] == "ready"
        assert ingest["document"]["chunk_count"] > 0

        switched = client.put(
            f"/api/knowledge-bases/{kb_id}",
            json={"split_strategy": "recursive"},
        )
        assert switched.status_code == 200
        documents = client.get(f"/api/knowledge-bases/{kb_id}/documents").json()["items"]
        assert all(document["status"] == "pending" for document in documents)


def test_preview_chunks_endpoint_runs_splitter(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test")
    with TestClient(create_app(settings)) as client:
        # Auth-disabled test app still exposes the endpoint to any caller.
        response = client.post(
            "/api/knowledge-bases/preview-chunks",
            json={
                "text": _MARKDOWN,
                "chunk_size": 128,
                "chunk_overlap": 10,
                "split_strategy": "heading",
                "parent_chunk": True,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["chunk_count"] == len(body["chunks"])
        assert body["chunks"]
        assert any(chunk["parent_id"] is not None for chunk in body["chunks"])

        # Invalid strategy rejected at the contract layer.
        bad = client.post(
            "/api/knowledge-bases/preview-chunks",
            json={
                "text": "x",
                "chunk_size": 200,
                "split_strategy": "semantic",
            },
        )
        assert bad.status_code == 422
