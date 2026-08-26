"""Hybrid retrieval, RRF fusion, and rerank tests (C4-1)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.main import create_app
from app.rag.chroma_store import ChromaVectorStore
from app.rag.embedder import EmbeddingCoordinator
from app.rag.hybrid import HitScoreDetail, component_threshold, rrf_fuse
from app.rag.keyword import InProcessBm25Index, tokenize
from app.rag.reranker import (
    NoopReranker,
    OpenAICompatReranker,
    RerankError,
    apply_rerank,
)
from app.rag.service import RagService
from app.rag.splitter import TextChunk
from app.rag.storage import DocumentStorage
from app.rag.store import VectorHit
from tests.test_rag import _wait_ingest

# ---------------------------------------------------------------- unit level


def test_tokenizer_mixes_latin_words_and_han_bigrams() -> None:
    tokens = tokenize("The Deployment rollout 部署流程")
    assert "deployment" in tokens
    assert "rollout" in tokens
    assert "the" not in tokens  # stopwords removed
    assert "部署" in tokens  # han unigram + bigram coverage
    assert "署流" in tokens  # han bigram across characters


def test_bm25_ranks_keyword_document_first_and_normalizes() -> None:
    index = InProcessBm25Index()
    index.add("a", "alpha beta gamma delta epsilon zeta")
    index.add("b", "rare-token beta gamma delta epsilon zeta")
    index.add("c", "zeta epsilon delta gamma beta alpha")
    scores = index.score("rare-token")
    assert set(scores) == {"b"}
    assert 0.0 < scores["b"] < 1.0
    assert InProcessBm25Index().score("anything") == {}


def _hit(hit_id: str, score: float, text: str = "x") -> VectorHit:
    return VectorHit(
        id=hit_id,
        document_id=f"doc-{hit_id}",
        filename="f.md",
        chunk_index=0,
        page=None,
        text=text,
        score=score,
    )


def test_rrf_fusion_merges_rankings_with_component_detail() -> None:
    vector = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
    keyword = [_hit("b", 0.5), _hit("d", 0.4)]
    fused = rrf_fuse(vector, keyword)
    ids = [hit.id for hit, _ in fused]
    # 'b' appears high in both lists and must win over 'a'.
    assert ids.index("b") < ids.index("a")
    assert set(ids) == {"a", "b", "c", "d"}
    by_id = {hit.id: detail for hit, detail in fused}
    assert by_id["a"].vector_score == 0.9
    assert by_id["a"].keyword_score is None
    assert by_id["b"].keyword_score == 0.5
    assert by_id["d"].vector_score is None
    for hit, _detail in fused:
        assert 0.0 < hit.score <= 1.0


def test_component_threshold_requires_any_retriever_confidence() -> None:
    detail = HitScoreDetail(
        hit_id="x", retrieval_mode="hybrid", vector_score=0.1, keyword_score=0.8
    )
    assert component_threshold(detail, 0.75) is True
    low = HitScoreDetail(hit_id="y", retrieval_mode="hybrid", vector_score=0.1, keyword_score=0.2)
    assert component_threshold(low, 0.75) is False
    empty = HitScoreDetail(hit_id="z", retrieval_mode="hybrid")
    assert component_threshold(empty, 0.0) is False


async def test_openai_compat_reranker_validates_provider_results(monkeypatch) -> None:
    original_client = httpx.AsyncClient
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.invalid/v1/rerank"
        assert request.headers["authorization"] == "Bearer rerank-secret"
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": "0", "relevance_score": 1.4},
                        {"index": 1, "score": -0.25},
                        {"index": 2, "score": 0.4},
                        {"index": 99, "score": 0.9},
                        {"score": 0.5},
                        "malformed",
                    ]
                },
            )
        if len(requests) == 2:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(503, json={"error": "unavailable"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "app.rag.reranker.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    reranker = OpenAICompatReranker(
        model="rerank-v1",
        api_key="rerank-secret",
        base_url="https://example.invalid/v1/",
        timeout_seconds=3,
    )

    assert reranker.name == "openai_compat_rerank:rerank-v1"
    assert await reranker.rerank("query", [], top_k=3) == []
    scores = await reranker.rerank("query", ["x" * 4001, "beta", "gamma"], top_k=99)
    assert scores == [1.0, 0.0, 0.4]
    assert requests[0] == {
        "model": "rerank-v1",
        "query": "query",
        "documents": ["x" * 4000, "beta", "gamma"],
        "top_n": 3,
    }
    assert await reranker.rerank("query", ["alpha"], top_k=1) == [0.0]
    with pytest.raises(RerankError, match="provider call failed"):
        await reranker.rerank("query", ["alpha"], top_k=1)


async def test_apply_rerank_reorders_and_degrades_on_failure() -> None:
    class FixedReranker:
        name = "fixed"

        async def rerank(self, query, documents, *, top_k):  # noqa: ANN001
            return [0.1, 0.9, 0.5][: len(documents)]

    hits = [_hit("a", 0.3), _hit("b", 0.2), _hit("c", 0.1)]
    reranked = await apply_rerank(FixedReranker(), "q", hits, top_k=3)
    assert reranked is not None
    assert [score for _hit, score in reranked] == [0.1, 0.9, 0.5]

    class FailingReranker:
        name = "failing"

        async def rerank(self, query, documents, *, top_k):  # noqa: ANN001
            raise RerankError("provider down")

    assert await apply_rerank(FailingReranker(), "q", hits, top_k=3) is None

    class MismatchedReranker:
        name = "mismatched"

        async def rerank(self, query, documents, *, top_k):  # noqa: ANN001
            return []

    assert await apply_rerank(MismatchedReranker(), "q", hits, top_k=3) is None
    # The disabled default preserves input order.
    noop = await NoopReranker().rerank("q", [h.text for h in hits], top_k=3)
    assert noop == [1.0, 1.0, 1.0]


# -------------------------------------------------------------- store level


async def test_chroma_query_text_keyword_ranks_target_chunk(tmp_path) -> None:
    store = ChromaVectorStore(tmp_path / "chroma")
    try:
        chunks = [TextChunk(index=0, text="canary rollout strategy", start_char=0, end_char=23)]
        embeddings = [[1.0, 0.0, 0.0, 0.0]]
        from app.rag.vector_store import validate_document_vectors

        dimensions = validate_document_vectors(chunks, embeddings)
        assert dimensions == 4
        await store.upsert_document(
            kb_id="kb-1",
            document_id="doc-1",
            filename="a.md",
            chunks=[
                TextChunk(index=0, text="unrelated filler content", start_char=0, end_char=25),
            ],
            embeddings=[[0.0, 1.0, 0.0, 0.0]],
        )
        await store.upsert_document(
            kb_id="kb-1",
            document_id="doc-2",
            filename="b.md",
            chunks=chunks,
            embeddings=embeddings,
        )
        hits = await store.query_text(kb_id="kb-1", query_text="canary rollout", top_k=5)
        assert [hit.document_id for hit in hits] == ["doc-2"]
        assert 0.0 < hits[0].score <= 1.0
        assert hits[0].text == "canary rollout strategy"
        # Empty queries never rank.
        assert await store.query_text(kb_id="kb-1", query_text="  ", top_k=5) == []
    finally:
        await store.close()


async def test_chroma_queries_filter_stale_documents_before_top_k(tmp_path) -> None:
    store = ChromaVectorStore(tmp_path / "chroma-ready-filter")
    try:
        await store.upsert_document(
            kb_id="kb-legacy",
            document_id="doc-stale",
            filename="stale.md",
            chunks=[
                TextChunk(
                    index=0,
                    text="canary rollout legacy stale vector",
                    start_char=0,
                    end_char=34,
                )
            ],
            embeddings=[[1.0, 0.0]],
        )
        await store.upsert_document(
            kb_id="kb-legacy",
            document_id="doc-ready",
            filename="ready.md",
            chunks=[
                TextChunk(
                    index=0,
                    text="canary rollout rebuilt content",
                    start_char=0,
                    end_char=30,
                )
            ],
            embeddings=[[0.8, 0.6]],
        )

        unfiltered = await store.query(kb_id="kb-legacy", vector=[1.0, 0.0], top_k=1)
        assert [hit.document_id for hit in unfiltered] == ["doc-stale"]
        vector_hits = await store.query(
            kb_id="kb-legacy",
            vector=[1.0, 0.0],
            top_k=1,
            document_ids={"doc-ready"},
        )
        keyword_hits = await store.query_text(
            kb_id="kb-legacy",
            query_text="canary rollout",
            top_k=1,
            document_ids={"doc-ready"},
        )
        assert [hit.document_id for hit in vector_hits] == ["doc-ready"]
        assert [hit.document_id for hit in keyword_hits] == ["doc-ready"]
        assert (
            await store.query(kb_id="kb-legacy", vector=[1.0, 0.0], top_k=1, document_ids=set())
            == []
        )
        assert (
            await store.query_text(
                kb_id="kb-legacy", query_text="canary", top_k=1, document_ids=set()
            )
            == []
        )
    finally:
        await store.close()


async def test_service_filters_stale_vectors_before_candidate_limit(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, local_embedding_dimensions=32)
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    secret_box = create_secret_box(settings)
    store = ChromaVectorStore(settings.chroma_dir)
    service = RagService(
        session_factory,
        DocumentStorage(settings.uploads_dir, settings.rag_max_upload_bytes),
        EmbeddingCoordinator(session_factory, settings, secret_box),
        store,
        ingest_timeout_seconds=1,
    )

    from app.db.models import Document, KnowledgeBase, ModelConfig

    try:
        async with session_factory() as session:
            kb = KnowledgeBase(
                id="kb-ready-filter",
                name="Ready filter",
                embedding_model_id="default-embedding",
                retrieval_mode="vector",
            )
            session.add_all(
                [
                    ModelConfig(
                        id="default-embedding",
                        name="Default embedding",
                        provider="openai_compat",
                        model_name="local",
                        kind="embedding",
                        is_default=True,
                    ),
                    kb,
                    Document(
                        id="doc-ready",
                        kb_id=kb.id,
                        filename="ready.md",
                        file_path="kb-ready-filter/ready.md",
                        mime_type="text/markdown",
                        size_bytes=13,
                        content_sha256="a" * 64,
                        status="ready",
                        chunk_count=1,
                    ),
                    Document(
                        id="doc-stale",
                        kb_id=kb.id,
                        filename="stale.md",
                        file_path="kb-ready-filter/stale.md",
                        mime_type="text/markdown",
                        size_bytes=16,
                        content_sha256="b" * 64,
                        status="pending",
                        chunk_count=0,
                    ),
                ]
            )
            await session.commit()
        query = "ready source"
        query_vector = (await service.embeddings.embed_texts(kb, [query])).vectors[0]
        await store.upsert_document(
            kb_id=kb.id,
            document_id="doc-stale",
            filename="stale.md",
            chunks=[
                TextChunk(
                    index=index,
                    text=f"stale legacy chunk {index}",
                    start_char=index * 20,
                    end_char=index * 20 + 19,
                )
                for index in range(4)
            ],
            embeddings=[query_vector for _index in range(4)],
        )
        await store.upsert_document(
            kb_id=kb.id,
            document_id="doc-ready",
            filename="ready.md",
            chunks=[TextChunk(index=0, text="ready source", start_char=0, end_char=12)],
            embeddings=[[-value for value in query_vector]],
        )

        result = await service.retrieve(kb.id, query, top_k=1, score_threshold=0.0)
        assert [hit.document_id for hit in result.hits] == ["doc-ready"]
    finally:
        await service.shutdown()
        await engine.dispose()


# ------------------------------------------------------------ service level


_TRACE_COLLIDERS = (
    "trace_00229",
    "trace_00513",
    "trace_00612",
    "trace_00717",
    "trace_00724",
    "trace_00847",
    "trace_00850",
    "trace_00946",
    "trace_01111",
)


def _corpus() -> list[tuple[str, bytes]]:
    """Fixed lexical-ID corpus that stresses the 64d offline hash embedder."""
    collision_context = " ".join(f"context_{index:02d}" for index in range(20))
    documents = [
        (
            "runbook-00.md",
            f"incident_00 trace_00055 trace_00229 {collision_context}".encode(),
        )
    ]
    documents.extend(
        (
            f"runbook-{index:02d}.md",
            f"incident_{index:02d} {_TRACE_COLLIDERS[index - 1]}".encode(),
        )
        for index in range(1, 10)
    )
    return documents


def _queries() -> list[str]:
    return [f"incident_{index:02d}" for index in range(10)]


def _recall_at_k(results: list[list[str]], targets: list[str], k: int) -> float:
    hits = sum(1 for top, target in zip(results, targets, strict=True) if target in top[:k])
    return hits / len(targets)


def test_hybrid_beats_vector_only_recall_on_fixed_corpus(tmp_path) -> None:
    """C4-1 acceptance: quantified hybrid-vs-vector Recall@5 on a fixed corpus."""
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
        corpus = _corpus()
        queries = _queries()
        targets = [f"runbook-{index:02d}.md" for index in range(len(corpus))]

        per_mode: dict[str, list[list[str]]] = {"vector": [], "hybrid": []}
        for mode in ("vector", "hybrid"):
            created = client.post(
                "/api/knowledge-bases",
                json={
                    "name": f"Hybrid benchmark {mode}",
                    "chunk_size": 400,
                    "chunk_overlap": 40,
                    "retrieval_mode": mode,
                },
            )
            assert created.status_code == 201, created.text
            kb_id = created.json()["id"]
            for filename, body in corpus:
                upload = client.post(
                    f"/api/knowledge-bases/{kb_id}/documents",
                    files={"file": (filename, body, "text/markdown")},
                )
                assert upload.status_code == 201, upload.text
                result = _wait_ingest(client, kb_id, upload.json()["id"])
                assert result["document"]["status"] == "ready", result

            for query in queries:
                retrieval = client.post(
                    f"/api/knowledge-bases/{kb_id}/retrieve",
                    json={"query": query, "top_k": 5, "score_threshold": 0},
                )
                assert retrieval.status_code == 200, retrieval.text
                per_mode[mode].append([hit["filename"] for hit in retrieval.json()["hits"]])

        vector_recall = _recall_at_k(per_mode["vector"], targets, 5)
        hybrid_recall = _recall_at_k(per_mode["hybrid"], targets, 5)
        # The roadmap acceptance gate requires a measured improvement, not a tie.
        # Exact incident IDs expose a deterministic collision in the offline
        # 64-dimensional feature hash; BM25 recovers the missed lexical match.
        assert vector_recall == 0.9, per_mode["vector"]
        assert hybrid_recall == 1.0, per_mode["hybrid"]
        assert hybrid_recall > vector_recall, (vector_recall, hybrid_recall)


def test_hybrid_configuration_and_score_details_flow(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test", local_embedding_dimensions=64)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/knowledge-bases",
            json={
                "name": "Hybrid KB",
                "chunk_size": 200,
                "chunk_overlap": 20,
                "retrieval_mode": "hybrid",
            },
        )
        assert created.status_code == 201, created.text
        kb = created.json()
        assert kb["retrieval_mode"] == "hybrid"
        assert kb["rerank_enabled"] is False
        assert kb["rerank_model_id"] is None

        invalid = client.post(
            "/api/knowledge-bases",
            json={"name": "Bad mode", "retrieval_mode": "fuzzy"},
        )
        assert invalid.status_code == 422

        rerank_without_model = client.post(
            "/api/knowledge-bases",
            json={"name": "Bad rerank", "rerank_enabled": True},
        )
        assert rerank_without_model.status_code == 422

        upload = client.post(
            f"/api/knowledge-bases/{kb['id']}/documents",
            files={"file": ("notes.md", b"canary rollout notes", "text/markdown")},
        )
        assert _wait_ingest(client, kb["id"], upload.json()["id"])["document"]["status"] == "ready"

        retrieval = client.post(
            f"/api/knowledge-bases/{kb['id']}/retrieve",
            json={
                "query": "canary rollout",
                "top_k": 3,
                "score_threshold": 0,
                "include_scores": True,
            },
        )
        assert retrieval.status_code == 200, retrieval.text
        body = retrieval.json()
        assert body["retrieval_mode"] == "hybrid"
        assert body["rerank_applied"] is False
        assert body["score_detail"], body
        detail = body["score_detail"][0]
        assert detail["retrieval_mode"] == "hybrid"
        assert detail["vector_score"] is not None
        assert detail["keyword_score"] is not None
        assert detail["fused_score"] is not None

        # Default responses stay lean: no score detail unless requested.
        lean = client.post(
            f"/api/knowledge-bases/{kb['id']}/retrieve",
            json={"query": "canary rollout", "score_threshold": 0},
        )
        assert lean.json()["score_detail"] is None

        # Switching retrieval mode keeps documents ready (no re-ingestion).
        switched = client.put(
            f"/api/knowledge-bases/{kb['id']}",
            json={"retrieval_mode": "vector"},
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["retrieval_mode"] == "vector"
        documents = client.get(f"/api/knowledge-bases/{kb['id']}/documents").json()["items"]
        assert all(document["status"] == "ready" for document in documents)


async def test_service_rerank_reorders_and_fails_open(tmp_path) -> None:
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

    from app.db.models import Document, KnowledgeBase, ModelConfig

    async with session_factory() as session:
        session.add_all(
            [
                ModelConfig(
                    id="default-embedding",
                    name="Default embedding",
                    provider="openai_compat",
                    model_name="local",
                    kind="embedding",
                    is_default=True,
                ),
                KnowledgeBase(
                    id="kb-hybrid",
                    name="Hybrid",
                    embedding_model_id="default-embedding",
                    retrieval_mode="hybrid",
                    rerank_enabled=True,
                    rerank_model_id="rerank-model",
                ),
                Document(
                    id="doc-1",
                    kb_id="kb-hybrid",
                    filename="doc.md",
                    file_path="kb-hybrid/doc-1.md",
                    mime_type="text/markdown",
                    size_bytes=18,
                    content_sha256="0" * 64,
                    status="ready",
                    chunk_count=1,
                ),
            ]
        )
        await session.commit()

        class StubReranker:
            name = "stub"

            def __init__(self, scores: list[float] | None = None) -> None:
                self.scores = scores

            async def rerank(self, query, documents, *, top_k):  # noqa: ANN001
                if self.scores is None:
                    raise RerankError("provider down")
                return list(self.scores[: len(documents)])

        async def call_rerank() -> Any:
            # Index one chunk so retrieval has candidates to rerank.
            store = service.vector_store
            await store.upsert_document(
                kb_id="kb-hybrid",
                document_id="doc-1",
                filename="doc.md",
                chunks=[TextChunk(index=0, text="alpha canary notes", start_char=0, end_char=18)],
                embeddings=[[1.0] * 32],
            )
            return await service.retrieve(
                "kb-hybrid",
                "canary notes",
                top_k=1,
                score_threshold=0.0,
            )

        original = service._resolve_reranker

        async def resolve_stub(scores: list[float] | None):  # noqa: ANN202
            return StubReranker(scores)

        async def resolve_success(_kb):  # noqa: ANN202
            return await resolve_stub([0.9])

        service._resolve_reranker = resolve_success  # type: ignore[method-assign]
        success = await call_rerank()
        assert success.rerank_applied is True
        assert success.score_detail and success.score_detail[0]["rerank_score"] == 0.9
        assert success.score_detail[0]["rerank_applied"] is True

        async def resolve_failure(_kb):  # noqa: ANN202
            return await resolve_stub(None)

        service._resolve_reranker = resolve_failure  # type: ignore[method-assign]
        failed_open = await call_rerank()
        assert failed_open.rerank_applied is False
        assert failed_open.hits, "fail-open keeps the pre-rerank order"

        service._resolve_reranker = original  # type: ignore[method-assign]
        await service.shutdown()
    await engine.dispose()


def test_dataclass_replace_helper_unchanged() -> None:
    detail = HitScoreDetail(hit_id="h", retrieval_mode="hybrid")
    patched = replace(detail, rerank_score=0.5, rerank_applied=True)
    assert patched.rerank_score == 0.5 and patched.rerank_applied is True
