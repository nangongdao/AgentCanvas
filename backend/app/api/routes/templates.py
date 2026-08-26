"""Searchable workflow template library and instantiation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_session
from app.api.routes.workflows import _to_out
from app.api.workflow_validation import validate_workflow_capabilities_or_422
from app.db.models import WorkflowTemplate
from app.db.repositories import (
    WorkflowRepo,
    WorkflowTemplateRepo,
    WorkflowVersionRepo,
)
from app.engine.validation import DSLValidationError, validate_dsl
from app.schemas.api import WorkflowOut
from app.schemas.dsl import WorkflowDSL
from app.schemas.templates import (
    TemplateParameter,
    WorkflowTemplateCreate,
    WorkflowTemplateInstantiate,
    WorkflowTemplateOut,
)
from app.services.dsl_migrations import normalize_workflow_dsl
from app.services.template_renderer import (
    TemplateParameterError,
    render_template_dsl,
    resolve_template_parameters,
)

router = APIRouter(prefix="/api/workflow-templates", tags=["workflow templates"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _to_template_out(row: WorkflowTemplate) -> WorkflowTemplateOut:
    return WorkflowTemplateOut(
        id=row.id,
        name=row.name,
        description=row.description or "",
        category=row.category,
        tags=list(row.tags_json or []),
        parameters=[TemplateParameter.model_validate(item) for item in row.parameters_json or []],
        dsl=row.dsl_json,
        is_official=row.is_official,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _template_or_404(session: AsyncSession, template_id: str) -> WorkflowTemplate:
    row = await WorkflowTemplateRepo(session).get(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow template not found")
    return row


@router.get("", response_model=list[WorkflowTemplateOut])
async def list_templates(
    session: SessionDep,
    _principal: ViewerDep,
    search: Annotated[str, Query(max_length=200)] = "",
    tag: Annotated[str, Query(max_length=40)] = "",
    category: Annotated[str, Query(max_length=64)] = "",
    official: bool | None = None,
) -> list[WorkflowTemplateOut]:
    rows = await WorkflowTemplateRepo(session).search(
        search=search, tag=tag, category=category, official=official
    )
    return [_to_template_out(row) for row in rows]


@router.get("/{template_id}", response_model=WorkflowTemplateOut)
async def get_template(
    template_id: str, session: SessionDep, _principal: ViewerDep
) -> WorkflowTemplateOut:
    return _to_template_out(await _template_or_404(session, template_id))


@router.post("", response_model=WorkflowTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: WorkflowTemplateCreate,
    session: SessionDep,
    _principal: EditorDep,
) -> WorkflowTemplateOut:
    workflow = await WorkflowRepo(session).get(body.workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    versions = WorkflowVersionRepo(session)
    source = (
        await versions.get_for_workflow(workflow.id, body.version_id)
        if body.version_id
        else await versions.ensure_current(workflow)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    row = await WorkflowTemplateRepo(session).create(
        name=body.name,
        description=body.description,
        category=body.category,
        tags=body.tags,
        parameters=[parameter.model_dump(mode="json") for parameter in body.parameters],
        dsl=source.dsl_json,
    )
    return _to_template_out(row)


@router.post(
    "/{template_id}/instantiate",
    response_model=WorkflowOut,
    status_code=status.HTTP_201_CREATED,
)
async def instantiate_template(
    template_id: str,
    body: WorkflowTemplateInstantiate,
    session: SessionDep,
    _principal: EditorDep,
) -> WorkflowOut:
    template = await _template_or_404(session, template_id)
    try:
        parameters = resolve_template_parameters(
            list(template.parameters_json or []), body.parameters
        )
        rendered = render_template_dsl(template.dsl_json, parameters)
        dsl = WorkflowDSL.model_validate(normalize_workflow_dsl(rendered))
        validate_dsl(dsl, strict=False)
    except (TemplateParameterError, DSLValidationError, ValueError) as exc:
        detail = exc.errors if isinstance(exc, DSLValidationError) else str(exc)
        raise HTTPException(status_code=422, detail=detail) from exc
    await validate_workflow_capabilities_or_422(session, dsl)

    name = body.name or dsl.name or template.name
    dsl_json = {**dsl.model_dump(mode="json"), "name": name}
    workflow = await WorkflowRepo(session).create(
        name=name,
        description=(body.description if body.description is not None else template.description),
        dsl=dsl_json,
    )
    return _to_out(workflow)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, session: SessionDep, _principal: EditorDep) -> None:
    row = await _template_or_404(session, template_id)
    if row.is_official:
        raise HTTPException(status_code=403, detail="official templates cannot be deleted")
    await WorkflowTemplateRepo(session).delete(row)


__all__ = ["router"]
