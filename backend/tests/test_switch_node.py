"""Switch node (C2-4) multi-way routing and merge semantics tests."""

from __future__ import annotations

import pytest

from app.engine.compiler import WorkflowCompiler
from app.engine.nodes import list_node_types
from app.engine.nodes.base import get_executor
from app.schemas.dsl import WorkflowDSL
from tests.conftest import FakeEmitter, make_ctx


def _three_way_workflow() -> WorkflowDSL:
    return WorkflowDSL.model_validate({
        "name": "three-way switch",
        "nodes": [
            {"id": "s", "type": "start", "config": {
                "input_schema": [{"name": "n", "type": "number", "required": True}]
            }},
            {"id": "sw", "type": "switch", "config": {
                "branches": [
                    {"id": "small", "group": {"op": "and", "rules": [
                        {"left": "{{input.n}}", "operator": "lt", "right": 10}]}},
                    {"id": "medium", "group": {"op": "and", "rules": [
                        {"left": "{{input.n}}", "operator": "lt", "right": 100}]}},
                ],
                "default_branch": "large",
                "merge_strategy": "first",
            }},
            {"id": "a", "type": "agent", "config": {"system_prompt": "s", "user_prompt": "small"}},
            {"id": "b", "type": "agent", "config": {"system_prompt": "s", "user_prompt": "medium"}},
            {"id": "c", "type": "agent", "config": {"system_prompt": "s", "user_prompt": "large"}},
            {"id": "e", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "1", "source": "s", "target": "sw"},
            {"id": "2", "source": "sw", "target": "a", "source_handle": "small"},
            {"id": "3", "source": "sw", "target": "b", "source_handle": "medium"},
            {"id": "4", "source": "sw", "target": "c", "source_handle": "large"},
            {"id": "5", "source": "a", "target": "e"},
            {"id": "6", "source": "b", "target": "e"},
            {"id": "7", "source": "c", "target": "e"},
        ],
    })


async def _branch_for(n: int) -> str:
    emitter = FakeEmitter()
    graph = WorkflowCompiler().compile(_three_way_workflow(), make_ctx(emitter))
    result = await graph.ainvoke(
        {"inputs": {"n": n}, "node_outputs": {}, "loop_counts": {}, "messages": [],
         "route": None, "error": None, "final_output": None},
        config={"recursion_limit": 50},
    )
    return str(result["node_outputs"]["sw"]["branch"])


@pytest.mark.asyncio
async def test_switch_routes_first_matching_branch() -> None:
    assert await _branch_for(5) == "small"


@pytest.mark.asyncio
async def test_switch_routes_second_branch() -> None:
    assert await _branch_for(50) == "medium"


@pytest.mark.asyncio
async def test_switch_routes_default_when_no_match() -> None:
    assert await _branch_for(500) == "large"


@pytest.mark.asyncio
async def test_switch_records_merge_strategy_in_output() -> None:
    emitter = FakeEmitter()
    graph = WorkflowCompiler().compile(_three_way_workflow(), make_ctx(emitter))
    result = await graph.ainvoke(
        {"inputs": {"n": 5}, "node_outputs": {}, "loop_counts": {}, "messages": [],
         "route": None, "error": None, "final_output": None},
        config={"recursion_limit": 50},
    )
    assert result["node_outputs"]["sw"]["merge_strategy"] == "first"


def test_switch_registered_and_metadata_exposed() -> None:
    types = {t["type"] for t in list_node_types()}
    assert "switch" in types
    meta = get_executor("switch").metadata()
    assert meta["label"] == "Switch"
    assert "merge_strategy" in meta["config_schema"]["properties"]
    assert "branches" in meta["config_schema"]["properties"]


def test_switch_config_defaults() -> None:
    from app.schemas.dsl import SwitchConfig

    cfg = SwitchConfig.model_validate(
        {"branches": [{"id": "a", "group": {"op": "and", "rules": []}}]}
    )
    assert cfg.default_branch == "else"
    assert cfg.merge_strategy.value == "last"


def test_switch_config_rejects_too_many_branches() -> None:
    from pydantic import ValidationError

    from app.schemas.dsl import SwitchConfig

    branches = [{"id": f"b{i}", "group": {"op": "and", "rules": []}} for i in range(33)]
    with pytest.raises(ValidationError):
        SwitchConfig.model_validate({"branches": branches})


def test_switch_default_branch_required_nonempty() -> None:
    from pydantic import ValidationError

    from app.schemas.dsl import SwitchConfig

    with pytest.raises(ValidationError):
        SwitchConfig(branches=[], default_branch="")
