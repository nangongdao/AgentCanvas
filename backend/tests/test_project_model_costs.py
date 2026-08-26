"""Execution model-call integration for project monthly cost quotas."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.security import create_secret_box
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Organization
from app.db.repositories import ExecutionQueueRepo, ExecutionRepo, ProjectRepo, WorkflowRepo
from app.engine.events import EventBus
from app.engine.execution_lease import LeaseLost, WorkerLease
from app.engine.executor import ExecutionEngine
from app.providers.base import (
    BaseChatProvider,
    ChatMessage,
    StreamChunk,
    ToolSchema,
    Usage,
)
from app.services.project_model_costs import ProjectModelCostMeter, ProjectModelCostUnavailable
from app.services.project_quotas import ProjectQuotaExceeded, ProjectQuotaService


class _CallBarrier:
    def __init__(self, parties: int) -> None:
        self.parties = parties
        self.arrived = 0
        self.ready = asyncio.Event()

    async def wait(self) -> None:
        self.arrived += 1
        if self.arrived >= self.parties:
            self.ready.set()
        await self.ready.wait()


class _UsageProvider(BaseChatProvider):
    name = "project-cost-test"

    def __init__(
        self,
        usage_chunks: Sequence[Usage],
        *,
        prompt_price: str | None,
        completion_price: str | None,
        barrier: _CallBarrier | None = None,
    ) -> None:
        super().__init__(
            model="project-cost-model",
            prompt_price_per_million_usd=prompt_price,
            completion_price_per_million_usd=completion_price,
            pricing_version="project-cost-v1",
        )
        self.usage_chunks = tuple(usage_chunks)
        self.barrier = barrier
        self.calls = 0

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSchema] = (),
        **params: object,
    ) -> AsyncIterator[StreamChunk]:
        del messages, tools, params
        self.calls += 1
        if self.barrier is not None:
            await self.barrier.wait()
        yield StreamChunk(type="text", text="priced")
        for usage in self.usage_chunks:
            yield StreamChunk(type="usage", usage=usage)
        yield StreamChunk(type="done")


async def _environment(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        model_max_concurrent=4,
        model_max_calls_per_execution=10,
    )
    await upgrade_database(settings)
    sql_engine = create_engine(settings)
    sessions = create_session_factory(sql_engine)
    async with sessions() as session:
        organization = Organization(name="Cost Org", slug="cost-org")
        session.add(organization)
        await session.flush()
        project = await ProjectRepo(session).create(organization.id, "Cost Project", "cost-project")
        await session.commit()
        project_id = project.id
    execution_engine = ExecutionEngine(
        session_factory=sessions,
        event_bus=EventBus(),
        secret_box=create_secret_box(settings),
        settings=settings,
    )
    return execution_engine, sql_engine, sessions, project_id


async def _configure(sessions, project_id: str, limit: int | None) -> None:
    async with sessions() as session:
        await ProjectQuotaService(session).configure(
            project_id, monthly_model_cost_units_limit=limit
        )
        await session.commit()


async def _usage(sessions, project_id: str) -> int:
    async with sessions() as session:
        return (await ProjectQuotaService(session).snapshot(project_id)).model_cost_units


async def test_execution_budget_charges_exact_units_and_preflights_ceiling(
    tmp_path: Path,
) -> None:
    engine, sql_engine, sessions, project_id = await _environment(tmp_path)
    await _configure(sessions, project_id, 1_000_000)
    provider = _UsageProvider(
        [Usage(prompt_tokens=2, total_tokens=2)],
        prompt_price="0.5",
        completion_price="0.5",
    )
    budget = engine._model_budget_for("exact-cost", project_id)  # noqa: SLF001
    assert budget is not None
    try:
        assert (await budget.chat(provider, [])).content == "priced"
        assert await _usage(sessions, project_id) == 1_000_000
        with pytest.raises(ProjectQuotaExceeded):
            await budget.chat(provider, [])
        assert provider.calls == 1
    finally:
        await sql_engine.dispose()


async def test_finite_quota_rejects_unknown_pricing_before_provider(
    tmp_path: Path,
) -> None:
    engine, sql_engine, sessions, project_id = await _environment(tmp_path)
    await _configure(sessions, project_id, 1_000_000)
    provider = _UsageProvider(
        [Usage(prompt_tokens=1, total_tokens=1)],
        prompt_price=None,
        completion_price=None,
    )
    budget = engine._model_budget_for("unknown-price", project_id)  # noqa: SLF001
    assert budget is not None
    try:
        with pytest.raises(ProjectModelCostUnavailable, match="pricing"):
            await budget.chat(provider, [])
        assert provider.calls == 0
        assert await _usage(sessions, project_id) == 0
    finally:
        await sql_engine.dispose()


async def test_finite_quota_requires_reported_usage(tmp_path: Path) -> None:
    engine, sql_engine, sessions, project_id = await _environment(tmp_path)
    await _configure(sessions, project_id, 1_000_000)
    provider = _UsageProvider([], prompt_price="0.5", completion_price="0.5")
    budget = engine._model_budget_for("missing-usage", project_id)  # noqa: SLF001
    assert budget is not None
    try:
        with pytest.raises(ProjectModelCostUnavailable, match="provider usage"):
            await budget.chat(provider, [])
        assert provider.calls == 1
        assert await _usage(sessions, project_id) == 0
    finally:
        await sql_engine.dispose()


async def test_unavoidable_overage_is_recorded_and_blocks_next_call(
    tmp_path: Path,
) -> None:
    engine, sql_engine, sessions, project_id = await _environment(tmp_path)
    await _configure(sessions, project_id, 1_000_000)
    provider = _UsageProvider(
        [Usage(prompt_tokens=3, total_tokens=3)],
        prompt_price="0.5",
        completion_price="0.5",
    )
    budget = engine._model_budget_for("overage", project_id)  # noqa: SLF001
    assert budget is not None
    try:
        with pytest.raises(ProjectQuotaExceeded):
            await budget.chat(provider, [])
        assert await _usage(sessions, project_id) == 1_500_000
        with pytest.raises(ProjectQuotaExceeded):
            await budget.chat(provider, [])
        assert provider.calls == 1
    finally:
        await sql_engine.dispose()


async def test_concurrent_calls_atomically_measure_overage(tmp_path: Path) -> None:
    engine, sql_engine, sessions, project_id = await _environment(tmp_path)
    await _configure(sessions, project_id, 1_000_000)
    barrier = _CallBarrier(2)
    providers = [
        _UsageProvider(
            [Usage(prompt_tokens=1, total_tokens=1)],
            prompt_price="0.8",
            completion_price="0.8",
            barrier=barrier,
        )
        for _ in range(2)
    ]
    budgets = [engine._model_budget_for(f"concurrent-{index}", project_id) for index in range(2)]
    assert all(budget is not None for budget in budgets)
    try:
        results = await asyncio.gather(
            *(
                budget.chat(provider, [])
                for budget, provider in zip(budgets, providers, strict=True)
                if budget
            ),
            return_exceptions=True,
        )
        assert len([result for result in results if isinstance(result, ProjectQuotaExceeded)]) == 1
        assert len([result for result in results if not isinstance(result, BaseException)]) == 1
        assert [provider.calls for provider in providers] == [1, 1]
        assert await _usage(sessions, project_id) == 1_600_000
    finally:
        await sql_engine.dispose()


async def test_streaming_charges_each_reported_usage_delta(tmp_path: Path) -> None:
    engine, sql_engine, sessions, project_id = await _environment(tmp_path)
    await _configure(sessions, project_id, None)
    provider = _UsageProvider(
        [
            Usage(prompt_tokens=1, total_tokens=1),
            Usage(completion_tokens=1, total_tokens=1),
        ],
        prompt_price="0.5",
        completion_price="0.25",
    )
    budget = engine._model_budget_for("stream-cost", project_id)  # noqa: SLF001
    assert budget is not None
    try:
        chunks = [chunk async for chunk in budget.stream_chat(provider, [])]
        assert [chunk.type for chunk in chunks] == ["text", "usage", "usage", "done"]
        assert await _usage(sessions, project_id) == 750_000
        assert budget.snapshot().estimated_cost_usd == "0.000000750000"
    finally:
        await sql_engine.dispose()


async def test_stale_worker_cannot_record_project_model_cost(tmp_path: Path) -> None:
    engine, sql_engine, sessions, project_id = await _environment(tmp_path)
    await _configure(sessions, project_id, None)
    provider = _UsageProvider(
        [Usage(prompt_tokens=1, total_tokens=1)],
        prompt_price="0.5",
        completion_price="0.5",
    )
    try:
        async with sessions() as session:
            workflow = await WorkflowRepo(session).create(
                "Stale cost",
                {"version": "1.0", "name": "Stale cost", "nodes": [], "edges": []},
                project_id=project_id,
            )
            execution = await ExecutionRepo(session).create(workflow.id, {})
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            claimed = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-old", lease_seconds=30
            )
            assert claimed is not None
            stale_lease = WorkerLease(item.id, "worker-old", claimed.lease_generation)
            claimed.status = "queued"
            claimed.owner_id = None
            replacement = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-new", lease_seconds=30
            )
            assert replacement is not None
            await session.commit()

        meter = ProjectModelCostMeter(sessions, project_id, lease=stale_lease)
        with pytest.raises(LeaseLost):
            await meter.record(provider, Usage(prompt_tokens=1, total_tokens=1))
        assert await _usage(sessions, project_id) == 0
    finally:
        await engine.shutdown(grace_period=0.1)
        await sql_engine.dispose()
