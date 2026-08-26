"""Code node executor — sandboxed inline Python (C2-2)."""

from __future__ import annotations

import logging
from typing import Any

from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.nodes.code_runner import CodeExecutionError, run_code
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_value
from app.schemas.dsl import CodeConfig, NodeSpec
from app.schemas.events import EventType
from app.services.execution_inspection import bounded_error, bounded_json_snapshot

logger = logging.getLogger(__name__)

MAX_INPUT_ITEMS = 64
MAX_INPUT_BYTES = 256 * 1024


@register_node("code")
class CodeNodeExecutor(BaseNodeExecutor):
    """Run user Python in an isolated subprocess with deny-by-default sandbox."""

    config_model = CodeConfig

    def metadata(self) -> dict[str, Any]:
        meta = super().metadata()
        meta["label"] = "Code"
        meta["description"] = "沙箱化 Python 代码片段,输入变量注入,默认禁网禁文件系统。"
        return meta

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        config = CodeConfig.model_validate(node.config or {})
        sandbox = ctx.sandbox

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            rendered_source = _render_source(config.source, template_ctx)
            rendered_inputs = _render_inputs(config.inputs, template_ctx)

            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "code_started",
                    "language": config.language.value,
                    "source_bytes": len(rendered_source.encode("utf-8")),
                    "input_names": list(rendered_inputs.keys()),
                    "sandbox": _sandbox_label(sandbox),
                    "allow_network": config.allow_network,
                    "allow_filesystem": list(config.allow_filesystem),
                },
            )

            try:
                result = await run_code(
                    rendered_source,
                    rendered_inputs,
                    timeout_seconds=config.timeout_seconds,
                    memory_limit_mb=config.memory_limit_mb,
                    process_count=config.process_count,
                    allow_network=config.allow_network,
                    allow_filesystem=list(config.allow_filesystem),
                    sandbox=sandbox,
                )
            except CodeExecutionError as exc:
                await ctx.emitter.emit(
                    EventType.NODE_STREAMING,
                    node_id=node.id,
                    payload={"kind": "code_failed", "error": bounded_error(str(exc))},
                )
                raise

            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "code_finished",
                    "output_preview": bounded_json_snapshot(result.output),
                },
            )
            return {
                "node_outputs": {
                    node.id: {
                        "output": result.output,
                        "meta": {
                            "language": config.language.value,
                            "sandbox": _sandbox_label(sandbox),
                        },
                    }
                }
            }

        return run


def _render_source(source: str, template_ctx: dict[str, Any]) -> str:
    rendered = render_value(source, template_ctx, strict=True)
    if not isinstance(rendered, str) or not rendered.strip():
        raise CodeExecutionError("code source rendered to an empty string")
    return rendered


def _render_inputs(
    inputs: dict[str, str], template_ctx: dict[str, Any]
) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    total = 0
    for name, expr in inputs.items():
        if len(rendered) >= MAX_INPUT_ITEMS:
            raise CodeExecutionError(f"code node inputs exceed the {MAX_INPUT_ITEMS} item limit")
        value = render_value(expr, template_ctx, strict=True)
        payload = bounded_json_snapshot(value)
        total += len(str(payload).encode("utf-8"))
        if total > MAX_INPUT_BYTES:
            raise CodeExecutionError("code node inputs exceed the configured byte limit")
        rendered[name] = payload
    return rendered


def _sandbox_label(sandbox: Any) -> str:
    describe = getattr(sandbox, "describe", None)
    if describe is None:
        return "none"
    return str(describe().get("backend", "none"))


__all__ = ["CodeNodeExecutor"]
