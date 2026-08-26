"""Agent (LLM) node executor — streaming simple mode + MCP tool-call loop."""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.injection_guard import fence_injected_data
from app.engine.nodes.agent_tools import resolve_tool_schemas, run_tool_loop
from app.engine.nodes.base import BaseNodeExecutor, CompileContext, NodeFn, register_node
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_string, render_value
from app.memory import MemoryEntry
from app.providers.base import ChatMessage
from app.schemas.dsl import AgentConfig, NodeSpec
from app.schemas.events import EventType
from app.services.workflow_capabilities import model_chain_ids, required_agent_capabilities

logger = logging.getLogger(__name__)


async def _resolve_session_id(inputs: dict[str, Any]) -> str:
    """Return the conversation session id, falling back to a seeded id."""
    session = inputs.get("session_id") or inputs.get("_session_id")
    if isinstance(session, str) and session:
        return session
    return "adhoc-session"


def _memory_enabled(cfg: AgentConfig) -> tuple[bool, int]:
    """Read the AgentConfig.memory toggle + window."""
    memory = cfg.memory or {}
    enabled = bool(memory.get("enabled", False))
    window = int(memory.get("window", 10) or 10)
    return enabled, max(1, window)


def _structured_output_params(cfg: AgentConfig) -> dict[str, Any]:
    """Merge node-level structured-output params (schema-constrained JSON).

    Only applies when the node declares ``output.format`` in json/json_schema
    plus an optional ``output.schema``; returns the original params otherwise
    so non-JSON agents are untouched.
    """
    output = cfg.output or {}
    output_format = str(output.get("format") or "").strip().lower()
    if output_format not in ("json", "json_schema"):
        return cfg.params
    prepared = dict(cfg.params or {})
    schema = output.get("schema")
    if isinstance(schema, dict) and schema:
        prepared["response_format"] = {
            "type": "json_schema",
            "json_schema": schema,
        }
    else:
        # json_object without an explicit schema — provider decides the shape.
        prepared.setdefault("response_format", {"type": "json_object"})
    return prepared


async def _load_memory(
    ctx: CompileContext, inputs: dict[str, Any], window: int
) -> tuple[MemoryEntry, ...]:
    """Return recent memory entries when a memory store is configured."""
    store = ctx.memory_store
    if store is None:
        return ()
    backend = getattr(store, "backend_name", "")
    if backend in {"", "none"}:
        return ()
    session_id = await _resolve_session_id(inputs)
    try:
        entries = await store.load(session_id, window=window)
    except Exception:  # noqa: BLE001 — memory is best-effort, never fail a run
        logger.debug("memory load failed; continuing without memory", exc_info=True)
        return ()
    return tuple(entries)


async def _persist_memory(
    ctx: CompileContext, inputs: dict[str, Any], user_prompt: str, content: str
) -> None:
    """Persist this exchange into the memory store (best-effort)."""
    store = ctx.memory_store
    if store is None:
        return
    backend = getattr(store, "backend_name", "")
    if backend in {"", "none"}:
        return
    if not user_prompt and not content:
        return
    session_id = await _resolve_session_id(inputs)
    try:
        if user_prompt:
            await store.append(session_id, "user", user_prompt)
        if content:
            await store.append(session_id, "assistant", content)
    except Exception:  # noqa: BLE001 — memory is best-effort
        logger.debug("memory persist failed; continuing", exc_info=True)


async def _apply_session_writes(
    ctx: CompileContext,
    inputs: dict[str, Any],
    cfg: AgentConfig,
    node_id: str,
    output: dict[str, Any],
) -> None:
    """Persist configured chat-session variables after a reply (C3-4).

    Templates render against the normal context plus this node's own output,
    so ``{{nodes.<id>.output}}`` and ``{{nodes.<id>.text}}`` resolve. Ad-hoc runs
    without a chat session are skipped so canvas test runs never write
    durable conversation state.
    """
    store = ctx.session_variable_store
    if store is None or not cfg.session_writes:
        return
    session_id = inputs.get("_session_id") or inputs.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    template_ctx = build_context(inputs=inputs, node_outputs={node_id: output})
    rendered: dict[str, Any] = {}
    for name, template in cfg.session_writes.items():
        rendered[name] = render_value(template, template_ctx)
    try:
        await store.write_many(session_id, rendered)
    except Exception:  # noqa: BLE001 — session memory is best-effort
        logger.debug("session variable write failed; continuing", exc_info=True)


def _knowledge_context(
    state: WorkflowState, context_nodes: list[str]
) -> tuple[str, list[dict[str, Any]]]:
    outputs = state.get("node_outputs") or {}
    selected = set(context_nodes)
    citations: list[dict[str, Any]] = []
    seen_citation_ids: set[str] = set()
    for node_id, output in outputs.items():
        if selected and node_id not in selected:
            continue
        if not isinstance(output, dict) or not isinstance(output.get("citations"), list):
            continue
        for source in output["citations"]:
            if not isinstance(source, dict) or not source.get("text"):
                continue
            citation_id = str(source.get("id") or "")
            if citation_id and citation_id in seen_citation_ids:
                continue
            if citation_id:
                seen_citation_ids.add(citation_id)
            citation = dict(source)
            citation["label"] = f"[{len(citations) + 1}]"
            citation["node_id"] = node_id
            citations.append(citation)

    if not citations:
        return "", []
    blocks = [
        f"{citation['label']} Source: {citation.get('filename', 'document')}"
        + (f", page {citation['page']}" if citation.get("page") else "")
        + f"\n{citation['text']}"
        for citation in citations
    ]
    context = (
        "Use the following retrieved knowledge when it is relevant. "
        "Cite supporting passages with their bracketed labels and do not invent sources.\n\n"
        + "\n\n".join(blocks)
    )
    return context, citations


@register_node("agent")
class AgentNodeExecutor(BaseNodeExecutor):
    config_model = AgentConfig
    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string", "description": "LLM 生成的文本内容"},
            "text": {"type": "string", "description": "output 的同步镜像,供流式引用"},
            "citations": {
                "type": "array",
                "description": "RAG 检索命中的引用来源",
                "items": {"type": "object"},
            },
            "meta": {
                "type": "object",
                "description": "用量、工具轨迹与引用元数据",
                "properties": {
                    "usage": {"type": "object"},
                    "tool_trace": {"type": "array", "items": {"type": "object"}},
                    "citations": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
    }

    def build(self, node: NodeSpec, ctx: CompileContext) -> NodeFn:
        cfg = AgentConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            template_ctx = build_context(
                inputs=state.get("inputs") or {},
                node_outputs=state.get("node_outputs") or {},
            )
            system_prompt = render_string(cfg.system_prompt, template_ctx)
            user_prompt = render_string(cfg.user_prompt, template_ctx)
            knowledge_context, citations = _knowledge_context(state, cfg.context_nodes)
            if knowledge_context:
                system_prompt = f"{system_prompt}\n\n" + fence_injected_data(
                    knowledge_context, kind="rag"
                )

            memory_enabled, memory_window = _memory_enabled(cfg)
            memory_entries = (
                await _load_memory(ctx, state.get("inputs") or {}, memory_window)
                if memory_enabled
                else ()
            )
            if memory_entries:
                memory_block = "Prior conversation memory (most recent first):\n" + "\n".join(
                    f"- {entry.role}: {entry.content}" for entry in memory_entries
                )
                system_prompt = f"{system_prompt}\n\n" + fence_injected_data(
                    memory_block, kind="memory"
                )

            providers = tuple(ctx.get_provider(model_id) for model_id in model_chain_ids(cfg))
            required_capabilities = required_agent_capabilities(cfg)
            messages = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ]
            output_params = _structured_output_params(cfg)

            # Tool-call loop when MCP tools are bound to this agent
            if cfg.tools and ctx.mcp_manager is not None:
                schemas, routing = await resolve_tool_schemas(cfg, ctx)
                if schemas:
                    content, tool_usage, trace = await run_tool_loop(
                        node,
                        cfg,
                        ctx,
                        messages,
                        schemas,
                        routing,
                        providers=providers,
                        params=output_params,
                    )
                    if memory_enabled:
                        await _persist_memory(ctx, state.get("inputs") or {}, user_prompt, content)
                    node_output = {
                        "output": content,
                        "text": content,
                        "citations": citations,
                        "meta": {
                            "usage": tool_usage,
                            "tool_trace": trace,
                            "citations": citations,
                        },
                    }
                    await _apply_session_writes(
                        ctx, state.get("inputs") or {}, cfg, node.id, node_output
                    )
                    return {"node_outputs": {node.id: node_output}}

            # Simple streaming mode
            text_parts: list[str] = []
            usage: dict[str, int] | None = None
            t0 = time.perf_counter()
            first_token_ms: int | None = None

            async for chunk in ctx.model_stream_chat_chain(
                providers,
                messages,
                required_capabilities=required_capabilities,
                params=output_params,
                node_id=node.id,
            ):
                if chunk.type == "text" and chunk.text:
                    if first_token_ms is None:
                        first_token_ms = int((time.perf_counter() - t0) * 1000)
                    text_parts.append(chunk.text)
                    await ctx.emitter.emit(
                        EventType.NODE_STREAMING,
                        node_id=node.id,
                        payload={"delta": chunk.text, "kind": "text"},
                    )
                elif chunk.type == "usage" and chunk.usage is not None:
                    usage = {
                        "prompt": chunk.usage.prompt_tokens,
                        "completion": chunk.usage.completion_tokens,
                        "total": chunk.usage.total_tokens,
                    }

            content = "".join(text_parts)
            if memory_enabled:
                await _persist_memory(ctx, state.get("inputs") or {}, user_prompt, content)
            meta = {"ttft_ms": first_token_ms, "usage": usage, "citations": citations}
            node_output = {
                "output": content,
                "text": content,
                "citations": citations,
                "meta": meta,
            }
            await _apply_session_writes(ctx, state.get("inputs") or {}, cfg, node.id, node_output)
            return {"node_outputs": {node.id: node_output}}

        return run
