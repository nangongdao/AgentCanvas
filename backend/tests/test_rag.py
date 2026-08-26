"""P4 knowledge upload, ingestion cache, retrieval, and cleanup tests."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from io import BytesIO
from typing import cast

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.core.config import Settings, validate_runtime_settings
from app.main import create_app
from app.rag.storage import DocumentStorage, UploadValidationError


def _upload(filename: str, content: bytes, mime: str = "text/plain") -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": mime}),
    )


def _wait_ingest(client: TestClient, kb_id: str, document_id: str) -> dict:
    accepted = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest"
    )
    assert accepted.status_code == 202, accepted.text
    deadline = time.monotonic() + 10
    state: dict = accepted.json()
    while time.monotonic() < deadline:
        state_response = client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest"
        )
        assert state_response.status_code == 200, state_response.text
        state = state_response.json()
        if state.get("job_status") in {"succeeded", "failed", "cancelled"}:
            return state
        time.sleep(0.02)
    raise AssertionError(f"ingestion did not finish: {state}")


async def test_document_storage_rejects_traversal_format_and_size(tmp_path) -> None:
    storage = DocumentStorage(tmp_path / "uploads", max_bytes=4)

    with pytest.raises(UploadValidationError, match="must not contain a path"):
        await storage.save("kb", _upload("../escape.txt", b"ok"))
    with pytest.raises(UploadValidationError, match="supported formats"):
        await storage.save("kb", _upload("script.exe", b"ok"))
    with pytest.raises(UploadValidationError, match="exceeds"):
        await storage.save("kb", _upload("large.txt", b"12345"))

    assert not list((tmp_path / "uploads").rglob("*"))


def test_invalid_embedding_runtime_limits_fail_fast(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="EMBEDDING_CONCURRENCY"):
        validate_runtime_settings(Settings(data_dir=tmp_path, embedding_concurrency=0))


def test_ingest_returns_durable_job_and_can_be_polled(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", local_embedding_dimensions=64)
    with TestClient(create_app(settings)) as client:
        kb = client.post(
            "/api/knowledge-bases", json={"name": "Async ingest", "chunk_size": 128, "chunk_overlap": 24}
        ).json()
        document = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files={"file": ("async.md", b"durable ingestion job", "text/markdown")},
        ).json()
        exact = client.get(
            f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}"
        )
        assert exact.status_code == 200, exact.text
        assert exact.json()["filename"] == "async.md"
        other_kb = client.post("/api/knowledge-bases", json={"name": "Other KB"}).json()
        assert (
            client.get(
                f"/api/knowledge-bases/{other_kb['id']}/documents/{document['id']}"
            ).status_code
            == 404
        )
        accepted = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}/ingest"
        )
        assert accepted.status_code == 202, accepted.text
        payload = accepted.json()
        assert payload["job_id"]
        assert payload["document"]["status"] == "processing"

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            polled = client.get(
                f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}/ingest"
            )
            assert polled.status_code == 200
            state = polled.json()
            if state["job_status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.02)
        assert state["job_status"] == "succeeded", state
        assert state["document"]["status"] == "ready"


def test_ingest_restart_recovery_is_visible_and_retryable(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", local_embedding_dimensions=64)
    with TestClient(create_app(settings)) as client:
        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Restart recovery", "chunk_size": 128, "chunk_overlap": 24},
        ).json()
        document = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files={"file": ("restart.md", b"recover this ingestion", "text/markdown")},
        ).json()

    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute(
            "UPDATE documents SET status = 'processing', error = NULL WHERE id = ?",
            (document["id"],),
        )
        connection.execute(
            """
            INSERT INTO ingest_jobs (
                id, document_id, kb_id, status, cache_hits, cache_misses,
                error, created_at, updated_at
            ) VALUES (?, ?, ?, 'running', 0, 0, NULL, ?, ?)
            """,
            (
                "restart-job",
                document["id"],
                kb["id"],
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ),
        )
        connection.commit()

    with TestClient(create_app(settings)) as client:
        recovered = client.get(
            f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}/ingest"
        )
        assert recovered.status_code == 200, recovered.text
        state = recovered.json()
        assert state["job_status"] == "failed"
        assert state["document"]["status"] == "failed"
        assert state["document"]["error"] == "backend restarted during document ingestion"

        retried = _wait_ingest(client, kb["id"], document["id"])
        assert retried["job_status"] == "succeeded", retried
        assert retried["document"]["status"] == "ready"


def test_rag_ingestion_cache_retrieval_and_delete(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        local_embedding_dimensions=64,
    )
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/knowledge-bases",
            json={
                "name": "Product handbook",
                "description": "Fixed retrieval corpus",
                "chunk_size": 128,
                "chunk_overlap": 24,
            },
        )
        assert created.status_code == 201, created.text
        kb_id = created.json()["id"]

        malicious = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": ("../escape.txt", b"no", "text/plain")},
        )
        assert malicious.status_code == 422

        empty = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        empty_result = _wait_ingest(client, kb_id, empty.json()["id"])
        assert empty_result["document"]["status"] == "failed"
        assert "no searchable text" in empty_result["document"]["error"]

        broken = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": ("broken.pdf", b"not a pdf", "application/pdf")},
        )
        broken_result = _wait_ingest(client, kb_id, broken.json()["id"])
        assert broken_result["document"]["status"] == "failed"
        assert "could not be parsed" in broken_result["document"]["error"]

        corpus = (
            b"AgentCanvas compiles a visual orchestration canvas into LangGraph. "
            b"The execution timeline replays durable SSE events after reconnect. "
            b"Knowledge documents are chunked and stored in Chroma for retrieval. "
        )
        first = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": ("handbook.md", corpus, "text/markdown")},
        )
        first_id = first.json()["id"]
        first_result = _wait_ingest(client, kb_id, first_id)
        assert first_result["document"]["status"] == "ready"
        assert first_result["document"]["chunk_count"] >= 2
        assert first_result["cache_misses"] == first_result["document"]["chunk_count"]

        duplicate = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files={"file": ("handbook-copy.md", corpus, "text/markdown")},
        )
        duplicate_id = duplicate.json()["id"]
        duplicate_result = _wait_ingest(client, kb_id, duplicate_id)
        assert duplicate_result["document"]["status"] == "ready"
        assert duplicate_result["cache_misses"] == 0
        assert duplicate_result["cache_hits"] == duplicate_result["document"]["chunk_count"]

        retrieval = client.post(
            f"/api/knowledge-bases/{kb_id}/retrieve",
            json={"query": "visual orchestration LangGraph", "top_k": 3, "score_threshold": 0},
        )
        assert retrieval.status_code == 200, retrieval.text
        assert retrieval.json()["hits"]
        assert all(hit["filename"].startswith("handbook") for hit in retrieval.json()["hits"])

        assert (
            client.delete(f"/api/knowledge-bases/{kb_id}/documents/{first_id}").status_code == 204
        )
        assert (
            client.delete(f"/api/knowledge-bases/{kb_id}/documents/{duplicate_id}").status_code
            == 204
        )
        after_delete = client.post(
            f"/api/knowledge-bases/{kb_id}/retrieve",
            json={"query": "visual orchestration LangGraph", "score_threshold": 0},
        )
        assert after_delete.json()["hits"] == []

        documents = client.get(f"/api/knowledge-bases/{kb_id}/documents").json()[
            "items"
        ]
        assert {document["status"] for document in documents} == {"failed"}


def test_knowledge_base_update_invalidation_and_delete_lifecycle(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", local_embedding_dimensions=64)
    with TestClient(create_app(settings)) as client:
        invalid_model = client.post(
            "/api/knowledge-bases",
            json={"name": "Invalid model", "embedding_model_id": "missing-model"},
        )
        assert invalid_model.status_code == 422
        assert client.put("/api/knowledge-bases/missing", json={"name": "Nope"}).status_code == 404
        assert client.post(
            "/api/knowledge-bases/missing/documents",
            files={"file": ("missing.txt", b"content", "text/plain")},
        ).status_code == 404

        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Mutable corpus", "chunk_size": 128, "chunk_overlap": 24},
        ).json()
        document = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files={"file": ("mutable.md", b"content that is indexed", "text/markdown")},
        ).json()
        assert _wait_ingest(client, kb["id"], document["id"])["job_status"] == "succeeded"

        invalid_overlap = client.put(
            f"/api/knowledge-bases/{kb['id']}",
            json={"chunk_size": 64, "chunk_overlap": 64},
        )
        assert invalid_overlap.status_code == 422

        renamed = client.put(
            f"/api/knowledge-bases/{kb['id']}",
            json={"name": "Renamed corpus"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Renamed corpus"

        invalidated = client.put(
            f"/api/knowledge-bases/{kb['id']}",
            json={"chunk_size": 256, "chunk_overlap": 32},
        )
        assert invalidated.status_code == 200, invalidated.text
        documents = client.get(
            f"/api/knowledge-bases/{kb['id']}/documents"
        ).json()["items"]
        assert documents[0]["status"] == "pending"
        assert documents[0]["chunk_count"] == 0
        assert client.post(
            f"/api/knowledge-bases/{kb['id']}/retrieve",
            json={"query": "indexed"},
        ).json()["hits"] == []

        assert client.delete(
            f"/api/knowledge-bases/{kb['id']}/documents/missing"
        ).status_code == 404
        assert client.delete(f"/api/knowledge-bases/{kb['id']}").status_code == 204
        assert client.get(f"/api/knowledge-bases/{kb['id']}").status_code == 404
        assert client.delete(f"/api/knowledge-bases/{kb['id']}").status_code == 404
        assert client.post(
            f"/api/knowledge-bases/{kb['id']}/retrieve",
            json={"query": "missing"},
        ).status_code == 404


def test_delete_knowledge_base_cancels_active_ingest(tmp_path, monkeypatch) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", local_embedding_dimensions=64)
    with TestClient(create_app(settings)) as client:
        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Delete active", "chunk_size": 128, "chunk_overlap": 24},
        ).json()
        document = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files={"file": ("active.md", b"active ingestion", "text/markdown")},
        ).json()
        started = threading.Event()

        async def slow_embed(*_args, **_kwargs):
            started.set()
            await asyncio.sleep(30)

        service = cast(FastAPI, client.app).state.container.rag_service
        monkeypatch.setattr(service.embeddings, "embed_texts", slow_embed)
        accepted = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents/{document['id']}/ingest"
        )
        assert accepted.status_code == 202, accepted.text
        deadline = time.monotonic() + 2
        while not started.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.is_set()

        deleted = client.delete(f"/api/knowledge-bases/{kb['id']}")
        assert deleted.status_code == 204, deleted.text
        assert client.get(f"/api/knowledge-bases/{kb['id']}").status_code == 404


def test_ingest_timeout_moves_document_out_of_processing(tmp_path, monkeypatch) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        ingest_timeout_seconds=0.5,
        local_embedding_dimensions=64,
    )
    with TestClient(create_app(settings)) as client:
        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Timeout corpus", "chunk_size": 128, "chunk_overlap": 24},
        ).json()
        document = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files={"file": ("slow.md", b"content that must be embedded", "text/markdown")},
        ).json()

        async def slow_embed(*_args, **_kwargs):
            await asyncio.sleep(5)

        monkeypatch.setattr(
            cast(FastAPI, client.app).state.container.rag_service.embeddings,
            "embed_texts",
            slow_embed,
        )
        result = _wait_ingest(client, kb["id"], document["id"])

    assert result["job_status"] == "failed"
    assert result["document"]["status"] == "failed"
    assert result["document"]["error"] == "document ingestion cancelled"


def test_fixed_corpus_retrieval_top_one_accuracy(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        local_embedding_dimensions=128,
    )
    corpus = {
        "deployment.md": b"Deployment release procedure: Kubernetes production uses canary rollout and health checks before traffic promotion.",
        "billing.md": b"Billing refund procedure: verify the invoice number and payment receipt before issuing customer credit.",
        "security.md": b"Security incident procedure: page the on-call rotation, isolate credentials, and record the incident timeline.",
    }
    queries = {
        "Kubernetes canary rollout": "deployment.md",
        "refund invoice customer credit": "billing.md",
        "security incident on-call rotation": "security.md",
    }

    with TestClient(create_app(settings)) as client:
        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Evaluation corpus", "chunk_size": 256, "chunk_overlap": 32},
        ).json()
        for filename, content in corpus.items():
            document = client.post(
                f"/api/knowledge-bases/{kb['id']}/documents",
                files={"file": (filename, content, "text/markdown")},
            ).json()
            ingested = _wait_ingest(client, kb["id"], document["id"])
            assert ingested["document"]["status"] == "ready"

        correct = 0
        for query, expected_filename in queries.items():
            response = client.post(
                f"/api/knowledge-bases/{kb['id']}/retrieve",
                json={"query": query, "top_k": 1, "score_threshold": 0},
            )
            hits = response.json()["hits"]
            assert hits
            correct += int(hits[0]["filename"] == expected_filename)

        assert correct / len(queries) == 1.0
