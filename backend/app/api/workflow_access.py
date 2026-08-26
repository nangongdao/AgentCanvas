"""Shared workflow aggregate lookup and project authorization."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenant_deps import authorize_workflow_project
from app.core.auth import Principal, Role
from app.db.models import Workflow, WorkflowVersion
from app.db.repositories import WorkflowRepo, WorkflowVersionRepo


async def authorize_workflow(
    session: AsyncSession,
    principal: Principal,
    workflow_id: str,
    *,
    required: Role,
) -> Workflow:
    workflow = await WorkflowRepo(session).get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(session, principal, workflow.project_id, required=required)
    return workflow


async def workflow_version_or_404(
    session: AsyncSession, workflow_id: str, version_id: str
) -> WorkflowVersion:
    version = await WorkflowVersionRepo(session).get_for_workflow(workflow_id, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="workflow version not found")
    return version


async def workflow_can_edit(
    session: AsyncSession,
    principal: Principal,
    workflow: Workflow,
) -> bool:
    if workflow.project_id is None:
        return principal.can(Role.EDITOR)
    try:
        await authorize_workflow_project(
            session,
            principal,
            workflow.project_id,
            required=Role.EDITOR,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise
    return True


__all__ = ["authorize_workflow", "workflow_can_edit", "workflow_version_or_404"]
