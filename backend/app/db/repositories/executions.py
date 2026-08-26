"""Execution and durable event persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Execution, ExecutionEventRow, Workflow
from app.db.pagination import PageSlice, PageSpec, paginate_select
from app.db.repositories.workflow_versions import WorkflowVersionRepo


class ExecutionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        execution_id: str | None = None,
        session_id: str | None = None,
        workflow_version_id: str | None = None,
        parent_execution_id: str | None = None,
        rerun_from_node_id: str | None = None,
        trigger_source: str = "manual",
    ) -> Execution:
        if workflow_version_id is None:
            workflow = await self.session.get(Workflow, workflow_id)
            if workflow is None:
                raise KeyError(f"workflow not found: {workflow_id}")
            version = await WorkflowVersionRepo(self.session).ensure_current(workflow)
            workflow_version_id = version.id
        resolved_execution_id = execution_id or uuid4().hex
        kwargs: dict[str, Any] = {
            "id": resolved_execution_id,
            "workflow_id": workflow_id,
            "workflow_version_id": workflow_version_id,
            # Create as queued; the worker flips to running when it acquires a lease.
            "status": "queued",
            "trigger_source": trigger_source,
            "input_json": inputs,
            "thread_id": resolved_execution_id,
            "session_id": session_id,
            "parent_execution_id": parent_execution_id,
            "rerun_from_node_id": rerun_from_node_id,
        }
        row = Execution(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, execution_id: str) -> Execution | None:
        return await self.session.get(Execution, execution_id)

    async def mark_cancelled(self, execution_id: str) -> bool:
        result = await self.session.execute(
            update(Execution)
            .where(
                Execution.id == execution_id,
                Execution.status.not_in(("succeeded", "failed", "cancelled")),
            )
            .values(status="cancelled", finished_at=datetime.now(UTC))
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def max_event_seq(self, execution_id: str) -> int:
        result = await self.session.execute(
            select(func.coalesce(func.max(ExecutionEventRow.seq), 0)).where(
                ExecutionEventRow.execution_id == execution_id
            )
        )
        return int(result.scalar_one())

    async def claim_for_resume(self, execution_id: str) -> Execution | None:
        """Atomically claim an interrupted execution for one resume caller."""
        result = await self.session.execute(
            update(Execution)
            .where(
                Execution.id == execution_id,
                Execution.status.in_(("waiting_approval", "interrupted")),
            )
            # Transition through queued; the worker lease flips it to running.
            .values(status="queued")
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            return None
        return await self.get(execution_id)

    async def list_for_workflow(self, workflow_id: str, limit: int = 50) -> list[Execution]:
        stmt = (
            select(Execution)
            .where(Execution.workflow_id == workflow_id)
            .order_by(Execution.started_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_workflow_page(
        self,
        workflow_id: str,
        spec: PageSpec,
        *,
        status_filter: set[str] | None = None,
    ) -> PageSlice[Execution]:
        stmt = select(Execution).where(Execution.workflow_id == workflow_id)
        if status_filter:
            stmt = stmt.where(Execution.status.in_(status_filter))
        return await paginate_select(
            self.session,
            stmt,
            id_column=Execution.id,
            columns={
                "started_at": Execution.started_at,
                "status": Execution.status,
                "id": Execution.id,
            },
            spec=spec,
            search_columns=(Execution.id, Execution.status, Execution.error),
        )

    async def update_status(
        self,
        execution: Execution,
        status: str,
        *,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        execution.status = status
        if output is not None:
            execution.output_json = output
        if error is not None:
            execution.error = error
        if status in {"succeeded", "failed", "cancelled"}:
            execution.finished_at = datetime.now(UTC)
        await self.session.flush()

    async def recover_running(self, error: str) -> int:
        """Fail only legacy running executions that have no durable queue row.

        Queue-backed work belongs to its current lease owner. Startup recovery
        must not rewrite that lease because another API/worker process may still
        be executing it; expired leases are reclaimed atomically by claim_next.
        """
        from app.db.repositories.execution_queue import ExecutionQueueRepo

        queue_repo = ExecutionQueueRepo(self.session)
        result = await self.session.execute(select(Execution).where(Execution.status == "running"))
        rows = list(result.scalars().all())
        recovered_at = datetime.now(UTC)
        failed = 0

        for execution in rows:
            queue_item = await queue_repo.get_for_execution(execution.id)
            if queue_item is not None and queue_item.status in {
                "queued",
                "retry_wait",
                "leased",
            }:
                continue
            seq_result = await self.session.execute(
                select(func.coalesce(func.max(ExecutionEventRow.seq), 0)).where(
                    ExecutionEventRow.execution_id == execution.id
                )
            )
            next_seq = int(seq_result.scalar_one()) + 1
            execution.status = "failed"
            execution.error = error
            execution.finished_at = recovered_at
            self.session.add(
                ExecutionEventRow(
                    execution_id=execution.id,
                    seq=next_seq,
                    event_type="workflow_failed",
                    node_id=None,
                    payload_json={"error": error, "reason": "backend_restart"},
                    ts=recovered_at,
                )
            )
            failed += 1

        await self.session.flush()
        return failed

    async def append_event(
        self,
        execution_id: str,
        seq: int,
        event_type: str,
        node_id: str | None,
        payload: dict[str, Any],
        ts: datetime | None = None,
    ) -> ExecutionEventRow:
        row = ExecutionEventRow(
            execution_id=execution_id,
            seq=seq,
            event_type=event_type,
            node_id=node_id,
            payload_json=payload,
            ts=ts or datetime.now(UTC),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def append_events(self, rows: list[dict[str, Any]]) -> list[ExecutionEventRow]:
        """Insert one ordered event batch in the caller's transaction."""
        event_rows = [ExecutionEventRow(**values) for values in rows]
        self.session.add_all(event_rows)
        await self.session.flush()
        return event_rows

    async def list_events_after(
        self, execution_id: str, after_seq: int = 0
    ) -> list[ExecutionEventRow]:
        stmt = (
            select(ExecutionEventRow)
            .where(
                ExecutionEventRow.execution_id == execution_id,
                ExecutionEventRow.seq > after_seq,
            )
            .order_by(ExecutionEventRow.seq.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def latest_event(
        self,
        execution_id: str,
        event_type: str,
    ) -> ExecutionEventRow | None:
        stmt = (
            select(ExecutionEventRow)
            .where(
                ExecutionEventRow.execution_id == execution_id,
                ExecutionEventRow.event_type == event_type,
            )
            .order_by(ExecutionEventRow.seq.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_events_after_id(
        self, after_id: int = 0, *, limit: int = 100
    ) -> list[ExecutionEventRow]:
        """Return committed events in global id order for the durable relay."""
        stmt = (
            select(ExecutionEventRow)
            .where(ExecutionEventRow.id > after_id)
            .order_by(ExecutionEventRow.id.asc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def prune_events_before(
        self, cutoff: datetime, *, batch_size: int
    ) -> int:
        """Delete one bounded batch of detail events older than ``cutoff``.

        Only events belonging to executions that have already reached a
        terminal state (``succeeded``/``failed``/``cancelled``) and finished
        before ``cutoff`` are eligible — pruning never touches a live or
        interrupted execution's event stream. The delete is capped at
        ``batch_size`` rows so a single prune step holds the write lock for
        a bounded duration and the scheduler's loop can yield between batches.
        Returns the number of rows deleted in this batch (0 when nothing
        remains eligible).
        """
        terminal_executions = select(Execution.id).where(
            Execution.status.in_(("succeeded", "failed", "cancelled")),
            Execution.finished_at.is_not(None),
            Execution.finished_at < cutoff,
        )
        # ``delete().limit()`` is not supported on all dialects at the ORM
        # layer (SQLite/PostgreSQL accept it at the SQL level), so emit a
        # raw ``DELETE ... WHERE id IN (SELECT ... LIMIT :n)`` form. The
        # sub-select picks the smallest event ids for eligible executions
        # so each batch drains the oldest rows first and the loop converges.
        eligible_ids = (
            select(ExecutionEventRow.id)
            .where(
                ExecutionEventRow.ts < cutoff,
                ExecutionEventRow.execution_id.in_(terminal_executions),
            )
            .order_by(ExecutionEventRow.id.asc())
            .limit(max(1, batch_size))
        )
        stmt = (
            delete(ExecutionEventRow)
            .where(ExecutionEventRow.id.in_(eligible_ids))
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_event_usage_in_range(
        self,
        *,
        start_ts: datetime,
        end_ts: datetime,
        after_id: int = 0,
        limit: int = 500,
    ) -> list[ExecutionEventRow]:
        """Stream events whose ``ts`` falls in ``[start_ts, end_ts)``.

        Backed by ``ix_execution_events_ts`` (C6-1); the caller paginates with
        ``after_id`` so a day's aggregation sweep never materializes the whole
        window at once.
        """
        stmt = (
            select(ExecutionEventRow)
            .where(
                ExecutionEventRow.ts >= start_ts,
                ExecutionEventRow.ts < end_ts,
                ExecutionEventRow.id > after_id,
            )
            .order_by(ExecutionEventRow.id.asc())
            .limit(max(1, limit))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
