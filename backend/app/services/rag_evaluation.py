"""Deterministic RAG regression metrics and fixed-corpus snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, KnowledgeBase, ModelConfig
from app.schemas.dsl import NodeType, RagConfig, WorkflowDSL
from app.schemas.evaluation import RagExpectation
from app.services.evaluators import EvaluationOutcome

_CITATION_PATTERN = re.compile(r"\[(\d+)]")


async def snapshot_rag_corpus(session: AsyncSession, kb_id: str) -> dict[str, Any]:
    """Capture the exact ready-document identity used by a regression run."""
    knowledge_base = await session.get(KnowledgeBase, kb_id)
    if knowledge_base is None:
        raise ValueError(f"RAG knowledge base not found: {kb_id}")
    embedding_model = await session.get(ModelConfig, knowledge_base.embedding_model_id)
    if embedding_model is None or embedding_model.kind != "embedding":
        raise ValueError(f"RAG embedding model not found: {knowledge_base.embedding_model_id}")
    result = await session.execute(
        select(Document)
        .where(Document.kb_id == kb_id, Document.status == "ready")
        .order_by(Document.id.asc())
    )
    documents = [
        {
            "id": row.id,
            "filename": row.filename,
            "content_sha256": row.content_sha256,
            "chunk_count": row.chunk_count,
        }
        for row in result.scalars()
    ]
    if not documents:
        raise ValueError("RAG evaluation requires at least one ready document")
    identity = {
        "knowledge_base_id": knowledge_base.id,
        "embedding_model_id": knowledge_base.embedding_model_id,
        "embedding_model": {
            "provider": embedding_model.provider,
            "model_name": embedding_model.model_name,
            "base_url": embedding_model.base_url,
            "params": embedding_model.params_json or {},
            "credential_fingerprint": (
                hashlib.sha256(embedding_model.api_key_encrypted.encode("utf-8")).hexdigest()
                if embedding_model.api_key_encrypted
                else None
            ),
        },
        "chunk_size": knowledge_base.chunk_size,
        "chunk_overlap": knowledge_base.chunk_overlap,
        "documents": documents,
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {**identity, "fingerprint": hashlib.sha256(encoded).hexdigest()}


async def prepare_rag_config(
    session: AsyncSession,
    dsl: WorkflowDSL,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve stable RAG/Agent nodes and bind the current corpus fingerprint."""
    prepared = dict(config)
    rag_nodes = [node for node in dsl.nodes if node.type == NodeType.RAG]
    requested_rag = str(prepared.get("rag_node_id") or "")
    if requested_rag:
        rag_node = next((node for node in rag_nodes if node.id == requested_rag), None)
        if rag_node is None:
            raise ValueError(f"RAG node not found in workflow version: {requested_rag}")
    elif len(rag_nodes) == 1:
        rag_node = rag_nodes[0]
    elif not rag_nodes:
        raise ValueError("RAG evaluator requires a workflow RAG node")
    else:
        raise ValueError(
            "RAG evaluator requires rag_node_id when a workflow has multiple RAG nodes"
        )

    rag_config = RagConfig.model_validate(rag_node.config or {})
    if not rag_config.kb_id:
        raise ValueError(f"RAG node '{rag_node.id}' has no knowledge base")
    retrieval_k = int(prepared.get("retrieval_k") or 5)
    if retrieval_k > rag_config.top_k:
        raise ValueError(f"retrieval_k {retrieval_k} exceeds RAG node top_k {rag_config.top_k}")

    agent_nodes = [node for node in dsl.nodes if node.type == NodeType.AGENT]
    requested_citation = str(prepared.get("citation_node_id") or "")
    if requested_citation:
        citation_node = next((node for node in agent_nodes if node.id == requested_citation), None)
        if citation_node is None:
            raise ValueError(
                f"citation Agent node not found in workflow version: {requested_citation}"
            )
    elif len(agent_nodes) == 1:
        citation_node = agent_nodes[0]
    else:
        citation_node = None
    if float(prepared.get("min_citation_coverage") or 0) > 0 and citation_node is None:
        reason = "no Agent node" if not agent_nodes else "multiple Agent nodes"
        raise ValueError(f"citation coverage requires citation_node_id ({reason})")

    prepared["rag_node_id"] = rag_node.id
    prepared["citation_node_id"] = citation_node.id if citation_node is not None else None
    prepared["rag_corpus"] = await snapshot_rag_corpus(session, rag_config.kb_id)
    return prepared


async def assert_rag_corpus_unchanged(
    session: AsyncSession, expected_snapshot: Mapping[str, Any]
) -> None:
    kb_id = str(expected_snapshot.get("knowledge_base_id") or "")
    expected = str(expected_snapshot.get("fingerprint") or "")
    try:
        current = await snapshot_rag_corpus(session, kb_id)
    except ValueError as exc:
        raise ValueError("RAG corpus changed after evaluation was queued") from exc
    if not expected or current["fingerprint"] != expected:
        raise ValueError("RAG corpus changed after evaluation was queued")


def _event_payload(event: Any) -> Mapping[str, Any]:
    payload = getattr(event, "payload_json", None)
    return payload if isinstance(payload, Mapping) else {}


def _retrieval_hits(events: Sequence[Any], rag_node_id: str) -> tuple[str, list[dict[str, Any]]]:
    query = ""
    hits: list[dict[str, Any]] | None = None
    for event in events:
        payload = _event_payload(event)
        if (
            str(getattr(event, "event_type", "")) == "node_streaming"
            and str(getattr(event, "node_id", "")) == rag_node_id
            and payload.get("kind") == "retrieval"
        ):
            citations = payload.get("citations")
            if isinstance(citations, list):
                query = str(payload.get("query") or "")
                hits = [dict(item) for item in citations if isinstance(item, Mapping)]
    if hits is None:
        raise ValueError(f"execution has no retrieval evidence for RAG node '{rag_node_id}'")
    return query, hits


def _citation_evidence(
    events: Sequence[Any], citation_node_id: str | None
) -> tuple[str, list[dict[str, Any]]]:
    if citation_node_id is None:
        return "", []
    answer = ""
    citations: list[dict[str, Any]] = []
    for event in events:
        if str(getattr(event, "event_type", "")) != "node_finished":
            continue
        if citation_node_id and str(getattr(event, "node_id", "")) != citation_node_id:
            continue
        output = _event_payload(event).get("output")
        if not isinstance(output, Mapping):
            continue
        if output.get("_truncated"):
            raise ValueError("citation evidence was truncated")
        if not isinstance(output.get("citations"), list):
            continue
        raw_answer = output.get("text", output.get("output", ""))
        answer = (
            raw_answer
            if isinstance(raw_answer, str)
            else json.dumps(raw_answer, ensure_ascii=False, sort_keys=True)
        )
        citations = [dict(item) for item in output["citations"] if isinstance(item, Mapping)]
    return answer, citations


def _expected_document_ids(expectation: RagExpectation) -> set[str]:
    if expectation.relevant_document_ids:
        return set(expectation.relevant_document_ids)
    return {chunk_id.rsplit(":", 1)[0] for chunk_id in expectation.relevant_chunk_ids}


def evaluate_rag_events(
    events: Sequence[Any],
    *,
    expected: Any,
    config: Mapping[str, Any],
) -> tuple[EvaluationOutcome, dict[str, Any]]:
    """Evaluate ranked retrieval, explicit citations, and answer abstention."""
    expectation = RagExpectation.model_validate(expected)
    rag_node_id = str(config.get("rag_node_id") or "")
    if not rag_node_id:
        raise ValueError("RAG evaluator config is missing rag_node_id")
    query, all_hits = _retrieval_hits(events, rag_node_id)
    retrieval_k = int(config.get("retrieval_k") or 5)
    hits = all_hits[:retrieval_k]
    answer, citations = _citation_evidence(
        events, str(config.get("citation_node_id") or "") or None
    )

    target_kind = "document" if expectation.relevant_document_ids else "chunk"
    targets = set(expectation.relevant_document_ids or expectation.relevant_chunk_ids)
    ranked_targets = [
        str(hit.get("document_id") if target_kind == "document" else hit.get("id")) for hit in hits
    ]
    matched = targets.intersection(ranked_targets)
    recall = len(matched) / len(targets) if targets else None
    first_rank = next(
        (index for index, target in enumerate(ranked_targets, start=1) if target in targets),
        None,
    )
    mrr = 1 / first_rank if first_rank is not None else (0.0 if targets else None)

    labels_in_answer = {f"[{number}]" for number in _CITATION_PATTERN.findall(answer)}
    cited_documents = {
        str(citation.get("document_id") or "")
        for citation in citations
        if str(citation.get("label") or "") in labels_in_answer
    }
    expected_documents = _expected_document_ids(expectation)
    citation_coverage = (
        len(cited_documents.intersection(expected_documents)) / len(expected_documents)
        if expected_documents
        else None
    )

    predicted_no_answer = len(all_hits) == 0
    correct_no_answer = predicted_no_answer == (not expectation.answerable)
    if expectation.answerable:
        required_metrics = [float(recall or 0), float(mrr or 0)]
        citation_threshold = float(config.get("min_citation_coverage") or 0)
        if citation_threshold > 0:
            required_metrics.append(float(citation_coverage or 0))
        passed = (
            float(recall or 0) >= float(config.get("min_recall_at_k", 1.0))
            and float(mrr or 0) >= float(config.get("min_mrr", 1.0))
            and float(citation_coverage or 0) >= citation_threshold
        )
        score = sum(required_metrics) / len(required_metrics)
    else:
        passed = correct_no_answer or not bool(config.get("require_correct_no_answer", True))
        score = 1.0 if correct_no_answer else 0.0

    metrics = {
        "k": retrieval_k,
        "answerable": expectation.answerable,
        "predicted_no_answer": predicted_no_answer,
        "correct_no_answer": correct_no_answer,
        "recall_at_k": recall,
        "mrr": mrr,
        "citation_coverage": citation_coverage,
    }
    actual = {
        "query": query,
        "retrieved": hits,
        "answer_text": answer,
        "cited_document_ids": sorted(cited_documents),
        "rag_metrics": metrics,
        "corpus_fingerprint": (config.get("rag_corpus") or {}).get("fingerprint"),
    }
    message = (
        f"Recall@{retrieval_k}={recall:.3f}, MRR={mrr:.3f}, citations={citation_coverage:.3f}"
        if expectation.answerable
        else (
            "correct no-answer" if correct_no_answer else "unexpected retrieval for no-answer case"
        )
    )
    return EvaluationOutcome(passed=passed, score=score, message=message), actual


def validate_rag_cases(cases: Sequence[Mapping[str, Any]]) -> None:
    for index, case in enumerate(cases, start=1):
        try:
            RagExpectation.model_validate(case.get("expected"))
        except ValueError as exc:
            case_id = str(case.get("id") or index)
            raise ValueError(f"invalid RAG expectation for case '{case_id}': {exc}") from exc


def summarize_rag_metrics(case_results: Sequence[Any]) -> dict[str, Any] | None:
    rows: list[Mapping[str, Any]] = []
    fingerprints: set[str] = set()
    for case in case_results:
        actual = case.actual_json if isinstance(case.actual_json, Mapping) else {}
        metrics = actual.get("rag_metrics")
        if isinstance(metrics, Mapping):
            rows.append(metrics)
            fingerprint = actual.get("corpus_fingerprint")
            if fingerprint:
                fingerprints.add(str(fingerprint))
    if not rows:
        return None

    def average(key: str) -> float | None:
        values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
        return sum(values) / len(values) if values else None

    answerable = [row for row in rows if bool(row.get("answerable"))]
    unanswerable = [row for row in rows if not bool(row.get("answerable"))]
    no_answers = [row for row in rows if bool(row.get("predicted_no_answer"))]
    correct = [row for row in rows if bool(row.get("correct_no_answer"))]
    return {
        "k": max(int(row.get("k") or 0) for row in rows),
        "recall_at_k": average("recall_at_k"),
        "mrr": average("mrr"),
        "citation_coverage": average("citation_coverage"),
        "answerable_cases": len(answerable),
        "unanswerable_cases": len(unanswerable),
        "no_answer_rate": len(no_answers) / len(rows),
        "no_answer_accuracy": len(correct) / len(rows),
        "correct_abstention_rate": (
            sum(bool(row.get("predicted_no_answer")) for row in unanswerable) / len(unanswerable)
            if unanswerable
            else None
        ),
        "false_no_answer_rate": (
            sum(bool(row.get("predicted_no_answer")) for row in answerable) / len(answerable)
            if answerable
            else None
        ),
        "false_answer_rate": (
            sum(not bool(row.get("predicted_no_answer")) for row in unanswerable)
            / len(unanswerable)
            if unanswerable
            else None
        ),
        "corpus_fingerprints": sorted(fingerprints),
    }


__all__ = [
    "assert_rag_corpus_unchanged",
    "evaluate_rag_events",
    "prepare_rag_config",
    "snapshot_rag_corpus",
    "summarize_rag_metrics",
    "validate_rag_cases",
]
