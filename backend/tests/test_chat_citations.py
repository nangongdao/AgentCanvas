"""Message-wide citation identity and coverage semantics."""

from __future__ import annotations

from app.services.chat_citations import citation_reference_counts, merge_citations


def _citation(citation_id: str, label: str, filename: str) -> dict[str, object]:
    return {
        "id": citation_id,
        "label": label,
        "document_id": f"document-{citation_id}",
        "filename": filename,
        "text": f"Evidence from {filename}",
    }


def test_multi_rag_agent_labels_replace_retrieval_local_labels() -> None:
    citations = merge_citations(
        [], {"citations": [_citation("chunk-a", "[1]", "a.md")]}, "retrieve-a"
    )
    citations = merge_citations(
        citations,
        {"citations": [_citation("chunk-b", "[1]", "b.md")]},
        "retrieve-b",
    )

    agent_citations = [
        {**_citation("chunk-a", "[1]", "a.md"), "node_id": "retrieve-a"},
        {**_citation("chunk-b", "[2]", "b.md"), "node_id": "retrieve-b"},
    ]
    citations = merge_citations(
        citations,
        {"output": {"citations": agent_citations}},
        "answer-agent",
    )

    assert [item["id"] for item in citations] == ["chunk-a", "chunk-b"]
    assert [item["label"] for item in citations] == ["[1]", "[2]"]
    assert [item["node_id"] for item in citations] == ["retrieve-a", "retrieve-b"]
    assert citation_reference_counts("Use both sources [1] [2].", citations) == (2, 2)


def test_citation_identity_and_reference_tokens_are_exact() -> None:
    citations = [_citation(f"chunk-{index}", f"[{index}]", f"{index}.md") for index in range(1, 11)]
    citations.append(
        {
            **_citation("chunk-10", "[10]", "10.md"),
            "node_id": "duplicate-retrieval",
        }
    )

    assert citation_reference_counts("Only the tenth source [10] is used.", citations) == (1, 10)
