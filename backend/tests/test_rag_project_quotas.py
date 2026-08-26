"""Project ownership, storage, and embedding quota coverage for RAG."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.core.config import Settings
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import ModelConfig, Organization
from app.db.repositories import ProjectRepo
from app.main import create_app
from app.rag.embedder import EmbeddingCoordinator
from app.rag.service import KnowledgeNotFoundError, RagService
from app.rag.storage import DocumentStorage
from app.rag.store import ChromaVectorStore
from app.services.project_quotas import ProjectQuotaExceeded, ProjectQuotaService
from tests.test_tenancy import _login_as, _register, auth_settings


def _upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": "text/plain"}),
    )


async def _rag_service(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, local_embedding_dimensions=32)
    await upgrade_database(settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    secret_box = create_secret_box(settings)
    async with sessions() as session:
        organization = Organization(name="RAG Org", slug="rag-org")
        session.add(organization)
        await session.flush()
        project = await ProjectRepo(session).create(organization.id, "RAG Project", "rag")
        session.add(
            ModelConfig(
                id="default-embedding",
                name="Default embedding",
                provider="openai_compat",
                model_name="local",
                kind="embedding",
                is_default=True,
            )
        )
        await session.commit()
        project_id = project.id
    embeddings = EmbeddingCoordinator(sessions, settings, secret_box)
    service = RagService(
        sessions,
        DocumentStorage(settings.uploads_dir, settings.rag_max_upload_bytes),
        embeddings,
        ChromaVectorStore(settings.chroma_dir),
    )
    return engine, sessions, service, embeddings, project_id, settings


async def test_upload_reserves_exact_bytes_and_delete_releases(tmp_path: Path) -> None:
    engine, sessions, service, _embeddings, project_id, settings = await _rag_service(tmp_path)
    try:
        kb = await service.create_knowledge_base(
            {
                "project_id": project_id,
                "name": "Storage quota",
                "description": "",
                "embedding_model_id": "default-embedding",
                "chunk_size": 128,
                "chunk_overlap": 16,
            }
        )
        async with sessions() as session:
            await ProjectQuotaService(session).configure(project_id, storage_bytes_limit=5)
            await session.commit()

        first = await service.upload_document(kb.id, _upload("first.txt", b"four"))
        with pytest.raises(ProjectQuotaExceeded):
            await service.upload_document(kb.id, _upload("second.txt", b"four"))
        assert len([path for path in settings.uploads_dir.rglob("*") if path.is_file()]) == 1
        async with sessions() as session:
            assert (await ProjectQuotaService(session).snapshot(project_id)).storage_bytes == 4

        await service.delete_document(kb.id, first.id)
        async with sessions() as session:
            assert (await ProjectQuotaService(session).snapshot(project_id)).storage_bytes == 0

        await service.upload_document(kb.id, _upload("one.txt", b"ab"))
        await service.upload_document(kb.id, _upload("two.txt", b"cd"))
        await service.delete_knowledge_base(kb.id)
        async with sessions() as session:
            assert (await ProjectQuotaService(session).snapshot(project_id)).storage_bytes == 0
        assert not [path for path in settings.uploads_dir.rglob("*") if path.is_file()]
    finally:
        await service.shutdown()
        await engine.dispose()


async def test_embedding_charges_utf8_cache_misses_before_provider(
    tmp_path: Path, monkeypatch: Any
) -> None:
    engine, sessions, service, embeddings, project_id, _settings = await _rag_service(tmp_path)

    class FakeProvider:
        model_key = "quota-fake"

        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        async def embed(self, texts) -> list[list[float]]:
            self.calls.append(list(texts))
            return [[1.0, 0.0] for _text in texts]

    provider = FakeProvider()

    async def get_provider(_kb):
        return provider

    monkeypatch.setattr(embeddings, "_provider", get_provider)
    try:
        kb = await service.create_knowledge_base(
            {
                "project_id": project_id,
                "name": "Embedding quota",
                "description": "",
                "embedding_model_id": "default-embedding",
                "chunk_size": 128,
                "chunk_overlap": 16,
            }
        )
        async with sessions() as session:
            await ProjectQuotaService(session).configure(
                project_id,
                monthly_embedding_input_bytes_limit=len("é".encode()),
            )
            await session.commit()

        first = await embeddings.embed_texts(kb, ["é"])
        second = await embeddings.embed_texts(kb, ["é"])
        assert first.cache_misses == 1
        assert second.cache_misses == 0
        assert provider.calls == [["é"]]
        async with sessions() as session:
            assert (
                await ProjectQuotaService(session).snapshot(project_id)
            ).embedding_input_bytes == 2

        with pytest.raises(ProjectQuotaExceeded):
            await embeddings.embed_texts(kb, ["x"])
        assert provider.calls == [["é"]]
    finally:
        await service.shutdown()
        await engine.dispose()


async def test_retrieval_rejects_foreign_project_scope(tmp_path: Path) -> None:
    engine, sessions, service, _embeddings, project_id, _settings = await _rag_service(tmp_path)
    try:
        async with sessions() as session:
            project = await ProjectRepo(session).get(project_id)
            assert project is not None
            foreign = await ProjectRepo(session).create(
                project.organization_id, "Foreign Project", "foreign"
            )
            await session.commit()
            foreign_id = foreign.id
        kb = await service.create_knowledge_base(
            {
                "project_id": project_id,
                "name": "Scoped retrieval",
                "description": "",
                "embedding_model_id": "default-embedding",
                "chunk_size": 128,
                "chunk_overlap": 16,
            }
        )
        with pytest.raises(KnowledgeNotFoundError):
            await service.retrieve(
                kb.id,
                "guess",
                top_k=3,
                score_threshold=0,
                project_id=foreign_id,
            )
        assert (
            await service.retrieve(
                kb.id,
                "allowed",
                top_k=3,
                score_threshold=0,
                project_id=project_id,
            )
        ).hits == []
    finally:
        await service.shutdown()
        await engine.dispose()


def test_knowledge_routes_enforce_project_membership(tmp_path: Path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        first_org = client.post("/api/organizations", json={"name": "First"}).json()
        second_org = client.post("/api/organizations", json={"name": "Second"}).json()
        _register(client, "member@example.com")
        _login_as(client, "admin@example.com")
        client.post(
            f"/api/organizations/{first_org['id']}/members",
            json={"email": "member@example.com", "role": "editor"},
        )
        first_project = client.post(
            f"/api/organizations/{first_org['id']}/projects", json={"name": "First"}
        ).json()
        second_project = client.post(
            f"/api/organizations/{second_org['id']}/projects", json={"name": "Second"}
        ).json()
        first_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "First KB", "project_id": first_project["id"]},
        ).json()
        second_kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Second KB", "project_id": second_project["id"]},
        ).json()

        _login_as(client, "member@example.com")
        allowed = client.get("/api/knowledge-bases", params={"project_id": first_project["id"]})
        assert allowed.status_code == 200
        assert [item["id"] for item in allowed.json()["items"]] == [first_kb["id"]]
        assert (
            client.get(
                "/api/knowledge-bases", params={"project_id": second_project["id"]}
            ).status_code
            == 403
        )
        assert client.get(f"/api/knowledge-bases/{second_kb['id']}").status_code == 403
        assert (
            client.post(
                f"/api/knowledge-bases/{second_kb['id']}/retrieve",
                json={"query": "guess"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/knowledge-bases",
                json={"name": "Denied", "project_id": second_project["id"]},
            ).status_code
            == 403
        )
