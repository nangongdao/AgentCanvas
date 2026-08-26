"""Durable execution queue and fenced worker lease (I1 Phase 4)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import RunnableConfig
from sqlalchemy import select

from app.core.config import Settings
from app.core.container import _persist_events
from app.db.base import create_engine, create_session_factory
from app.db.migrations import CURRENT_REVISION, CURRENT_TABLES, upgrade_database
from app.db.models import Execution, ExecutionQueueItem, Workflow
from app.db.repositories import ExecutionQueueRepo, ExecutionRepo, WorkflowRepo
from app.engine.events import EventBus
from app.engine.execution_launch import ExecutionLaunchMixin
from app.engine.execution_lease import LeaseLost, WorkerLease
from app.engine.execution_worker import InProcessExecutionWorker
from app.engine.executor import ExecutionEngine
from app.engine.fenced_checkpointer import FencedCheckpointer
from app.schemas.events import EventType, ExecutionEvent


async def _session_factory(tmp_path):
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    engine = create_engine(settings)
    return settings, engine, create_session_factory(engine)


async def test_upgrade_creates_execution_queue_table(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path)
    await upgrade_database(settings)
    from sqlalchemy import inspect

    engine = create_engine(settings)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
            version = await connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar_one() == CURRENT_REVISION
        assert "execution_queue_items" in tables
        assert tables >= CURRENT_TABLES
    finally:
        await engine.dispose()


async def test_enqueue_and_claim_is_fenced(tmp_path) -> None:
    _settings, engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            workflow = Workflow(
                id="wf-queue",
                name="Queue",
                dsl_json={"version": "1.0", "name": "Queue", "nodes": [], "edges": []},
            )
            session.add(workflow)
            await session.flush()
            execution = await ExecutionRepo(session).create("wf-queue", {})
            queue = ExecutionQueueRepo(session)
            item = await queue.enqueue(execution.id, kind="start")
            await session.commit()
            assert execution.status == "queued"
            assert item.status == "queued"
            assert item.lease_generation == 0

        async with session_factory() as session:
            queue = ExecutionQueueRepo(session)
            first = await queue.claim_next(owner_id="worker-a", lease_seconds=30)
            second = await queue.claim_next(owner_id="worker-b", lease_seconds=30)
            await session.commit()
            assert first is not None
            assert first.execution_id == execution.id
            assert first.status == "leased"
            assert first.owner_id == "worker-a"
            assert first.lease_generation == 1
            assert first.attempt == 1
            assert second is None

        async with session_factory() as session:
            queue = ExecutionQueueRepo(session)
            stale = await queue.complete(
                item_id=item.id,
                owner_id="worker-b",
                lease_generation=1,
            )
            fresh = await queue.complete(
                item_id=item.id,
                owner_id="worker-a",
                lease_generation=1,
            )
            await session.commit()
            assert stale is False
            assert fresh is True
            row = await session.get(ExecutionQueueItem, item.id)
            assert row is not None
            assert row.status == "done"
    finally:
        await engine.dispose()


async def test_stale_worker_cannot_heartbeat_or_fail(tmp_path) -> None:
    _settings, engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-fence",
                    name="Fence",
                    dsl_json={"version": "1.0", "name": "Fence", "nodes": [], "edges": []},
                )
            )
            await session.flush()
            execution = await ExecutionRepo(session).create("wf-fence", {})
            item = await ExecutionQueueRepo(session).enqueue(execution.id, kind="start")
            await session.commit()

        async with session_factory() as session:
            claimed = await ExecutionQueueRepo(session).claim_next(
                owner_id="owner-1", lease_seconds=30
            )
            await session.commit()
            assert claimed is not None
            generation = claimed.lease_generation

        async with session_factory() as session:
            queue = ExecutionQueueRepo(session)
            # Simulate reassignment by forcing a second claim after expiry.
            row = await session.get(ExecutionQueueItem, item.id)
            assert row is not None
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            row.status = "queued"
            row.owner_id = None
            await session.flush()
            reclaimed = await queue.claim_next(owner_id="owner-2", lease_seconds=30)
            await session.commit()
            assert reclaimed is not None
            assert reclaimed.lease_generation == generation + 1

        async with session_factory() as session:
            queue = ExecutionQueueRepo(session)
            assert (
                await queue.heartbeat(
                    item_id=item.id,
                    owner_id="owner-1",
                    lease_generation=generation,
                    lease_seconds=30,
                )
                is False
            )
            assert (
                await queue.fail(
                    item_id=item.id,
                    owner_id="owner-1",
                    lease_generation=generation,
                    error="stale",
                    retryable=False,
                )
                is False
            )
            assert (
                await queue.fail(
                    item_id=item.id,
                    owner_id="owner-2",
                    lease_generation=generation + 1,
                    error="real failure",
                    retryable=False,
                )
                is True
            )
            await session.commit()
            row = await session.get(ExecutionQueueItem, item.id)
            assert row is not None
            assert row.status == "dead_letter"
            assert row.last_error == "real failure"
    finally:
        await engine.dispose()


async def test_expired_generation_cannot_be_revived_by_heartbeat(tmp_path) -> None:
    _settings, engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-expired-heartbeat",
                    name="Expired heartbeat",
                    dsl_json={
                        "version": "1.0",
                        "name": "Expired heartbeat",
                        "nodes": [],
                        "edges": [],
                    },
                )
            )
            await session.flush()
            execution = await ExecutionRepo(session).create("wf-expired-heartbeat", {})
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            claimed = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-expired", lease_seconds=30
            )
            assert claimed is not None
            expired_at = datetime.now(UTC) - timedelta(seconds=1)
            claimed.lease_expires_at = expired_at
            await session.commit()

        async with session_factory() as session:
            revived = await ExecutionQueueRepo(session).heartbeat(
                item_id=item.id,
                owner_id="worker-expired",
                lease_generation=claimed.lease_generation,
                lease_seconds=30,
            )
            await session.commit()
            assert revived is False
            row = await session.get(ExecutionQueueItem, item.id)
            assert row is not None
            assert row.lease_expires_at is not None
            persisted_expiry = row.lease_expires_at
            if persisted_expiry.tzinfo is None:
                persisted_expiry = persisted_expiry.replace(tzinfo=UTC)
            assert persisted_expiry == expired_at
    finally:
        await engine.dispose()


async def test_worker_cancels_claim_when_heartbeat_outage_exceeds_local_lease() -> None:
    @asynccontextmanager
    async def unavailable_session():
        raise ConnectionError("database unavailable")
        yield  # pragma: no cover

    engine = SimpleNamespace(session_factory=unavailable_session)
    worker = InProcessExecutionWorker(engine, owner_id="worker-local-deadline", lease_seconds=1)
    claimed_task = asyncio.create_task(asyncio.sleep(60))
    heartbeat_task = asyncio.create_task(
        worker._heartbeat_loop(  # noqa: SLF001
            item_id="lease-item",
            lease_generation=1,
            claimed_task=claimed_task,
        )
    )
    try:
        await asyncio.wait_for(heartbeat_task, timeout=2.0)
        assert claimed_task.cancelled()
    finally:
        heartbeat_task.cancel()
        claimed_task.cancel()
        await asyncio.gather(heartbeat_task, claimed_task, return_exceptions=True)


async def test_sqlite_worker_waits_for_execution_creations_to_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SimpleNamespace(_execution_create_inflight=1)
    worker = InProcessExecutionWorker(engine)
    claim_started = asyncio.Event()

    async def fake_claim() -> bool:
        claim_started.set()
        return True

    monkeypatch.setattr(worker, "_claim_and_dispatch_unlocked", fake_claim)
    # C6-4: deferral now returns the "deferred" marker (short backoff) instead
    # of False so the loop does not sleep a full poll interval per attempt.
    assert await worker._claim_and_dispatch() == "deferred"  # noqa: SLF001
    assert not claim_started.is_set()

    engine._execution_create_inflight = 0
    assert await worker._claim_and_dispatch() is True  # noqa: SLF001
    assert claim_started.is_set()


async def test_sqlite_worker_claims_after_bounded_creation_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = SimpleNamespace(_execution_create_inflight=1)
    worker = InProcessExecutionWorker(engine, create_deferral_seconds=0.01)
    worker._create_deferral_started_at = asyncio.get_running_loop().time() - 1  # noqa: SLF001
    claim_started = asyncio.Event()

    async def fake_claim() -> bool:
        claim_started.set()
        return True

    monkeypatch.setattr(worker, "_claim_and_dispatch_unlocked", fake_claim)
    assert await worker._claim_and_dispatch() is True  # noqa: SLF001
    assert claim_started.is_set()


async def test_cancelled_sqlite_creation_releases_inflight_registration() -> None:
    create_lock = asyncio.Lock()
    await create_lock.acquire()
    worker_kicked = asyncio.Event()
    engine = SimpleNamespace(
        _shutting_down=False,
        _execution_create_lock=create_lock,
        _execution_create_inflight=0,
        _worker=SimpleNamespace(kick=worker_kicked.set),
    )
    creation = asyncio.create_task(
        ExecutionLaunchMixin._start_version(engine, {}, workflow_id="workflow")
    )
    try:
        while engine._execution_create_inflight == 0:
            await asyncio.sleep(0)
        creation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await creation
        assert engine._execution_create_inflight == 0
        assert worker_kicked.is_set()
    finally:
        if not creation.done():
            creation.cancel()
            await asyncio.gather(creation, return_exceptions=True)
        create_lock.release()


async def test_start_enqueues_and_worker_runs(tmp_path, monkeypatch) -> None:
    settings, engine, session_factory = await _session_factory(tmp_path)
    bus = EventBus()
    # Keep the worker paused until we have asserted the durable queue row.
    exec_engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=bus,
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
        start_worker=False,
    )
    ran = asyncio.Event()

    async def fake_run(execution_id: str, *args, **kwargs) -> None:
        async with session_factory() as session:
            row = await session.get(Execution, execution_id)
            assert row is not None
            assert row.status == "running"
        ran.set()
        await exec_engine._set_status(execution_id, "succeeded", output={"ok": True})  # noqa: SLF001

    monkeypatch.setattr(exec_engine, "_run", fake_run)
    try:
        async with session_factory() as session:
            workflow = await WorkflowRepo(session).create(
                "wf",
                {"version": "1.0", "name": "wf", "nodes": [], "edges": []},
            )
            await session.commit()
            workflow_id = workflow.id

        execution_id = await exec_engine.start(workflow_id, {})
        async with session_factory() as session:
            row = await session.get(Execution, execution_id)
            item = await ExecutionQueueRepo(session).get_for_execution(execution_id)
            assert row is not None
            assert row.status == "queued"
            assert item is not None
            assert item.kind == "start"
            assert item.status == "queued"

        exec_engine._worker.start()  # noqa: SLF001
        exec_engine._worker.kick()  # noqa: SLF001
        await asyncio.wait_for(ran.wait(), timeout=2.0)

        deadline = asyncio.get_running_loop().time() + 2.0
        while True:
            async with session_factory() as session:
                status = await session.scalar(
                    select(Execution.status).where(Execution.id == execution_id)
                )
            if status == "succeeded":
                break
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail(f"worker did not persist terminal status; last status={status}")
            await asyncio.sleep(0.02)

        async with session_factory() as session:
            row = await session.get(Execution, execution_id)
            item = await ExecutionQueueRepo(session).get_for_execution(execution_id)
            assert row is not None
            assert row.status == "succeeded"
            assert item is not None
            assert item.status == "done"
    finally:
        await exec_engine.shutdown(grace_period=0.1)
        await engine.dispose()


async def test_startup_recovery_does_not_steal_live_worker_lease(tmp_path) -> None:
    from app.core.container import recover_interrupted_executions

    _settings, engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-recover",
                    name="Recover",
                    dsl_json={"version": "1.0", "name": "Recover", "nodes": [], "edges": []},
                )
            )
            await session.flush()
            running = await ExecutionRepo(session).create(
                "wf-recover", {}, execution_id="running-exec"
            )
            waiting = await ExecutionRepo(session).create(
                "wf-recover", {}, execution_id="waiting-exec"
            )
            waiting.status = "waiting_approval"
            queue = ExecutionQueueRepo(session)
            item = await queue.enqueue(running.id, kind="start")
            claimed = await queue.claim_next(owner_id="dead-worker", lease_seconds=30)
            assert claimed is not None
            running.status = "running"
            await session.commit()
            assert item.id == claimed.id

        recovered = await recover_interrupted_executions(session_factory)
        assert recovered == 0

        async with session_factory() as session:
            running_row = await session.get(Execution, "running-exec")
            waiting_row = await session.get(Execution, "waiting-exec")
            item_row = await ExecutionQueueRepo(session).get_for_execution("running-exec")
            assert running_row is not None
            assert running_row.status == "running"
            assert running_row.error is None
            assert waiting_row is not None
            assert waiting_row.status == "waiting_approval"
            assert item_row is not None
            assert item_row.status == "leased"
            assert item_row.owner_id == "dead-worker"
            assert item_row.attempt == 1
    finally:
        await engine.dispose()


async def test_terminal_event_rolls_back_when_state_transition_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, session_factory = await _session_factory(tmp_path)
    exec_engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=EventBus(),
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
        start_worker=False,
    )
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-atomic-terminal",
                    name="Atomic terminal",
                    dsl_json={
                        "version": "1.0",
                        "name": "Atomic terminal",
                        "nodes": [],
                        "edges": [],
                    },
                )
            )
            await session.flush()
            execution = await ExecutionRepo(session).create(
                "wf-atomic-terminal", {}, execution_id="atomic-terminal-exec"
            )
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            claimed = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-atomic", lease_seconds=30
            )
            assert claimed is not None
            lease = WorkerLease(item.id, "worker-atomic", claimed.lease_generation)
            await session.commit()

        async def fail_status_write(*_args, **_kwargs) -> None:
            raise RuntimeError("injected state write failure")

        monkeypatch.setattr(ExecutionRepo, "update_status", fail_status_write)
        with pytest.raises(RuntimeError, match="injected state write failure"):
            await exec_engine._set_status(  # noqa: SLF001
                "atomic-terminal-exec",
                "succeeded",
                output={"ok": True},
                lease=lease,
                event_type=EventType.WORKFLOW_FINISHED,
                event_payload={"output": {"ok": True}},
            )

        async with session_factory() as session:
            execution = await session.get(Execution, "atomic-terminal-exec")
            assert execution is not None
            assert execution.status == "running"
            assert await ExecutionRepo(session).list_events_after(execution.id) == []
    finally:
        await exec_engine.shutdown(grace_period=0.1)
        await engine.dispose()


async def test_queued_cancel_rolls_back_when_terminal_event_insert_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, session_factory = await _session_factory(tmp_path)
    exec_engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=EventBus(),
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
        start_worker=False,
    )
    try:
        async with session_factory() as session:
            workflow = await WorkflowRepo(session).create(
                "Atomic cancel",
                {"version": "1.0", "name": "Atomic cancel", "nodes": [], "edges": []},
            )
            execution = await ExecutionRepo(session).create(workflow.id, {})
            await ExecutionQueueRepo(session).enqueue(execution.id)
            execution_id = execution.id
            await session.commit()

        async def fail_event_insert(*_args, **_kwargs) -> None:
            raise RuntimeError("injected cancel event failure")

        monkeypatch.setattr(ExecutionRepo, "append_event", fail_event_insert)
        with pytest.raises(RuntimeError, match="injected cancel event failure"):
            await exec_engine.cancel(execution_id)

        async with session_factory() as session:
            execution = await session.get(Execution, execution_id)
            item = await ExecutionQueueRepo(session).get_for_execution(execution_id)
            assert execution is not None and execution.status == "queued"
            assert item is not None and item.status == "queued"
            assert await ExecutionRepo(session).list_events_after(execution_id) == []
    finally:
        await exec_engine.shutdown(grace_period=0.1)
        await engine.dispose()


async def test_worker_failure_rolls_back_queue_and_state_when_event_insert_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, engine, session_factory = await _session_factory(tmp_path)
    exec_engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=EventBus(),
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
        start_worker=False,
    )
    worker = InProcessExecutionWorker(exec_engine, owner_id="worker-atomic-failure")
    try:
        async with session_factory() as session:
            workflow = await WorkflowRepo(session).create(
                "Atomic worker failure",
                {
                    "version": "1.0",
                    "name": "Atomic worker failure",
                    "nodes": [],
                    "edges": [],
                },
            )
            execution = await ExecutionRepo(session).create(workflow.id, {})
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            claimed = await ExecutionQueueRepo(session).claim_next(
                owner_id=worker.owner_id,
                lease_seconds=30,
            )
            assert claimed is not None
            execution_id = execution.id
            item_id = item.id
            generation = claimed.lease_generation
            await session.commit()

        async def fail_event_insert(*_args, **_kwargs) -> None:
            raise RuntimeError("injected worker event failure")

        monkeypatch.setattr(ExecutionRepo, "append_event", fail_event_insert)
        with pytest.raises(RuntimeError, match="injected worker event failure"):
            await worker._finish_failure(  # noqa: SLF001
                item_id=item_id,
                execution_id=execution_id,
                lease_generation=generation,
                error="dispatch failed",
            )

        async with session_factory() as session:
            execution = await session.get(Execution, execution_id)
            stored_item = await ExecutionQueueRepo(session).get_for_execution(execution_id)
            assert execution is not None and execution.status == "running"
            assert stored_item is not None and stored_item.status == "leased"
            assert await ExecutionRepo(session).list_events_after(execution_id) == []
    finally:
        await exec_engine.shutdown(grace_period=0.1)
        await engine.dispose()


async def test_stale_worker_cannot_write_checkpoint_after_reassignment(tmp_path) -> None:
    _settings, engine, session_factory = await _session_factory(tmp_path)
    delegate = MemorySaver()
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-stale-checkpoint",
                    name="Stale checkpoint",
                    dsl_json={
                        "version": "1.0",
                        "name": "Stale checkpoint",
                        "nodes": [],
                        "edges": [],
                    },
                )
            )
            await session.flush()
            execution = await ExecutionRepo(session).create(
                "wf-stale-checkpoint", {}, execution_id="stale-checkpoint-exec"
            )
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            first = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-old", lease_seconds=30
            )
            assert first is not None
            stale_lease = WorkerLease(item.id, "worker-old", first.lease_generation)
            first.status = "queued"
            first.owner_id = None
            first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            replacement = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-new", lease_seconds=30
            )
            assert replacement is not None
            await session.commit()

        config: RunnableConfig = {"configurable": {"thread_id": execution.id, "checkpoint_ns": ""}}
        await delegate.aput(config, empty_checkpoint(), {"source": "input"}, {})
        saver = FencedCheckpointer(delegate, session_factory, stale_lease)
        with pytest.raises(LeaseLost):
            await saver.aget_tuple(config)
        with pytest.raises(LeaseLost):
            await saver.aput(config, empty_checkpoint(), {"source": "loop"}, {})
        assert await delegate.aget_tuple(config) is not None
    finally:
        await engine.dispose()


async def test_stale_worker_cannot_fail_reassigned_queue_item(tmp_path) -> None:
    _settings, engine, session_factory = await _session_factory(tmp_path)
    try:
        async with session_factory() as session:
            workflow = await WorkflowRepo(session).create(
                "Stale failure",
                {"version": "1.0", "name": "Stale failure", "nodes": [], "edges": []},
            )
            execution = await ExecutionRepo(session).create(workflow.id, {})
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            old = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-old", lease_seconds=30
            )
            assert old is not None
            old_generation = old.lease_generation
            old.status = "queued"
            old.owner_id = None
            old.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            replacement = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-new", lease_seconds=30
            )
            assert replacement is not None
            changed = await ExecutionQueueRepo(session).fail(
                item_id=item.id,
                owner_id="worker-old",
                lease_generation=old_generation,
                error="stale failure",
            )
            await session.commit()

        assert changed is False
        async with session_factory() as session:
            stored = await ExecutionQueueRepo(session).get(item.id)
            assert stored is not None
            assert stored.status == "leased"
            assert stored.owner_id == "worker-new"
            assert stored.last_error is None
    finally:
        await engine.dispose()


async def test_stale_worker_cannot_commit_state_or_events(tmp_path) -> None:
    settings, engine, session_factory = await _session_factory(tmp_path)
    execution_leases: dict[str, WorkerLease] = {}
    exec_engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=EventBus(),
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
        execution_leases=execution_leases,
        start_worker=False,
    )
    try:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id="wf-stale-commit",
                    name="Stale commit",
                    dsl_json={
                        "version": "1.0",
                        "name": "Stale commit",
                        "nodes": [],
                        "edges": [],
                    },
                )
            )
            await session.flush()
            execution = await ExecutionRepo(session).create(
                "wf-stale-commit", {}, execution_id="stale-exec"
            )
            item = await ExecutionQueueRepo(session).enqueue(execution.id)
            first = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-old", lease_seconds=30
            )
            assert first is not None
            old_lease = WorkerLease(item.id, "worker-old", first.lease_generation)
            await session.commit()

        async with session_factory() as session:
            row = await session.get(ExecutionQueueItem, item.id)
            assert row is not None
            row.status = "queued"
            row.owner_id = None
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            second = await ExecutionQueueRepo(session).claim_next(
                owner_id="worker-new", lease_seconds=30
            )
            assert second is not None
            assert second.lease_generation == old_lease.generation + 1
            await session.commit()

        with pytest.raises(LeaseLost):
            await exec_engine._set_status(  # noqa: SLF001
                "stale-exec",
                "succeeded",
                output={"stale": True},
                lease=old_lease,
            )

        execution_leases["stale-exec"] = old_lease
        await _persist_events(
            session_factory,
            [
                ExecutionEvent(
                    execution_id="stale-exec",
                    event_type=EventType.WORKFLOW_FINISHED,
                    seq=1,
                    payload={"stale": True},
                )
            ],
            execution_leases,
        )

        async with session_factory() as session:
            execution = await session.get(Execution, "stale-exec")
            assert execution is not None
            assert execution.status == "running"
            assert execution.output_json is None
            assert await ExecutionRepo(session).list_events_after("stale-exec") == []
    finally:
        await exec_engine.shutdown(grace_period=0.1)
        await engine.dispose()


async def test_api_only_cancel_leaves_running_state_for_lease_owner(tmp_path) -> None:
    settings, engine, session_factory = await _session_factory(tmp_path)
    exec_engine = ExecutionEngine(
        session_factory=session_factory,
        event_bus=EventBus(),
        secret_box=None,  # type: ignore[arg-type]
        settings=settings,
        start_worker=False,
    )
    try:
        async with session_factory() as session:
            workflow = await WorkflowRepo(session).create(
                "remote cancel",
                {"version": "1.0", "name": "remote cancel", "nodes": [], "edges": []},
            )
            execution = await ExecutionRepo(session).create(workflow.id, {})
            await ExecutionQueueRepo(session).enqueue(execution.id)
            claimed = await ExecutionQueueRepo(session).claim_next(
                owner_id="remote-worker", lease_seconds=30
            )
            assert claimed is not None
            execution_id = execution.id
            await session.commit()

        assert await exec_engine.cancel(execution_id) is True
        async with session_factory() as session:
            execution = await session.get(Execution, execution_id)
            item = await ExecutionQueueRepo(session).get_for_execution(execution_id)
            assert execution is not None and execution.status == "running"
            assert item is not None and item.status == "leased"
            assert item.cancel_requested is True
    finally:
        await exec_engine.shutdown(grace_period=0.1)
        await engine.dispose()
