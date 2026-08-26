"""Condition node executor — evaluates branches and records the route."""

from __future__ import annotations

from typing import Any

from app.engine.conditions import pick_branch
from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context
from app.schemas.dsl import ConditionConfig, NodeSpec


@register_node("condition")
class ConditionNodeExecutor(BaseNodeExecutor):
    config_model = ConditionConfig
    # Condition nodes record the chosen branch id in both ``output`` and
    # ``branch`` so downstream routing/templating can reference either.
    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string", "description": "命中的分支 id"},
            "branch": {"type": "string", "description": "命中的分支 id(与 output 相同)"},
        },
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = ConditionConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            branch = pick_branch(cfg, template_ctx)
            return {
                "route": branch,
                "node_outputs": {node.id: {"output": branch, "branch": branch}},
            }

        return run
