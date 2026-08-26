"""Workflow outbound callback management."""

from __future__ import annotations

import ipaddress
import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.workflow_access import authorize_workflow
from app.core.auth import Role
from app.core.container import ServiceContainer
from app.db.models import WorkflowCallback, WorkflowCallbackDelivery
from app.db.repositories import WorkflowCallbackRepo
from app.schemas.api import (
    WorkflowCallbackCreate,
    WorkflowCallbackDeliveryOut,
    WorkflowCallbackIssueOut,
    WorkflowCallbackOut,
    WorkflowCallbackUpdate,
)
from app.services.audit import record_audit

router = APIRouter(tags=["workflow callbacks"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _generated_secret() -> str:
    return secrets.token_urlsafe(32)


def _callback_out(row: WorkflowCallback) -> WorkflowCallbackOut:
    return WorkflowCallbackOut(
        id=row.id,
        workflow_id=row.workflow_id,
        project_id=row.project_id,
        url=row.url,
        event_types=list(row.event_types_json or []),  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        has_secret=bool(row.secret_encrypted),
        timeout_seconds=row.timeout_seconds,
        max_attempts=row.max_attempts,
        retry_delay_seconds=row.retry_delay_seconds,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_delivered_at=row.last_delivered_at,
    )


def _delivery_out(row: WorkflowCallbackDelivery) -> WorkflowCallbackDeliveryOut:
    return WorkflowCallbackDeliveryOut(
        id=row.id,
        callback_id=row.callback_id,
        workflow_id=row.workflow_id,
        source_type=row.source_type,
        source_id=row.source_id,
        event_type=row.event_type,
        status=row.status,
        attempt_count=row.attempt_count,
        last_status_code=row.last_status_code,
        last_error=row.last_error,
        created_at=row.created_at,
        delivered_at=row.delivered_at,
    )


def _reject_private_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        raise HTTPException(status_code=422, detail="callback URL host is not allowed")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise HTTPException(status_code=422, detail="callback URL host is not allowed")


async def _workflow(session: AsyncSession, principal, workflow_id: str, role: Role):
    return await authorize_workflow(session, principal, workflow_id, required=role)


@router.get("/api/workflows/{workflow_id}/callback", response_model=WorkflowCallbackOut)
async def get_workflow_callback(
    workflow_id: str, session: SessionDep, principal: ViewerDep
) -> WorkflowCallbackOut:
    await _workflow(session, principal, workflow_id, Role.VIEWER)
    row = await WorkflowCallbackRepo(session).get_for_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow callback not configured")
    return _callback_out(row)


@router.post(
    "/api/workflows/{workflow_id}/callback",
    response_model=WorkflowCallbackIssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_callback(
    workflow_id: str,
    body: WorkflowCallbackCreate,
    session: SessionDep,
    principal: EditorDep,
    container: ContainerDep,
) -> WorkflowCallbackIssueOut:
    workflow = await _workflow(session, principal, workflow_id, Role.EDITOR)
    _reject_private_url(body.url)
    if await WorkflowCallbackRepo(session).get_for_workflow(workflow_id) is not None:
        raise HTTPException(status_code=409, detail="workflow callback already exists")
    secret = body.secret or _generated_secret()
    row = await WorkflowCallbackRepo(session).create(
        workflow_id,
        workflow.project_id,
        url=body.url,
        secret_encrypted=container.secret_resolver.encrypt(secret),
        event_types=list(body.event_types),
        timeout_seconds=body.timeout_seconds,
        max_attempts=body.max_attempts,
        retry_delay_seconds=body.retry_delay_seconds,
    )
    await record_audit(
        session,
        principal,
        action="workflow_callback.created",
        resource_type="workflow_callback",
        resource_id=row.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow_id, "event_types": list(body.event_types)},
    )
    return WorkflowCallbackIssueOut(callback=_callback_out(row), secret=secret)


@router.put(
    "/api/workflows/{workflow_id}/callback",
    response_model=WorkflowCallbackIssueOut,
)
async def update_workflow_callback(
    workflow_id: str,
    body: WorkflowCallbackUpdate,
    session: SessionDep,
    principal: EditorDep,
    container: ContainerDep,
) -> WorkflowCallbackIssueOut:
    workflow = await _workflow(session, principal, workflow_id, Role.EDITOR)
    row = await WorkflowCallbackRepo(session).get_for_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow callback not configured")
    changes = body.model_dump(exclude_unset=True)
    secret = changes.pop("secret", None)
    rotate_secret = bool(changes.pop("rotate_secret", False))
    event_types = changes.pop("event_types", None)
    if event_types is not None:
        changes["event_types_json"] = list(event_types)
    if "url" in changes:
        _reject_private_url(str(changes["url"]))
    issued_secret = str(secret) if secret is not None else None
    if rotate_secret and issued_secret is None:
        issued_secret = _generated_secret()
    if issued_secret is not None:
        changes["secret_encrypted"] = container.secret_resolver.encrypt(issued_secret)
    await WorkflowCallbackRepo(session).update(row, **changes)
    await record_audit(
        session,
        principal,
        action="workflow_callback.updated",
        resource_type="workflow_callback",
        resource_id=row.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow_id, "changed_fields": sorted(body.model_fields_set)},
    )
    return WorkflowCallbackIssueOut(callback=_callback_out(row), secret=issued_secret)


@router.delete("/api/workflows/{workflow_id}/callback", status_code=status.HTTP_204_NO_CONTENT)
async def disable_workflow_callback(
    workflow_id: str, session: SessionDep, principal: EditorDep
) -> None:
    workflow = await _workflow(session, principal, workflow_id, Role.EDITOR)
    row = await WorkflowCallbackRepo(session).get_for_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow callback not configured")
    await WorkflowCallbackRepo(session).disable(row)
    await record_audit(
        session,
        principal,
        action="workflow_callback.disabled",
        resource_type="workflow_callback",
        resource_id=row.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow_id},
    )


@router.get(
    "/api/workflows/{workflow_id}/callback/deliveries",
    response_model=list[WorkflowCallbackDeliveryOut],
)
async def list_workflow_callback_deliveries(
    workflow_id: str,
    session: SessionDep,
    principal: ViewerDep,
    limit: int = 100,
) -> list[WorkflowCallbackDeliveryOut]:
    await _workflow(session, principal, workflow_id, Role.VIEWER)
    row = await WorkflowCallbackRepo(session).get_for_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow callback not configured")
    bounded_limit = min(max(limit, 1), 200)
    result = await session.execute(
        select(WorkflowCallbackDelivery)
        .where(WorkflowCallbackDelivery.callback_id == row.id)
        .order_by(WorkflowCallbackDelivery.created_at.desc())
        .limit(bounded_limit)
    )
    return [_delivery_out(item) for item in result.scalars().all()]


__all__ = ["router"]
