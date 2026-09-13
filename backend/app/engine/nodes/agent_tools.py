"""Agent tool-call loop: bind MCP tools to the LLM and iterate."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.engine.nodes.base import CompileContext
from app.mcphub import McpServerScopeError
from app.providers.base import BaseChatProvider, ChatMessage, ToolSchema
from app.schemas.dsl import AgentConfig, NodeSpec
from app.schemas.events import EventType
from app.services.workflow_capabilities import required_agent_capabilities

logger = logging.getLogger(__name__)


async def resolve_tool_schemas(
    cfg: AgentConfig, ctx: CompileContext
) -> tuple[list[ToolSchema], dict[str, str]]:
    """Fetch schemas for the agent's bound tools.

    Returns (schemas, name->server_id map). Tool names are exposed to the LLM
    as-is; collisions across servers keep the first occurrence.
    """
    schemas: list[ToolSchema] = []
    routing: dict[str, str] = {}
    if ctx.mcp_manager is None:
        return schemas, routing
    for ref in cfg.tools:
        try:
            scope = {"project_id": ctx.project_id} if ctx.project_id is not None else {}
            tools = await ctx.mcp_manager.list_tools(ref.server_id, **scope)
        except McpServerScopeError:
            raise
        except Exception as exc:  # noqa: BLE001 - degrade, don't kill the run
            logger.warning("failed to list tools from %s: %s", ref.server_id, exc)
            continue
        for tool in tools:
            name = tool.get("name", "")
            if not name or name in routing:
                continue
            if ref.tool_name and ref.tool_name != name:
                continue
            routing[name] = ref.server_id
            schemas.append(
                ToolSchema(
                    name=name,
                    description=tool.get("description", ""),
                    parameters=tool.get("input_schema", {}),
                )
            )
    return schemas, routing


async def run_tool_loop(
    node: NodeSpec,
    cfg: AgentConfig,
    ctx: CompileContext,
    messages: list[ChatMessage],
    schemas: list[ToolSchema],
    routing: dict[str, str],
    *,
    providers: Sequence[BaseChatProvider],
    params: Mapping[str, Any],
) -> tuple[str, dict[str, int] | None, list[dict[str, Any]]]:
    """Iterate LLM <-> tool calls until a final text answer or round limit.

    Returns (final_text, usage, tool_trace).
    """
    trace: list[dict[str, Any]] = []
    usage: dict[str, int] | None = None

    for round_no in range(1, cfg.max_tool_rounds + 1):
        result = await ctx.model_chat_chain(
            providers,
            messages,
            required_capabilities=required_agent_capabilities(cfg),
            tools=schemas,
            params=params,
            node_id=node.id,
            load_balance=cfg.load_balance,
        )
        if result.usage is not None:
            usage = {
                "prompt": result.usage.prompt_tokens,
                "completion": result.usage.completion_tokens,
                "total": result.usage.total_tokens,
            }
        if not result.tool_calls:
            return result.content, usage, trace

        messages.append(
            ChatMessage(
                role="assistant",
                content=result.content or "",
                tool_calls=result.tool_calls,
            )
        )
        for tc in result.tool_calls:
            try:
                arguments = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                arguments = {"_raw": tc.arguments}
            server_id = routing.get(tc.name, "")

            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "tool_call",
                    "round": round_no,
                    "tool_name": tc.name,
                    "server_id": server_id,
                    "arguments": arguments,
                },
            )
            if not server_id or ctx.mcp_manager is None:
                content = f"error: unknown tool '{tc.name}'"
            else:
                try:
                    scope = {"project_id": ctx.project_id} if ctx.project_id is not None else {}
                    outcome = await ctx.mcp_manager.call_tool(
                        server_id, tc.name, arguments, **scope
                    )
                    content = str(outcome.get("content", ""))
                    if outcome.get("is_error"):
                        content = f"error: {content}"
                except McpServerScopeError:
                    raise
                except Exception as exc:  # noqa: BLE001 - surface to LLM
                    content = f"error: {exc}"

            trace.append(
                {
                    "round": round_no,
                    "tool_name": tc.name,
                    "server_id": server_id,
                    "arguments": arguments,
                    "result": content[:2000],
                }
            )
            await ctx.emitter.emit(
                EventType.NODE_STREAMING,
                node_id=node.id,
                payload={
                    "kind": "tool_result",
                    "round": round_no,
                    "tool_name": tc.name,
                    "result": content[:2000],
                },
            )
            messages.append(ChatMessage(role="tool", content=content, tool_call_id=tc.id))

    # Round limit reached — ask for a final answer without tools
    final = await ctx.model_chat_chain(
        providers,
        messages,
        required_capabilities=required_agent_capabilities(cfg),
        params=params,
        node_id=node.id,
        load_balance=cfg.load_balance,
    )
    return final.content, usage, trace
