"""Workflow schedule management endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_session
from app.api.workflow_access import authorize_workflow
from app.core.auth import Role
from app.db.models import WorkflowSchedule, WorkflowVersion
from app.db.repositories import WorkflowScheduleRepo, WorkflowVersionRepo
from app.engine.input_validation import WorkflowInputError, validate_execution_inputs
from app.schemas.api import (
    WorkflowScheduleCreate,
    WorkflowScheduleOut,
    WorkflowScheduleUpdate,
)
from app.schemas.dsl import WorkflowDSL
from app.services.audit import record_audit
from app.services.schedule_time import next_cron_run

router = APIRouter(prefix="/api/workflows/{workflow_id}/schedules", tags=["workflow schedules"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _published_version(session: AsyncSession, workflow_id: str, number: int) -> WorkflowVersion:
    row = await WorkflowVersionRepo(session).get_by_number(workflow_id, number)
    if row is None or row.status != "published":
        raise HTTPException(status_code=409, detail="workflow must have a published version")
    return row


async def _schedule_out(session: AsyncSession, row: WorkflowSchedule) -> WorkflowScheduleOut:
    version = await WorkflowVersionRepo(session).get(row.published_version_id)
    if version is None:
        raise HTTPException(status_code=500, detail="schedule published version is missing")
    return WorkflowScheduleOut(
        id=row.id,
        workflow_id=row.workflow_id,
        published_version_id=row.published_version_id,
        published_version_number=version.number,
        name=row.name,
        cron_expression=row.cron_expression,
        timezone=row.timezone,
        status=row.status,
        inputs=dict(row.input_json or {}),
        misfire_policy=row.misfire_policy,  # type: ignore[arg-type]
        failure_policy=row.failure_policy,  # type: ignore[arg-type]
        retry_delay_seconds=row.retry_delay_seconds,
        next_run_at=row.next_run_at,
        pending_run_at=row.pending_run_at,
        last_run_at=row.last_run_at,
        last_execution_id=row.last_execution_id,
        last_error=row.last_error,
        failure_count=row.failure_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _schedule_or_404(
    session: AsyncSession, workflow_id: str, schedule_id: str
) -> WorkflowSchedule:
    row = await WorkflowScheduleRepo(session).get_for_workflow(workflow_id, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow schedule not found")
    return row


def _validated_inputs(version: WorkflowVersion, values: dict[str, object]) -> dict[str, object]:
    try:
        return validate_execution_inputs(WorkflowDSL.model_validate(version.dsl_json), values)
    except WorkflowInputError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc


@router.get("", response_model=list[WorkflowScheduleOut])
async def list_workflow_schedules(
    workflow_id: str, session: SessionDep, principal: ViewerDep
) -> list[WorkflowScheduleOut]:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    rows = await WorkflowScheduleRepo(session).list_for_workflow(workflow_id)
    return [await _schedule_out(session, row) for row in rows]


@router.post("", response_model=WorkflowScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_workflow_schedule(
    workflow_id: str,
    body: WorkflowScheduleCreate,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowScheduleOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    version = await _published_version(session, workflow.id, workflow.version)
    inputs = _validated_inputs(version, body.inputs)
    now = datetime.now(UTC)
    try:
        row = await WorkflowScheduleRepo(session).create(
            workflow_id=workflow.id,
            published_version_id=version.id,
            name=body.name,
            cron_expression=body.cron_expression,
            timezone=body.timezone,
            input_json=inputs,
            misfire_policy=body.misfire_policy,
            failure_policy=body.failure_policy,
            retry_delay_seconds=body.retry_delay_seconds,
            next_run_at=next_cron_run(body.cron_expression, body.timezone, now),
            enabled=body.enabled,
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="schedule name already exists") from exc
    await record_audit(
        session,
        principal,
        action="workflow_schedule.created",
        resource_type="workflow_schedule",
        resource_id=row.id,
        resource_name=row.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id, "version": version.number},
    )
    return await _schedule_out(session, row)


@router.put("/{schedule_id}", response_model=WorkflowScheduleOut)
async def update_workflow_schedule(
    workflow_id: str,
    schedule_id: str,
    body: WorkflowScheduleUpdate,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowScheduleOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    row = await _schedule_or_404(session, workflow_id, schedule_id)
    version = await _published_version(session, workflow.id, workflow.version)
    changes = body.model_dump(exclude_unset=True)
    inputs = changes.pop("inputs", None)
    enabled = changes.pop("enabled", None)
    timing_changed = "cron_expression" in changes or "timezone" in changes
    row.input_json = _validated_inputs(
        version,
        inputs if inputs is not None else dict(row.input_json or {}),
    )
    for field_name, value in changes.items():
        setattr(row, field_name, value)
    row.published_version_id = version.id
    row.pending_run_at = None
    row.last_error = None
    if enabled is not None:
        row.status = "active" if enabled else "disabled"
        row.pending_run_at = None
    if timing_changed or enabled is True:
        row.next_run_at = next_cron_run(row.cron_expression, row.timezone, datetime.now(UTC))
    row.updated_at = datetime.now(UTC)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="schedule name already exists") from exc
    await record_audit(
        session,
        principal,
        action="workflow_schedule.updated",
        resource_type="workflow_schedule",
        resource_id=row.id,
        resource_name=row.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id, "changed_fields": sorted(body.model_fields_set)},
    )
    return await _schedule_out(session, row)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_workflow_schedule(
    workflow_id: str,
    schedule_id: str,
    session: SessionDep,
    principal: EditorDep,
) -> None:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    row = await _schedule_or_404(session, workflow_id, schedule_id)
    await WorkflowScheduleRepo(session).disable(row)
    await record_audit(
        session,
        principal,
        action="workflow_schedule.disabled",
        resource_type="workflow_schedule",
        resource_id=row.id,
        resource_name=row.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id},
    )


__all__ = ["router"]
