"""Tenant-aware workflow presence and soft-lock endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.workflow_access import authorize_workflow, workflow_can_edit
from app.core.actor import ActorIdentity
from app.core.auth import Principal, Role
from app.core.collaboration import (
    ClientIdentityConflict,
    CollaborationActor,
    CollaborationConflict,
    CollaborationSnapshot,
    CollaborationUnavailable,
    LeaseMismatch,
    LockUnavailable,
)
from app.core.container import ServiceContainer
from app.schemas.collaboration import (
    CLIENT_ID_PATTERN,
    CollaborationHeartbeat,
    CollaborationLockRelease,
    CollaborationLockRequest,
    CollaborationSnapshotOut,
)

router = APIRouter(prefix="/api/workflows/{workflow_id}/collaboration", tags=["collaboration"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _actor(principal: Principal) -> CollaborationActor:
    actor = ActorIdentity.from_principal(principal)
    return CollaborationActor(key=actor.key, subject=actor.subject)


def _to_out(snapshot: CollaborationSnapshot, *, can_edit: bool) -> CollaborationSnapshotOut:
    # Shared backends may force read-only even when project RBAC would allow edit.
    effective_can_edit = can_edit and not snapshot.read_only
    return CollaborationSnapshotOut.model_validate(
        {
            "workflow_id": snapshot.workflow_id,
            "revision": snapshot.revision,
            "can_edit": effective_can_edit,
            "participants": snapshot.participants,
            "lock": snapshot.lock,
            "lease_id": snapshot.own_lease_id,
            "backend": snapshot.backend,
            "read_only": snapshot.read_only,
        },
        from_attributes=True,
    )


def _conflict(exc: CollaborationConflict) -> HTTPException:
    if isinstance(exc, CollaborationUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc) or "shared collaboration backend unavailable; editing is read-only",
        )
    if isinstance(exc, LockUnavailable):
        detail = "workflow lock is held by another client; set takeover=true to replace it"
    elif isinstance(exc, LeaseMismatch):
        detail = "workflow lock lease is stale or not owned by this client"
    elif isinstance(exc, ClientIdentityConflict):
        detail = "collaboration client ID belongs to another authenticated actor"
    else:
        detail = str(exc)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.get("", response_model=CollaborationSnapshotOut)
async def get_collaboration(
    workflow_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
    client_id: Annotated[
        str,
        Query(min_length=8, max_length=128, pattern=CLIENT_ID_PATTERN),
    ],
) -> CollaborationSnapshotOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    try:
        snapshot = await container.collaboration_hub.snapshot(
            workflow_id, client_id, _actor(principal)
        )
    except CollaborationConflict as exc:
        raise _conflict(exc) from exc
    return _to_out(
        snapshot,
        can_edit=await workflow_can_edit(session, principal, workflow),
    )


@router.post("/heartbeat", response_model=CollaborationSnapshotOut)
async def heartbeat(
    workflow_id: str,
    body: CollaborationHeartbeat,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
) -> CollaborationSnapshotOut:
    required = Role.EDITOR if body.lease_id is not None else Role.VIEWER
    if not principal.can(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{required.value} role required",
        )
    workflow = await authorize_workflow(session, principal, workflow_id, required=required)
    try:
        snapshot = await container.collaboration_hub.heartbeat(
            workflow_id,
            body.client_id,
            _actor(principal),
            lease_id=body.lease_id,
        )
    except CollaborationConflict as exc:
        raise _conflict(exc) from exc
    return _to_out(
        snapshot,
        can_edit=await workflow_can_edit(session, principal, workflow),
    )


@router.put("/lock", response_model=CollaborationSnapshotOut)
async def acquire_lock(
    workflow_id: str,
    body: CollaborationLockRequest,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> CollaborationSnapshotOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    try:
        snapshot = await container.collaboration_hub.acquire_lock(
            workflow_id,
            body.client_id,
            _actor(principal),
            takeover=body.takeover,
            lease_id=body.lease_id,
        )
    except CollaborationConflict as exc:
        raise _conflict(exc) from exc
    return _to_out(snapshot, can_edit=True)


@router.delete("/lock", response_model=CollaborationSnapshotOut)
async def release_lock(
    workflow_id: str,
    body: CollaborationLockRelease,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> CollaborationSnapshotOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    try:
        snapshot = await container.collaboration_hub.release_lock(
            workflow_id,
            body.client_id,
            _actor(principal),
            body.lease_id,
        )
    except CollaborationConflict as exc:
        raise _conflict(exc) from exc
    return _to_out(snapshot, can_edit=True)


@router.delete("/presence/{client_id}", response_model=CollaborationSnapshotOut)
async def leave(
    workflow_id: str,
    client_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
) -> CollaborationSnapshotOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    try:
        snapshot = await container.collaboration_hub.leave(
            workflow_id, client_id, _actor(principal)
        )
    except CollaborationConflict as exc:
        raise _conflict(exc) from exc
    return _to_out(
        snapshot,
        can_edit=await workflow_can_edit(session, principal, workflow),
    )


@router.get("/stream")
async def stream_collaboration(
    workflow_id: str,
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
    client_id: Annotated[
        str,
        Query(min_length=8, max_length=128, pattern=CLIENT_ID_PATTERN),
    ],
) -> EventSourceResponse:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    can_edit = await workflow_can_edit(session, principal, workflow)
    # The stream no longer needs the request-scoped DB transaction after RBAC.
    # Release its connection before holding the HTTP response open indefinitely.
    await session.rollback()
    hub = container.collaboration_hub
    actor = _actor(principal)
    sweep_interval = container.settings.collaboration_sweep_interval_seconds

    async def events() -> AsyncIterator[dict[str, str]]:
        queue, snapshot = await hub.subscribe(workflow_id, client_id, actor)
        try:
            yield _sse_snapshot(snapshot, can_edit=can_edit)
            while not await request.is_disconnected():
                try:
                    revision = await asyncio.wait_for(queue.get(), timeout=sweep_interval)
                except TimeoutError:
                    if await hub.expire(workflow_id):
                        _drain_changes(queue)
                        yield _sse_snapshot(
                            await hub.snapshot(workflow_id, client_id, actor),
                            can_edit=can_edit,
                        )
                    else:
                        yield {"comment": "ping"}
                    continue
                if revision is None:
                    break
                yield _sse_snapshot(
                    await hub.snapshot(workflow_id, client_id, actor),
                    can_edit=can_edit,
                )
        finally:
            hub.unsubscribe(workflow_id, queue)

    return EventSourceResponse(events())


def _sse_snapshot(snapshot: CollaborationSnapshot, *, can_edit: bool) -> dict[str, str]:
    data = _to_out(snapshot, can_edit=can_edit).model_dump(mode="json")
    return {
        "id": str(snapshot.revision),
        "event": "snapshot",
        "data": json.dumps(data, ensure_ascii=False),
    }


def _drain_changes(queue: asyncio.Queue[int | None]) -> None:
    while not queue.empty():
        queue.get_nowait()
