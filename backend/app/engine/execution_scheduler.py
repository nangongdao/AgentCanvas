"""Recovery scheduler for expired execution worker leases."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Execution, ExecutionQueueItem
from app.db.repositories import (
    ExecutionQueueRepo,
    ExecutionRepo,
    WorkflowRepo,
    WorkflowVersionRepo,
)
from app.engine.dsl_traversal import iter_workflow_nodes
from app.schemas.dsl import NodeType, WorkflowDSL
from app.services.execution_rerun import node_side_effect_reason
from app.services.project_quotas import ProjectQuotaService

logger = logging.getLogger(__name__)


def workflow_replay_block_reason(dsl: WorkflowDSL) -> str | None:
    """Return why an ambiguously interrupted workflow cannot be replayed."""
    for node in iter_workflow_nodes(dsl):
        try:
            NodeType(node.type)
        except ValueError:
            return f"dynamic plugin node '{node.id}' may mutate external systems"
        reason = node_side_effect_reason(node)
        if reason:
            return f"node '{node.id}': {reason}"
    return None


class ExecutionLeaseScheduler:
    """Classify and recover expired leases under durable queue-row locks."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_seconds: float = 1.0,
        batch_size: int = 100,
        max_attempts: int = 3,
    ) -> None:
        self.session_factory = session_factory
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.batch_size = max(1, int(batch_size))
        self.max_attempts = max(1, int(max_attempts))
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopped = False

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="execution-lease-scheduler")

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

    async def recover_once(self) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            queue = ExecutionQueueRepo(session)
            rows = await queue.list_expired(limit=self.batch_size)
            recovered = 0
            for item in rows:
                recovered += int(await self._recover_item(session, item, now=now))
            if rows:
                await session.commit()
            else:
                await session.rollback()
            return recovered

    async def _recover_item(
        self,
        session: AsyncSession,
        item: ExecutionQueueItem,
        *,
        now: datetime,
    ) -> bool:
        queue = ExecutionQueueRepo(session)
        await queue.lock_lease(item.id)
        current = await session.get(ExecutionQueueItem, item.id, with_for_update=True)
        expires_at = current.lease_expires_at if current is not None else None
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if current is None or current.status != "leased" or expires_at is None or expires_at >= now:
            return False
        item = current
        execution = await ExecutionRepo(session).get(item.execution_id)
        if execution is None:
            self._retire(item, status="dead_letter", error="execution row missing", now=now)
            return True
        if execution.status in {"succeeded", "failed", "cancelled", "waiting_approval"}:
            self._retire(item, status="done", error=None, now=now)
            return True
        workflow = await WorkflowRepo(session).get(execution.workflow_id)
        if item.cancel_requested:
            await ExecutionRepo(session).update_status(execution, "cancelled")
            self._retire(item, status="done", error=None, now=now)
            await self._release_quota(session, execution, workflow.project_id if workflow else None)
            await self._append_terminal_event(
                session,
                execution,
                event_type="workflow_cancelled",
                payload={"reason": "lease_expired_after_cancel"},
                now=now,
            )
            return True

        reason = await self._replay_block_reason(session, execution)
        if reason is None and item.attempt < self.max_attempts:
            self._retire(
                item,
                status="queued",
                error="worker lease expired; scheduled for replay",
                now=now,
            )
            execution.status = "queued"
            execution.error = None
            execution.finished_at = None
            return True

        error = reason or f"worker lease expired after {item.attempt} attempt(s)"
        self._retire(item, status="dead_letter", error=error, now=now)
        await ExecutionRepo(session).update_status(execution, "failed", error=error)
        await self._release_quota(session, execution, workflow.project_id if workflow else None)
        await self._append_terminal_event(
            session,
            execution,
            event_type="workflow_failed",
            payload={
                "error": error,
                "reason": "ambiguous_worker_loss",
                "dead_letter": True,
            },
            now=now,
        )
        return True

    async def _replay_block_reason(self, session: AsyncSession, execution: Execution) -> str | None:
        version = await WorkflowVersionRepo(session).get(execution.workflow_version_id)
        if version is None:
            return "workflow snapshot missing"
        try:
            dsl = WorkflowDSL.model_validate(version.dsl_json)
        except Exception as exc:  # noqa: BLE001 - corrupt immutable snapshot fails closed
            return f"workflow snapshot invalid: {exc}"
        return workflow_replay_block_reason(dsl)

    @staticmethod
    def _retire(
        item: ExecutionQueueItem,
        *,
        status: str,
        error: str | None,
        now: datetime,
    ) -> None:
        item.status = status
        item.owner_id = None
        item.lease_expires_at = None
        item.available_at = now
        item.last_error = error
        item.updated_at = now

    @staticmethod
    async def _release_quota(
        session: AsyncSession,
        execution: Execution,
        project_id: str | None,
    ) -> None:
        if project_id is not None:
            await ProjectQuotaService(session).release(project_id, "execution", execution.id)

    @staticmethod
    async def _append_terminal_event(
        session: AsyncSession,
        execution: Execution,
        *,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        repo = ExecutionRepo(session)
        await repo.append_event(
            execution.id,
            await repo.max_event_seq(execution.id) + 1,
            event_type,
            None,
            payload,
            ts=now,
        )

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                recovered = await self.recover_once()
            except Exception:
                logger.exception("execution lease scheduler batch failed")
                recovered = 0
            if self._stopped:
                break
            if recovered >= self.batch_size:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue


__all__ = ["ExecutionLeaseScheduler", "workflow_replay_block_reason"]
