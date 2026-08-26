"""Transactional claim and durable queue scheduling for human approval resumes."""

from __future__ import annotations

from typing import Any

from app.core.auth import Principal
from app.db.repositories import (
    ExecutionQueueRepo,
    ExecutionRepo,
    WorkflowRepo,
    WorkflowVersionRepo,
)
from app.engine.execution_errors import EngineShuttingDown
from app.engine.execution_launch import ExecutionLaunchMixin
from app.schemas.dsl import WorkflowDSL
from app.services.audit import record_audit, summarize_human_approval


class ExecutionResumeMixin:
    async def resume(
        self: Any,
        execution_id: str,
        decision: dict[str, Any],
        *,
        audit_principal: Principal | None = None,
        debug: dict[str, Any] | None = None,
    ) -> str:
        """Claim a waiting execution and enqueue its resumed graph run."""
        if self._shutting_down:
            raise EngineShuttingDown("execution engine is shutting down")
        async with self.session_factory() as session:
            repo = ExecutionRepo(session)
            queue = ExecutionQueueRepo(session)
            row = await repo.claim_for_resume(execution_id)
            if row is None:
                existing = await repo.get(execution_id)
                if existing is None:
                    raise KeyError(execution_id)
                raise ValueError(f"execution {execution_id} is not waiting for approval")
            workflow = await WorkflowRepo(session).get(row.workflow_id)
            if workflow is None:
                raise KeyError(row.workflow_id)
            version = await WorkflowVersionRepo(session).get(row.workflow_version_id)
            if version is None:
                raise KeyError(row.workflow_version_id)
            # Validate the immutable snapshot still parses before enqueue.
            _ = WorkflowDSL.model_validate(version.dsl_json)
            await self._reserve_project_execution(session, workflow.project_id, execution_id)
            if audit_principal is not None:
                await record_audit(
                    session,
                    audit_principal,
                    action="human_approval.submitted",
                    resource_type="execution",
                    resource_id=row.id,
                    resource_name=workflow.name,
                    project_id=workflow.project_id,
                    details={
                        "workflow_id": workflow.id,
                        "workflow_version": version.number,
                        **summarize_human_approval(decision),
                    },
                )
            resume_payload: dict[str, Any] = {"decision": decision}
            debug_payload = ExecutionLaunchMixin._debug_payload(debug)
            if debug_payload is not None:
                resume_payload["debug"] = debug_payload["debug"]
            await queue.enqueue(
                execution_id,
                kind="resume",
                payload=resume_payload,
            )
            await session.commit()

        self._worker.kick()
        return execution_id


__all__ = ["ExecutionResumeMixin"]
