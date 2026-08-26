"""Workflow-version selection, idempotent row creation, and queue enqueue."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.db.repositories import ExecutionQueueRepo, ExecutionRepo, WorkflowRepo, WorkflowVersionRepo
from app.engine.execution_errors import EngineShuttingDown
from app.engine.input_validation import validate_execution_inputs
from app.schemas.dsl import WorkflowDSL


def _execution_id_for_key(workflow_id: str, idempotency_key: str | None) -> str | None:
    """Derive a stable primary key for an API idempotency key."""
    if not idempotency_key:
        return None
    normalized = idempotency_key.strip()
    if not normalized:
        return None
    if len(normalized) > 200:
        raise ValueError("Idempotency-Key must be at most 200 characters")
    return hashlib.sha256(f"{workflow_id}\0{normalized}".encode()).hexdigest()[:32]


class ExecutionLaunchMixin:
    """Start current or immutable workflow versions through one creation path."""

    async def start(
        self: Any,
        workflow_id: str,
        inputs: dict[str, Any],
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        trigger_source: str = "manual",
        debug: dict[str, Any] | None = None,
    ) -> str:
        return await self._start_version(
            inputs,
            workflow_id=workflow_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            trigger_source=trigger_source,
            debug=debug,
        )

    async def start_version(
        self: Any,
        workflow_version_id: str,
        inputs: dict[str, Any],
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        trigger_source: str = "manual",
        debug: dict[str, Any] | None = None,
    ) -> str:
        """Start an immutable workflow snapshot, used by evaluation and A/B runs."""
        return await self._start_version(
            inputs,
            workflow_version_id=workflow_version_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            trigger_source=trigger_source,
            debug=debug,
        )

    async def _start_version(
        self: Any,
        inputs: dict[str, Any],
        *,
        workflow_id: str | None = None,
        workflow_version_id: str | None = None,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        trigger_source: str = "manual",
        debug: dict[str, Any] | None = None,
    ) -> str:
        if self._shutting_down:
            raise EngineShuttingDown("execution engine is shutting down")
        created = True
        create_lock_acquired = False
        sqlite_create_lock = self._execution_create_lock
        if sqlite_create_lock is not None:
            self._execution_create_inflight += 1
            try:
                await sqlite_create_lock.acquire()
            except BaseException:
                self._execution_create_inflight -= 1
                if self._execution_create_inflight == 0:
                    self._worker.kick()
                raise
            create_lock_acquired = True
        try:
            async with self.session_factory() as session:
                wf_repo = WorkflowRepo(session)
                ex_repo = ExecutionRepo(session)
                versions = WorkflowVersionRepo(session)
                queue = ExecutionQueueRepo(session)
                if workflow_version_id is not None:
                    workflow_version = await versions.get(workflow_version_id)
                    if workflow_version is None:
                        raise KeyError(f"workflow version not found: {workflow_version_id}")
                    workflow_id = workflow_version.workflow_id
                    workflow = await wf_repo.get(workflow_id)
                else:
                    if workflow_id is None:
                        raise ValueError("workflow_id or workflow_version_id is required")
                    workflow = await wf_repo.get(workflow_id)
                    workflow_version = (
                        await versions.ensure_current(workflow) if workflow is not None else None
                    )
                if workflow is None or workflow_version is None:
                    raise KeyError(f"workflow not found: {workflow_id}")
                workflow_id = workflow.id
                if trigger_source not in {"manual", "webhook", "schedule", "api"}:
                    raise ValueError("invalid execution trigger source")
                dsl = WorkflowDSL.model_validate(workflow_version.dsl_json)
                validated_inputs = validate_execution_inputs(dsl, inputs)
                execution_id = _execution_id_for_key(workflow_id, idempotency_key)
                if execution_id:
                    existing = await ex_repo.get(execution_id)
                    if existing is not None:
                        if (
                            existing.workflow_id != workflow_id
                            or existing.workflow_version_id != workflow_version.id
                            or existing.input_json != validated_inputs
                            or existing.session_id != session_id
                            or existing.trigger_source != trigger_source
                        ):
                            raise ValueError(
                                "Idempotency-Key was already used with different execution inputs"
                            )
                        created = False
                    else:
                        try:
                            await ex_repo.create(
                                workflow_id=workflow_id,
                                inputs=validated_inputs,
                                execution_id=execution_id,
                                session_id=session_id,
                                workflow_version_id=workflow_version.id,
                                trigger_source=trigger_source,
                            )
                            await self._reserve_project_execution(
                                session, workflow.project_id, execution_id
                            )
                            # The execution row was just created with a fresh
                            # id — no queue row can exist, so skip the lookup.
                            await queue.enqueue_new(
                                execution_id,
                                kind="start",
                                payload=self._debug_payload(debug),
                            )
                            await session.commit()
                        except IntegrityError:
                            await session.rollback()
                            existing = await ex_repo.get(execution_id)
                            if existing is None:
                                raise
                            if (
                                existing.input_json != validated_inputs
                                or existing.workflow_version_id != workflow_version.id
                                or existing.session_id != session_id
                                or existing.trigger_source != trigger_source
                            ):
                                raise ValueError(
                                    "Idempotency-Key was already used with different execution inputs"
                                ) from None
                            created = False
                else:
                    execution = await ex_repo.create(
                        workflow_id=workflow_id,
                        inputs=validated_inputs,
                        session_id=session_id,
                        workflow_version_id=workflow_version.id,
                        trigger_source=trigger_source,
                    )
                    await self._reserve_project_execution(
                        session, workflow.project_id, execution.id
                    )
                    # Same fresh-id guarantee as the idempotent branch above.
                    await queue.enqueue_new(
                        execution.id,
                        kind="start",
                        payload=self._debug_payload(debug),
                    )
                    await session.commit()
                    execution_id = execution.id
        finally:
            if create_lock_acquired and self._execution_create_lock is not None:
                self._execution_create_lock.release()
            if sqlite_create_lock is not None:
                self._execution_create_inflight -= 1
                if self._execution_create_inflight == 0:
                    self._worker.kick()

        if created and sqlite_create_lock is None:
            self._worker.kick()
        return execution_id

    @staticmethod
    def _debug_payload(debug: dict[str, Any] | None) -> dict[str, Any] | None:
        """Serialize debug-run controls onto the queue payload.

        The worker carries this into the runner so breakpoints/single-step
        persist for the whole run; resume re-supplies it from the resume
        endpoint so no DB column is needed.
        """
        if not debug:
            return None
        breakpoints = debug.get("breakpoints")
        single_step = bool(debug.get("single_step"))
        if not breakpoints and not single_step:
            return None
        payload: dict[str, Any] = {}
        if isinstance(breakpoints, list):
            payload["breakpoints"] = [str(item) for item in breakpoints]
        payload["single_step"] = single_step
        return {"debug": payload}


__all__ = ["ExecutionLaunchMixin", "_execution_id_for_key"]
