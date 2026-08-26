"""Shared HTTP adapter for workflow capability validation."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.dsl import WorkflowDSL
from app.services.workflow_capabilities import (
    WorkflowCapabilityError,
    validate_workflow_capabilities,
)


async def validate_workflow_capabilities_or_422(
    session: AsyncSession,
    dsl: WorkflowDSL,
) -> None:
    try:
        await validate_workflow_capabilities(session, dsl)
    except WorkflowCapabilityError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc


__all__ = ["validate_workflow_capabilities_or_422"]
