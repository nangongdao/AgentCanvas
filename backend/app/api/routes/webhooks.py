"""Workflow webhook trigger management and public invocation endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.workflow_access import authorize_workflow
from app.core.auth import Role
from app.core.container import ServiceContainer
from app.db.models import WebhookTrigger
from app.db.repositories import (
    ExecutionRepo,
    WebhookTriggerRepo,
    WorkflowVersionRepo,
)
from app.engine.execution_errors import EngineShuttingDown
from app.engine.executor import ExecutionConcurrencyLimit
from app.engine.input_validation import WorkflowInputError, validate_execution_inputs
from app.schemas.api import (
    WebhookInvokeOut,
    WebhookTriggerCreate,
    WebhookTriggerIssueOut,
    WebhookTriggerOut,
    WebhookTriggerUpdate,
)
from app.schemas.dsl import WorkflowDSL
from app.services.audit import record_audit
from app.services.project_quotas import ProjectQuotaExceeded

router = APIRouter(tags=["webhooks"])

# Sync mode polls the execution row until it reaches a terminal state. SQLite
# writes from the worker land in the API process's shared in-memory engine, so
# a short re-read loop is sufficient; the bounded timeout protects callers from
# waiting on a stalled queue forever.
_TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
_SYNC_POLL_INTERVAL_SECONDS = 0.05
_SYNC_MAX_TIMEOUT_SECONDS = 60
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_credential() -> str:
    return secrets.token_urlsafe(32)


def _trigger_out(trigger: WebhookTrigger, version_number: int) -> WebhookTriggerOut:
    return WebhookTriggerOut(
        id=trigger.id,
        workflow_id=trigger.workflow_id,
        published_version_id=trigger.published_version_id,
        published_version_number=version_number,
        token_prefix=trigger.token_prefix,
        has_signing_secret=bool(trigger.secret_encrypted),
        status=trigger.status,
        ip_allowlist=list(trigger.ip_allowlist or []),
        created_at=trigger.created_at,
        updated_at=trigger.updated_at,
        last_triggered_at=trigger.last_triggered_at,
    )


def _check_ip(request: Request, allowlist: list[str]) -> None:
    if not allowlist:
        return
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="request IP is not allowed") from exc
    if not any(address in ipaddress.ip_network(value, strict=False) for value in allowlist):
        raise HTTPException(status_code=403, detail="request IP is not allowed")


def _verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    if not signature or not signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="valid webhook signature required")
    provided = signature.removeprefix("sha256=").strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if len(provided) != len(expected) or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid webhook signature")


async def _management_workflow(session: AsyncSession, principal: Any, workflow_id: str, role: Role):
    return await authorize_workflow(session, principal, workflow_id, required=role)


async def _out_with_version(session: AsyncSession, trigger: WebhookTrigger) -> WebhookTriggerOut:
    version = await WorkflowVersionRepo(session).get(trigger.published_version_id)
    if version is None:
        raise HTTPException(status_code=500, detail="webhook published version is missing")
    return _trigger_out(trigger, version.number)


@router.get("/api/workflows/{workflow_id}/webhook", response_model=WebhookTriggerOut)
async def get_webhook(
    workflow_id: str, session: SessionDep, principal: ViewerDep
) -> WebhookTriggerOut:
    await _management_workflow(session, principal, workflow_id, Role.VIEWER)
    trigger = await WebhookTriggerRepo(session).get_for_workflow(workflow_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="webhook trigger not configured")
    return await _out_with_version(session, trigger)


@router.post(
    "/api/workflows/{workflow_id}/webhook",
    response_model=WebhookTriggerIssueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook(
    workflow_id: str,
    body: WebhookTriggerCreate,
    session: SessionDep,
    principal: EditorDep,
    container: ContainerDep,
) -> WebhookTriggerIssueOut:
    workflow = await _management_workflow(session, principal, workflow_id, Role.EDITOR)
    version = await WorkflowVersionRepo(session).get_by_number(workflow.id, workflow.version)
    if version is None or version.status != "published":
        raise HTTPException(status_code=409, detail="workflow must have a published version")
    if await WebhookTriggerRepo(session).get_for_workflow(workflow_id) is not None:
        raise HTTPException(status_code=409, detail="webhook trigger already exists")
    token = _new_credential()
    secret = body.secret or _new_credential()
    trigger = await WebhookTriggerRepo(session).create(
        workflow_id,
        version.id,
        token_hash=_sha256(token),
        token_prefix=token[:12],
        secret_encrypted=container.secret_resolver.encrypt(secret),
        ip_allowlist=body.ip_allowlist,
    )
    await record_audit(
        session,
        principal,
        action="workflow_webhook.created",
        resource_type="webhook_trigger",
        resource_id=trigger.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id, "version": version.number},
    )
    return WebhookTriggerIssueOut(trigger=_trigger_out(trigger, version.number), token=token, secret=secret)


@router.put(
    "/api/workflows/{workflow_id}/webhook",
    response_model=WebhookTriggerIssueOut,
)
async def rotate_webhook(
    workflow_id: str,
    body: WebhookTriggerCreate,
    session: SessionDep,
    principal: EditorDep,
    container: ContainerDep,
) -> WebhookTriggerIssueOut:
    workflow = await _management_workflow(session, principal, workflow_id, Role.EDITOR)
    version = await WorkflowVersionRepo(session).get_by_number(workflow.id, workflow.version)
    if version is None or version.status != "published":
        raise HTTPException(status_code=409, detail="workflow must have a published version")
    trigger = await WebhookTriggerRepo(session).get_for_workflow(workflow_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="webhook trigger not configured")
    token = _new_credential()
    secret = body.secret or _new_credential()
    await WebhookTriggerRepo(session).rotate(
        trigger,
        published_version_id=version.id,
        token_hash=_sha256(token),
        token_prefix=token[:12],
        secret_encrypted=container.secret_resolver.encrypt(secret),
        ip_allowlist=body.ip_allowlist,
    )
    await record_audit(
        session,
        principal,
        action="workflow_webhook.rotated",
        resource_type="webhook_trigger",
        resource_id=trigger.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id, "version": version.number},
    )
    return WebhookTriggerIssueOut(trigger=_trigger_out(trigger, version.number), token=token, secret=secret)


@router.patch("/api/workflows/{workflow_id}/webhook", response_model=WebhookTriggerOut)
async def update_webhook(
    workflow_id: str,
    body: WebhookTriggerUpdate,
    session: SessionDep,
    principal: EditorDep,
) -> WebhookTriggerOut:
    await _management_workflow(session, principal, workflow_id, Role.EDITOR)
    trigger = await WebhookTriggerRepo(session).get_for_workflow(workflow_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="webhook trigger not configured")
    await WebhookTriggerRepo(session).set_ip_allowlist(trigger, body.ip_allowlist)
    return await _out_with_version(session, trigger)


@router.delete("/api/workflows/{workflow_id}/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def disable_webhook(
    workflow_id: str, session: SessionDep, principal: EditorDep
) -> None:
    workflow = await _management_workflow(session, principal, workflow_id, Role.EDITOR)
    trigger = await WebhookTriggerRepo(session).get_for_workflow(workflow_id)
    if trigger is None:
        raise HTTPException(status_code=404, detail="webhook trigger not configured")
    await WebhookTriggerRepo(session).disable(trigger)
    await record_audit(
        session,
        principal,
        action="workflow_webhook.disabled",
        resource_type="webhook_trigger",
        resource_id=trigger.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id},
    )


@router.post("/api/hooks/{workflow_id}/{token}", response_model=WebhookInvokeOut)
async def invoke_webhook(
    workflow_id: str,
    token: str,
    request: Request,
    response: Response,
    container: ContainerDep,
    session: SessionDep,
    signature: Annotated[str | None, Header(alias="X-AgentCanvas-Signature")] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    sync: Annotated[bool, Query()] = False,
    timeout_seconds: Annotated[float, Query(ge=0.01, le=_SYNC_MAX_TIMEOUT_SECONDS)] = 5.0,
) -> WebhookInvokeOut:
    trigger = await WebhookTriggerRepo(session).get_by_token_hash(_sha256(token))
    if trigger is None or trigger.workflow_id != workflow_id or trigger.status != "active":
        raise HTTPException(status_code=404, detail="webhook not found")
    _check_ip(request, list(trigger.ip_allowlist or []))
    raw = await request.body()
    secret = container.secret_resolver.decrypt(trigger.secret_encrypted)
    _verify_signature(secret, raw, signature)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="webhook body must be a JSON object")
    version = await WorkflowVersionRepo(session).get(trigger.published_version_id)
    if version is None or version.workflow_id != workflow_id or version.status != "published":
        raise HTTPException(status_code=409, detail="webhook published version is no longer available")
    version_id = version.id
    trigger_id = trigger.id
    try:
        inputs = validate_execution_inputs(WorkflowDSL.model_validate(version.dsl_json), payload)
        await session.rollback()
        execution_id = await container.execution_engine.start_version(
            version_id,
            inputs,
            idempotency_key=idempotency_key,
            trigger_source="webhook",
        )
    except WorkflowInputError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExecutionConcurrencyLimit as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ProjectQuotaExceeded as exc:
        await container.workflow_callback_dispatcher.enqueue_quota_alert_safely(
            workflow_id=workflow_id,
            project_id=None,
            metric=exc.metric,
            limit=exc.limit,
            usage=exc.usage,
            requested=exc.requested,
        )
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    async with container.session_factory() as touch_session:
        touched = await WebhookTriggerRepo(touch_session).get(trigger_id)
        if touched is not None:
            await WebhookTriggerRepo(touch_session).touch_triggered(touched)
            await touch_session.commit()

    if not sync:
        response.status_code = status.HTTP_202_ACCEPTED
        return WebhookInvokeOut(
            execution_id=execution_id,
            status="queued",
            accepted_version_id=version_id,
            input_json=inputs,
        )

    # Sync mode: poll the execution row until it reaches a terminal state or
    # the bounded caller-supplied timeout elapses. On timeout we fall back to
    # the async 202 response so callers can retry or follow up via SSE.
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    terminal = await _await_terminal(container, execution_id, deadline)
    if terminal is None:
        async with container.session_factory() as peek:
            current = await ExecutionRepo(peek).get(execution_id)
        current_status = current.status if current is not None else "queued"
        response.status_code = status.HTTP_202_ACCEPTED
        return WebhookInvokeOut(
            execution_id=execution_id,
            status=current_status,
            accepted_version_id=version_id,
            input_json=inputs,
        )
    response.status_code = status.HTTP_200_OK
    return WebhookInvokeOut(
        execution_id=execution_id,
        status=terminal.status,
        accepted_version_id=version_id,
        input_json=inputs,
        output_json=terminal.output_json,
        error=terminal.error,
    )


async def _await_terminal(
    container: ServiceContainer,
    execution_id: str,
    deadline: float,
) -> Any:
    """Poll the execution row until terminal or the loop-time deadline."""
    repo_factory = container.session_factory
    while True:
        async with repo_factory() as peek:
            row = await ExecutionRepo(peek).get(execution_id)
        if row is not None and row.status in _TERMINAL_STATUSES:
            return row
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(_SYNC_POLL_INTERVAL_SECONDS)
