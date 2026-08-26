"""Tool (MCP) node executor — deterministic tool call with timeout + retry."""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from typing import Any

from app.engine.nodes.base import (
    BaseNodeExecutor,
    CompileContext,
    NodeFn,
    register_node,
)
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_value
from app.schemas.dsl import NodeSpec, ToolConfig
from app.schemas.events import EventType

logger = logging.getLogger(__name__)


@register_node("tool")
class ToolNodeExecutor(BaseNodeExecutor):
    config_model = ToolConfig
    # MCP tool output is provider-defined, so ``output`` is left open. The
    # declarative schema still exposes the ``text`` mirror and ``meta`` so the
    # data-flow pane can show where these fields come from.
    output_schema = {
        "type": "object",
        "properties": {
            "output": {
                "description": "解析后的工具返回值:优先 JSON,否则原始文本",
            },
            "text": {"type": "string", "description": "原始工具返回文本"},
            "meta": {
                "type": "object",
                "description": "工具调用元数据",
                "properties": {
                    "server_id": {"type": "string"},
                    "tool_name": {"type": "string"},
                },
            },
        },
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = ToolConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            if ctx.mcp_manager is None:
                raise RuntimeError("MCP manager is not available")

            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            arguments = render_value(cfg.arguments, template_ctx)
            if not isinstance(arguments, dict):
                arguments = {"value": arguments}

            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "tool_call",
                    "server_id": cfg.server_id,
                    "tool_name": cfg.tool_name,
                    "arguments": arguments,
                },
            )

            max_attempts = int((cfg.retry or {}).get("max_attempts", 2))
            scope = {"project_id": ctx.project_id} if ctx.project_id is not None else {}
            result = await ctx.mcp_manager.call_tool(
                cfg.server_id,
                cfg.tool_name,
                arguments,
                timeout=cfg.timeout_seconds,
                max_attempts=max_attempts,
                **scope,
            )
            content = str(result.get("content", ""))
            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "tool_result",
                    "server_id": cfg.server_id,
                    "tool_name": cfg.tool_name,
                    "result": content[:2000],
                    "is_error": bool(result.get("is_error")),
                },
            )
            if result.get("is_error"):
                raise RuntimeError(
                    f"tool '{cfg.tool_name}' returned error: {content}"
                )

            parsed: Any = content
            with suppress(json.JSONDecodeError, TypeError):
                parsed = json.loads(content)

            return {
                "node_outputs": {
                    node.id: {
                        "output": parsed,
                        "text": content,
                        "meta": {
                            "server_id": cfg.server_id,
                            "tool_name": cfg.tool_name,
                        },
                    }
                }
            }

        return run
