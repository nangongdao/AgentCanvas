"""Workflow version lifecycle, cloning, and comparison routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_session
from app.api.routes.workflows import _to_out
from app.api.workflow_access import authorize_workflow, workflow_version_or_404
from app.api.workflow_validation import validate_workflow_capabilities_or_422
from app.core.auth import Role
from app.db.models import WorkflowVersion
from app.db.repositories import (
    EvaluationRunRepo,
    WebhookTriggerRepo,
    WorkflowApiPublicationRepo,
    WorkflowRepo,
    WorkflowScheduleRepo,
    WorkflowVersionRepo,
)
from app.engine.input_validation import WorkflowInputError, validate_execution_inputs
from app.engine.validation import DSLValidationError, validate_dsl
from app.schemas.api import WorkflowOut
from app.schemas.dsl import WorkflowDSL
from app.schemas.workflow_versions import (
    WorkflowClone,
    WorkflowDiffOut,
    WorkflowEvaluationPolicyIn,
    WorkflowEvaluationPolicyOut,
    WorkflowExportOut,
    WorkflowMergeConflictOut,
    WorkflowMergeOut,
    WorkflowMergeRequest,
    WorkflowRollback,
    WorkflowVersionOut,
)
from app.services.audit import record_audit
from app.services.dsl_migrations import normalize_workflow_dsl
from app.services.workflow_diff import diff_workflow_dsl
from app.services.workflow_merge import merge_workflow_snapshots

router = APIRouter(prefix="/api/workflows/{workflow_id}", tags=["workflow versions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _version_out(row: WorkflowVersion) -> WorkflowVersionOut:
    return WorkflowVersionOut(
        id=row.id,
        workflow_id=row.workflow_id,
        number=row.number,
        status=row.status,  # type: ignore[arg-type]
        name=row.name,
        description=row.description or "",
        dsl=row.dsl_json,
        change_summary=row.change_summary or "",
        created_at=row.created_at,
        published_at=row.published_at,
        archived_at=row.archived_at,
    )


def _validated_dsl(raw: dict[str, object]) -> dict[str, object]:
    try:
        dsl = WorkflowDSL.model_validate(normalize_workflow_dsl(raw))
        validate_dsl(dsl)
    except DSLValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dsl.model_dump(mode="json")


@router.get("/versions", response_model=list[WorkflowVersionOut])
async def list_workflow_versions(
    workflow_id: str, session: SessionDep, principal: ViewerDep
) -> list[WorkflowVersionOut]:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    rows = await WorkflowVersionRepo(session).list_for_workflow(workflow_id)
    return [_version_out(row) for row in rows]


@router.get("/versions/diff", response_model=WorkflowDiffOut)
async def diff_workflow_versions(
    workflow_id: str,
    session: SessionDep,
    principal: ViewerDep,
    base_id: Annotated[str, Query(min_length=1, max_length=32)],
    target_id: Annotated[str, Query(min_length=1, max_length=32)],
) -> WorkflowDiffOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    base = await workflow_version_or_404(session, workflow_id, base_id)
    target = await workflow_version_or_404(session, workflow_id, target_id)
    return WorkflowDiffOut.model_validate(
        diff_workflow_dsl(base.id, base.dsl_json, target.id, target.dsl_json)
    )


@router.post("/versions/merge", response_model=WorkflowMergeOut)
async def merge_workflow_versions(
    workflow_id: str,
    body: WorkflowMergeRequest,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowMergeOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    if workflow.version != body.remote_version:
        raise HTTPException(
            status_code=409,
            detail=(
                "remote version advanced during merge: "
                f"expected {body.remote_version}, got {workflow.version}"
            ),
        )
    base = await workflow_version_or_404(session, workflow_id, body.base_version_id)
    local_dsl = _validated_dsl(body.local_dsl)
    await validate_workflow_capabilities_or_422(session, WorkflowDSL.model_validate(local_dsl))

    result = merge_workflow_snapshots(
        base_name=base.name,
        base_dsl=base.dsl_json,
        local_name=body.local_name,
        local_dsl=local_dsl,
        remote_name=workflow.name,
        remote_dsl=workflow.dsl_json,
    )
    conflicts = [
        WorkflowMergeConflictOut.model_validate(conflict, from_attributes=True)
        for conflict in result.conflicts
    ]
    if conflicts:
        return WorkflowMergeOut(
            status="conflict",
            workflow_id=workflow.id,
            base_version=base.number,
            remote_version=workflow.version,
            name=result.name,
            dsl=result.dsl,
            conflicts=conflicts,
        )

    merged_payload = _validated_dsl(result.dsl)
    await validate_workflow_capabilities_or_422(session, WorkflowDSL.model_validate(merged_payload))
    changed = result.name != workflow.name or merged_payload != workflow.dsl_json
    if changed:
        try:
            await WorkflowRepo(session).update(
                workflow,
                name=result.name,
                dsl=merged_payload,
                expected_version=body.remote_version,
                change_summary=body.change_summary,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await record_audit(
            session,
            principal,
            action="workflow.merged",
            resource_type="workflow",
            resource_id=workflow.id,
            resource_name=workflow.name,
            project_id=workflow.project_id,
            details={
                "base_version": base.number,
                "remote_version": body.remote_version,
                "saved_version": workflow.version,
            },
        )
    return WorkflowMergeOut(
        status="merged",
        workflow_id=workflow.id,
        base_version=base.number,
        remote_version=body.remote_version,
        saved_version=workflow.version,
        name=workflow.name,
        dsl=workflow.dsl_json,
    )


@router.get("/versions/{version_id}", response_model=WorkflowVersionOut)
async def get_workflow_version(
    workflow_id: str,
    version_id: str,
    session: SessionDep,
    principal: ViewerDep,
) -> WorkflowVersionOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    row = await workflow_version_or_404(session, workflow_id, version_id)
    return _version_out(row)


@router.get("/versions/{version_id}/export", response_model=WorkflowExportOut)
async def export_workflow_version(
    workflow_id: str,
    version_id: str,
    session: SessionDep,
    principal: ViewerDep,
) -> WorkflowExportOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    row = await workflow_version_or_404(session, workflow_id, version_id)
    return WorkflowExportOut(
        name=row.name,
        description=row.description or "",
        source_workflow_id=row.workflow_id,
        source_version_number=row.number,
        source_version_status=row.status,  # type: ignore[arg-type]
        dsl=row.dsl_json,
    )


async def _enforce_evaluation_gate(
    session: AsyncSession, workflow: Any, row: WorkflowVersion
) -> None:
    """Block publish when the workflow has an eval gate the new version fails.

    Reads ``workflows.evaluation_policy`` (``{"dataset_version_id", "threshold"}``)
    and the newest completed run of the to-be-published version against that
    dataset. Raises 409 when the pass rate is below the threshold or no
    completed run exists yet.
    """
    policy = workflow.evaluation_policy
    if not policy:
        return
    dataset_version_id = str(policy.get("dataset_version_id") or "")
    threshold = float(policy.get("threshold") or 0.0)
    if not dataset_version_id:
        return
    run = await EvaluationRunRepo(session).get_latest_completed(
        dataset_version_id, row.id
    )
    if run is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"publish blocked by evaluation gate: no completed run for "
                f"dataset version {dataset_version_id} on this workflow version"
            ),
        )
    summary = run.summary_json or {}
    passed = int(summary.get("passed") or 0)
    total = passed + int(summary.get("failed") or 0) + int(summary.get("error") or 0)
    pass_rate = (passed / total) if total else 0.0
    if pass_rate < threshold:
        raise HTTPException(
            status_code=409,
            detail=(
                f"publish blocked by evaluation gate: pass rate {pass_rate:.2%} "
                f"below threshold {threshold:.2%}"
            ),
        )


@router.put("/evaluation-policy", response_model=WorkflowEvaluationPolicyOut)
async def set_evaluation_policy(
    workflow_id: str,
    body: WorkflowEvaluationPolicyIn,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowEvaluationPolicyOut:
    """Attach (or clear by threshold=0) the publish eval gate to a workflow."""
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    if body.threshold <= 0:
        workflow.evaluation_policy = None
    else:
        workflow.evaluation_policy = {
            "dataset_version_id": body.dataset_version_id,
            "threshold": body.threshold,
        }
    await session.commit()
    await record_audit(
        session,
        principal,
        action="workflow.evaluation_policy.updated",
        resource_type="workflow",
        resource_id=workflow.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"threshold": body.threshold},
    )
    policy = workflow.evaluation_policy or {}
    return WorkflowEvaluationPolicyOut(
        dataset_version_id=str(policy.get("dataset_version_id") or ""),
        threshold=float(policy.get("threshold") or 0.0),
    )


@router.get("/evaluation-policy", response_model=WorkflowEvaluationPolicyOut | None)
async def get_evaluation_policy(
    workflow_id: str,
    session: SessionDep,
    principal: ViewerDep,
) -> WorkflowEvaluationPolicyOut | None:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    policy = workflow.evaluation_policy
    if not policy:
        return None
    return WorkflowEvaluationPolicyOut(
        dataset_version_id=str(policy.get("dataset_version_id") or ""),
        threshold=float(policy.get("threshold") or 0.0),
    )


@router.post("/publish", response_model=WorkflowVersionOut)
async def publish_workflow(
    workflow_id: str, session: SessionDep, principal: EditorDep
) -> WorkflowVersionOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    await validate_workflow_capabilities_or_422(
        session, WorkflowDSL.model_validate(workflow.dsl_json)
    )
    row = await WorkflowVersionRepo(session).publish(workflow)
    await _enforce_evaluation_gate(session, workflow, row)
    trigger = await WebhookTriggerRepo(session).get_for_workflow(workflow.id)
    if trigger is not None:
        await WebhookTriggerRepo(session).bind_version(trigger, row.id)
    api_publication = await WorkflowApiPublicationRepo(session).get_for_workflow(workflow.id)
    if api_publication is not None:
        await WorkflowApiPublicationRepo(session).bind_version(api_publication, row.id)
    schedules = await WorkflowScheduleRepo(session).list_for_workflow(workflow.id)
    schedule_updated_at = datetime.now(UTC)
    for schedule in schedules:
        schedule.published_version_id = row.id
        try:
            validate_execution_inputs(
                WorkflowDSL.model_validate(row.dsl_json), dict(schedule.input_json or {})
            )
        except WorkflowInputError as exc:
            schedule.status = "error"
            schedule.last_error = f"published workflow inputs are incompatible: {exc}"
        schedule.updated_at = schedule_updated_at
    if schedules:
        await session.flush()
    await record_audit(
        session,
        principal,
        action="workflow_version.published",
        resource_type="workflow_version",
        resource_id=row.id,
        resource_name=row.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id, "version": row.number},
    )
    return _version_out(row)


@router.post("/versions/{version_id}/rollback", response_model=WorkflowVersionOut)
async def rollback_workflow(
    workflow_id: str,
    version_id: str,
    body: WorkflowRollback,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowVersionOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    repo = WorkflowVersionRepo(session)
    source = await workflow_version_or_404(session, workflow_id, version_id)
    await validate_workflow_capabilities_or_422(
        session, WorkflowDSL.model_validate(source.dsl_json)
    )
    row = await repo.rollback(workflow, source, change_summary=body.change_summary)
    await record_audit(
        session,
        principal,
        action="workflow_version.rolled_back",
        resource_type="workflow_version",
        resource_id=row.id,
        resource_name=row.name,
        project_id=workflow.project_id,
        details={
            "workflow_id": workflow.id,
            "source_version": source.number,
            "saved_version": row.number,
        },
    )
    return _version_out(row)


@router.post("/clone", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def clone_workflow(
    workflow_id: str,
    body: WorkflowClone,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    versions = WorkflowVersionRepo(session)
    source = (
        await workflow_version_or_404(session, workflow_id, body.version_id)
        if body.version_id
        else await versions.ensure_current(workflow)
    )
    await validate_workflow_capabilities_or_422(
        session, WorkflowDSL.model_validate(source.dsl_json)
    )
    clone_name = body.name or f"{source.name} (Copy)"
    clone_dsl = {**source.dsl_json, "name": clone_name}
    clone = await WorkflowRepo(session).create(
        name=clone_name,
        description=body.description if body.description is not None else source.description,
        dsl=clone_dsl,
        project_id=workflow.project_id,
    )
    await record_audit(
        session,
        principal,
        action="workflow.cloned",
        resource_type="workflow",
        resource_id=clone.id,
        resource_name=clone.name,
        project_id=clone.project_id,
        details={
            "source_workflow_id": workflow.id,
            "source_version": source.number,
            "version": clone.version,
        },
    )
    return _to_out(clone)


__all__ = ["router"]
