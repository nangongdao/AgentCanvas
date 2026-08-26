"""Workflow RAG node and Agent citation-context integration tests."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.engine.compiler import WorkflowCompiler
from app.engine.nodes.agent import _knowledge_context
from app.engine.state import WorkflowState
from app.rag.service import RetrievalResult
from app.rag.store import VectorHit
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeChatProvider, FakeEmitter, make_ctx


class FakeRagService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, float]] = []

    async def retrieve(
        self,
        kb_id: str,
        query: str,
        *,
        top_k: int,
        score_threshold: float,
    ) -> RetrievalResult:
        self.calls.append((kb_id, query, top_k, score_threshold))
        return RetrievalResult(
            query=query,
            hits=[
                VectorHit(
                    id="doc-1:0",
                    document_id="doc-1",
                    filename="handbook.md",
                    chunk_index=0,
                    page=None,
                    text="Deploy AgentCanvas with a canary rollout.",
                    score=0.91,
                    kb_id="kb-handbook",
                    parent_text=(
                        "# Deployment\n\nDeploy AgentCanvas with a canary rollout. "
                        "Monitor error budgets before promotion."
                    ),
                )
            ],
        )


def test_agent_context_deduplicates_same_chunk_across_rag_nodes() -> None:
    shared = {
        "id": "shared-chunk",
        "document_id": "document-1",
        "filename": "shared.md",
        "text": "Shared evidence",
    }
    state: WorkflowState = {
        "inputs": {},
        "node_outputs": {
            "retrieve-a": {"citations": [{**shared, "label": "[1]"}]},
            "retrieve-b": {"citations": [{**shared, "label": "[1]"}]},
        },
        "loop_counts": {},
        "messages": [],
        "route": None,
        "error": None,
        "final_output": None,
    }

    context, citations = _knowledge_context(
        state,
        ["retrieve-a", "retrieve-b"],
    )

    assert context.count("Shared evidence") == 1
    assert citations == [
        {
            **shared,
            "label": "[1]",
            "node_id": "retrieve-a",
        }
    ]


async def test_rag_node_injects_structured_citations_into_agent(
    fake_emitter: FakeEmitter,
) -> None:
    provider = FakeChatProvider(["Use a canary rollout [1]."])
    rag_service = FakeRagService()
    ctx = replace(make_ctx(fake_emitter, provider), rag_service=rag_service)
    workflow = WorkflowDSL.model_validate(
        {
            "name": "rag-agent",
            "nodes": [
                {"id": "start", "type": "start"},
                {
                    "id": "retrieve",
                    "type": "rag",
                    "config": {
                        "kb_id": "kb-handbook",
                        "query": "{{input.question}}",
                        "top_k": 3,
                        "score_threshold": 0.25,
                    },
                },
                {
                    "id": "agent",
                    "type": "agent",
                    "config": {
                        "system_prompt": "Answer from approved knowledge.",
                        "user_prompt": "{{input.question}}",
                        "context_nodes": ["retrieve"],
                    },
                },
                {"id": "end", "type": "end"},
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "retrieve"},
                {"id": "e2", "source": "retrieve", "target": "agent"},
                {"id": "e3", "source": "agent", "target": "end"},
            ],
        }
    )
    state: dict[str, Any] = {
        "inputs": {"question": "How should this be deployed?"},
        "node_outputs": {},
        "loop_counts": {},
        "messages": [],
        "route": None,
        "error": None,
        "final_output": None,
    }

    result = await WorkflowCompiler().compile(workflow, ctx).ainvoke(state)

    assert rag_service.calls == [("kb-handbook", "How should this be deployed?", 3, 0.25)]
    rag_output = result["node_outputs"]["retrieve"]
    assert rag_output["output"].startswith("[1]")
    assert rag_output["citations"][0]["filename"] == "handbook.md"
    assert rag_output["citations"][0]["kb_id"] == "kb-handbook"
    assert rag_output["citations"][0]["parent_text"].startswith("# Deployment")
    agent_output = result["node_outputs"]["agent"]
    assert agent_output["citations"][0]["label"] == "[1]"
    assert agent_output["citations"][0]["node_id"] == "retrieve"
    system_message = provider.calls[0][0].content
    assert "<untrusted-data kind=rag" in system_message
    assert "handbook.md" in system_message
    assert "canary rollout" in system_message
    retrieval_events = [
        event
        for event in fake_emitter.of_type(EventType.NODE_STREAMING)
        if event["payload"].get("kind") == "retrieval"
    ]
    assert retrieval_events[0]["payload"]["hit_count"] == 1
