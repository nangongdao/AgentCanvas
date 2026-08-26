"""Workflow CRUD routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_session
from app.api.etag import etag_json_response
from app.api.pagination import PageParams, PageResult, page_result
from app.api.tenant_deps import authorize_workflow_project
from app.api.workflow_validation import validate_workflow_capabilities_or_422
from app.core.auth import Role
from app.db.repositories import WorkflowRepo
from app.engine.validation import DSLValidationError, validate_dsl
from app.schemas.api import WorkflowCreate, WorkflowOut, WorkflowUpdate
from app.schemas.dsl import WorkflowDSL
from app.services.audit import record_audit
from app.services.dsl_migrations import normalize_workflow_dsl

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _to_out(row) -> WorkflowOut:
    return WorkflowOut(
        id=row.id,
        name=row.name,
        description=row.description or "",
        dsl=row.dsl_json,
        version=row.version,
        is_archived=row.is_archived,
        project_id=row.project_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=PageResult[WorkflowOut])
async def list_workflows(
    request: Request,
    session: SessionDep,
    principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
    project_id: Annotated[str | None, Query()] = None,
) -> Response:
    if project_id is not None:
        await authorize_workflow_project(session, principal, project_id, required=Role.VIEWER)
    spec = params.to_spec(
        allowed_sorts={"created_at", "updated_at", "name", "id"},
        default_sort="updated_at",
    )
    page = await WorkflowRepo(session).list_page(spec, project_id=project_id)
    result = page_result(page, _to_out)
    # C6-4: high-frequency list surface — serve a 304 when the body is unchanged.
    return etag_json_response(request, result.model_dump(mode="json"))


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    body: WorkflowCreate, session: SessionDep, principal: EditorDep
) -> WorkflowOut:
    await authorize_workflow_project(session, principal, body.project_id, required=Role.EDITOR)
    try:
        dsl = WorkflowDSL.model_validate(normalize_workflow_dsl(body.dsl))
        validate_dsl(dsl, strict=False)
    except DSLValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await validate_workflow_capabilities_or_422(session, dsl)

    row = await WorkflowRepo(session).create(
        name=body.name or dsl.name,
        dsl=dsl.model_dump(mode="json"),
        description=body.description,
        project_id=body.project_id,
    )
    await record_audit(
        session,
        principal,
        action="workflow.created",
        resource_type="workflow",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={"version": row.version},
    )
    return _to_out(row)


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: str, session: SessionDep, principal: ViewerDep) -> WorkflowOut:
    row = await WorkflowRepo(session).get(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(session, principal, row.project_id, required=Role.VIEWER)
    return _to_out(row)


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str,
    body: WorkflowUpdate,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowOut:
    repo = WorkflowRepo(session)
    row = await repo.get(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(session, principal, row.project_id, required=Role.EDITOR)
    previous_version = row.version

    dsl_dict = None
    capability_dsl = WorkflowDSL.model_validate(row.dsl_json)
    if body.dsl is not None:
        try:
            dsl = WorkflowDSL.model_validate(normalize_workflow_dsl(body.dsl))
            validate_dsl(dsl, strict=False)
            dsl_dict = dsl.model_dump(mode="json")
            capability_dsl = dsl
        except DSLValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.errors) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    await validate_workflow_capabilities_or_422(session, capability_dsl)

    try:
        row = await repo.update(
            row,
            name=body.name,
            dsl=dsl_dict,
            description=body.description,
            expected_version=body.version,
            change_summary=body.change_summary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await record_audit(
        session,
        principal,
        action="workflow.updated",
        resource_type="workflow",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={
            "changed_fields": sorted(body.model_fields_set - {"change_summary"}),
            "previous_version": previous_version,
            "saved_version": row.version,
        },
    )

    return _to_out(row)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_workflow(workflow_id: str, session: SessionDep, principal: EditorDep) -> None:
    repo = WorkflowRepo(session)
    row = await repo.get(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(session, principal, row.project_id, required=Role.EDITOR)
    await repo.archive(row)
    await record_audit(
        session,
        principal,
        action="workflow.archived",
        resource_type="workflow",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={"version": row.version},
    )
