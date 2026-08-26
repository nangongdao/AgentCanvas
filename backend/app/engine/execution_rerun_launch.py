"""Failed-node rerun creation and durable queue enqueue."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from app.db.repositories import (
    ExecutionQueueRepo,
    ExecutionRepo,
    WorkflowRepo,
    WorkflowVersionRepo,
)
from app.engine.execution_errors import EngineShuttingDown
from app.engine.execution_launch import _execution_id_for_key
from app.schemas.dsl import WorkflowDSL
from app.services.execution_rerun import build_execution_rerun_plan


class ExecutionRerunLaunchMixin:
    async def rerun_from_node(
        self: Any,
        source_execution_id: str,
        *,
        node_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Create a new execution from a failed node using durable snapshots."""
        if self._shutting_down:
            raise EngineShuttingDown("execution engine is shutting down")
        created = True
        create_lock_acquired = False
        if self._execution_create_lock is not None:
            await self._execution_create_lock.acquire()
            create_lock_acquired = True
        try:
            await self.event_bus.flush()
            async with self.session_factory() as session:
                repo = ExecutionRepo(session)
                queue = ExecutionQueueRepo(session)
                source = await repo.get(source_execution_id)
                if source is None:
                    raise KeyError(source_execution_id)
                if source.status != "failed":
                    raise ValueError(
                        f"execution {source_execution_id} is {source.status}; "
                        "only failed executions can rerun"
                    )
                workflow = await WorkflowRepo(session).get(source.workflow_id)
                if workflow is None:
                    raise KeyError(source.workflow_id)
                workflow_version = await WorkflowVersionRepo(session).get(
                    source.workflow_version_id
                )
                if workflow_version is None:
                    raise KeyError(source.workflow_version_id)
                dsl = WorkflowDSL.model_validate(workflow_version.dsl_json)
                events = await repo.list_events_after(source_execution_id)
                plan = build_execution_rerun_plan(dsl, events, node_id=node_id)
                inputs = dict(source.input_json or {})
                rerun_id = _execution_id_for_key(
                    f"{source.workflow_id}:rerun:{source_execution_id}:{plan.node_id}",
                    idempotency_key,
                )
                payload = {
                    "node_outputs": plan.node_outputs,
                    "reused_node_ids": list(plan.reused_node_ids),
                }
                if rerun_id:
                    existing = await repo.get(rerun_id)
                    if existing is not None:
                        if (
                            existing.workflow_id != source.workflow_id
                            or existing.workflow_version_id != source.workflow_version_id
                            or existing.input_json != inputs
                            or existing.parent_execution_id != source_execution_id
                            or existing.rerun_from_node_id != plan.node_id
                        ):
                            raise ValueError(
                                "Idempotency-Key was already used with a different rerun request"
                            )
                        created = False
                    else:
                        try:
                            await repo.create(
                                source.workflow_id,
                                inputs,
                                execution_id=rerun_id,
                                session_id=source.session_id,
                                workflow_version_id=source.workflow_version_id,
                                parent_execution_id=source_execution_id,
                                rerun_from_node_id=plan.node_id,
                            )
                            await self._reserve_project_execution(
                                session, workflow.project_id, rerun_id
                            )
                            await queue.enqueue(rerun_id, kind="rerun", payload=payload)
                            await session.commit()
                        except IntegrityError:
                            await session.rollback()
                            existing = await repo.get(rerun_id)
                            if existing is None:
                                raise
                            if (
                                existing.parent_execution_id != source_execution_id
                                or existing.rerun_from_node_id != plan.node_id
                            ):
                                raise ValueError(
                                    "Idempotency-Key was already used with a different rerun request"
                                ) from None
                            created = False
                else:
                    execution = await repo.create(
                        source.workflow_id,
                        inputs,
                        session_id=source.session_id,
                        workflow_version_id=source.workflow_version_id,
                        parent_execution_id=source_execution_id,
                        rerun_from_node_id=plan.node_id,
                    )
                    rerun_id = execution.id
                    await self._reserve_project_execution(session, workflow.project_id, rerun_id)
                    await queue.enqueue(rerun_id, kind="rerun", payload=payload)
                    await session.commit()
        finally:
            if create_lock_acquired and self._execution_create_lock is not None:
                self._execution_create_lock.release()

        if created:
            self._worker.kick()
        return rerun_id


__all__ = ["ExecutionRerunLaunchMixin"]
