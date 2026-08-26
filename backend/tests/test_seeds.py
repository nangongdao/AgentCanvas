"""Tests for the P6 seed workflows (valid DSL + compilable)."""

from __future__ import annotations

import pytest

from app.engine.compiler import WorkflowCompiler
from app.engine.nodes.base import CompileContext
from app.engine.validation import validate_dsl
from app.schemas.dsl import WorkflowDSL
from app.services.seeds import SEED_WORKFLOWS
from tests.conftest import FakeChatProvider, FakeEmitter


@pytest.mark.parametrize("seed", SEED_WORKFLOWS, ids=[s["id"] for s in SEED_WORKFLOWS])
def test_seed_dsl_validates(seed) -> None:
    dsl = WorkflowDSL.model_validate(seed["dsl"])
    validate_dsl(dsl)  # raises on invalid


@pytest.mark.parametrize("seed", SEED_WORKFLOWS, ids=[s["id"] for s in SEED_WORKFLOWS])
async def test_seed_dsl_compiles(seed, fake_emitter: FakeEmitter) -> None:
    dsl = WorkflowDSL.model_validate(seed["dsl"])
    provider = FakeChatProvider(["ok"])
    ctx = CompileContext(
        execution_id="seed-test",
        emitter=fake_emitter,  # type: ignore[arg-type]
        get_provider=lambda _mid: provider,
        max_loop_iterations=10,
    )
    # demo-subworkflow embeds demo-linear; supply a stub loader so the child
    # graph compiles. Real resolution is covered in test_subworkflow_node.
    if seed["id"] == "demo-subworkflow":
        from app.engine.subworkflow_resolver import SubworkflowCache

        child = WorkflowDSL.model_validate(
            next(s for s in SEED_WORKFLOWS if s["id"] == "demo-linear")["dsl"]
        )
        cache = SubworkflowCache({("demo-linear", ""): child})
        object.__setattr__(ctx, "subworkflow_loader", cache.lookup)
    graph = WorkflowCompiler().compile(dsl, ctx)
    assert graph is not None


def test_seed_ids_unique() -> None:
    ids = [s["id"] for s in SEED_WORKFLOWS]
    assert len(ids) == len(set(ids))
