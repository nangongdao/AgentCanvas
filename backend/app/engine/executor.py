"""Execution engine: compile DSL, run graph, stream events."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.model_budget import (
    ModelCallBudget,
    ModelCallConfig,
    ModelCallGate,
    ModelCallUsage,
)
from app.core.observability import Observability
from app.core.resilience import ResilienceConfig, ResilienceRegistry
from app.core.sandbox import SandboxBackend
from app.core.secret_providers import SecretResolver
from app.core.security import SecretBox
from app.db.repositories import (
    ExecutionRepo,
    WorkflowRepo,
)
from app.engine.checkpoint import make_checkpointer
from app.engine.compiler import WorkflowCompiler
from app.engine.events import EventBus
from app.engine.execution_dry_run import ExecutionDryRunMixin
from app.engine.execution_errors import EngineShuttingDown as EngineShuttingDown
from app.engine.execution_errors import ExecutionConcurrencyLimit
from app.engine.execution_launch import ExecutionLaunchMixin
from app.engine.execution_lease import LeaseLost, WorkerLease
from app.engine.execution_quota import ExecutionQuotaMixin
from app.engine.execution_rerun_launch import ExecutionRerunLaunchMixin
from app.engine.execution_resume import ExecutionResumeMixin
from app.engine.execution_resume_runner import ExecutionResumeRunnerMixin
from app.engine.execution_runner import ExecutionRunnerMixin, _interrupt_request  # noqa: F401
from app.engine.execution_worker import InProcessExecutionWorker
from app.engine.fenced_checkpointer import FencedCheckpointer
from app.engine.provider_runtime import ProviderRuntimeMixin
from app.engine.subworkflow_resolver import SubworkflowCache, resolve_subworkflows
from app.schemas.dsl import WorkflowDSL
from app.schemas.events import EventType, ExecutionEvent
from app.services.project_model_costs import ProjectModelCostMeter

logger = logging.getLogger(__name__)


class ExecutionEngine(
    ExecutionLaunchMixin,
    ExecutionResumeMixin,
    ExecutionRerunLaunchMixin,
    ExecutionQuotaMixin,
    ProviderRuntimeMixin,
    ExecutionRunnerMixin,
    ExecutionResumeRunnerMixin,
    ExecutionDryRunMixin,
):
    """Owns background workflow runs and live task bookkeeping."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        event_bus: EventBus,
        secret_box: SecretBox,
        secret_resolver: SecretResolver | None = None,
        compiler: WorkflowCompiler | None = None,
        settings: Settings | None = None,
        mcp_manager: Any = None,
        rag_service: Any = None,
        memory_store: Any = None,
        model_gate: ModelCallGate | None = None,
        observability: Observability | None = None,
        resilience: ResilienceRegistry | None = None,
        resilience_config: ResilienceConfig | None = None,
        execution_leases: dict[str, WorkerLease] | None = None,
        worker_owner_id: str | None = None,
        start_worker: bool = True,
        workflow_callback_dispatcher: Any = None,
        sandbox: SandboxBackend | None = None,
        subworkflow_loader: Any = None,
        session_variable_store: Any = None,
    ) -> None:
        self.session_factory = session_factory
        self.event_bus = event_bus
        self.secret_box = secret_box
        self.secret_resolver = secret_resolver or (
            SecretResolver(secret_box) if secret_box is not None else None
        )
        self.compiler = compiler or WorkflowCompiler()
        self.settings = settings
        self.mcp_manager = mcp_manager
        self.rag_service = rag_service
        self.memory_store = memory_store
        self.workflow_callback_dispatcher = workflow_callback_dispatcher
        self.observability = observability
        self.resilience = resilience
        self.resilience_config = resilience_config or ResilienceConfig()
        self.sandbox = sandbox
        self.subworkflow_loader = subworkflow_loader
        self.session_variable_store = session_variable_store
        self.model_gate = model_gate or (
            ModelCallGate(
                ModelCallConfig(
                    max_concurrent=settings.model_max_concurrent,
                    max_calls_per_execution=settings.model_max_calls_per_execution,
                    timeout_seconds=settings.model_timeout_seconds,
                    max_calls_per_window=settings.model_rate_limit_calls,
                    window_seconds=settings.model_rate_limit_window_seconds,
                    max_concurrent_per_execution=settings.model_max_concurrent_per_execution,
                    max_tokens_per_execution=settings.model_max_tokens_per_execution,
                    max_cost_usd_per_execution=settings.model_max_cost_usd_per_execution,
                )
            )
            if settings is not None
            else None
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._execution_leases = execution_leases if execution_leases is not None else {}
        self._provider_cache: dict[str, Any] = {}
        self._execution_semaphore = (
            asyncio.Semaphore(settings.execution_max_concurrent) if settings is not None else None
        )
        self._execution_create_lock = (
            asyncio.Lock()
            if settings is not None and settings.effective_database_url.startswith("sqlite")
            else None
        )
        self._execution_create_inflight = 0
        # Once shutdown() begins draining, no new executions may be scheduled.
        # ``start``/``resume`` raise ``EngineShuttingDown`` so callers can map
        # that to a 503 instead of silently enqueuing work that will be killed.
        self._shutting_down: bool = False
        self._worker = InProcessExecutionWorker(
            self,
            owner_id=worker_owner_id,
            poll_seconds=(settings.execution_worker_poll_seconds if settings is not None else 0.5),
            lease_seconds=(settings.execution_lease_seconds if settings is not None else 30),
            max_attempts=(settings.execution_lease_max_attempts if settings is not None else 3),
        )
        if start_worker:
            self._worker.start()

    def _model_budget_for(
        self,
        execution_id: str,
        project_id: str | None,
        lease: WorkerLease | None = None,
        initial_usage: ModelCallUsage | None = None,
    ) -> ModelCallBudget | None:
        if self.model_gate is None:
            return None
        meter = (
            ProjectModelCostMeter(
                self.session_factory,
                project_id,
                execution_id=execution_id,
                lease=lease,
                callback_dispatcher=self.workflow_callback_dispatcher,
            )
            if project_id is not None
            else None
        )
        return self.model_gate.budget_for(
            execution_id,
            cost_meter=meter,
            initial_usage=initial_usage,
        )

    async def _resolve_subworkflow_cache(self, dsl: WorkflowDSL) -> SubworkflowCache:
        """Pre-load referenced published subworkflow DSLs for sync compilation.

        Returns an empty cache when no loader is bound (subworkflow nodes then
        fail at build time with a clear message). Cycle detection runs here.
        """
        loader = self.subworkflow_loader
        if loader is None:
            return SubworkflowCache({})
        return await resolve_subworkflows(dsl, loader)

    async def cancel(self, execution_id: str, *, idempotency_key: str | None = None) -> bool:
        if idempotency_key is not None and len(idempotency_key.strip()) > 200:
            raise ValueError("Idempotency-Key must be at most 200 characters")
        from app.db.repositories import ExecutionQueueRepo

        remote_cancel_requested = False
        async with self.session_factory() as session:
            repo = ExecutionRepo(session)
            row = await repo.get(execution_id)
            if row is None:
                raise KeyError(execution_id)
            workflow = await WorkflowRepo(session).get(row.workflow_id)
            project_id = workflow.project_id if workflow is not None else None
            if row.status == "cancelled":
                await self._release_project_execution(session, project_id, execution_id)
                await session.commit()
                return True
            if row.status in {"succeeded", "failed"}:
                raise ValueError(f"execution {execution_id} is already {row.status}")
            queue_item = await ExecutionQueueRepo(session).request_cancel(execution_id)
            if row.status == "queued":
                changed = await repo.mark_cancelled(execution_id)
                if changed:
                    await self._release_project_execution(session, project_id, execution_id)
                    queue_row = await ExecutionQueueRepo(session).get_for_execution(execution_id)
                    if queue_row is not None and queue_row.status in {
                        "queued",
                        "retry_wait",
                    }:
                        queue_row.status = "done"
                        queue_row.owner_id = None
                        queue_row.lease_expires_at = None
                    committed_event = await self._append_cancel_event(repo, execution_id)
                    await session.commit()
                    self.event_bus.publish_committed(committed_event)
                    await self.event_bus.close_execution(execution_id)
                    return True
            remote_cancel_requested = queue_item is not None and queue_item.status == "leased"
            await session.commit()

        task = self._tasks.get(execution_id)
        if task and not task.done():
            task.cancel()
            return True
        if remote_cancel_requested:
            # The owning worker observes cancel_requested on its next heartbeat
            # and commits the terminal state with its still-valid lease.
            return True

        async with self.session_factory() as session:
            repo = ExecutionRepo(session)
            changed = await repo.mark_cancelled(execution_id)
            if changed:
                await self._release_project_execution(session, project_id, execution_id)
                committed_event = await self._append_cancel_event(repo, execution_id)
            await session.commit()
        if not changed:
            async with self.session_factory() as session:
                current = await ExecutionRepo(session).get(execution_id)
            return current is not None and current.status == "cancelled"

        self.event_bus.publish_committed(committed_event)
        await self.event_bus.close_execution(execution_id)
        return True

    async def _append_cancel_event(
        self,
        repo: ExecutionRepo,
        execution_id: str,
    ) -> ExecutionEvent:
        event_ts = datetime.now(UTC)
        event_seq = await repo.max_event_seq(execution_id) + 1
        payload: dict[str, Any] = {"reason": "cancel_request"}
        interrupted = await repo.latest_event(
            execution_id,
            EventType.WORKFLOW_INTERRUPTED.value,
        )
        if interrupted is not None and isinstance(interrupted.payload_json, dict):
            usage = ModelCallUsage.from_dict(interrupted.payload_json.get("cost"))
            if usage is not None:
                payload["cost"] = usage.to_dict()
        await repo.append_event(
            execution_id,
            event_seq,
            EventType.WORKFLOW_CANCELLED.value,
            None,
            payload,
            event_ts,
        )
        return ExecutionEvent(
            execution_id=execution_id,
            event_type=EventType.WORKFLOW_CANCELLED,
            seq=event_seq,
            payload=payload,
            ts=event_ts.isoformat(),
        )

    def active_executions(self) -> list[str]:
        """Return ids of executions still running in this process."""
        return [eid for eid, task in self._tasks.items() if not task.done()]

    async def _acquire_execution_slot(self) -> bool:
        semaphore = self._execution_semaphore
        if semaphore is None:
            return False
        if semaphore.locked():
            raise ExecutionConcurrencyLimit("execution concurrency limit reached")
        await semaphore.acquire()
        return True

    def _release_execution_slot(self, acquired: bool) -> None:
        if acquired and self._execution_semaphore is not None:
            self._execution_semaphore.release()

    async def shutdown(self, grace_period: float = 15.0) -> None:
        """Drain in-flight executions, then flush terminal events.

        Sequence (R-07 / U3-1):

        1. Flip ``_shutting_down`` so new ``start``/``resume`` calls are
           rejected with ``EngineShuttingDown`` (callers map this to 503).
        2. Wait up to ``grace_period`` for live tasks to reach a natural
           terminal state (succeeded / failed / cancelled).
        3. Cancel anything still running. Each cancelled task's own
           ``_run``/``_run_resume`` finally-block emits
           ``workflow_cancelled`` and persists the terminal state, so the
           DB always reflects what happened.
        4. Flush the event bus so the final terminal events are persisted
           before the DB engine is disposed by the container.
        """
        self._shutting_down = True
        try:
            await self._worker.stop()
        except Exception:
            logger.exception("execution worker stop failed; continuing shutdown")
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            logger.info(
                "execution engine shutdown: draining %d in-flight execution(s) with %.1fs grace",
                len(tasks),
                grace_period,
            )
            _done, pending = await asyncio.wait(
                tasks, timeout=grace_period, return_when=asyncio.ALL_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                cancelled, _ = await asyncio.wait(pending, timeout=5.0)
                still = [task for task in pending if task not in cancelled]
                if still:
                    logger.warning(
                        "execution engine shutdown: %d task(s) did not cancel cleanly",
                        len(still),
                    )
            # Give just-finished/cancelled tasks a final chance to flush their
            # terminal events through the EventBus persist batch.
            await self.event_bus.flush()
        await self._close_cached_providers()

    async def _checkpointer(self, lease: WorkerLease | None = None) -> Any:
        """Lazily build the process-wide checkpointer (None if no settings)."""
        if self.settings is None:
            return None
        saver = await make_checkpointer(self.settings)
        if lease is None:
            return saver
        return FencedCheckpointer(saver, self.session_factory, lease)

    def _fallback_output(self, node_outputs: dict[str, Any]) -> dict[str, Any]:
        if not node_outputs:
            return {}
        # last value
        last_key = list(node_outputs.keys())[-1]
        val = node_outputs[last_key]
        if isinstance(val, dict) and "output" in val:
            out = val["output"]
            return out if isinstance(out, dict) else {"result": out}
        return {"result": val}

    async def _set_status(
        self,
        execution_id: str,
        status: str,
        *,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        lease: WorkerLease | None = None,
        event_type: EventType | None = None,
        event_payload: dict[str, Any] | None = None,
        event_node_id: str | None = None,
    ) -> None:
        from app.db.repositories import ExecutionQueueRepo

        if lease is not None:
            # Persist all events emitted before the state transition while the
            # same fencing token is still active (notably waiting_approval).
            await self.event_bus.flush()
        committed_event: ExecutionEvent | None = None
        async with self.session_factory() as session:
            repo = ExecutionRepo(session)
            queue = ExecutionQueueRepo(session)
            if lease is not None and not await queue.owns_lease(
                item_id=lease.item_id,
                owner_id=lease.owner_id,
                lease_generation=lease.generation,
                lock=True,
            ):
                await session.rollback()
                raise LeaseLost(
                    f"execution {execution_id} lost lease {lease.item_id}:{lease.generation}"
                )
            row = await repo.get(execution_id)
            if row is None:
                return
            if event_type is not None:
                event_ts = datetime.now(UTC)
                event_seq = await repo.max_event_seq(execution_id) + 1
                await repo.append_event(
                    execution_id,
                    event_seq,
                    event_type.value,
                    event_node_id,
                    dict(event_payload or {}),
                    event_ts,
                )
                committed_event = ExecutionEvent(
                    execution_id=execution_id,
                    event_type=event_type,
                    seq=event_seq,
                    node_id=event_node_id,
                    payload=dict(event_payload or {}),
                    ts=event_ts.isoformat(),
                )
            await repo.update_status(row, status, output=output, error=error)
            # Human interrupts must free the queue lease in the same commit as the
            # waiting_approval transition; otherwise a resume can race the worker's
            # post-run complete() and hit "already has active queue work".
            if status == "waiting_approval":
                item = await queue.get_for_execution(execution_id)
                if item is not None and item.status in {"queued", "leased", "retry_wait"}:
                    item.status = "done"
                    item.owner_id = None
                    item.lease_expires_at = None
                    item.updated_at = datetime.now(UTC)
            if status != "running":
                workflow = await WorkflowRepo(session).get(row.workflow_id)
                await self._release_project_execution(
                    session,
                    workflow.project_id if workflow is not None else None,
                    execution_id,
                )
            await session.commit()
        if committed_event is not None:
            self.event_bus.publish_committed(committed_event)
