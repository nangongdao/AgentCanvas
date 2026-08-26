"""End-to-end compiler tests: compile DSL and run the graph with fakes."""

from __future__ import annotations

from typing import Any

import pytest

from app.engine.compiler import WorkflowCompiler
from app.engine.nodes.base import LoopLimitExceeded
from app.engine.nodes.supervisor import parse_decision
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeChatProvider, FakeEmitter, make_ctx


def dsl(nodes: list[dict], edges: list[dict], name: str = "t") -> WorkflowDSL:
    return WorkflowDSL.model_validate({"name": name, "nodes": nodes, "edges": edges})


def agent(node_id: str, user_prompt: str = "hi", **cfg: Any) -> dict:
    return {
        "id": node_id,
        "type": "agent",
        "config": {"system_prompt": "sys", "user_prompt": user_prompt, **cfg},
    }


INIT: dict[str, Any] = {
    "inputs": {},
    "node_outputs": {},
    "loop_counts": {},
    "messages": [],
    "route": None,
    "error": None,
    "final_output": None,
}


async def run_graph(d: WorkflowDSL, emitter: FakeEmitter, provider: FakeChatProvider, **kw: Any):
    ctx = make_ctx(emitter, provider, **kw)
    graph = WorkflowCompiler().compile(d, ctx)
    state = dict(INIT)
    return await graph.ainvoke(state, config={"recursion_limit": 50})


class TestLinear:
    async def test_linear_chain(self, fake_emitter: FakeEmitter) -> None:
        provider = FakeChatProvider(["answer-1"])
        d = dsl(
            [
                {"id": "s", "type": "start", "config": {}},
                agent("a1", "{{input.q}}"),
                {"id": "e", "type": "end", "config": {}},
            ],
            [
                {"id": "e1", "source": "s", "target": "a1"},
                {"id": "e2", "source": "a1", "target": "e"},
            ],
        )
        result = await run_graph(d, fake_emitter, provider)
        assert result["node_outputs"]["a1"]["output"] == "answer-1"
        started = [e["node_id"] for e in fake_emitter.of_type(EventType.NODE_STARTED)]
        assert started == ["s", "a1", "e"]


class TestConditionBranch:
    def _dsl(self) -> WorkflowDSL:
        return dsl(
            [
                {"id": "s", "type": "start", "config": {}},
                {
                    "id": "cond",
                    "type": "condition",
                    "config": {
                        "branches": [
                            {
                                "id": "big",
                                "group": {
                                    "op": "and",
                                    "rules": [
                                        {"left": "{{input.x}}", "operator": "gt", "right": 5}
                                    ],
                                },
                            }
                        ],
                        "default_branch": "small",
                    },
                },
                agent("a_big"),
                agent("a_small"),
                {"id": "e", "type": "end", "config": {}},
            ],
            [
                {"id": "e1", "source": "s", "target": "cond"},
                {"id": "e2", "source": "cond", "target": "a_big", "source_handle": "big"},
                {"id": "e3", "source": "cond", "target": "a_small", "source_handle": "small"},
                {"id": "e4", "source": "a_big", "target": "e"},
                {"id": "e5", "source": "a_small", "target": "e"},
            ],
        )

    async def test_takes_matching_branch(self, fake_emitter: FakeEmitter) -> None:
        provider = FakeChatProvider(["big-path"])
        ctx = make_ctx(fake_emitter, provider)
        graph = WorkflowCompiler().compile(self._dsl(), ctx)
        state = {**INIT, "inputs": {"x": 10}}
        result = await graph.ainvoke(state, config={"recursion_limit": 50})
        assert "a_big" in result["node_outputs"]
        assert "a_small" not in result["node_outputs"]
        taken = fake_emitter.of_type(EventType.EDGE_TAKEN)
        assert len(taken) == 1
        assert taken[0]["payload"]["branch"] == "big"
        assert taken[0]["payload"]["target"] == "a_big"

    async def test_takes_default_branch(self, fake_emitter: FakeEmitter) -> None:
        provider = FakeChatProvider(["small-path"])
        ctx = make_ctx(fake_emitter, provider)
        graph = WorkflowCompiler().compile(self._dsl(), ctx)
        state = {**INIT, "inputs": {"x": 1}}
        result = await graph.ainvoke(state, config={"recursion_limit": 50})
        assert "a_small" in result["node_outputs"]
        assert "a_big" not in result["node_outputs"]
        taken = fake_emitter.of_type(EventType.EDGE_TAKEN)
        assert taken[0]["payload"]["branch"] == "small"


class TestParallelJoin:
    async def test_fanout_and_barrier(self, fake_emitter: FakeEmitter) -> None:
        provider = FakeChatProvider(["r1", "r2", "merged"])
        d = dsl(
            [
                {"id": "s", "type": "start", "config": {}},
                agent("a1"),
                agent("a2"),
                agent("merge", "{{nodes.a1.output}} + {{nodes.a2.output}}"),
                {"id": "e", "type": "end", "config": {}},
            ],
            [
                {"id": "e1", "source": "s", "target": "a1"},
                {"id": "e2", "source": "s", "target": "a2"},
                {"id": "e3", "source": "a1", "target": "merge"},
                {"id": "e4", "source": "a2", "target": "merge"},
                {"id": "e5", "source": "merge", "target": "e"},
            ],
        )
        result = await run_graph(d, fake_emitter, provider)
        outs = result["node_outputs"]
        assert {"a1", "a2", "merge"} <= set(outs)
        # merge must start only after BOTH a1 and a2 finished (join barrier)
        order = [
            e["node_id"]
            for e in fake_emitter.events
            if e["type"] in (EventType.NODE_STARTED, EventType.NODE_FINISHED)
        ]
        merge_start = order.index("merge")
        assert order.index("a1", 1) < merge_start and order.index("a2", 1) < merge_start
        # merge prompt saw both upstream outputs
        merge_call = provider.calls[-1]
        user_msg = merge_call[-1].content
        assert "r1" in user_msg and "r2" in user_msg


class TestSupervisor:
    def _dsl(self) -> WorkflowDSL:
        return dsl(
            [
                {"id": "s", "type": "start", "config": {}},
                agent(
                    "sup",
                    "{{input.q}}",
                    agent_mode="supervisor",
                    workers=["w1", "w2"],
                ),
                agent("w1"),
                agent("w2"),
                {"id": "e", "type": "end", "config": {}},
            ],
            [
                {"id": "e1", "source": "s", "target": "sup"},
                {"id": "e2", "source": "sup", "target": "w1"},
                {"id": "e3", "source": "sup", "target": "w2"},
                {"id": "e4", "source": "w1", "target": "sup"},
                {"id": "e5", "source": "w2", "target": "sup"},
                {"id": "e6", "source": "sup", "target": "e"},
            ],
        )

    async def test_routes_worker_then_finish(self, fake_emitter: FakeEmitter) -> None:
        provider = FakeChatProvider(
            [
                '{"next": "w1", "task": "do it", "reason": "start"}',
                "w1 work done",
                '{"next": "FINISH", "task": "", "reason": "all done"}',
            ]
        )
        ctx = make_ctx(fake_emitter, provider)
        graph = WorkflowCompiler().compile(self._dsl(), ctx)
        result = await graph.ainvoke(dict(INIT), config={"recursion_limit": 50})
        outs = result["node_outputs"]
        assert outs["w1"]["output"] == "w1 work done"
        assert "w2" not in outs
        taken = fake_emitter.of_type(EventType.EDGE_TAKEN)
        assert [t["payload"]["target"] for t in taken] == ["w1", "e"]

    async def test_loop_guard_stops_runaway_supervisor(self, fake_emitter: FakeEmitter) -> None:
        # Supervisor always picks w1: sup->w1->sup->w1... until guard fires
        provider = FakeChatProvider(['{"next": "w1", "task": "x", "reason": "loop"}'])
        ctx = make_ctx(fake_emitter, provider, max_loop_iterations=3)
        graph = WorkflowCompiler().compile(self._dsl(), ctx)
        with pytest.raises(LoopLimitExceeded):
            await graph.ainvoke(dict(INIT), config={"recursion_limit": 100})
        failed = fake_emitter.of_type(EventType.NODE_FAILED)
        assert any("loop limit" in e["payload"].get("error", "") for e in failed)


class TestParseDecision:
    def test_clean_json(self) -> None:
        d = parse_decision('{"next": "w1", "task": "t", "reason": "r"}', ["w1"])
        assert d["next"] == "w1"

    def test_json_in_prose(self) -> None:
        text = 'Sure, here is my decision: {"next": "w2", "task": "go"} hope that helps'
        assert parse_decision(text, ["w1", "w2"])["next"] == "w2"

    def test_finish(self) -> None:
        assert parse_decision('{"next": "FINISH"}', ["w1"])["next"] == "FINISH"

    def test_mention_fallback(self) -> None:
        assert parse_decision("I think w1 should handle this", ["w1"])["next"] == "w1"

    def test_garbage_falls_back_to_finish(self) -> None:
        assert parse_decision("no idea", ["w1"])["next"] == "FINISH"

    def test_unknown_worker_rejected(self) -> None:
        assert parse_decision('{"next": "hacker"}', ["w1"])["next"] == "FINISH"
