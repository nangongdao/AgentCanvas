"""Workflow runtime state and reducers for LangGraph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge dicts so parallel branches can write node_outputs safely."""
    out: dict[str, Any] = {}
    if left:
        out.update(left)
    if right:
        out.update(right)
    return out


def merge_counts_max(
    left: dict[str, int] | None, right: dict[str, int] | None
) -> dict[str, int]:
    """Keep the max count per key (loop protection across branches)."""
    out: dict[str, int] = dict(left or {})
    for key, value in (right or {}).items():
        out[key] = max(out.get(key, 0), value)
    return out


class WorkflowState(TypedDict, total=False):
    inputs: dict[str, Any]
    node_outputs: Annotated[dict[str, Any], merge_dicts]
    messages: Annotated[list[Any], add_messages]
    loop_counts: Annotated[dict[str, int], merge_counts_max]
    route: str | None
    error: str | None
    final_output: dict[str, Any] | None
