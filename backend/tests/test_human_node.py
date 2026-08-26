"""Tests for the human-approval node: interrupt + resume."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.engine.compiler import WorkflowCompiler
from app.engine.nodes.base import CompileContext
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeChatProvider, FakeEmitter


def _human_workflow() -> WorkflowDSL:
    return WorkflowDSL.model_validate(
        {
            "name": "human",
            "nodes": [
                {"id": "s", "type": "start", "config": {}},
                {"id": "agent1", "type": "agent", "config": {"user_prompt": "draft"}},
                {
                    "id": "approve",
                    "type": "human",
                    "config": {
                        "title": "人工审批",
                        "instruction": "请确认是否继续",
                    },
                },
                {"id": "agent2", "type": "agent", "config": {"user_prompt": "final"}},
                {"id": "e", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "1", "source": "s", "target": "agent1"},
                {"id": "2", "source": "agent1", "target": "approve"},
                {"id": "3", "source": "approve", "target": "agent2"},
                {"id": "4", "source": "agent2", "target": "e"},
            ],
        }
    )


def _ctx(emitter: FakeEmitter, provider: FakeChatProvider) -> CompileContext:
    return CompileContext(
        execution_id="human-exec",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _mid: provider,
        max_loop_iterations=10,
    )


def _init() -> dict[str, Any]:
    return {
        "inputs": {},
        "node_outputs": {},
        "loop_counts": {},
        "messages": [],
        "route": None,
        "error": None,
        "final_output": None,
    }


async def test_human_node_interrupts_until_resumed() -> None:
    from langgraph.errors import GraphInterrupt

    emitter = FakeEmitter()
    provider = FakeChatProvider(["draft-output"])
    ctx = _ctx(emitter, provider)
    graph = WorkflowCompiler().compile(_human_workflow(), ctx, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t1"}, "recursion_limit": 50}

    # First run must pause at the human node (GraphInterrupt surfaced).
    paused = False
    try:
        await graph.ainvoke(_init(), config=config)
    except GraphInterrupt:
        paused = True
    assert paused or graph.get_state(config).next  # either path means it stopped
    assert emitter.of_type(EventType.NODE_FAILED) == []

    # Resume with an approve decision — the run completes.
    await graph.ainvoke(Command(resume={"approved": True}), config=config)
    state = graph.get_state(config).values
    human_out = (state.get("node_outputs") or {}).get("approve")
    assert isinstance(human_out, dict)
    assert human_out["approved"] is True
