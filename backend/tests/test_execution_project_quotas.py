"""Execution lifecycle integration for project concurrency quotas."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Execution, Organization, ProjectQuotaReservation
from app.db.repositories import ExecutionRepo, ProjectRepo, WorkflowRepo
from app.engine.events import EventBus
from app.engine.executor import ExecutionEngine
from app.services.project_quotas import ProjectQuotaExceeded, ProjectQuotaService

DSL = {
    "version": "1.0",
    "name": "Quota execution",
    "nodes": [
        {"id": "start", "type": "start", "config": {}},
        {"id": "agent", "type": "agent", "config": {}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [
        {"id": "start-agent", "source": "start", "target": "agent"},
        {"id": "agent-end", "source": "agent", "target": "end"},
    ],
}


async def _engine(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, execution_max_concurrent=8)
    await upgrade_database(settings)
    sql_engine = create_engine(settings)
    sessions = create_session_factory(sql_engine)
    async with sessions() as session:
        organization = Organization(name="Execution Org", slug="execution-org")
        session.add(organization)
        await session.flush()
        project = await ProjectRepo(session).create(
            organization.id, "Execution Project", "execution-project"
        )
        workflow = await WorkflowRepo(session).create("Quota execution", DSL, project_id=project.id)
        await session.commit()
        project_id = project.id
        workflow_id = workflow.id
    engine = ExecutionEngine(
        session_factory=sessions,
        event_bus=EventBus(),
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
    )
    return engine, sql_engine, sessions, project_id, workflow_id


def _install_finishing_runner(
    monkeypatch: Any,
    engine: ExecutionEngine,
) -> tuple[asyncio.Event, asyncio.Event]:
    started = asyncio.Event()
    release = asyncio.Event()

    async def run(
        execution_id: str,
        _workflow_id: str,
        _dsl: Any,
        _inputs: dict[str, Any],
        _session_id: str | None = None,
        *,
        slot_acquired: bool = False,
        **_kwargs: Any,
    ) -> None:
        started.set()
        try:
            await release.wait()
            await engine._set_status(execution_id, "succeeded", output={})  # noqa: SLF001
        except asyncio.CancelledError:
            await engine._set_status(execution_id, "cancelled")  # noqa: SLF001
            raise
        finally:
            engine._release_execution_slot(slot_acquired)  # noqa: SLF001

    monkeypatch.setattr(engine, "_run", run)
    monkeypatch.setattr(engine, "_run_resume", run)
    return started, release


async def test_start_reserves_once_and_releases_on_terminal(
    tmp_path: Path, monkeypatch: Any
) -> None:
    engine, sql_engine, sessions, project_id, workflow_id = await _engine(tmp_path)
    started, release = _install_finishing_runner(monkeypatch, engine)
    try:
        async with sessions() as session:
            await ProjectQuotaService(session).configure(project_id, concurrent_execution_limit=1)
            await session.commit()

        execution_id = await engine.start(workflow_id, {}, idempotency_key="same")
        await asyncio.wait_for(started.wait(), timeout=2.0)
        task = engine._tasks[execution_id]  # noqa: SLF001
        assert await engine.start(workflow_id, {}, idempotency_key="same") == execution_id
        with pytest.raises(ProjectQuotaExceeded):
            await engine.start(workflow_id, {})

        async with sessions() as session:
            service = ProjectQuotaService(session)
            assert (await service.snapshot(project_id)).concurrent_executions == 1
            assert await session.scalar(select(func.count(Execution.id))) == 1
            assert (
                await session.scalar(
                    select(func.count(ProjectQuotaReservation.id)).where(
                        ProjectQuotaReservation.project_id == project_id,
                        ProjectQuotaReservation.kind == "execution",
                    )
                )
                == 1
            )

        release.set()
        await task
        async with sessions() as session:
            assert (
                await ProjectQuotaService(session).snapshot(project_id)
            ).concurrent_executions == 0
            row = await session.get(Execution, execution_id)
            assert row is not None and row.status == "succeeded"
    finally:
        release.set()
        await engine.shutdown(grace_period=0.1)
        await sql_engine.dispose()


async def test_resume_quota_rejection_rolls_back_claim(tmp_path: Path, monkeypatch: Any) -> None:
    engine, sql_engine, sessions, project_id, workflow_id = await _engine(tmp_path)
    _started, release = _install_finishing_runner(monkeypatch, engine)
    try:
        async with sessions() as session:
            execution = await ExecutionRepo(session).create(workflow_id, {})
            execution.status = "waiting_approval"
            await ProjectQuotaService(session).configure(project_id, concurrent_execution_limit=0)
            await session.commit()
            execution_id = execution.id

        with pytest.raises(ProjectQuotaExceeded):
            await engine.resume(execution_id, {"approved": True})
        async with sessions() as session:
            row = await session.get(Execution, execution_id)
            assert row is not None and row.status == "waiting_approval"
            assert (
                await ProjectQuotaService(session).snapshot(project_id)
            ).concurrent_executions == 0

        async with sessions() as session:
            await ProjectQuotaService(session).configure(project_id, concurrent_execution_limit=1)
            await session.commit()
        assert await engine.resume(execution_id, {"approved": True}) == execution_id
        await asyncio.wait_for(_started.wait(), timeout=2.0)
        task = engine._tasks[execution_id]  # noqa: SLF001
        release.set()
        await task
        async with sessions() as session:
            row = await session.get(Execution, execution_id)
            assert row is not None and row.status == "succeeded"
            assert (
                await ProjectQuotaService(session).snapshot(project_id)
            ).concurrent_executions == 0
    finally:
        release.set()
        await engine.shutdown(grace_period=0.1)
        await sql_engine.dispose()


async def test_rerun_quota_rejection_rolls_back_new_execution(tmp_path: Path) -> None:
    engine, sql_engine, sessions, project_id, workflow_id = await _engine(tmp_path)
    try:
        async with sessions() as session:
            repo = ExecutionRepo(session)
            source = await repo.create(workflow_id, {})
            source.status = "failed"
            await repo.append_event(
                source.id,
                1,
                "node_finished",
                "start",
                {"output": {"query": "retry"}},
            )
            await repo.append_event(
                source.id,
                2,
                "node_started",
                "agent",
                {"input": {"inputs": {}, "upstream_outputs": {"start": {}}}},
            )
            await repo.append_event(source.id, 3, "node_failed", "agent", {"error": "no"})
            await ProjectQuotaService(session).configure(project_id, concurrent_execution_limit=0)
            await session.commit()
            source_id = source.id

        with pytest.raises(ProjectQuotaExceeded):
            await engine.rerun_from_node(source_id)
        async with sessions() as session:
            assert await session.scalar(select(func.count(Execution.id))) == 1
            assert await session.scalar(select(func.count(ProjectQuotaReservation.id))) == 0
            source = await session.get(Execution, source_id)
            assert source is not None and source.status == "failed"
    finally:
        await engine.shutdown(grace_period=0.1)
        await sql_engine.dispose()
