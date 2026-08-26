"""Execution start / status / SSE event stream routes."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from starlette.responses import StreamingResponse

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.etag import etag_json_response
from app.api.pagination import PageParams, PageResult, page_result
from app.api.tenant_deps import authorize_workflow_project
from app.core.auth import Role
from app.core.container import ServiceContainer
from app.db.repositories import ExecutionRepo, WorkflowRepo
from app.engine.dsl_traversal import iter_located_workflow_nodes
from app.engine.execution_dry_run import DryRunError
from app.engine.executor import EngineShuttingDown, ExecutionConcurrencyLimit
from app.engine.input_validation import WorkflowInputError
from app.engine.sse_multiplex import iter_multi_execution_sse, parse_after_map
from app.engine.sse_tail import iter_execution_sse
from app.schemas.api import (
    ExecutionCreate,
    ExecutionOut,
    ExecutionRerun,
    ExecutionResume,
    NodeDryRunRequest,
    NodeDryRunResponse,
)
from app.schemas.dsl import WorkflowDSL
from app.schemas.execution_inspection import ExecutionInspectionOut
from app.services.execution_inspection import build_execution_inspection
from app.services.project_quotas import ProjectQuotaExceeded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["executions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


async def _access_execution(
    session: AsyncSession,
    principal,
    execution_id: str,
    *,
    required: Role,
):
    """Load an execution and enforce project RBAC on its owning workflow.

    Executions are project-scoped transitively through their workflow, so every
    reader/mutator must satisfy the workflow's project role before touching
    input/output/snapshot data or issuing engine state changes.
    """
    row = await ExecutionRepo(session).get(execution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="execution not found")
    workflow = await WorkflowRepo(session).get(row.workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(session, principal, workflow.project_id, required=required)
    return row


def _exec_out(row) -> ExecutionOut:
    return ExecutionOut(
        id=row.id,
        workflow_id=row.workflow_id,
        workflow_version_id=row.workflow_version_id,
        workflow_version_number=(
            row.workflow_version.number if row.workflow_version is not None else None
        ),
        status=row.status,
        trigger_source=row.trigger_source,
        input_json=row.input_json or {},
        output_json=row.output_json,
        error=row.error,
        parent_execution_id=row.parent_execution_id,
        rerun_from_node_id=row.rerun_from_node_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


async def _release_read_transaction(session: AsyncSession) -> None:
    """Return the request connection before an engine-owned transaction starts."""
    if session.in_transaction():
        await session.rollback()


@router.post(
    "/workflows/{workflow_id}/run",
    response_model=ExecutionOut,
    status_code=201,
)
async def run_workflow(
    workflow_id: str,
    body: ExecutionCreate,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExecutionOut:
    workflow = await WorkflowRepo(session).get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(session, principal, workflow.project_id, required=Role.EDITOR)
    workflow_project_id = workflow.project_id
    await _release_read_transaction(session)
    try:
        execution_id = await container.execution_engine.start(
            workflow_id,
            body.inputs or {},
            session_id=body.session_id,
            idempotency_key=idempotency_key,
            trigger_source="manual",
            debug=body.debug.model_dump() if body.debug is not None else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowInputError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExecutionConcurrencyLimit as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ProjectQuotaExceeded as exc:
        await container.workflow_callback_dispatcher.enqueue_quota_alert_safely(
            workflow_id=workflow_id,
            project_id=workflow_project_id,
            metric=exc.metric,
            limit=exc.limit,
            usage=exc.usage,
            requested=exc.requested,
        )
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("failed to start execution")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Re-read from DB (engine created the row in its own session)
    row = await ExecutionRepo(session).get(execution_id)
    if row is None:
        # Extremely short race — synthesize
        return ExecutionOut(
            id=execution_id,
            workflow_id=workflow_id,
            status="running",
            trigger_source="manual",
            input_json=body.inputs or {},
        )
    return _exec_out(row)


@router.post(
    "/workflows/{workflow_id}/dry-run",
    response_model=NodeDryRunResponse,
)
async def dry_run_node(
    workflow_id: str,
    body: NodeDryRunRequest,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> NodeDryRunResponse:
    """Execute a single node with mock inputs (C2-6).

    Runs synchronously on an ephemeral event bus; nothing is written to the
    executions table or event log. The node config comes from the canvas live
    state, so editors can iterate on a node without triggering real runs.
    """
    workflow = await WorkflowRepo(session).get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(
        session, principal, workflow.project_id, required=Role.EDITOR
    )
    workflow_project_id = workflow.project_id
    await _release_read_transaction(session)
    try:
        result = await container.execution_engine.dry_run_node(
            workflow_id=workflow_id,
            node_id=body.node_id,
            node_type=body.node_type,
            node_config=body.node_config,
            mock_inputs=body.inputs,
            project_id=workflow_project_id,
        )
    except DryRunError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("dry-run failed for node %s", body.node_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return NodeDryRunResponse(**result)


@router.get("/executions/{execution_id}", response_model=ExecutionOut)
async def get_execution(
    execution_id: str, session: SessionDep, principal: ViewerDep
) -> ExecutionOut:
    row = await _access_execution(session, principal, execution_id, required=Role.VIEWER)
    return _exec_out(row)


@router.get(
    "/executions/{execution_id}/inspection",
    response_model=ExecutionInspectionOut,
)
async def inspect_execution(
    execution_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
) -> ExecutionInspectionOut:
    repo = ExecutionRepo(session)
    row = await _access_execution(session, principal, execution_id, required=Role.VIEWER)
    await container.event_bus.flush()
    events = await repo.list_events_after(execution_id)
    dsl = WorkflowDSL.model_validate(row.workflow_version.dsl_json)
    metadata = {
        key: {
            "type": str(located.node.type),
            "label": str(located.node.name or located.node.id),
        }
        for located in iter_located_workflow_nodes(dsl)
        for key in (located.path, located.path_key)
    }
    inspection = build_execution_inspection(events, node_metadata=metadata)
    return ExecutionInspectionOut(
        execution_id=row.id,
        workflow_id=row.workflow_id,
        workflow_version_id=row.workflow_version_id,
        workflow_version_number=row.workflow_version.number,
        status=row.status,
        parent_execution_id=row.parent_execution_id,
        rerun_from_node_id=row.rerun_from_node_id,
        **inspection,
    )


ALLOWED_EXECUTION_STATUSES = frozenset(
    {
        "queued",
        "running",
        "waiting_approval",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    }
)


@router.get(
    "/workflows/{workflow_id}/executions",
    response_model=PageResult[ExecutionOut],
)
async def list_executions(
    workflow_id: str,
    request: Request,
    session: SessionDep,
    principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
    status: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated status filter for cold/hot separation, e.g. "
                "'running,queued,waiting_approval' for active or "
                "'succeeded,failed,cancelled' for historical."
            ),
            max_length=200,
        ),
    ] = None,
) -> Response:
    workflow = await WorkflowRepo(session).get(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    await authorize_workflow_project(session, principal, workflow.project_id, required=Role.VIEWER)
    spec = params.to_spec(
        allowed_sorts={"started_at", "status", "id"},
        default_sort="started_at",
        scope=f"executions:{workflow_id}",
    )
    status_filter: set[str] | None = None
    if status:
        requested = {value.strip() for value in status.split(",") if value.strip()}
        invalid = requested - ALLOWED_EXECUTION_STATUSES
        if invalid:
            choices = ", ".join(sorted(ALLOWED_EXECUTION_STATUSES))
            raise HTTPException(
                status_code=422,
                detail=f"invalid status value(s): {', '.join(sorted(invalid))}; expected one of: {choices}",
            )
        status_filter = requested
    page = await ExecutionRepo(session).list_for_workflow_page(
        workflow_id, spec, status_filter=status_filter
    )
    result = page_result(page, _exec_out)
    # C6-4: polled list surface (history popover refresh) — 304 when unchanged.
    return etag_json_response(request, result.model_dump(mode="json"))


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> dict[str, bool]:
    await _access_execution(session, principal, execution_id, required=Role.EDITOR)
    await _release_read_transaction(session)
    try:
        ok = await container.execution_engine.cancel(execution_id, idempotency_key=idempotency_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="execution not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"cancelled": ok}


@router.post(
    "/executions/{execution_id}/rerun",
    response_model=ExecutionOut,
    status_code=201,
)
async def rerun_execution(
    execution_id: str,
    body: ExecutionRerun,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExecutionOut:
    source_execution = await _access_execution(
        session, principal, execution_id, required=Role.EDITOR
    )
    workflow = await WorkflowRepo(session).get(source_execution.workflow_id)
    source_workflow_id = source_execution.workflow_id
    workflow_project_id = workflow.project_id if workflow is not None else None
    await _release_read_transaction(session)
    try:
        rerun_id = await container.execution_engine.rerun_from_node(
            execution_id,
            node_id=body.node_id,
            idempotency_key=idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="execution or version not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExecutionConcurrencyLimit as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ProjectQuotaExceeded as exc:
        await container.workflow_callback_dispatcher.enqueue_quota_alert_safely(
            workflow_id=source_workflow_id,
            project_id=workflow_project_id,
            metric=exc.metric,
            limit=exc.limit,
            usage=exc.usage,
            requested=exc.requested,
        )
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    row = await ExecutionRepo(session).get(rerun_id)
    if row is None:
        raise HTTPException(status_code=404, detail="rerun execution not found")
    return _exec_out(row)


@router.post("/executions/{execution_id}/resume", response_model=ExecutionOut)
async def resume_execution(
    execution_id: str,
    body: ExecutionResume,
    container: ContainerDep,
    session: SessionDep,
    principal: EditorDep,
) -> ExecutionOut:
    """Resume a workflow interrupted at a human-approval node."""
    source_execution = await _access_execution(
        session, principal, execution_id, required=Role.EDITOR
    )
    workflow = await WorkflowRepo(session).get(source_execution.workflow_id)
    source_workflow_id = source_execution.workflow_id
    workflow_project_id = workflow.project_id if workflow is not None else None
    await _release_read_transaction(session)
    try:
        await container.execution_engine.resume(
            execution_id,
            body.decision,
            audit_principal=principal,
            debug=body.debug.model_dump() if body.debug is not None else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExecutionConcurrencyLimit as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ProjectQuotaExceeded as exc:
        await container.workflow_callback_dispatcher.enqueue_quota_alert_safely(
            workflow_id=source_workflow_id,
            project_id=workflow_project_id,
            metric=exc.metric,
            limit=exc.limit,
            usage=exc.usage,
            requested=exc.requested,
        )
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    row = await ExecutionRepo(session).get(execution_id)
    if row is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return _exec_out(row)


@router.get("/executions/{execution_id}/events")
async def stream_events(
    execution_id: str,
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> EventSourceResponse:
    """SSE stream with Last-Event-ID / ?after= replay + multi-source live tail."""
    await _access_execution(session, principal, execution_id, required=Role.VIEWER)
    bus = container.event_bus
    observability = container.observability
    session_factory = container.session_factory
    heartbeat = container.settings.sse_heartbeat_seconds
    # Resolve dynamically so an API process can recover its Redis connection
    # after serving PostgreSQL-polled SSE during an outage.
    event_stream = container.event_relay.event_stream
    pg_poll_seconds = container.settings.event_relay_poll_seconds

    # Prefer Last-Event-ID header when present
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def gen():
        async for message in iter_execution_sse(
            execution_id=execution_id,
            after=after,
            bus=bus,
            session_factory=session_factory,
            request_is_disconnected=request.is_disconnected,
            heartbeat_seconds=heartbeat,
            event_stream=event_stream,
            pg_poll_seconds=pg_poll_seconds,
            on_opened=observability.sse_opened,
            on_replay=observability.sse_replay,
            on_closed=observability.sse_closed,
        ):
            yield message

    return EventSourceResponse(gen())


MAX_MULTIPLEX_EXECUTIONS = 10


@router.get("/executions/events/multi")
async def stream_events_multi(
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
    ids: Annotated[
        str,
        Query(
            description="Comma-separated execution ids (max 10) to multiplex on one SSE connection.",
            max_length=800,
        ),
    ],
    after: Annotated[
        str | None,
        Query(
            description=(
                "Per-execution reconnect cursor 'id:seq,id:seq'. Malformed "
                "pairs are ignored and replay idempotently."
            ),
            max_length=2000,
        ),
    ] = None,
) -> EventSourceResponse:
    """Multiplex several executions' event streams over one SSE connection.

    Each execution keeps its own seq space; the SSE ``id`` is the composite
    ``{execution_id}:{seq}`` so clients can resume each stream independently.
    One execution reaching a terminal state closes only its channel — the
    connection stays open until every requested execution has terminated.
    """
    requested = [value.strip() for value in ids.split(",") if value.strip()]
    unique_ids = list(dict.fromkeys(requested))
    if not unique_ids:
        raise HTTPException(status_code=422, detail="ids must contain at least one execution id")
    if len(unique_ids) > MAX_MULTIPLEX_EXECUTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"ids supports at most {MAX_MULTIPLEX_EXECUTIONS} executions per connection",
        )
    for execution_id in unique_ids:
        await _access_execution(session, principal, execution_id, required=Role.VIEWER)

    bus = container.event_bus
    observability = container.observability
    session_factory = container.session_factory
    heartbeat = container.settings.sse_heartbeat_seconds
    # Resolve dynamically so an API process can recover its Redis connection
    # after serving PostgreSQL-polled SSE during an outage.
    event_stream = container.event_relay.event_stream
    pg_poll_seconds = container.settings.event_relay_poll_seconds

    after_map = parse_after_map(after)
    header_cursor = request.headers.get("Last-Event-ID")
    if header_cursor:
        for execution_id, seq in parse_after_map(header_cursor).items():
            after_map[execution_id] = max(after_map.get(execution_id, 0), seq)

    async def gen():
        async for message in iter_multi_execution_sse(
            execution_ids=unique_ids,
            after_map=after_map,
            bus=bus,
            session_factory=session_factory,
            request_is_disconnected=request.is_disconnected,
            heartbeat_seconds=heartbeat,
            event_stream=event_stream,
            pg_poll_seconds=pg_poll_seconds,
            on_opened=observability.sse_opened,
            on_replay=observability.sse_replay,
            on_closed=observability.sse_closed,
        ):
            yield message

    return EventSourceResponse(gen())


@router.get("/executions/{execution_id}/events/export")
async def export_execution_events(
    execution_id: str,
    container: ContainerDep,
    session: SessionDep,
    principal: ViewerDep,
) -> StreamingResponse:
    """Export the full event log of one execution as a JSON attachment.

    Provides an offline archive of an execution's detail events before they
    age out of the retention window (C6-1). The response is a single JSON
    array of ``{seq, event_type, node_id, ts, payload}`` objects so it can be
    replayed or audited without the live database. The event bus is flushed
    first so any buffered events have been persisted.
    """
    from json import dumps

    await _access_execution(session, principal, execution_id, required=Role.VIEWER)
    # Flush the in-memory event bus so buffered events land before we read.
    await container.event_bus.flush()
    events = await ExecutionRepo(session).list_events_after(execution_id, 0)

    def serialize() -> Iterator[bytes]:
        yield b"["
        for index, event in enumerate(events):
            if index:
                yield b","
            payload = {
                "seq": event.seq,
                "event_type": event.event_type,
                "node_id": event.node_id,
                "ts": event.ts.isoformat() if event.ts else None,
                "payload": event.payload_json,
            }
            yield dumps(payload, default=str).encode("utf-8")
        yield b"]"

    return StreamingResponse(
        serialize(),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="events-{execution_id}.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )
