"""Supervisor agent node — dynamic routing via LangGraph Command."""

from __future__ import annotations

import json
import logging
import re

from langgraph.types import Command

from app.core.injection_guard import fence_injected_data
from app.engine.nodes.base import CompileContext, NodeFn
from app.engine.state import WorkflowState
from app.engine.templates import build_context, render_string
from app.providers.base import ChatMessage
from app.schemas.dsl import AgentConfig, NodeSpec
from app.schemas.events import EventType
from app.services.workflow_capabilities import model_chain_ids, required_agent_capabilities

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{[^{}]*\}")

SUPERVISOR_PROMPT = """你是一个多 Agent 调度器(supervisor)。你管理以下 worker:

{workers_desc}

根据当前任务进展,决定下一步交给哪个 worker,或在任务完成时结束。

历史进展:
{history}

必须只输出一个 JSON 对象,格式:
{{"next": "<worker_id 或 FINISH>", "task": "<给该 worker 的具体指令>", "reason": "<一句话理由>"}}"""


def parse_decision(text: str, workers: list[str]) -> dict[str, str]:
    """Robustly extract the routing decision JSON from LLM output."""
    candidates = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        for m in _JSON_RE.finditer(text):
            try:
                candidates.append(json.loads(m.group()))
            except json.JSONDecodeError:
                continue
    for cand in candidates:
        if isinstance(cand, dict) and "next" in cand:
            nxt = str(cand["next"]).strip()
            if nxt == "FINISH" or nxt in workers:
                return {
                    "next": nxt,
                    "task": str(cand.get("task", "")),
                    "reason": str(cand.get("reason", "")),
                }
    # Fallback: mention detection
    for w in workers:
        if w in text:
            return {"next": w, "task": "", "reason": "fallback: worker mentioned in output"}
    return {"next": "FINISH", "task": "", "reason": "fallback: unparseable output"}


def build_supervisor_fn(
    node: NodeSpec,
    cfg: AgentConfig,
    ctx: CompileContext,
    *,
    finish_target: str,
) -> NodeFn:
    """Create the supervisor node function. finish_target = node to go to on FINISH."""
    workers = list(cfg.workers)

    async def run(state: WorkflowState) -> Command:
        template_ctx = build_context(
            inputs=state.get("inputs") or {},
            node_outputs=state.get("node_outputs") or {},
        )
        outputs = state.get("node_outputs") or {}
        history_lines: list[str] = []
        for wid in workers:
            if wid in outputs:
                val = outputs[wid]
                text = val.get("output") if isinstance(val, dict) else val
                history_lines.append(f"- {wid}: {str(text)[:400]}")
        history = "\n".join(history_lines) or "(尚无 worker 产出)"

        workers_desc = "\n".join(f"- {w}" for w in workers)
        base_system = render_string(cfg.system_prompt, template_ctx)
        fenced_history = fence_injected_data(history, kind="worker-history")
        system = (
            base_system
            + "\n\n"
            + SUPERVISOR_PROMPT.format(
                workers_desc=workers_desc, history=fenced_history or "(尚无 worker 产出)"
            )
        )
        user = render_string(cfg.user_prompt, template_ctx)

        providers = tuple(ctx.get_provider(model_id) for model_id in model_chain_ids(cfg))
        result = await ctx.model_chat_chain(
            providers,
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user),
            ],
            required_capabilities=required_agent_capabilities(cfg),
            params=cfg.params,
            node_id=node.id,
        )
        decision = parse_decision(result.content, workers)
        goto = finish_target if decision["next"] == "FINISH" else decision["next"]

        await ctx.emitter.emit(
            EventType.EDGE_TAKEN,
            node_id=node.id,
            payload={"source": node.id, "target": goto, "decision": decision},
        )
        return Command(
            goto=goto,
            update={
                "route": decision["next"],
                "node_outputs": {
                    node.id: {
                        "output": decision["reason"],
                        "decision": decision,
                    }
                },
            },
        )

    return run
