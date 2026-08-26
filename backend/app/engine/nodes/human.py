"""Human-node executor — LangGraph interrupt() for human-in-the-loop approval.

When a workflow reaches a ``human`` node it calls LangGraph's ``interrupt()``
with the HumanConfig payload (title/instruction/form_schema). That pauses the
graph; the executor loop in ``ExecutionEngine`` catches the resulting
``GraphInterrupt`` transition and surfaces it as a ``workflow_interrupted``
event plus a ``waiting_approval`` execution status. A subsequent resume call
resumes the graph, the interrupt returns the supplied decision, and the node
records the decision into ``node_outputs`` so downstream nodes can route on it.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.schemas.dsl import HumanConfig, NodeSpec
from app.schemas.events import EventType

logger = logging.getLogger(__name__)


@register_node("human")
class HumanNodeExecutor(BaseNodeExecutor):
    config_model = HumanConfig
    # The human node's output is the resume decision (free-form), mirrored to
    # ``output``/``approved``/``decision`` so downstream routing/templating can
    # branch on the boolean or read the raw decision payload.
    output_schema = {
        "type": "object",
        "properties": {
            "output": {"description": "人工审批决策的原始 payload"},
            "approved": {
                "type": "boolean",
                "description": "由决策解析得到的布尔审批结果",
            },
            "decision": {"description": "人工审批决策的原始 payload(与 output 相同)"},
        },
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = HumanConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            request = {
                "title": cfg.title,
                "instruction": cfg.instruction,
                "form_schema": cfg.form_schema,
                "timeout_hours": cfg.timeout_hours,
                "node_id": node.id,
            }

            # ``interrupt`` raises a sentinel that LangGraph catches and persists
            # in the checkpoint. On resume the call returns the resumed value.
            decision: Any = interrupt(request)

            approved = _coerce_decision(decision)
            await ctx.emitter.emit(
                EventType.NODE_FINISHED,
                node_id=node.id,
                payload={"decision": decision, "approved": approved},
            )
            return {
                "node_outputs": {
                    node.id: {
                        "output": decision,
                        "approved": approved,
                        "decision": decision,
                    }
                }
            }

        return run


def _coerce_decision(decision: Any) -> bool:
    """Interpret a resume payload as a boolean approval."""
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, str):
        return decision.strip().lower() in {"approve", "approved", "yes", "true", "ok"}
    if isinstance(decision, dict):
        for key in ("approved", "approve", "decision", "ok"):
            if key in decision:
                return _coerce_decision(decision[key])
    return False


__all__ = ["HumanNodeExecutor"]
