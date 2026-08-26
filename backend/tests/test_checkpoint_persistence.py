"""Durable checkpointer integration: interrupt -> restart -> resume.

Uses the *production* checkpointer factory (AsyncSqliteSaver with the strict
serde allowlist) rather than MemorySaver, and simulates a full process
restart by closing the connection and reopening the same SQLite file. This is
the G1 acceptance path: a workflow that paused at a human-approval node must
be resumable after the backend restarts, with the decision applied.
"""

from __future__ import annotations

from typing import Any

from langgraph.types import Command

from app.core.config import Settings
from app.engine.checkpoint import (
    close_checkpointer,
    copy_sqlite_checkpoints,
    make_checkpointer,
)
from app.engine.compiler import WorkflowCompiler
from app.engine.nodes.base import CompileContext
from app.schemas.dsl import WorkflowDSL
from tests.conftest import FakeChatProvider, FakeEmitter

THREAD = "durable-thread-1"


def _human_workflow() -> WorkflowDSL:
    return WorkflowDSL.model_validate(
        {
            "name": "durable-human",
            "nodes": [
                {"id": "s", "type": "start", "config": {}},
                {"id": "agent1", "type": "agent", "config": {"user_prompt": "draft"}},
                {
                    "id": "approve",
                    "type": "human",
                    "config": {"title": "审批", "instruction": "继续?"},
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


def _compile(emitter: FakeEmitter, provider: FakeChatProvider):
    ctx = CompileContext(
        execution_id="durable-exec",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _mid: provider,
        max_loop_iterations=10,
    )
    return WorkflowCompiler().compile(_human_workflow(), ctx, checkpointer=None)


async def test_interrupt_survives_restart_and_resumes(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, environment="test")
    config = {"configurable": {"thread_id": THREAD}, "recursion_limit": 50}

    # --- Run #1: pauses at the human node, persisted durably ---
    await close_checkpointer()
    saver = await make_checkpointer(settings)
    emitter1 = FakeEmitter()
    graph = _compile(emitter1, FakeChatProvider(["draft"]))
    graph.checkpointer = saver
    await graph.ainvoke(_init(), config=config)
    snapshot = await graph.aget_state(config)
    assert snapshot.next  # paused before agent2

    # --- "Restart": drop all in-memory saver state, reopen the same file ---
    await close_checkpointer()
    assert (settings.data_dir / "checkpoints.db").exists()
    saver2 = await make_checkpointer(settings)

    # A brand-new graph, compiled from the DB workflow, resumes the same thread.
    emitter2 = FakeEmitter()
    graph2 = _compile(emitter2, FakeChatProvider(["final"]))
    graph2.checkpointer = saver2
    await graph2.ainvoke(Command(resume={"approved": True}), config=config)

    state = await graph2.aget_state(config)
    human_out = (state.values.get("node_outputs") or {}).get("approve")
    assert isinstance(human_out, dict)
    assert human_out["approved"] is True
    # agent2 ran after resume
    assert (state.values.get("node_outputs") or {}).get("agent2") is not None
    await close_checkpointer()


async def test_sqlite_checkpoint_copy_preserves_resumable_interrupt(tmp_path) -> None:
    source_settings = Settings(data_dir=tmp_path / "source", environment="test")
    target_settings = Settings(data_dir=tmp_path / "target", environment="test")
    config = {
        "configurable": {"thread_id": "migrated-durable-thread"},
        "recursion_limit": 50,
    }

    await close_checkpointer()
    source = await make_checkpointer(source_settings)
    graph = _compile(FakeEmitter(), FakeChatProvider(["draft"]))
    graph.checkpointer = source
    await graph.ainvoke(_init(), config=config)
    assert (await graph.aget_state(config)).next
    await close_checkpointer()

    target = await make_checkpointer(target_settings)
    result = await copy_sqlite_checkpoints(source_settings.checkpoint_db_path, target)
    assert result.checkpoints > 0
    assert result.threads == 1
    await close_checkpointer()

    reopened = await make_checkpointer(target_settings)
    resumed = _compile(FakeEmitter(), FakeChatProvider(["final"]))
    resumed.checkpointer = reopened
    await resumed.ainvoke(Command(resume={"approved": True}), config=config)

    state = await resumed.aget_state(config)
    human_out = (state.values.get("node_outputs") or {}).get("approve")
    assert isinstance(human_out, dict)
    assert human_out["approved"] is True
    assert (state.values.get("node_outputs") or {}).get("agent2") is not None
    await close_checkpointer()
