"""Expired worker lease scheduling and ambiguous-loss policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Execution, ExecutionQueueItem
from app.db.repositories import ExecutionQueueRepo, ExecutionRepo, WorkflowRepo
from app.engine.execution_scheduler import ExecutionLeaseScheduler

SAFE_DSL = {"version": "1.0", "name": "safe", "nodes": [], "edges": []}
TOOL_DSL = {
    "version": "1.0",
    "name": "tool",
    "nodes": [
        {"id": "start", "type": "start"},
        {
            "id": "mutate",
            "type": "tool",
            "config": {"server_id": "external", "tool_name": "write"},
        },
        {"id": "end", "type": "end"},
    ],
    "edges": [
        {"id": "a", "source": "start", "target": "mutate"},
        {"id": "b", "source": "mutate", "target": "end"},
    ],
}


async def _runtime(tmp_path):
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    return engine, create_session_factory(engine)


async def _leased_execution(sessions, dsl, *, execution_id: str):
    async with sessions() as session:
        workflow = await WorkflowRepo(session).create(execution_id, dsl)
        execution = await ExecutionRepo(session).create(workflow.id, {}, execution_id=execution_id)
        item = await ExecutionQueueRepo(session).enqueue(execution.id)
        claimed = await ExecutionQueueRepo(session).claim_next(
            owner_id="worker-old", lease_seconds=30
        )
        assert claimed is not None
        item_id = item.id
        await session.commit()
    async with sessions() as session:
        item = await session.get(ExecutionQueueItem, item_id)
        assert item is not None
        item.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    return item_id


async def test_only_scheduler_requeues_safe_expired_lease(tmp_path) -> None:
    engine, sessions = await _runtime(tmp_path)
    try:
        item_id = await _leased_execution(sessions, SAFE_DSL, execution_id="safe-expired")
        async with sessions() as session:
            assert (
                await ExecutionQueueRepo(session).claim_next(
                    owner_id="worker-too-early", lease_seconds=30
                )
                is None
            )
            await session.rollback()

        scheduler = ExecutionLeaseScheduler(sessions, max_attempts=3)
        assert await scheduler.recover_once() == 1

        async with sessions() as session:
            item = await session.get(ExecutionQueueItem, item_id)
            execution = await session.get(Execution, "safe-expired")
            assert item is not None and item.status == "queued"
            assert item.owner_id is None
            assert execution is not None and execution.status == "queued"
            reclaimed = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-new", lease_seconds=30
            )
            assert reclaimed is not None
            assert reclaimed.lease_generation == 2
    finally:
        await engine.dispose()


async def test_scheduler_dead_letters_ambiguous_side_effecting_loss(tmp_path) -> None:
    engine, sessions = await _runtime(tmp_path)
    try:
        item_id = await _leased_execution(sessions, TOOL_DSL, execution_id="tool-expired")
        scheduler = ExecutionLeaseScheduler(sessions, max_attempts=3)
        assert await scheduler.recover_once() == 1

        async with sessions() as session:
            item = await session.get(ExecutionQueueItem, item_id)
            execution = await session.get(Execution, "tool-expired")
            events = await ExecutionRepo(session).list_events_after("tool-expired")
            assert item is not None and item.status == "dead_letter"
            assert "tool calls may mutate" in (item.last_error or "")
            assert execution is not None and execution.status == "failed"
            assert [event.event_type for event in events] == ["workflow_failed"]
            assert events[0].payload_json["reason"] == "ambiguous_worker_loss"
    finally:
        await engine.dispose()


async def test_scheduler_finishes_queue_row_after_terminal_state_commit(tmp_path) -> None:
    engine, sessions = await _runtime(tmp_path)
    try:
        item_id = await _leased_execution(sessions, SAFE_DSL, execution_id="terminal-expired")
        async with sessions() as session:
            execution = await session.get(Execution, "terminal-expired")
            assert execution is not None
            await ExecutionRepo(session).update_status(execution, "succeeded", output={"ok": True})
            await session.commit()

        scheduler = ExecutionLeaseScheduler(sessions)
        assert await scheduler.recover_once() == 1
        async with sessions() as session:
            item = await session.get(ExecutionQueueItem, item_id)
            execution = await session.get(Execution, "terminal-expired")
            assert item is not None and item.status == "done"
            assert execution is not None and execution.status == "succeeded"
            assert execution.output_json == {"ok": True}
    finally:
        await engine.dispose()
