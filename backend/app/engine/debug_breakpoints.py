"""Debug-run breakpoint nodes (C2-7).

A debug run pauses the graph at selected nodes so the editor can inspect and
edit intermediate state before continuing. Because LangGraph replays a node
function that calls ``interrupt()``, a breakpoint cannot be wrapped around a
node with side effects (an agent LLM call or HTTP request would run twice).
Instead the compiler injects a **separate, side-effect-free breakpoint node**
on the linear edge leaving the target node. The target runs once; the
breakpoint node then calls ``interrupt()`` (reusing the human-approval
mechanism) and, on resume, applies any ``state_patch`` the editor supplied
before yielding control to the next node.

Scope (per the C2-7 decision): breakpoints are injected only on plain linear
edges. Condition/switch/supervisor and parallel join-barrier edges keep their
existing routing — those node types are reached by single-stepping through
the linear nodes around them.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from app.engine.nodes import CompileContext, NodeFn
from app.engine.state import WorkflowState
from app.schemas.dsl import NodeSpec, NodeType
from app.schemas.events import EventType
from app.services.execution_inspection import bounded_json_snapshot

logger = logging.getLogger(__name__)

_PREFIX = "__debug_bp_"
# Node types whose exit edges are routed conditionally or via Command and so
# cannot host an injected breakpoint node without rewriting routing logic.
_NON_LINEAR_TYPES: frozenset[NodeType] = frozenset(
    {NodeType.CONDITION, NodeType.SWITCH, NodeType.START, NodeType.END}
)


def breakpoint_node_id(target_id: str) -> str:
    """Return the synthetic breakpoint node id placed after ``target_id``."""
    return f"{_PREFIX}{target_id}"


def is_breakpoint_node_id(node_id: str) -> bool:
    return node_id.startswith(_PREFIX)


def should_break(ctx: CompileContext, node: NodeSpec) -> bool:
    """True if a breakpoint node must be injected after ``node``."""
    if node.type in _NON_LINEAR_TYPES:
        return False
    if ctx.debug_single_step:
        return True
    return node.id in ctx.debug_breakpoints


def build_breakpoint_fn(target_id: str, ctx: CompileContext) -> NodeFn:
    """Build the side-effect-free breakpoint node function.

    It calls ``interrupt`` with the current node outputs (a snapshot for the
    editor) and, on resume, applies a ``state_patch`` dict overwriting keys in
    ``node_outputs`` so the editor can edit intermediate variables before the
    graph continues.
    """

    async def run(state: WorkflowState) -> dict[str, Any]:
        node_outputs = state.get("node_outputs") or {}
        request = {
            "title": f"断点: {target_id}",
            "instruction": "调试暂停 — 查看/编辑中间状态后继续",
            "breakpoint": target_id,
            "node_id": target_id,
            "node_outputs": bounded_json_snapshot(node_outputs),
        }
        decision: Any = interrupt(request)
        patch = None
        if isinstance(decision, dict):
            patch = decision.get("state_patch")
        await ctx.emitter.emit(
            EventType.NODE_STREAMING,
            node_id=target_id,
            payload={
                "kind": "debug_breakpoint_resumed",
                "state_patch_applied": bool(patch),
            },
        )
        if isinstance(patch, dict) and patch:
            return {"node_outputs": dict(patch)}
        return {}

    return run


__all__ = [
    "breakpoint_node_id",
    "build_breakpoint_fn",
    "is_breakpoint_node_id",
    "should_break",
]
