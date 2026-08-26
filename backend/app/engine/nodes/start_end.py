"""Start and End node executors."""

from __future__ import annotations

from typing import Any

from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_value
from app.schemas.dsl import EndConfig, NodeSpec, StartConfig


@register_node("start")
class StartNodeExecutor(BaseNodeExecutor):
    config_model = StartConfig
    # Start passes the workflow inputs through verbatim; both ``output`` and
    # every top-level input key are exposed so downstream templates can reach
    # them via ``{{nodes.start.output.x}}`` or ``{{nodes.start.x}}``.
    output_schema = {
        "type": "object",
        "description": "工作流输入原样透传:output 为输入对象,其余键与输入同构",
        "properties": {"output": {"description": "工作流输入对象"}},
        "additionalProperties": True,
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        async def run(state: WorkflowState) -> dict[str, Any]:
            inputs = dict(state.get("inputs") or {})
            return {
                "node_outputs": {node.id: {"output": inputs, **inputs}},
            }

        return run


@register_node("end")
class EndNodeExecutor(BaseNodeExecutor):
    config_model = EndConfig
    # End renders ``output_template`` into ``output`` and the graph's
    # ``final_output``. The shape is user-defined by the template.
    output_schema = {
        "type": "object",
        "description": "按 output_template 渲染的最终输出;字段由模板决定",
        "properties": {"output": {"description": "渲染后的最终输出值"}},
        "additionalProperties": True,
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = EndConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            rendered = render_value(cfg.output_template, template_ctx)
            # Fallback: if template empty, use last agent-like output
            if not rendered or rendered == cfg.output_template:
                outputs = state.get("node_outputs") or {}
                # Prefer any non-start output
                fallback = None
                for nid, val in outputs.items():
                    if nid == node.id:
                        continue
                    fallback = val.get("output") if isinstance(val, dict) else val
                if isinstance(rendered, dict) and fallback is not None:
                    # Replace unresolved placeholders with fallback for common key
                    fixed = {}
                    for k, v in rendered.items():
                        if v in (None, "", cfg.output_template.get(k)):
                            fixed[k] = fallback
                        else:
                            fixed[k] = v
                    rendered = fixed
            return {
                "node_outputs": {node.id: {"output": rendered}},
                "final_output": rendered if isinstance(rendered, dict) else {"result": rendered},
            }

        return run
