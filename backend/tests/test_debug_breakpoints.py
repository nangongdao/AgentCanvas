"""Debug-run breakpoints (C2-7): node-level pause + resume + state patch."""

from __future__ import annotations

import contextlib
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from app.core.sandbox import NoSandbox
from app.engine.compiler import WorkflowCompiler
from app.engine.debug_breakpoints import (
    breakpoint_node_id,
    is_breakpoint_node_id,
    should_break,
)
from app.engine.nodes.base import CompileContext
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeEmitter


def _linear_workflow() -> WorkflowDSL:
    """start -> addone -> double -> end. addone: n+1; double: (n+1)*2."""
    return WorkflowDSL.model_validate(
        {
            "name": "debug-linear",
            "nodes": [
                {
                    "id": "s",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "n", "type": "number", "required": True}]
                    },
                },
                {
                    "id": "addone",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": 'output = {"value": inputs["n"] + 1}',
                        "inputs": {"n": "{{input.n}}"},
                        "timeout_seconds": 5.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                        "allow_network": False,
                        "allow_filesystem": [],
                    },
                },
                {
                    "id": "double",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": 'output = {"value": inputs["prev"] * 2}',
                        "inputs": {"prev": "{{nodes.addone.output.value}}"},
                        "timeout_seconds": 5.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                        "allow_network": False,
                        "allow_filesystem": [],
                    },
                },
                {"id": "e", "type": "end", "config": {"output_template": {"result": "{{nodes.double.output.value}}"}}},
            ],
            "edges": [
                {"id": "1", "source": "s", "target": "addone"},
                {"id": "2", "source": "addone", "target": "double"},
                {"id": "3", "source": "double", "target": "e"},
            ],
        }
    )


def _ctx(
    emitter: FakeEmitter,
    *,
    breakpoints: frozenset[str] = frozenset(),
    single_step: bool = False,
) -> CompileContext:
    return CompileContext(
        execution_id="debug-exec",
        emitter=emitter,  # type: ignore[arg-type]
        get_provider=lambda _mid: None,
        max_loop_iterations=10,
        sandbox=NoSandbox(),
        debug_breakpoints=breakpoints,
        debug_single_step=single_step,
    )


def _init(n: int = 10) -> dict[str, Any]:
    return {
        "inputs": {"n": n},
        "node_outputs": {},
        "loop_counts": {},
        "messages": [],
        "route": None,
        "error": None,
        "final_output": None,
    }


def test_should_break_respects_breakpoints_and_single_step() -> None:
    from app.schemas.dsl import NodeSpec, NodeType

    code_node = NodeSpec(id="c", type=NodeType.CODE, config={})
    start_node = NodeSpec(id="s", type=NodeType.START, config={})
    ctx_none = CompileContext(
        execution_id="x",
        emitter=FakeEmitter(),  # type: ignore[arg-type]
        get_provider=lambda _mid: None,
    )
    ctx_bp = CompileContext(
        execution_id="x",
        emitter=FakeEmitter(),  # type: ignore[arg-type]
        get_provider=lambda _mid: None,
        debug_breakpoints=frozenset({"c"}),
    )
    ctx_step = CompileContext(
        execution_id="x",
        emitter=FakeEmitter(),  # type: ignore[arg-type]
        get_provider=lambda _mid: None,
        debug_single_step=True,
    )
    assert not should_break(ctx_none, code_node)
    assert should_break(ctx_bp, code_node)
    assert not should_break(ctx_bp, start_node)
    assert should_break(ctx_step, code_node)
    assert not should_break(ctx_step, start_node)


def test_breakpoint_node_id_roundtrip() -> None:
    bp = breakpoint_node_id("addone")
    assert bp == "__debug_bp_addone"
    assert is_breakpoint_node_id(bp)
    assert not is_breakpoint_node_id("addone")


async def test_debug_run_pauses_at_breakpoint_then_completes_on_resume() -> None:
    emitter = FakeEmitter()
    ctx = _ctx(emitter, breakpoints=frozenset({"addone"}))
    graph = WorkflowCompiler().compile(_linear_workflow(), ctx, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "debug1"}, "recursion_limit": 50}

    paused = False
    try:
        await graph.ainvoke(_init(), config=config)
    except GraphInterrupt:
        paused = True
    state = graph.get_state(config)
    assert paused or state.next  # stopped at the breakpoint

    # The breakpoint node id appears in the graph's pending task list.
    pending = [n for n in (state.next or [])]
    assert any(is_breakpoint_node_id(str(n)) for n in pending)
    assert emitter.of_type(EventType.NODE_FAILED) == []
    # addone ran once before pausing (its output is visible in the snapshot).
    snapshot = state.values
    assert (snapshot.get("node_outputs") or {}).get("addone") is not None

    # Resume (LangGraph requires a non-empty resume value to clear an
    # interrupt; an empty dict does not resume). With no patch the run
    # completes to the end using addone's original output.
    await graph.ainvoke(Command(resume={"resume": True}), config=config)
    final = graph.get_state(config).values
    assert (final.get("final_output") or {}).get("result") == 22  # (10+1)*2


async def test_debug_run_state_patch_overrides_intermediate_value() -> None:
    emitter = FakeEmitter()
    ctx = _ctx(emitter, breakpoints=frozenset({"addone"}))
    graph = WorkflowCompiler().compile(_linear_workflow(), ctx, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "patch1"}, "recursion_limit": 50}

    with contextlib.suppress(GraphInterrupt):
        await graph.ainvoke(_init(), config=config)
    assert graph.get_state(config).next  # paused

    # Editor rewrites addone's output.value from 11 to 100 before continuing.
    await graph.ainvoke(
        Command(resume={"state_patch": {"addone": {"output": {"value": 100}}}}),
        config=config,
    )
    final = graph.get_state(config).values
    # double consumes the patched addone.output.value: 100*2 = 200
    assert (final.get("final_output") or {}).get("result") == 200


async def test_single_step_pauses_after_each_linear_node() -> None:
    emitter = FakeEmitter()
    ctx = _ctx(emitter, single_step=True)
    graph = WorkflowCompiler().compile(_linear_workflow(), ctx, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "step1"}, "recursion_limit": 50}

    # First invoke stops at the first breakpoint (after addone).
    await graph.ainvoke(_init(), config=config)
    pauses = 1 if graph.get_state(config).next else 0
    # Step until completion; each non-empty ``next`` after a resume means the
    # graph paused at the next breakpoint rather than completing.
    for _ in range(10):
        state = graph.get_state(config)
        if not state.next:
            break
        await graph.ainvoke(Command(resume={"resume": True}), config=config)
        if graph.get_state(config).next:
            pauses += 1
    final = graph.get_state(config).values
    assert (final.get("final_output") or {}).get("result") == 22
    # At least two pauses: after addone and after double.
    assert pauses >= 2


async def test_non_debug_run_is_unaffected() -> None:
    """A normal run (no breakpoints) must compile and complete normally."""
    emitter = FakeEmitter()
    ctx = _ctx(emitter)  # no breakpoints, no single-step
    graph = WorkflowCompiler().compile(_linear_workflow(), ctx, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "plain1"}, "recursion_limit": 50}

    await graph.ainvoke(_init(), config=config)
    state = graph.get_state(config).values
    assert not graph.get_state(config).next  # completed, never paused
    assert (state.get("final_output") or {}).get("result") == 22
    # No synthetic breakpoint nodes leaked into node_outputs.
    assert all(not is_breakpoint_node_id(k) for k in (state.get("node_outputs") or {}))
