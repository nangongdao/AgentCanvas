"""Iteration node batching, concurrency, failure policy, and guard contracts."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.engine.compiler import WorkflowCompiler
from app.engine.nodes import (
    BaseNodeExecutor,
    NodeFn,
    clear_external_executors,
    list_node_types,
    register_executor_instance,
)
from app.engine.state import WorkflowState
from app.main import create_app
from app.schemas.dsl import NodeSpec, WorkflowDSL
from app.schemas.events import EventType
from tests.conftest import FakeEmitter, make_ctx


class _ProbeConfig(BaseModel):
    fail_on: int | None = None


class _ProbeExecutor(BaseNodeExecutor):
    config_model = _ProbeConfig

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.seen: list[int] = []

    def build(self, node: NodeSpec, ctx: Any) -> NodeFn:
        config = _ProbeConfig.model_validate(node.config or {})

        async def run(state: WorkflowState) -> dict[str, Any]:
            item = int((state.get("inputs") or {})["item"])
            self.seen.append(item)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                if item == config.fail_on:
                    raise ValueError(f"rejected item {item}")
                return {"node_outputs": {node.id: {"output": item * 2}}}
            finally:
                self.active -= 1

        return run


def _workflow(*, strategy: str = "abort", concurrency: int = 2) -> WorkflowDSL:
    return WorkflowDSL.model_validate(
        {
            "name": "iteration-contract",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {
                        "items": "{{input.items}}",
                        "batch_size": 3,
                        "concurrency_limit": concurrency,
                        "failure_strategy": strategy,
                        "subgraph": {
                            "nodes": [
                                {"id": "item_start", "type": "start", "config": {}},
                                {
                                    "id": "work",
                                    "type": "test.iteration_probe",
                                    "config": {"fail_on": -1},
                                },
                                {
                                    "id": "item_end",
                                    "type": "end",
                                    "config": {
                                        "output_template": {
                                            "value": "{{nodes.work.output}}",
                                            "index": "{{input.index}}",
                                        }
                                    },
                                },
                            ],
                            "edges": [
                                {"id": "i1", "source": "item_start", "target": "work"},
                                {"id": "i2", "source": "work", "target": "item_end"},
                            ],
                        },
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"batch": "{{nodes.each.output}}"}},
                },
            ],
            "edges": [
                {"id": "e1", "source": "start", "target": "each"},
                {"id": "e2", "source": "each", "target": "end"},
            ],
        }
    )


async def _run(workflow: WorkflowDSL, emitter: FakeEmitter) -> dict[str, Any]:
    graph = WorkflowCompiler().compile(workflow, make_ctx(emitter))
    return await graph.ainvoke(
        {
            "inputs": {"items": [1, 2, 3, 4, 5]},
            "node_outputs": {},
            "loop_counts": {},
            "messages": [],
            "route": None,
            "error": None,
            "final_output": None,
        },
        config={"recursion_limit": 50},
    )


@pytest.fixture
def probe() -> Generator[_ProbeExecutor]:
    executor = _ProbeExecutor()
    register_executor_instance("test.iteration_probe", executor)
    yield executor
    clear_external_executors()


async def test_iteration_preserves_order_and_caps_concurrency(
    fake_emitter: FakeEmitter, probe: _ProbeExecutor
) -> None:
    result = await _run(_workflow(concurrency=2), fake_emitter)
    batch = result["node_outputs"]["each"]["output"]

    assert probe.max_active == 2
    assert [entry["output"] for entry in batch["results"]] == [
        {"value": 2, "index": 0},
        {"value": 4, "index": 1},
        {"value": 6, "index": 2},
        {"value": 8, "index": 3},
        {"value": 10, "index": 4},
    ]
    assert batch["items_total"] == 5
    assert batch["items_succeeded"] == 5
    assert batch["items_failed"] == 0
    started = fake_emitter.of_type(EventType.NODE_STARTED)
    assert any(event["node_id"] == "each[0].work" for event in started)
    assert {
        event["node_id"]
        for event in started
        if event["node_id"].startswith("each[") and event["node_id"].endswith(".work")
    } == {f"each[{index}].work" for index in range(5)}


async def test_iteration_compiles_child_graph_once(
    fake_emitter: FakeEmitter,
    probe: _ProbeExecutor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_compile = WorkflowCompiler.compile
    compile_count = 0

    def counting_compile(self: WorkflowCompiler, *args: Any, **kwargs: Any):
        nonlocal compile_count
        compile_count += 1
        return original_compile(self, *args, **kwargs)

    monkeypatch.setattr(WorkflowCompiler, "compile", counting_compile)
    await _run(_workflow(concurrency=3), fake_emitter)

    assert compile_count == 2


async def test_iteration_processes_one_hundred_items_in_order(
    fake_emitter: FakeEmitter, probe: _ProbeExecutor
) -> None:
    graph = WorkflowCompiler().compile(_workflow(concurrency=8), make_ctx(fake_emitter))
    result = await graph.ainvoke(
        {
            "inputs": {"items": list(range(100))},
            "node_outputs": {},
            "loop_counts": {},
            "messages": [],
            "route": None,
            "error": None,
            "final_output": None,
        },
        config={"recursion_limit": 50},
    )
    batch = result["node_outputs"]["each"]["output"]

    assert probe.max_active <= 8
    assert batch["items_total"] == 100
    assert batch["items_succeeded"] == 100
    assert batch["items_failed"] == 0
    assert [entry["output"]["value"] for entry in batch["results"]] == [
        item * 2 for item in range(100)
    ]


@pytest.mark.parametrize(
    ("strategy", "result_statuses"),
    [("skip", ["succeeded", "succeeded"]), ("collect_error", ["succeeded", "failed", "succeeded"])],
)
async def test_iteration_failure_collection(
    fake_emitter: FakeEmitter,
    probe: _ProbeExecutor,
    strategy: str,
    result_statuses: list[str],
) -> None:
    workflow = _workflow(strategy=strategy)
    graph = WorkflowCompiler().compile(workflow, make_ctx(fake_emitter))
    result = await graph.ainvoke(
        {
            "inputs": {"items": [1, -1, 2]},
            "node_outputs": {},
            "loop_counts": {},
            "messages": [],
            "route": None,
            "error": None,
            "final_output": None,
        },
        config={"recursion_limit": 50},
    )
    batch = result["node_outputs"]["each"]["output"]

    assert [entry["status"] for entry in batch["results"]] == result_statuses
    assert batch["items_succeeded"] == 2
    assert batch["items_failed"] == 1
    assert batch["failures"][0]["index"] == 1
    assert "rejected item -1" in batch["failures"][0]["error"]


async def test_iteration_abort_fails_parent_node(
    fake_emitter: FakeEmitter, probe: _ProbeExecutor
) -> None:
    graph = WorkflowCompiler().compile(
        _workflow(strategy="abort", concurrency=1), make_ctx(fake_emitter)
    )

    with pytest.raises(RuntimeError, match="iteration item 0 failed"):
        await graph.ainvoke(
            {
                "inputs": {"items": [-1, 1, 2]},
                "node_outputs": {},
                "loop_counts": {},
                "messages": [],
                "route": None,
                "error": None,
                "final_output": None,
            },
            config={"recursion_limit": 50},
        )
    assert probe.seen == [-1]


async def test_iteration_namespaces_nested_edge_events(fake_emitter: FakeEmitter) -> None:
    workflow = WorkflowDSL.model_validate(
        {
            "name": "iteration-edge-events",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {
                        "items": "{{input.items}}",
                        "subgraph": {
                            "nodes": [
                                {"id": "item_start", "type": "start", "config": {}},
                                {
                                    "id": "branch",
                                    "type": "condition",
                                    "config": {
                                        "branches": [
                                            {
                                                "id": "match",
                                                "group": {
                                                    "rules": [
                                                        {
                                                            "left": "{{input.item}}",
                                                            "operator": "eq",
                                                            "right": 1,
                                                        }
                                                    ]
                                                },
                                            }
                                        ],
                                        "default_branch": "else",
                                    },
                                },
                                {"id": "matched", "type": "end", "config": {}},
                                {"id": "fallback", "type": "end", "config": {}},
                            ],
                            "edges": [
                                {
                                    "id": "to_branch",
                                    "source": "item_start",
                                    "target": "branch",
                                },
                                {
                                    "id": "to_match",
                                    "source": "branch",
                                    "source_handle": "match",
                                    "target": "matched",
                                },
                                {
                                    "id": "to_fallback",
                                    "source": "branch",
                                    "source_handle": "else",
                                    "target": "fallback",
                                },
                            ],
                        },
                    },
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "outer_1", "source": "start", "target": "each"},
                {"id": "outer_2", "source": "each", "target": "end"},
            ],
        }
    )

    await _run(workflow, fake_emitter)

    edge = fake_emitter.of_type(EventType.EDGE_TAKEN)[0]
    assert edge["node_id"] == "each[0].branch"
    assert edge["payload"]["source"] == "each[0].branch"
    assert edge["payload"]["target"] == "each[0].matched"
    assert edge["payload"]["node_path"] == "each.branch"
    assert edge["payload"]["node_path_segments"] == ["each", "branch"]


def test_iteration_rejects_invalid_nested_graph(probe: _ProbeExecutor) -> None:
    workflow = _workflow()
    workflow.nodes[1].config["subgraph"]["nodes"] = []

    with pytest.raises(ValueError, match="invalid iteration subgraph"):
        WorkflowCompiler().compile(workflow, make_ctx(FakeEmitter()))


def test_iteration_rejects_non_durable_human_child(probe: _ProbeExecutor) -> None:
    workflow = _workflow()
    workflow.nodes[1].config["subgraph"] = {
        "nodes": [
            {"id": "item_start", "type": "start", "config": {}},
            {"id": "approval", "type": "human", "config": {}},
            {"id": "item_end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "i1", "source": "item_start", "target": "approval"},
            {"id": "i2", "source": "approval", "target": "item_end"},
        ],
    }

    with pytest.raises(ValueError, match="do not support human approval"):
        WorkflowCompiler().compile(workflow, make_ctx(FakeEmitter()))


def _looping_iteration(*, recursion_limit: int) -> WorkflowDSL:
    return WorkflowDSL.model_validate(
        {
            "name": "iteration-loop-guards",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {
                        "items": "{{input.items}}",
                        "recursion_limit": recursion_limit,
                        "subgraph": {
                            "nodes": [
                                {"id": "item_start", "type": "start", "config": {}},
                                {
                                    "id": "repeat",
                                    "type": "condition",
                                    "config": {
                                        "branches": [
                                            {
                                                "id": "loop",
                                                "group": {
                                                    "rules": [
                                                        {
                                                            "left": "{{input.item}}",
                                                            "operator": "not_empty",
                                                        }
                                                    ]
                                                },
                                            }
                                        ],
                                        "default_branch": "else",
                                    },
                                },
                                {"id": "item_end", "type": "end", "config": {}},
                            ],
                            "edges": [
                                {
                                    "id": "to_repeat",
                                    "source": "item_start",
                                    "target": "repeat",
                                },
                                {
                                    "id": "repeat_loop",
                                    "source": "repeat",
                                    "source_handle": "loop",
                                    "target": "repeat",
                                },
                                {
                                    "id": "repeat_end",
                                    "source": "repeat",
                                    "source_handle": "else",
                                    "target": "item_end",
                                },
                            ],
                        },
                    },
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "outer_1", "source": "start", "target": "each"},
                {"id": "outer_2", "source": "each", "target": "end"},
            ],
        }
    )


@pytest.mark.parametrize(
    ("max_loop_iterations", "recursion_limit", "message"),
    [(2, 50, "loop limit exceeded"), (100, 3, "Recursion limit")],
)
async def test_iteration_child_enforces_both_loop_guards(
    fake_emitter: FakeEmitter,
    max_loop_iterations: int,
    recursion_limit: int,
    message: str,
) -> None:
    graph = WorkflowCompiler().compile(
        _looping_iteration(recursion_limit=recursion_limit),
        make_ctx(fake_emitter, max_loop_iterations=max_loop_iterations),
    )

    with pytest.raises(RuntimeError, match=message):
        await graph.ainvoke(
            {
                "inputs": {"items": [1]},
                "node_outputs": {},
                "loop_counts": {},
                "messages": [],
                "route": None,
                "error": None,
                "final_output": None,
            },
            config={"recursion_limit": 100},
        )


def test_iteration_config_schema_is_published_through_node_types() -> None:
    metadata = next(item for item in list_node_types() if item["type"] == "iteration")
    schema = metadata["config_schema"]

    assert set(schema["required"]) == {"subgraph"}
    assert schema["properties"]["batch_size"]["maximum"] == 1_000
    assert schema["properties"]["concurrency_limit"]["maximum"] == 100
    assert schema["properties"]["failure_strategy"]["enum"] == [
        "abort",
        "skip",
        "collect_error",
    ]
    assert schema["properties"]["subgraph"]["$ref"].endswith("/$defs/IterationSubgraph")


def test_iteration_processes_one_hundred_items_over_http(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs=",
        rate_limit_execution_requests=100,
    )
    name = "Iteration HTTP acceptance"
    workflow = {
        "name": name,
        "dsl": {
            "version": "1.0",
            "name": name,
            "variables": [{"name": "items", "type": "array", "required": True}],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 30,
                "recursion_limit": 50,
            },
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "each",
                    "type": "iteration",
                    "config": {
                        "items": "{{input.items}}",
                        "batch_size": 25,
                        "concurrency_limit": 10,
                        "failure_strategy": "abort",
                        "subgraph": {
                            "nodes": [
                                {"id": "item_start", "type": "start", "config": {}},
                                {
                                    "id": "item_end",
                                    "type": "end",
                                    "config": {
                                        "output_template": {
                                            "value": "{{input.item}}",
                                            "index": "{{input.index}}",
                                        }
                                    },
                                },
                            ],
                            "edges": [
                                {
                                    "id": "item_edge",
                                    "source": "item_start",
                                    "target": "item_end",
                                }
                            ],
                        },
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"batch": "{{nodes.each.output}}"}},
                },
            ],
            "edges": [
                {"id": "edge_1", "source": "start", "target": "each"},
                {"id": "edge_2", "source": "each", "target": "end"},
            ],
        },
    }

    with TestClient(create_app(settings)) as client:
        created = client.post("/api/workflows", json=workflow)
        assert created.status_code == 201, created.text
        started = client.post(
            f"/api/workflows/{created.json()['id']}/run",
            json={"inputs": {"items": list(range(100))}},
        )
        assert started.status_code == 201, started.text

        deadline = time.monotonic() + 45
        execution: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = client.get(f"/api/executions/{started.json()['id']}")
            assert response.status_code == 200, response.text
            execution = response.json()
            if execution["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"iteration execution did not finish: {execution}")

        assert execution["status"] == "succeeded", execution
        batch = execution["output_json"]["batch"]
        assert batch["items_total"] == 100
        assert batch["items_succeeded"] == 100
        assert batch["items_failed"] == 0
        assert [entry["output"] for entry in batch["results"]] == [
            {"value": item, "index": index} for index, item in enumerate(range(100))
        ]
