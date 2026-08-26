"""D2 Phase 5 fixed-corpus RAG regression acceptance tests."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import create_app
from app.rag.service import RetrievalResult
from app.rag.store import VectorHit
from app.schemas.evaluation import RagExpectation
from app.services.evaluation_reports import build_comparison_summary
from app.services.rag_evaluation import evaluate_rag_events, summarize_rag_metrics
from tests.test_auth import ADMIN_TOKEN, EDITOR_TOKEN, VIEWER_TOKEN, auth_settings, bearer


def _event(event_type: str, node_id: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        event_type=event_type,
        node_id=node_id,
        payload_json=payload,
    )


def _wait_ingest(client: TestClient, kb_id: str, document_id: str) -> dict:
    headers = bearer(EDITOR_TOKEN)
    accepted = client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest",
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    deadline = time.monotonic() + 10
    latest: dict = accepted.json()
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/knowledge-bases/{kb_id}/documents/{document_id}/ingest",
            headers=bearer(VIEWER_TOKEN),
        )
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["job_status"] in {"succeeded", "failed", "cancelled"}:
            return latest
        time.sleep(0.02)
    raise AssertionError(f"ingestion did not finish: {latest}")


def _wait_run(client: TestClient, run_id: str) -> dict:
    deadline = time.monotonic() + 15
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/evaluation-runs/{run_id}", headers=bearer(VIEWER_TOKEN))
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] in {"completed", "failed", "cancelled"}:
            return latest
        time.sleep(0.05)
    raise AssertionError(f"RAG evaluation did not finish: {latest}")


def test_rag_expectation_and_ranked_metric_contract() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        RagExpectation.model_validate({"answerable": True})
    with pytest.raises(ValidationError, match="must not declare"):
        RagExpectation.model_validate({"answerable": False, "relevant_document_ids": ["doc-1"]})

    hits = [
        {
            "id": "doc-1:0",
            "label": "[1]",
            "document_id": "doc-1",
            "filename": "one.md",
            "chunk_index": 0,
            "score": 0.9,
            "text": "one",
        },
        {
            "id": "doc-2:0",
            "label": "[2]",
            "document_id": "doc-2",
            "filename": "two.md",
            "chunk_index": 0,
            "score": 0.8,
            "text": "two",
        },
    ]
    events = [
        _event(
            "node_streaming",
            "retrieve",
            {"kind": "retrieval", "query": "probe", "citations": hits},
        ),
        _event(
            "node_finished",
            "answer",
            {"output": {"text": "Supported by [2].", "citations": hits}},
        ),
    ]
    config = {
        "rag_node_id": "retrieve",
        "citation_node_id": "answer",
        "retrieval_k": 2,
        "min_recall_at_k": 0.5,
        "min_mrr": 0.5,
        "min_citation_coverage": 0.5,
        "require_correct_no_answer": True,
        "rag_corpus": {"fingerprint": "fixed"},
    }
    outcome, actual = evaluate_rag_events(
        events,
        expected={
            "answerable": True,
            "relevant_document_ids": ["doc-2", "doc-3"],
        },
        config=config,
    )
    assert outcome.passed is True
    assert outcome.score == 0.5
    assert actual["rag_metrics"] == {
        "k": 2,
        "answerable": True,
        "predicted_no_answer": False,
        "correct_no_answer": True,
        "recall_at_k": 0.5,
        "mrr": 0.5,
        "citation_coverage": 0.5,
    }
    assert actual["cited_document_ids"] == ["doc-2"]

    no_answer, no_answer_actual = evaluate_rag_events(
        [_event("node_streaming", "retrieve", {"kind": "retrieval", "citations": []})],
        expected={"answerable": False},
        config=config,
    )
    assert no_answer.passed is True
    assert no_answer_actual["rag_metrics"]["correct_no_answer"] is True

    summary = summarize_rag_metrics(
        [SimpleNamespace(actual_json=actual), SimpleNamespace(actual_json=no_answer_actual)]
    )
    assert summary is not None
    assert summary["recall_at_k"] == 0.5
    assert summary["mrr"] == 0.5
    assert summary["citation_coverage"] == 0.5
    assert summary["no_answer_rate"] == 0.5
    assert summary["no_answer_accuracy"] == 1.0
    assert summary["correct_abstention_rate"] == 1.0
    assert summary["false_no_answer_rate"] == 0.0
    assert summary["false_answer_rate"] == 0.0
    assert summary["corpus_fingerprints"] == ["fixed"]


def test_rag_comparison_reports_metric_deltas() -> None:
    run_a = SimpleNamespace(
        id="run-a",
        workflow_version_id="version-a",
        summary_json={
            "pass_rate": 0.5,
            "rag": {
                "recall_at_k": 0.5,
                "mrr": 0.25,
                "citation_coverage": 0.5,
                "no_answer_rate": 0.5,
                "no_answer_accuracy": 0.75,
                "correct_abstention_rate": 0.5,
                "false_no_answer_rate": 0.5,
                "false_answer_rate": 0.25,
            },
        },
    )
    run_b = SimpleNamespace(
        id="run-b",
        workflow_version_id="version-b",
        summary_json={
            "pass_rate": 1.0,
            "rag": {
                "recall_at_k": 1.0,
                "mrr": 0.75,
                "citation_coverage": 0.75,
                "no_answer_rate": 0.25,
                "no_answer_accuracy": 1.0,
                "correct_abstention_rate": 1.0,
                "false_no_answer_rate": 0.0,
                "false_answer_rate": 0.0,
            },
        },
    )

    comparison = build_comparison_summary(run_a, run_b)

    assert comparison["delta_b_minus_a"]["rag"] == {
        "recall_at_k": 0.5,
        "mrr": 0.5,
        "citation_coverage": 0.25,
        "no_answer_rate": -0.25,
        "no_answer_accuracy": 0.25,
        "correct_abstention_rate": 0.5,
        "false_no_answer_rate": -0.5,
        "false_answer_rate": -0.25,
    }
    assert comparison["quality_winner"] == "variant_b"


def test_fixed_corpus_rag_evaluation_over_http(tmp_path, monkeypatch) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        editor = bearer(EDITOR_TOKEN)
        knowledge_base = client.post(
            "/api/knowledge-bases",
            headers=editor,
            json={"name": "RAG regression", "chunk_size": 256, "chunk_overlap": 32},
        ).json()
        document = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            headers=editor,
            files={
                "file": (
                    "deployment.md",
                    b"Canary deployment requires health checks before traffic promotion.",
                    "text/markdown",
                )
            },
        ).json()
        assert (
            _wait_ingest(client, knowledge_base["id"], document["id"])["job_status"] == "succeeded"
        )

        async def deterministic_retrieve(
            kb_id: str,
            query: str,
            *,
            top_k: int,
            score_threshold: float,
        ) -> RetrievalResult:
            del top_k, score_threshold
            assert kb_id == knowledge_base["id"]
            hits = (
                [
                    VectorHit(
                        id=f"{document['id']}:0",
                        document_id=document["id"],
                        filename=document["filename"],
                        chunk_index=0,
                        page=None,
                        text="Canary deployment requires health checks.",
                        score=0.99,
                    )
                ]
                if query == "known"
                else []
            )
            return RetrievalResult(query=query, hits=hits)

        app = cast(FastAPI, client.app)
        monkeypatch.setattr(
            app.state.container.rag_service,
            "retrieve",
            deterministic_retrieve,
        )
        workflow_body = {
            "name": "RAG evaluation target",
            "dsl": {
                "version": "1.0",
                "name": "RAG evaluation target",
                "variables": [{"name": "question", "type": "string", "required": True}],
                "settings": {
                    "max_loop_iterations": 20,
                    "timeout_seconds": 30,
                    "recursion_limit": 50,
                },
                "nodes": [
                    {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                    {
                        "id": "retrieve",
                        "type": "rag",
                        "position": {"x": 200, "y": 0},
                        "config": {
                            "kb_id": knowledge_base["id"],
                            "query": "{{input.question}}",
                            "top_k": 3,
                            "score_threshold": 0,
                            "output_format": "chunks",
                        },
                    },
                    {"id": "end", "type": "end", "position": {"x": 400, "y": 0}},
                ],
                "edges": [
                    {"id": "start-rag", "source": "start", "target": "retrieve"},
                    {"id": "rag-end", "source": "retrieve", "target": "end"},
                ],
            },
        }
        workflow = client.post("/api/workflows", headers=editor, json=workflow_body).json()
        version = client.post(f"/api/workflows/{workflow['id']}/publish", headers=editor).json()
        dataset = client.post(
            "/api/evaluation-datasets",
            headers=editor,
            json={
                "name": "Fixed RAG cases",
                "cases": [
                    {
                        "id": "known",
                        "inputs": {"question": "known"},
                        "expected": {
                            "answerable": True,
                            "relevant_document_ids": [document["id"]],
                        },
                    },
                    {
                        "id": "unknown",
                        "inputs": {"question": "unknown"},
                        "expected": {"answerable": False},
                    },
                ],
            },
        ).json()

        invalid_k = client.post(
            "/api/evaluation-runs",
            headers=editor,
            json={
                "dataset_version_id": dataset["versions"][0]["id"],
                "workflow_version_id": version["id"],
                "evaluator_type": "rag",
                "retrieval_k": 4,
            },
        )
        assert invalid_k.status_code == 409
        assert "exceeds RAG node top_k" in invalid_k.text

        started = client.post(
            "/api/evaluation-runs",
            headers=editor,
            json={
                "dataset_version_id": dataset["versions"][0]["id"],
                "workflow_version_id": version["id"],
                "evaluator_type": "rag",
                "retrieval_k": 1,
                "min_recall_at_k": 1,
                "min_mrr": 1,
                "min_citation_coverage": 0,
            },
        )
        assert started.status_code == 201, started.text
        report = _wait_run(client, started.json()["id"])
        assert report["status"] == "completed", report
        assert report["summary"]["pass_rate"] == 1.0
        assert report["summary"]["rag"] == {
            "k": 1,
            "recall_at_k": 1.0,
            "mrr": 1.0,
            "citation_coverage": 0.0,
            "answerable_cases": 1,
            "unanswerable_cases": 1,
            "no_answer_rate": 0.5,
            "no_answer_accuracy": 1.0,
            "correct_abstention_rate": 1.0,
            "false_no_answer_rate": 0.0,
            "false_answer_rate": 0.0,
            "corpus_fingerprints": [report["evaluator_config"]["rag_corpus"]["fingerprint"]],
        }
        corpus = report["evaluator_config"]["rag_corpus"]
        assert corpus["knowledge_base_id"] == knowledge_base["id"]
        assert corpus["documents"][0]["id"] == document["id"]
        assert len(corpus["fingerprint"]) == 64
        assert [case["actual"]["rag_metrics"]["correct_no_answer"] for case in report["cases"]] == [
            True,
            True,
        ]


def test_embedding_model_mutation_invalidates_dependent_indexes(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        admin = bearer(ADMIN_TOKEN)
        editor = bearer(EDITOR_TOKEN)
        model = client.post(
            "/api/models",
            headers=admin,
            json={
                "id": "regression-embedding",
                "name": "Regression embedding",
                "provider": "openai_compat",
                "model_name": "embedding-v1",
                "kind": "embedding",
            },
        )
        assert model.status_code == 201, model.text
        knowledge_base = client.post(
            "/api/knowledge-bases",
            headers=editor,
            json={
                "name": "Model dependency",
                "embedding_model_id": "regression-embedding",
                "chunk_size": 256,
                "chunk_overlap": 32,
            },
        ).json()
        document = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            headers=editor,
            files={
                "file": (
                    "indexed.md",
                    b"indexed regression content",
                    "text/markdown",
                )
            },
        ).json()
        assert (
            _wait_ingest(client, knowledge_base["id"], document["id"])["job_status"] == "succeeded"
        )

        changed = client.put(
            "/api/models/regression-embedding",
            headers=admin,
            json={"model_name": "embedding-v2"},
        )
        assert changed.status_code == 200, changed.text
        documents = client.get(
            f"/api/knowledge-bases/{knowledge_base['id']}/documents",
            headers=bearer(VIEWER_TOKEN),
        ).json()["items"]
        assert documents[0]["status"] == "pending"
        assert documents[0]["chunk_count"] == 0
        retrieval = client.post(
            f"/api/knowledge-bases/{knowledge_base['id']}/retrieve",
            headers=bearer(VIEWER_TOKEN),
            json={"query": "indexed", "score_threshold": 0},
        )
        assert retrieval.status_code == 200
        assert retrieval.json()["hits"] == []

        wrong_kind = client.put(
            "/api/models/regression-embedding",
            headers=admin,
            json={"kind": "chat"},
        )
        assert wrong_kind.status_code == 409
        assert client.delete("/api/models/regression-embedding", headers=admin).status_code == 409
