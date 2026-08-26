"""Deterministic helpers behind the U4 runtime performance gate."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.core.container import build_container
from app.core.observability import Observability
from app.db.models import Execution, WorkflowVersion
from scripts.performance_budget import (
    assert_budgets,
    assert_execution_budgets,
    percentile,
    seed_replay,
    timed_wave,
)


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([5, 1, 4, 2, 3], 0.50) == 3
    assert percentile([5, 1, 4, 2, 3], 0.95) == 5
    assert percentile([], 0.95) == 0


def test_budget_rejects_latency_and_replay_regressions() -> None:
    report = {
        "execution_create": {"50": {"p95_ms": 500}},
        "pagination_10k": {"p95_ms": 300},
        "sse_replay_1000": {
            "events": 999,
            "contiguous": False,
            "duplicate_terminal": True,
        },
    }
    with pytest.raises(RuntimeError, match="performance budget failed"):
        assert_budgets(report)


def test_execution_only_budget_rejects_selected_level() -> None:
    report = {"execution_create": {"10": {"p95_ms": 1_500}}}
    with pytest.raises(RuntimeError, match="execution create c=10"):
        assert_execution_budgets(report)


@pytest.mark.asyncio
async def test_timed_wave_cancels_and_awaits_siblings_after_failure() -> None:
    sibling_started = asyncio.Event()
    sibling_stopped = asyncio.Event()

    async def operation(index: int) -> httpx.Response:
        if index == 0:
            await sibling_started.wait()
            return httpx.Response(500, text="failed")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("cancelled sibling unexpectedly resumed")
        finally:
            sibling_stopped.set()

    with pytest.raises(RuntimeError, match="operation returned 500"):
        await asyncio.wait_for(timed_wave(2, operation), timeout=1)

    assert sibling_stopped.is_set()


@pytest.mark.asyncio
async def test_replay_seed_binds_current_workflow_version(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="disabled",
        secret_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    container = await build_container(
        settings,
        observability=Observability(environment="test"),
    )
    try:
        app = SimpleNamespace(state=SimpleNamespace(container=container))
        assert await seed_replay(app) == "perf-sse-replay"
        async with container.session_factory() as session:
            execution = await session.get(Execution, "perf-sse-replay")
            assert execution is not None
            version = await session.scalar(
                select(WorkflowVersion).where(
                    WorkflowVersion.id == execution.workflow_version_id
                )
            )
            assert version is not None
            assert version.workflow_id == "demo-linear"
    finally:
        await container.shutdown()
