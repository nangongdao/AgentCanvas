"""Shared structured-citation collection and lookup for chat surfaces."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

MAX_MESSAGE_CITATIONS = 100
_CITATION_LABEL = re.compile(r"\[\d+\]")


def merge_citations(
    current: Sequence[Mapping[str, Any]],
    payload: object,
    node_id: object = None,
) -> list[dict[str, Any]]:
    """Merge bounded citations from a retrieval event or node output."""
    if not isinstance(payload, Mapping):
        return [dict(item) for item in current]
    raw = payload.get("citations")
    if not isinstance(raw, list):
        output = payload.get("output")
        raw = output.get("citations") if isinstance(output, Mapping) else None
    if not isinstance(raw, list):
        return [dict(item) for item in current]

    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in current:
        citation = dict(item)
        citation_id = str(citation.get("id") or "")
        if not citation_id:
            continue
        position = positions.get(citation_id)
        if position is None:
            positions[citation_id] = len(merged)
            merged.append(citation)
        else:
            merged[position] = {**merged[position], **citation}

    for value in raw:
        if not isinstance(value, Mapping):
            continue
        raw_citation_id = value.get("id")
        if not raw_citation_id or not value.get("filename") or not value.get("text"):
            continue
        citation = dict(value)
        if node_id and not citation.get("node_id"):
            citation["node_id"] = str(node_id)
        key = str(raw_citation_id)
        position = positions.get(key)
        if position is not None:
            # Agent nodes renumber citations across all selected RAG nodes.
            # Their later output must update the retrieval-local label rather
            # than being discarded as a duplicate.
            merged[position] = {**merged[position], **citation}
        elif len(merged) < MAX_MESSAGE_CITATIONS:
            positions[key] = len(merged)
            merged.append(citation)
    return merged


def find_citation(citations: object, citation_id: str) -> dict[str, Any] | None:
    """Find an exact citation id in one persisted assistant message."""
    if not isinstance(citations, list):
        return None
    for value in citations:
        if isinstance(value, Mapping) and str(value.get("id") or "") == citation_id:
            return dict(value)
    return None


def citation_reference_counts(content: str, citations: object) -> tuple[int, int]:
    """Return ``(referenced, available)`` unique citations for one answer."""
    if not isinstance(citations, list):
        return 0, 0
    referenced_labels = set(_CITATION_LABEL.findall(content))
    seen: set[str] = set()
    available = 0
    referenced = 0
    for value in citations:
        if not isinstance(value, Mapping):
            continue
        citation_id = str(value.get("id") or "")
        label = str(value.get("label") or "")
        if not citation_id or not label:
            continue
        if citation_id in seen:
            continue
        seen.add(citation_id)
        available += 1
        if label in referenced_labels:
            referenced += 1
    return referenced, available


__all__ = [
    "MAX_MESSAGE_CITATIONS",
    "citation_reference_counts",
    "find_citation",
    "merge_citations",
]
