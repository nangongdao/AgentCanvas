"""Cross-project resource transfer endpoint (C7-4)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_session
from app.api.tenant_deps import authorize_project
from app.core.auth import Role
from app.db.models import App, KnowledgeBase, Workflow
from app.services.audit import record_audit
from app.services.resource_transfer import TransferError, transfer_resource

router = APIRouter(prefix="/api/transfers", tags=["resource transfer"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class TransferRequest(BaseModel):
    resource_type: Literal["workflow", "knowledge_base", "app"]
    resource_id: str = Field(min_length=1, max_length=32)
    target_project_id: str = Field(min_length=1, max_length=32)


class TransferOutcome(BaseModel):
    resource_type: str
    resource_id: str
    from_project_id: str | None
    to_project_id: str


async def _source_row(
    session: AsyncSession, body: TransferRequest
) -> Workflow | KnowledgeBase | App | None:
    if body.resource_type == "workflow":
        return await session.get(Workflow, body.resource_id)
    if body.resource_type == "knowledge_base":
        return await session.get(KnowledgeBase, body.resource_id)
    return await session.get(App, body.resource_id)


@router.post("", response_model=TransferOutcome)
async def transfer_resource_ownership(
    body: TransferRequest, session: SessionDep, principal: ViewerDep
) -> TransferOutcome:
    """Move a resource to another project (admin of both ends required)."""
    # Authorize the destination first: the target must exist and the caller
    # must administer its organization.
    target = await authorize_project(
        session, principal, body.target_project_id, required=Role.ADMIN
    )
    row = await _source_row(session, body)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resource not found")
    if row.project_id is not None:
        await authorize_project(session, principal, row.project_id, required=Role.ADMIN)
    try:
        result = await transfer_resource(
            session,
            resource_type=body.resource_type,
            resource_id=body.resource_id,
            target_project_id=body.target_project_id,
        )
    except TransferError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await record_audit(
        session,
        principal,
        action="resource.transferred",
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        resource_name=getattr(row, "name", None),
        organization_id=target.organization_id,
        details={
            "from_project_id": result.from_project_id,
            "to_project_id": result.to_project_id,
        },
    )
    return TransferOutcome(
        resource_type=result.resource_type,
        resource_id=result.resource_id,
        from_project_id=result.from_project_id,
        to_project_id=result.to_project_id,
    )


__all__ = ["router"]
