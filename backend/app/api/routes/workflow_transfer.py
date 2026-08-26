"""Portable workflow import boundary."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, get_session
from app.api.routes.workflows import _to_out
from app.api.workflow_validation import validate_workflow_capabilities_or_422
from app.db.repositories import WorkflowRepo
from app.engine.validation import DSLValidationError, validate_dsl
from app.schemas.api import WorkflowOut
from app.schemas.dsl import WorkflowDSL
from app.schemas.workflow_versions import WorkflowImport
from app.services.audit import record_audit
from app.services.dsl_migrations import UnsupportedDSLVersion, normalize_workflow_dsl

router = APIRouter(prefix="/api/workflows", tags=["workflow transfer"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/import", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def import_workflow(
    body: WorkflowImport, session: SessionDep, principal: EditorDep
) -> WorkflowOut:
    try:
        dsl = WorkflowDSL.model_validate(normalize_workflow_dsl(body.dsl))
        validate_dsl(dsl, strict=False)
    except DSLValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors) from exc
    except (UnsupportedDSLVersion, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await validate_workflow_capabilities_or_422(session, dsl)

    name = body.name or dsl.name
    dsl_json = {**dsl.model_dump(mode="json"), "name": name}
    row = await WorkflowRepo(session).create(
        name=name,
        description=body.description,
        dsl=dsl_json,
    )
    await record_audit(
        session,
        principal,
        action="workflow.imported",
        resource_type="workflow",
        resource_id=row.id,
        resource_name=row.name,
        details={"version": row.version, "source_schema_version": body.dsl.get("version")},
    )
    return _to_out(row)


__all__ = ["router"]
