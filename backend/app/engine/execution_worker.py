"""In-process worker that claims fenced queue leases and runs graphs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.db.repositories import (
    ExecutionQueueRepo,
    ExecutionRepo,
    WorkflowRepo,
    WorkflowVersionRepo,
)
from app.engine.execution_lease import WorkerLease
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType, ExecutionEvent

logger = logging.getLogger(__name__)


class InProcessExecutionWorker:
    """Single-process worker loop used until dedicated worker processes exist."""

    def __init__(
        self,
        engine: Any,
        *,
        owner_id: str | None = None,
        poll_seconds: float = 0.5,
        lease_seconds: int = 30,
        max_attempts: int = 3,
        create_deferral_seconds: float = 30.0,
    ) -> None:
        self.engine = engine
        self.owner_id = owner_id or f"local-{uuid4().hex[:12]}"
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        # Bounded deferral: while an execution creation holds the SQLite write
        # lock (``_execution_create_inflight`` > 0), the worker waits instead of
        # racing the lock, but only up to ``create_deferral_seconds`` so a
        # stuck/leaked inflight counter cannot starve the queue forever.
        self.create_deferral_seconds = create_deferral_seconds
        # While deferred, retry on this short backoff so the loop notices the
        # end of a creation wave almost immediately (C6-4).
        self.deferral_backoff_seconds = 0.05
        self._create_deferral_started_at: float | None = None
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name=f"execution-worker-{self.owner_id}")

    def kick(self) -> None:
        self._wake.set()

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                outcome = await self._claim_and_dispatch()
            except Exception:
                logger.exception("execution worker loop failed")
                outcome = False
            if self._stopped:
                break
            if outcome is True:
                continue
            # A creation deferral retries on a short backoff instead of the
            # full poll interval: once the creation wave drains (inflight
            # drops to zero) the worker resumes claiming within milliseconds,
            # not after another poll_seconds of idle sleep (C6-4).
            wait = (
                self.deferral_backoff_seconds
                if outcome == "deferred"
                else self.poll_seconds
            )
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except TimeoutError:
                continue

    async def _claim_and_dispatch(self) -> bool | str:
        inflight = getattr(self.engine, "_execution_create_inflight", 0)
        if inflight > 0:
            now = asyncio.get_running_loop().time()
            started = self._create_deferral_started_at
            if started is None:
                self._create_deferral_started_at = now
                return "deferred"
            if now - started < self.create_deferral_seconds:
                return "deferred"
            # Deferred long enough that a creation is likely stuck; stop waiting
            # so the queue is not starved indefinitely.
            logger.warning(
                "execution worker deferred %.1fs past creation inflight; proceeding",
                now - started,
            )
            self._create_deferral_started_at = None
        else:
            self._create_deferral_started_at = None
        return await self._claim_and_dispatch_unlocked()

    async def _claim_and_dispatch_unlocked(self) -> bool:
        if getattr(self.engine, "_shutting_down", False):
            return False
        try:
            slot_acquired = await self.engine._acquire_execution_slot()
        except Exception:
            return False
        item = None
        try:
            async with self.engine.session_factory() as session:
                queue = ExecutionQueueRepo(session)
                item = await queue.claim_next(
                    owner_id=self.owner_id,
                    lease_seconds=self.lease_seconds,
                )
                if item is None:
                    await session.rollback()
                    self.engine._release_execution_slot(slot_acquired)
                    return False
                execution = await ExecutionRepo(session).get(item.execution_id)
                if execution is None:
                    await queue.fail(
                        item_id=item.id,
                        owner_id=self.owner_id,
                        lease_generation=item.lease_generation,
                        error="execution row missing",
                        retryable=False,
                    )
                    await session.commit()
                    self.engine._release_execution_slot(slot_acquired)
                    return True
                if execution.status in {
                    "succeeded",
                    "failed",
                    "cancelled",
                    "waiting_approval",
                }:
                    await queue.complete(
                        item_id=item.id,
                        owner_id=self.owner_id,
                        lease_generation=item.lease_generation,
                    )
                    await session.commit()
                    self.engine._release_execution_slot(slot_acquired)
                    return True
                workflow = await WorkflowRepo(session).get(execution.workflow_id)
                version = await WorkflowVersionRepo(session).get(execution.workflow_version_id)
                if workflow is None or version is None:
                    error = "workflow snapshot missing"
                    await queue.fail(
                        item_id=item.id,
                        owner_id=self.owner_id,
                        lease_generation=item.lease_generation,
                        error=error,
                        retryable=False,
                    )
                    await ExecutionRepo(session).update_status(execution, "failed", error=error)
                    await self.engine._release_project_execution(
                        session,
                        workflow.project_id if workflow is not None else None,
                        execution.id,
                    )
                    terminal_event = await self._append_terminal_event(
                        session,
                        execution.id,
                        EventType.WORKFLOW_FAILED,
                        {"error": error, "reason": "workflow_snapshot_missing"},
                    )
                    await session.commit()
                    self.engine.event_bus.publish_committed(terminal_event)
                    await self.engine.event_bus.close_execution(execution.id)
                    self.engine._release_execution_slot(slot_acquired)
                    return True
                if item.cancel_requested:
                    cancel_terminal_event: ExecutionEvent | None = None
                    changed = await ExecutionRepo(session).mark_cancelled(execution.id)
                    await queue.complete(
                        item_id=item.id,
                        owner_id=self.owner_id,
                        lease_generation=item.lease_generation,
                    )
                    if changed:
                        await self.engine._release_project_execution(
                            session, workflow.project_id, execution.id
                        )
                        cancel_terminal_event = await self._append_terminal_event(
                            session,
                            execution.id,
                            EventType.WORKFLOW_CANCELLED,
                            {"reason": "cancel_request"},
                        )
                    await session.commit()
                    if cancel_terminal_event is not None:
                        self.engine.event_bus.publish_committed(cancel_terminal_event)
                        await self.engine.event_bus.close_execution(execution.id)
                    self.engine._release_execution_slot(slot_acquired)
                    return True
                dsl = WorkflowDSL.model_validate(version.dsl_json)
                inputs = dict(execution.input_json or {})
                payload = dict(item.payload_json or {})
                kind = item.kind
                item_id = item.id
                lease_generation = item.lease_generation
                execution_id = execution.id
                workflow_id = workflow.id
                project_id = workflow.project_id
                session_id = execution.session_id
                start_node_id = execution.rerun_from_node_id
                parent_execution_id = execution.parent_execution_id
                decision = (
                    payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
                )
                node_outputs = payload.get("node_outputs")
                reused_node_ids = tuple(payload.get("reused_node_ids") or ())
                debug_payload = payload.get("debug") if isinstance(payload.get("debug"), dict) else None
                event_repo = ExecutionRepo(session)
                last_event_seq = await event_repo.max_event_seq(execution_id)
                claim_event_type = (
                    EventType.NODE_STREAMING if kind == "resume" else EventType.WORKFLOW_STARTED
                )
                if kind == "resume":
                    claim_event_payload: dict[str, Any] = {
                        "kind": "resume",
                        "decision": decision,
                    }
                else:
                    claim_event_payload = {"workflow_id": workflow_id, "inputs": inputs}
                    if kind == "rerun" and parent_execution_id and start_node_id:
                        claim_event_payload["rerun"] = {
                            "parent_execution_id": parent_execution_id,
                            "from_node_id": start_node_id,
                            "reused_node_ids": list(reused_node_ids),
                        }
                claim_event_ts = datetime.now(UTC)
                last_event_seq += 1
                await event_repo.append_event(
                    execution_id,
                    last_event_seq,
                    claim_event_type.value,
                    None,
                    claim_event_payload,
                    claim_event_ts,
                )
                claim_event = ExecutionEvent(
                    execution_id=execution_id,
                    event_type=claim_event_type,
                    seq=last_event_seq,
                    payload=claim_event_payload,
                    ts=claim_event_ts.isoformat(),
                )
                await session.commit()
        except BaseException:
            self.engine._release_execution_slot(slot_acquired)
            raise

        assert item is not None
        operation = {
            "start": "workflow.execute",
            "resume": "workflow.resume",
            "rerun": "workflow.rerun",
        }.get(kind, "workflow.execute")
        lease = WorkerLease(
            item_id=item_id,
            owner_id=self.owner_id,
            generation=lease_generation,
        )
        self.engine.event_bus.seed_seq(execution_id, last_event_seq)
        self.engine.event_bus.publish_committed(claim_event)
        self.engine._execution_leases[execution_id] = lease

        async def run_claimed() -> None:
            claimed_task = asyncio.current_task()
            assert claimed_task is not None
            heartbeat = asyncio.create_task(
                self._heartbeat_loop(
                    item_id=item_id,
                    lease_generation=lease_generation,
                    claimed_task=claimed_task,
                ),
                name=f"queue-heartbeat-{execution_id}",
            )
            try:
                if kind == "resume":
                    await self.engine._run_resume(
                        execution_id,
                        workflow_id,
                        dsl,
                        inputs,
                        decision,
                        project_id=project_id,
                        slot_acquired=slot_acquired,
                        lease=lease,
                        debug=debug_payload,
                    )
                else:
                    await self.engine._run(
                        execution_id,
                        workflow_id,
                        dsl,
                        inputs,
                        session_id,
                        project_id=project_id,
                        slot_acquired=slot_acquired,
                        start_node_id=start_node_id if kind == "rerun" else None,
                        initial_node_outputs=node_outputs
                        if isinstance(node_outputs, dict)
                        else None,
                        rerun_from_execution_id=parent_execution_id if kind == "rerun" else None,
                        reused_node_ids=reused_node_ids if kind == "rerun" else (),
                        lease=lease,
                        debug=debug_payload,
                    )
                await self._finish_success(
                    item_id=item_id,
                    execution_id=execution_id,
                    lease_generation=lease_generation,
                )
            except asyncio.CancelledError:
                await self._finish_cancelled(
                    item_id=item_id,
                    execution_id=execution_id,
                    lease_generation=lease_generation,
                )
                raise
            except Exception as exc:
                logger.exception("queued execution %s failed", execution_id)
                await self._finish_failure(
                    item_id=item_id,
                    execution_id=execution_id,
                    lease_generation=lease_generation,
                    error=str(exc),
                )
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
                if self.engine._execution_leases.get(execution_id) == lease:
                    self.engine._execution_leases.pop(execution_id, None)

        task = asyncio.create_task(
            self.engine._observe_task(execution_id, workflow_id, operation, run_claimed()),
            name=f"queue-{kind}-{execution_id}",
        )
        self.engine._tasks[execution_id] = task

        def forget(_task: asyncio.Task[None], eid: str = execution_id) -> None:
            self.engine._tasks.pop(eid, None)

        task.add_done_callback(forget)
        return True

    async def _heartbeat_loop(
        self,
        *,
        item_id: str,
        lease_generation: int,
        claimed_task: asyncio.Task[None],
    ) -> None:
        """Extend the fenced lease while the graph is still running."""
        # Heartbeat well before expiry so a slow tick cannot drop ownership.
        interval = max(1.0, min(self.lease_seconds / 3.0, 10.0))
        loop = asyncio.get_running_loop()
        local_deadline = loop.time() + self.lease_seconds
        while True:
            await asyncio.sleep(interval)
            try:
                async with self.engine.session_factory() as session:
                    ok = await ExecutionQueueRepo(session).heartbeat(
                        item_id=item_id,
                        owner_id=self.owner_id,
                        lease_generation=lease_generation,
                        lease_seconds=self.lease_seconds,
                    )
                    item = await ExecutionQueueRepo(session).get(item_id)
                    cancel_requested = bool(item and item.cancel_requested)
                    await session.commit()
                if not ok:
                    logger.warning(
                        "execution lease heartbeat rejected for item %s generation %s",
                        item_id,
                        lease_generation,
                    )
                    claimed_task.cancel("execution lease lost")
                    return
                local_deadline = loop.time() + self.lease_seconds
                if cancel_requested:
                    logger.info(
                        "execution cancellation observed for item %s generation %s",
                        item_id,
                        lease_generation,
                    )
                    claimed_task.cancel("execution cancellation requested")
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "execution lease heartbeat failed for item %s generation %s",
                    item_id,
                    lease_generation,
                )
                if loop.time() >= local_deadline:
                    logger.error(
                        "cancelling item %s generation %s after heartbeat outage exceeded lease",
                        item_id,
                        lease_generation,
                    )
                    claimed_task.cancel("execution lease heartbeat unavailable")
                    return

    async def _finish_success(
        self,
        *,
        item_id: str,
        execution_id: str,
        lease_generation: int,
    ) -> None:
        async with self.engine.session_factory() as session:
            execution = await ExecutionRepo(session).get(execution_id)
            queue = ExecutionQueueRepo(session)
            if execution is not None and execution.status == "waiting_approval":
                await queue.release_for_approval(
                    item_id=item_id,
                    owner_id=self.owner_id,
                    lease_generation=lease_generation,
                )
            else:
                await queue.complete(
                    item_id=item_id,
                    owner_id=self.owner_id,
                    lease_generation=lease_generation,
                )
            await session.commit()

    async def _finish_failure(
        self,
        *,
        item_id: str,
        execution_id: str,
        lease_generation: int,
        error: str,
    ) -> None:
        terminal_event: ExecutionEvent | None = None
        async with self.engine.session_factory() as session:
            queue = ExecutionQueueRepo(session)
            fenced = await queue.fail(
                item_id=item_id,
                owner_id=self.owner_id,
                lease_generation=lease_generation,
                error=error,
                # Side-effecting MCP/plugin work is not blindly retried (ADR).
                retryable=False,
                max_attempts=self.max_attempts,
            )
            execution = await ExecutionRepo(session).get(execution_id)
            if fenced and execution is not None and execution.status == "running":
                await ExecutionRepo(session).update_status(execution, "failed", error=error)
                workflow = await WorkflowRepo(session).get(execution.workflow_id)
                await self.engine._release_project_execution(
                    session,
                    workflow.project_id if workflow is not None else None,
                    execution_id,
                )
                terminal_event = await self._append_terminal_event(
                    session,
                    execution_id,
                    EventType.WORKFLOW_FAILED,
                    {
                        "error": error,
                        "reason": "worker_dispatch_failure",
                        "dead_letter": True,
                    },
                )
            await session.commit()
        if terminal_event is not None:
            self.engine.event_bus.publish_committed(terminal_event)
            await self.engine.event_bus.close_execution(execution_id)

    async def _finish_cancelled(
        self,
        *,
        item_id: str,
        execution_id: str,
        lease_generation: int,
    ) -> None:
        async with self.engine.session_factory() as session:
            queue = ExecutionQueueRepo(session)
            await queue.complete(
                item_id=item_id,
                owner_id=self.owner_id,
                lease_generation=lease_generation,
            )
            await session.commit()

    @staticmethod
    async def _append_terminal_event(
        session: Any,
        execution_id: str,
        event_type: EventType,
        payload: dict[str, Any],
    ) -> ExecutionEvent:
        repo = ExecutionRepo(session)
        event_seq = await repo.max_event_seq(execution_id) + 1
        event_ts = datetime.now(UTC)
        await repo.append_event(
            execution_id,
            event_seq,
            event_type.value,
            None,
            payload,
            event_ts,
        )
        return ExecutionEvent(
            execution_id=execution_id,
            event_type=event_type,
            seq=event_seq,
            payload=payload,
            ts=event_ts.isoformat(),
        )


__all__ = ["InProcessExecutionWorker"]
