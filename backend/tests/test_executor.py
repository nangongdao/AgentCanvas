"""ExecutionEngine graceful shutdown tests (R-07 / U3-1).

``shutdown(grace_period)`` must: reject new start/resume calls, drain
in-flight tasks up to the grace period, cancel stragglers (each task's own
finally-block persists a consistent terminal event), and flush the EventBus
so terminal events land in the DB before the engine is disposed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.repositories import ExecutionRepo, WorkflowRepo, WorkflowVersionRepo
from app.engine.events import EventBus
from app.engine.execution_runner import _requires_durable_checkpoint
from app.engine.executor import (
    EngineShuttingDown,
    ExecutionEngine,
    _interrupt_request,
)
from app.engine.subworkflow_resolver import SubworkflowCache
from app.schemas.dsl import WorkflowDSL


async def _engine(tmp_path):
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    sql_engine = create_engine(settings)
    session_factory = create_session_factory(sql_engine)
    async with session_factory() as session:
        await WorkflowRepo(session).create(
            "wf",
            {"version": "1.0", "name": "wf", "nodes": [], "edges": []},
        )
        await session.commit()

    bus = EventBus()
    engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=bus,
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
    )
    return engine, sql_engine


async def test_shutdown_with_no_tasks_is_quick(tmp_path) -> None:
    engine, sql_engine = await _engine(tmp_path)
    try:
        await engine.shutdown(grace_period=5.0)
        assert engine._shutting_down  # noqa: SLF001
    finally:
        await sql_engine.dispose()


def test_interrupt_request_extracts_langgraph_value() -> None:
    from langgraph.types import Interrupt

    request = {"title": "Approve", "instruction": "Review", "node_id": "human"}
    assert _interrupt_request((Interrupt(value=request),)) == request


def test_durable_checkpoint_is_reserved_for_interrupt_capable_runs() -> None:
    linear = WorkflowDSL.model_validate(
        {
            "name": "linear",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        }
    )
    approval = WorkflowDSL.model_validate(
        {
            "name": "approval",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "approve",
                    "type": "human",
                    "config": {"title": "Approve", "instruction": "Continue?"},
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "to-approval", "source": "start", "target": "approve"},
                {"id": "to-end", "source": "approve", "target": "end"},
            ],
        }
    )
    parent = WorkflowDSL.model_validate(
        {
            "name": "parent",
            "nodes": [
                {"id": "start", "type": "start", "config": {}},
                {
                    "id": "child",
                    "type": "subworkflow",
                    "config": {
                        "workflow_id": "approval-workflow",
                        "version_id": "approval-v1",
                    },
                },
                {"id": "end", "type": "end", "config": {}},
            ],
            "edges": [
                {"id": "to-child", "source": "start", "target": "child"},
                {"id": "to-end", "source": "child", "target": "end"},
            ],
        }
    )
    subworkflows = SubworkflowCache(
        {("approval-workflow", "approval-v1"): approval}
    )

    assert not _requires_durable_checkpoint(linear, None)
    assert not _requires_durable_checkpoint(linear, {"breakpoints": [], "single_step": False})
    assert _requires_durable_checkpoint(linear, {"breakpoints": ["start"]})
    assert _requires_durable_checkpoint(linear, {"single_step": True})
    assert _requires_durable_checkpoint(approval, None)
    assert _requires_durable_checkpoint(parent, None, subworkflows)


async def test_shutdown_waits_for_completing_task(tmp_path) -> None:
    engine, sql_engine = await _engine(tmp_path)
    try:
        async def short_run() -> None:
            await asyncio.sleep(0.05)

        task = asyncio.create_task(short_run())
        engine._tasks["done"] = task  # noqa: SLF001
        await engine.shutdown(grace_period=1.0)
        assert task.done()
        assert not task.cancelled()
    finally:
        await sql_engine.dispose()


async def test_shutdown_cancels_stragglers_after_grace(tmp_path) -> None:
    engine, sql_engine = await _engine(tmp_path)
    try:
        async def long_run() -> None:
            await asyncio.sleep(100)

        task = asyncio.create_task(long_run())
        engine._tasks["stuck"] = task  # noqa: SLF001
        await engine.shutdown(grace_period=0.05)
        assert task.done()
        assert task.cancelled()
    finally:
        await sql_engine.dispose()


async def test_start_and_resume_rejected_after_shutdown(tmp_path) -> None:
    engine, sql_engine = await _engine(tmp_path)
    try:
        await engine.shutdown(grace_period=0.1)
        with pytest.raises(EngineShuttingDown):
            await engine.start("wf", {})
        with pytest.raises(EngineShuttingDown):
            await engine.resume("nope", {"approved": False})
    finally:
        await sql_engine.dispose()


async def test_shutdown_is_idempotent(tmp_path) -> None:
    engine, sql_engine = await _engine(tmp_path)
    try:
        await engine.shutdown(grace_period=0.1)
        await engine.shutdown(grace_period=0.1)  # must not raise
    finally:
        await sql_engine.dispose()


async def test_start_enqueues_when_running_execution_slots_are_full(tmp_path) -> None:
    """Queue accepts work even when the local worker has no free slots."""
    engine, sql_engine = await _engine(tmp_path)
    acquired = 0
    try:
        semaphore = engine._execution_semaphore  # noqa: SLF001
        assert semaphore is not None
        while not semaphore.locked():
            await semaphore.acquire()
            acquired += 1
        async with engine.session_factory() as session:
            workflow_id = (await WorkflowRepo(session).list())[0].id
        execution_id = await engine.start(workflow_id, {})
        async with engine.session_factory() as session:
            row = await ExecutionRepo(session).get(execution_id)
            assert row is not None
            assert row.status == "queued"
        # Worker claim path is what enforces the local concurrency ceiling.
        assert not engine.active_executions()
    finally:
        for _ in range(acquired):
            semaphore.release()
        await engine.shutdown(grace_period=0.1)
        await sql_engine.dispose()


async def test_execution_create_persists_final_thread_id_in_one_flush(
    tmp_path, monkeypatch
) -> None:
    engine, sql_engine = await _engine(tmp_path)
    try:
        async with engine.session_factory() as session:
            workflow = (await WorkflowRepo(session).list())[0]
            version = await WorkflowVersionRepo(session).ensure_current(workflow)
            original_flush = session.flush
            flushes = 0

            async def counted_flush(*args, **kwargs) -> None:
                nonlocal flushes
                flushes += 1
                await original_flush(*args, **kwargs)

            monkeypatch.setattr(session, "flush", counted_flush)
            row = await ExecutionRepo(session).create(
                workflow.id,
                {},
                workflow_version_id=version.id,
            )

            assert row.id == row.thread_id
            assert flushes == 1
    finally:
        await engine.shutdown(grace_period=0.1)
        await sql_engine.dispose()


async def test_concurrent_resume_has_one_winner(tmp_path, monkeypatch) -> None:
    """A waiting execution is atomically claimed by one resume request."""
    engine, sql_engine = await _engine(tmp_path)
    release = asyncio.Event()
    try:
        async with engine.session_factory() as session:
            workflow_id = (await WorkflowRepo(session).list())[0].id
            row = await ExecutionRepo(session).create(workflow_id, {})
            row.status = "waiting_approval"
            await session.commit()
            execution_id = row.id

        async def hold_resume(*_args, **_kwargs) -> None:
            await release.wait()

        monkeypatch.setattr(engine, "_run_resume", hold_resume)
        results = await asyncio.gather(
            *(engine.resume(execution_id, {"approved": True}) for _ in range(10)),
            return_exceptions=True,
        )

        assert sum(result == execution_id for result in results) == 1, repr(results)
        rejected = [result for result in results if result != execution_id]
        assert len(rejected) == 9
        assert all(isinstance(result, ValueError) for result in rejected), repr(results)
        # Give the in-process worker a chance to lease the resume work unit.
        for _ in range(100):
            if engine._tasks:  # noqa: SLF001
                break
            await asyncio.sleep(0.02)
        assert engine._tasks, "worker never claimed the resume queue item"  # noqa: SLF001
        release.set()
        await asyncio.gather(*engine._tasks.values())  # noqa: SLF001
    finally:
        await engine.shutdown(grace_period=0.1)
        await sql_engine.dispose()
