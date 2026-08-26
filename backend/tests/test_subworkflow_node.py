"""Subworkflow node (C2-5) embedding, mapping, namespacing, and cycle tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.db.base import Base, create_engine, create_session_factory
from app.db.models import Workflow, WorkflowVersion
from app.engine.compiler import WorkflowCompiler
from app.engine.nodes import list_node_types
from app.engine.nodes.base import CompileContext, get_executor
from app.engine.subworkflow_resolver import (
    SubworkflowCache,
    SubworkflowCycleError,
    resolve_subworkflows,
)
from app.schemas.dsl import SubworkflowConfig, WorkflowDSL
from app.schemas.events import EventType
from app.services.workflow_subworkflow_loader import build_subworkflow_loader
from tests.conftest import FakeEmitter, make_ctx


def _child_dsl() -> WorkflowDSL:
    """A published child workflow that doubles its input and returns it."""
    return WorkflowDSL.model_validate(
        {
            "name": "child",
            "variables": [{"name": "n", "type": "number", "required": True}],
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "n", "type": "number", "required": True}]
                    },
                },
                {
                    "id": "doubler",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": 'output = {"doubled": inputs["n"] * 2}',
                        "inputs": {"n": "{{input.n}}"},
                        "timeout_seconds": 5.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"doubled": "{{nodes.doubler.output.doubled}}"}},
                },
            ],
            "edges": [
                {"id": "1", "source": "start", "target": "doubler"},
                {"id": "2", "source": "doubler", "target": "end"},
            ],
        }
    )


def _parent_workflow(
    *,
    input_mapping: dict[str, str] | None = None,
    output_mapping: dict[str, str] | None = None,
) -> WorkflowDSL:
    mapping = input_mapping if input_mapping is not None else {"n": "{{input.value}}"}
    return WorkflowDSL.model_validate(
        {
            "name": "parent",
            "variables": [{"name": "value", "type": "number", "required": True}],
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "config": {
                        "input_schema": [{"name": "value", "type": "number", "required": True}]
                    },
                },
                {
                    "id": "sub",
                    "type": "subworkflow",
                    "config": {
                        "workflow_id": "child-wf",
                        "version_id": "child-v1",
                        "input_mapping": mapping,
                        "output_mapping": output_mapping or {},
                        "recursion_limit": 50,
                    },
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "1", "source": "start", "target": "sub"},
                {"id": "2", "source": "sub", "target": "end"},
            ],
        }
    )


def _make_ctx(emitter: FakeEmitter, child: WorkflowDSL | None = None) -> CompileContext:
    ctx = make_ctx(emitter)
    cache = SubworkflowCache({("child-wf", "child-v1"): child or _child_dsl()})
    object.__setattr__(ctx, "subworkflow_loader", cache.lookup)
    return ctx


async def _invoke(parent: WorkflowDSL, ctx: CompileContext, *, value: int = 21) -> dict[str, Any]:
    graph = WorkflowCompiler().compile(parent, ctx)
    return await graph.ainvoke(
        {
            "inputs": {"value": value},
            "node_outputs": {},
            "loop_counts": {},
            "messages": [],
            "route": None,
            "error": None,
            "final_output": None,
        },
        config={"recursion_limit": 100},
    )


@pytest.mark.asyncio
async def test_subworkflow_embeds_child_and_maps_output() -> None:
    emitter = FakeEmitter()
    result = await _invoke(_parent_workflow(), _make_ctx(emitter), value=21)
    # The child doubled 21 -> 42; default output mapping exposes the whole child output.
    assert result["node_outputs"]["sub"]["output"]["doubled"] == 42


@pytest.mark.asyncio
async def test_subworkflow_input_mapping_renders_templates() -> None:
    emitter = FakeEmitter()
    result = await _invoke(_parent_workflow(), _make_ctx(emitter), value=10)
    assert result["node_outputs"]["sub"]["output"]["doubled"] == 20


@pytest.mark.asyncio
async def test_subworkflow_output_mapping_dotted_path() -> None:
    emitter = FakeEmitter()
    parent = _parent_workflow(output_mapping={"result": "doubled"})
    result = await _invoke(parent, _make_ctx(emitter), value=7)
    assert result["node_outputs"]["sub"]["output"]["result"] == 14


@pytest.mark.asyncio
async def test_subworkflow_namespaces_child_events_under_node() -> None:
    emitter = FakeEmitter()
    await _invoke(_parent_workflow(), _make_ctx(emitter), value=3)
    # Child node events are qualified as "sub.<child_node>".
    node_started = emitter.events
    child_starts = [e for e in node_started if e["type"] == EventType.NODE_STARTED and (e["node_id"] or "").startswith("sub.doubler")]
    assert child_starts, "child node events must be namespaced under the subworkflow node"
    # Each child event carries a node_path_segments prefix with the parent node id.
    for event in child_starts:
        segments = event["payload"].get("node_path_segments") or []
        assert segments[0] == "sub"


@pytest.mark.asyncio
async def test_subworkflow_emits_started_and_finished_markers() -> None:
    emitter = FakeEmitter()
    await _invoke(_parent_workflow(), _make_ctx(emitter), value=5)
    markers = [
        e for e in emitter.events
        if e["type"] == EventType.NODE_STREAMING and e["node_id"] == "sub"
    ]
    kinds = [m["payload"].get("kind") for m in markers]
    assert "subworkflow_started" in kinds
    assert "subworkflow_finished" in kinds


def test_subworkflow_registered_and_metadata_exposed() -> None:
    types = {t["type"] for t in list_node_types()}
    assert "subworkflow" in types
    meta = get_executor("subworkflow").metadata()
    assert meta["label"] == "Subworkflow"
    props = meta["config_schema"]["properties"]
    for key in ("workflow_id", "version_id", "input_mapping", "output_mapping", "recursion_limit"):
        assert key in props


def test_subworkflow_config_defaults() -> None:
    cfg = SubworkflowConfig(workflow_id="wf", version_id="v1")
    assert cfg.input_mapping == {}
    assert cfg.output_mapping == {}
    assert cfg.recursion_limit == 50


def test_subworkflow_config_rejects_same_workflow_and_version_id() -> None:
    # validate_config is enforced at the executor (DSL save/compile boundary),
    # not by the Pydantic model, so it pairs with the loader's id checks.
    executor = get_executor("subworkflow")
    with pytest.raises(ValueError, match="must differ"):
        executor.validate_config({"workflow_id": "same", "version_id": "same"})


def test_subworkflow_config_rejects_blank_workflow_id() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SubworkflowConfig(workflow_id="", version_id="v1")


def test_subworkflow_config_allows_blank_version_id_for_current_resolution() -> None:
    # Empty version_id means "resolve the workflow's current published version".
    cfg = SubworkflowConfig(workflow_id="wf", version_id="")
    assert cfg.version_id == ""


def test_subworkflow_config_enforces_recursion_limit_bounds() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SubworkflowConfig(workflow_id="wf", version_id="v1", recursion_limit=1)
    with pytest.raises(ValidationError):
        SubworkflowConfig(workflow_id="wf", version_id="v1", recursion_limit=1001)


def test_subworkflow_loader_missing_raises_at_build() -> None:
    emitter = FakeEmitter()
    ctx = make_ctx(emitter)  # no subworkflow_loader
    with pytest.raises(Exception, match="subworkflow_loader"):
        WorkflowCompiler().compile(_parent_workflow(), ctx)


def test_subworkflow_missing_version_surfaces_value_error() -> None:
    emitter = FakeEmitter()
    cache = SubworkflowCache({})  # referenced version absent
    ctx = make_ctx(emitter)
    object.__setattr__(ctx, "subworkflow_loader", cache.lookup)
    with pytest.raises(ValueError, match="could not be loaded"):
        WorkflowCompiler().compile(_parent_workflow(), ctx)


@pytest.mark.asyncio
async def test_resolve_subworkflows_detects_cycle() -> None:
    # A -> B -> A: build two DSLs referencing each other.
    a = WorkflowDSL.model_validate(
        {
            "name": "a",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "sub",
                    "type": "subworkflow",
                    "config": {"workflow_id": "b", "version_id": "b-v1"},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "1", "source": "start", "target": "sub"},
                {"id": "2", "source": "sub", "target": "end"},
            ],
        }
    )
    b = WorkflowDSL.model_validate(
        {
            "name": "b",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "sub",
                    "type": "subworkflow",
                    "config": {"workflow_id": "a", "version_id": "a-v1"},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "1", "source": "start", "target": "sub"},
                {"id": "2", "source": "sub", "target": "end"},
            ],
        }
    )

    async def loader(wf_id: str, _ver: str, _anc: frozenset[str]) -> WorkflowDSL | None:
        return {"a": a, "b": b}.get(wf_id)

    with pytest.raises(SubworkflowCycleError, match="cyclic"):
        await resolve_subworkflows(a, loader)


@pytest.mark.asyncio
async def test_resolve_subworkflows_dedupes_shared_children() -> None:
    # Two subworkflow nodes referencing the same version should load it once.
    parent = WorkflowDSL.model_validate(
        {
            "name": "parent",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "sub1",
                    "type": "subworkflow",
                    "config": {"workflow_id": "child", "version_id": "child-v1"},
                },
                {
                    "id": "sub2",
                    "type": "subworkflow",
                    "config": {"workflow_id": "child", "version_id": "child-v1"},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "1", "source": "start", "target": "sub1"},
                {"id": "2", "source": "sub1", "target": "sub2"},
                {"id": "3", "source": "sub2", "target": "end"},
            ],
        }
    )
    calls: list[str] = []

    async def loader(wf_id: str, _ver: str, _anc: frozenset[str]) -> WorkflowDSL | None:
        calls.append(wf_id)
        return _child_dsl()

    cache = await resolve_subworkflows(parent, loader)
    assert len(cache) == 1
    assert calls == ["child"]


@pytest.mark.asyncio
async def test_subworkflow_runtime_propagates_child_failure() -> None:
    emitter = FakeEmitter()
    bad_child = WorkflowDSL.model_validate(
        {
            "name": "bad child",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "boom",
                    "type": "code",
                    "config": {
                        "language": "python",
                        "source": "raise RuntimeError('boom')",
                        "inputs": {},
                        "timeout_seconds": 5.0,
                        "memory_limit_mb": 64,
                        "process_count": 8,
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "config": {"output_template": {"r": "{{nodes.boom.output}}"}},
                },
            ],
            "edges": [
                {"id": "1", "source": "start", "target": "boom"},
                {"id": "2", "source": "boom", "target": "end"},
            ],
        }
    )
    ctx = _make_ctx(emitter, child=bad_child)
    with pytest.raises(RuntimeError, match="subworkflow node 'sub' failed"):
        await _invoke(_parent_workflow(), ctx, value=1)


@pytest.mark.asyncio
async def test_db_subworkflow_loader_resolves_explicit_and_current_published(
    tmp_path: Path,
) -> None:
    engine = create_engine(Settings(data_dir=tmp_path))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    try:
        dsl_json = _child_dsl().model_dump(mode="json")
        async with factory() as session:
            session.add(
                Workflow(
                    id="child-wf",
                    name="Child",
                    dsl_json=dsl_json,
                    version=2,
                )
            )
            session.add_all(
                [
                    WorkflowVersion(
                        id="child-v1",
                        workflow_id="child-wf",
                        number=1,
                        status="published",
                        name="Child v1",
                        dsl_json=dsl_json,
                    ),
                    WorkflowVersion(
                        id="child-v2",
                        workflow_id="child-wf",
                        number=2,
                        status="draft",
                        name="Child v2",
                        dsl_json=dsl_json,
                    ),
                ]
            )
            await session.commit()

        loader = build_subworkflow_loader(factory)
        explicit = await loader("child-wf", "child-v1", frozenset())
        current = await loader("child-wf", "", frozenset())
        assert explicit is not None
        assert current is not None
        assert explicit.name == current.name == "child"
        assert explicit.nodes == current.nodes
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_db_subworkflow_loader_rejects_missing_mismatched_unpublished_and_invalid(
    tmp_path: Path,
) -> None:
    engine = create_engine(Settings(data_dir=tmp_path))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    try:
        dsl_json = _child_dsl().model_dump(mode="json")
        async with factory() as session:
            session.add_all(
                [
                    Workflow(
                        id="child-wf",
                        name="Child",
                        dsl_json=dsl_json,
                    ),
                    Workflow(
                        id="other-wf",
                        name="Other",
                        dsl_json=dsl_json,
                    ),
                    WorkflowVersion(
                        id="draft-v1",
                        workflow_id="child-wf",
                        number=1,
                        status="draft",
                        name="Draft",
                        dsl_json=dsl_json,
                    ),
                    WorkflowVersion(
                        id="other-v1",
                        workflow_id="other-wf",
                        number=1,
                        status="published",
                        name="Other",
                        dsl_json=dsl_json,
                    ),
                    WorkflowVersion(
                        id="invalid-v2",
                        workflow_id="child-wf",
                        number=2,
                        status="published",
                        name="Invalid",
                        dsl_json={"version": "2.0"},
                    ),
                ]
            )
            await session.commit()

        loader = build_subworkflow_loader(factory)
        assert await loader("child-wf", "missing", frozenset()) is None
        assert await loader("child-wf", "other-v1", frozenset()) is None
        assert await loader("child-wf", "draft-v1", frozenset()) is None
        assert await loader("child-wf", "invalid-v2", frozenset()) is None
    finally:
        await engine.dispose()
