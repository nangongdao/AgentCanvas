"""Switch node executor — multi-way branch with explicit merge semantics (C2-4)."""

from __future__ import annotations

from typing import Any

from app.engine.conditions import pick_branch
from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context
from app.schemas.dsl import NodeSpec, SwitchConfig


@register_node("switch")
class SwitchNodeExecutor(BaseNodeExecutor):
    """Multi-way routing with a declared fan-in merge strategy.

    Routing reuses ``pick_branch`` (first matching branch wins). The node
    records the chosen branch and the declared ``merge_strategy`` so fan-in
    points and audits can resolve same-key outputs deterministically.
    """

    config_model = SwitchConfig
    # Like condition, plus the declared merge strategy for fan-in auditing.
    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string", "description": "命中的分支 id"},
            "branch": {"type": "string", "description": "命中的分支 id(与 output 相同)"},
            "merge_strategy": {
                "type": "string",
                "description": "汇聚时的变量合并策略",
                "enum": ["last", "first", "error", "collect"],
            },
        },
    }

    def metadata(self) -> dict[str, Any]:
        meta = super().metadata()
        meta["label"] = "Switch"
        meta["description"] = "多路分支节点,显式声明汇聚时的变量合并策略。"
        return meta

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = SwitchConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            branch = pick_branch(cfg, template_ctx)
            return {
                "route": branch,
                "node_outputs": {
                    node.id: {
                        "output": branch,
                        "branch": branch,
                        "merge_strategy": cfg.merge_strategy.value,
                    }
                },
            }

        return run


__all__ = ["SwitchNodeExecutor"]
