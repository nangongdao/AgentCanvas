"""P5 Chat routes - conversational sessions over a bound workflow.

A chat session is bound to a workflow (and optionally a model config). Each
user turn runs the workflow with ``user_query`` (plus the session inputs)
injected, streams tokens back via SSE, and persists the user + assistant
messages to ``chat_messages``. The session id is also passed to the engine
as ``session_id`` so the in-graph Agent node can load conversation memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_container, get_session
from app.api.pagination import PageParams, PageResult, page_result
from app.core.container import ServiceContainer
from app.db.repositories import (
    ChatFeedbackRepo,
    ChatMessageRepo,
    ChatSessionRepo,
    ChatSessionVariableRepo,
    EvaluationDatasetRepo,
    WorkflowRepo,
)
from app.engine.executor import EngineShuttingDown, ExecutionConcurrencyLimit
from app.schemas.api import (
    ChatExportFormat,
    ChatFeedbackOut,
    ChatFeedbackUpsert,
    ChatMessageEditRequest,
    ChatMessageOut,
    ChatPromoteToDatasetOut,
    ChatPromoteToDatasetRequest,
    ChatRegenerateRequest,
    ChatSend,
    ChatSessionCreate,
    ChatSessionOut,
    ChatSessionVariableMap,
    ChatSessionVariableOut,
    ChatSessionVariableUpsert,
)
from app.services.chat_citations import merge_citations
from app.services.project_quotas import ProjectQuotaExceeded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _session_out(row) -> ChatSessionOut:
    return ChatSessionOut(
        id=row.id,
        title=row.title,
        workflow_id=row.workflow_id,
        model_config_id=row.model_config_id,
        app_id=row.app_id,
        inputs=row.inputs_json or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _message_out(row, feedback_rating: str | None = None) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id,
        session_id=row.session_id,
        role=row.role,
        content=row.content,
        node_ref=row.node_ref,
        execution_id=row.execution_id,
        citations=row.citations_json or [],
        feedback_rating=feedback_rating,
        created_at=row.created_at,
    )


async def _feedback_ratings(session: AsyncSession, rows) -> dict[str, str]:
    """Batch-load the durable feedback rating for a page of messages."""
    ids = [row.id for row in rows]
    if not ids:
        return {}
    return await ChatFeedbackRepo(session).ratings_for_messages(ids)


@router.get("/sessions", response_model=PageResult[ChatSessionOut])
async def list_sessions(
    session: SessionDep,
    _principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
    workflow_id: str | None = None,
) -> PageResult[ChatSessionOut]:
    spec = params.to_spec(
        allowed_sorts={"created_at", "updated_at", "title", "id"},
        default_sort="updated_at",
        scope=f"chat-sessions:{workflow_id or '*'}",
    )
    page = await ChatSessionRepo(session).list_page(spec, workflow_id=workflow_id)
    return page_result(page, _session_out)


@router.post("/sessions", response_model=ChatSessionOut, status_code=201)
async def create_session(
    body: ChatSessionCreate,
    session: SessionDep,
    _principal: EditorDep,
) -> ChatSessionOut:
    if body.workflow_id:
        wf = await WorkflowRepo(session).get(body.workflow_id)
        if wf is None:
            raise HTTPException(status_code=404, detail="workflow not found")
    row = await ChatSessionRepo(session).create(
        title=body.title,
        workflow_id=body.workflow_id,
        model_config_id=body.model_config_id,
        inputs=body.inputs,
    )
    await session.commit()
    return _session_out(row)


@router.get("/sessions/{session_id}", response_model=ChatSessionOut)
async def get_chat_session(
    session_id: str, session: SessionDep, _principal: ViewerDep
) -> ChatSessionOut:
    row = await ChatSessionRepo(session).get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return _session_out(row)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, session: SessionDep, _principal: EditorDep) -> None:
    deleted = await ChatSessionRepo(session).delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    await session.commit()


@router.get(
    "/sessions/{session_id}/messages",
    response_model=PageResult[ChatMessageOut],
)
async def list_messages(
    session_id: str,
    session: SessionDep,
    _principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
) -> PageResult[ChatMessageOut]:
    row = await ChatSessionRepo(session).get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    spec = params.to_spec(
        allowed_sorts={"created_at", "role", "id"},
        default_sort="created_at",
        scope=f"chat-messages:{session_id}",
    )
    page = await ChatMessageRepo(session).list_for_session_page(session_id, spec)
    ratings = await _feedback_ratings(session, page.rows)
    return page_result(page, lambda row: _message_out(row, ratings.get(row.id)))


@router.post("/sessions/{session_id}/send")
async def send_message(
    session_id: str,
    body: ChatSend,
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    _principal: EditorDep,
) -> StreamingResponse:
    """Run the bound workflow with the user message and stream tokens back."""
    repo = ChatSessionRepo(session)
    chat_session = await repo.get(session_id)
    if chat_session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not chat_session.workflow_id:
        raise HTTPException(status_code=400, detail="session has no bound workflow")

    workflow_id = chat_session.workflow_id
    merged_inputs = {**(chat_session.inputs_json or {}), **body.inputs, "user_query": body.message}

    # Persist the user turn immediately.
    msg_repo = ChatMessageRepo(session)
    await msg_repo.append(
        session_id=session_id,
        role="user",
        content=body.message,
    )
    await repo.touch(chat_session)
    await session.commit()

    try:
        execution_id = await container.execution_engine.start(
            workflow_id, merged_inputs, session_id=session_id
        )
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExecutionConcurrencyLimit as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except ProjectQuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc

    async def event_stream():
        bus = container.event_bus
        queue = bus.subscribe(execution_id)
        try:
            # Replay persisted events first so late subscribers see full history.
            from app.db.repositories import ExecutionRepo

            async with container.session_factory() as db:
                rows = await ExecutionRepo(db).list_events_after(execution_id, 0)
            citations: list[dict] = []
            for ev in rows:
                citations = merge_citations(citations, ev.payload_json, ev.node_id)
                yield _sse(
                    {
                        "event_type": ev.event_type,
                        "seq": ev.seq,
                        "node_id": ev.node_id,
                        "payload": ev.payload_json,
                        "ts": ev.ts.isoformat() if ev.ts else None,
                    }
                )

            terminal = {"workflow_finished", "workflow_failed", "workflow_cancelled"}
            assistant_text = ""
            final_output = None
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                payload = event.to_dict()
                yield _sse(payload)
                etype = payload.get("event_type") or payload.get("type")
                citations = merge_citations(
                    citations,
                    payload.get("payload"),
                    payload.get("node_id"),
                )
                if etype == "node_streaming" and payload.get("payload", {}).get("kind") == "text":
                    assistant_text += payload["payload"].get("delta", "")
                if etype == "workflow_finished":
                    final_output = payload.get("payload", {}).get("output")
                if etype in terminal:
                    break

            reply = _extract_reply(final_output, assistant_text)
            if reply:
                async with container.session_factory() as db:
                    await ChatMessageRepo(db).append(
                        session_id=session_id,
                        role="assistant",
                        content=reply,
                        execution_id=execution_id,
                        citations=citations,
                    )
                    await db.commit()
        finally:
            bus.unsubscribe(execution_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Execution-ID": execution_id},
    )


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _extract_reply(final_output: object, streamed: str) -> str:
    """Pick the best text to persist as the assistant message."""
    if isinstance(final_output, dict):
        for key in ("answer", "result", "text", "output"):
            value = final_output.get(key)
            if isinstance(value, str) and value:
                return value
        # fall through to streamed text
    return streamed or ""


# ---- C3-4: chat deepening ----


def _feedback_out(row) -> ChatFeedbackOut:
    return ChatFeedbackOut(
        id=row.id,
        message_id=row.message_id,
        session_id=row.session_id,
        rating=row.rating,
        comment=row.comment or "",
        promoted_dataset_version_id=row.promoted_dataset_version_id,
        promoted_at=row.promoted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _variable_out(row) -> ChatSessionVariableOut:
    return ChatSessionVariableOut(name=row.name, value=row.value_json, updated_at=row.updated_at)


@router.post(
    "/sessions/{session_id}/messages/{message_id}/feedback",
    response_model=ChatFeedbackOut,
)
async def upsert_feedback(
    session_id: str,
    message_id: str,
    body: ChatFeedbackUpsert,
    session: SessionDep,
    _principal: ViewerDep,
) -> ChatFeedbackOut:
    """Create or update end-user feedback on an assistant message."""
    msg_repo = ChatMessageRepo(session)
    message = await msg_repo.get(message_id)
    if message is None or message.session_id != session_id:
        raise HTTPException(status_code=404, detail="message not found")
    if message.role != "assistant":
        raise HTTPException(status_code=422, detail="feedback only allowed on assistant messages")
    row = await ChatFeedbackRepo(session).upsert(
        message=message, rating=body.rating, comment=body.comment
    )
    await session.commit()
    return _feedback_out(row)


@router.get(
    "/sessions/{session_id}/messages/{message_id}/feedback",
    response_model=ChatFeedbackOut,
)
async def get_feedback(
    session_id: str,
    message_id: str,
    session: SessionDep,
    _principal: ViewerDep,
) -> ChatFeedbackOut:
    row = await ChatFeedbackRepo(session).get_for_message(message_id)
    if row is None or row.session_id != session_id:
        raise HTTPException(status_code=404, detail="feedback not found")
    return _feedback_out(row)


@router.delete(
    "/sessions/{session_id}/messages/{message_id}/feedback",
    status_code=204,
)
async def delete_feedback(
    session_id: str,
    message_id: str,
    session: SessionDep,
    _principal: EditorDep,
) -> None:
    deleted = await ChatFeedbackRepo(session).delete(message_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="feedback not found")
    await session.commit()


@router.post(
    "/sessions/{session_id}/messages/{message_id}/promote",
    response_model=ChatPromoteToDatasetOut,
)
async def promote_message_to_dataset(
    session_id: str,
    message_id: str,
    body: ChatPromoteToDatasetRequest,
    request: Request,
    session: SessionDep,
    _principal: EditorDep,
) -> ChatPromoteToDatasetOut:
    """Promote a 👎 assistant message into an evaluation dataset (C3-4 ↔ D2).

    Builds a new case with the user query as input and the assistant reply
    as the expected answer candidate. If ``dataset_id`` is omitted, a new
    dataset is created; otherwise a new immutable version is appended.
    """
    msg_repo = ChatMessageRepo(session)
    message = await msg_repo.get(message_id)
    if message is None or message.session_id != session_id:
        raise HTTPException(status_code=404, detail="message not found")
    if message.role != "assistant":
        raise HTTPException(status_code=422, detail="only assistant messages can be promoted")

    feedback_repo = ChatFeedbackRepo(session)
    feedback = await feedback_repo.get_for_message(message_id)

    # Find the immediately preceding user message in the same session.
    prior_messages = await msg_repo.list_for_session(session_id)
    user_query = ""
    seen_target = False
    for msg in reversed(prior_messages):
        if not seen_target:
            if msg.id == message_id:
                seen_target = True
            continue
        if msg.role == "user":
            user_query = msg.content
            break

    case_name = body.case_name or (
        prior_user_label(user_query) if user_query else "Promoted chat turn"
    )
    expected = body.expected if body.expected is not None else {"answer": message.content}
    new_case: dict[str, Any] = {
        "id": f"chat-{message_id[:24]}",
        "name": case_name,
        "inputs": {"user_query": user_query} if user_query else {},
        "expected": expected,
        "source": {
            "chat_message_id": message.id,
            "session_id": session_id,
            "execution_id": message.execution_id,
        },
    }

    dataset_repo = EvaluationDatasetRepo(session)
    if body.dataset_id:
        dataset = await dataset_repo.get(body.dataset_id)
        if dataset is None:
            raise HTTPException(status_code=404, detail="evaluation dataset not found")
        existing_cases = list(dataset.versions[-1].cases_json or [])
        existing_cases.append(new_case)
        dataset = await dataset_repo.update(
            dataset,
            name=dataset.name,
            description=dataset.description,
            cases=existing_cases,
            change_summary=body.change_summary,
        )
        version_id = dataset.versions[-1].id
        dataset_id = dataset.id
    else:
        dataset_name = (body.dataset_name or "Promoted from chat feedback").strip()
        dataset = await dataset_repo.create(
            name=dataset_name,
            description="Curated from chat feedback (C3-4).",
            cases=[new_case],
            change_summary=body.change_summary or "Initial case promoted from chat feedback",
        )
        version_id = dataset.versions[-1].id
        dataset_id = dataset.id

    if feedback is not None:
        await feedback_repo.mark_promoted(feedback, version_id)

    await session.commit()
    return ChatPromoteToDatasetOut(
        dataset_id=dataset_id,
        dataset_version_id=version_id,
        case_id=new_case["id"],
        feedback_id=feedback.id if feedback else "",
    )


def prior_user_label(query: str) -> str:
    clipped = query.strip().replace("\n", " ")
    if len(clipped) > 60:
        clipped = clipped[:57] + "..."
    return clipped or "Promoted chat turn"


@router.get(
    "/sessions/{session_id}/variables",
    response_model=ChatSessionVariableMap,
)
async def list_session_variables(
    session_id: str, session: SessionDep, _principal: ViewerDep
) -> ChatSessionVariableMap:
    row = await ChatSessionRepo(session).get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    variables = await ChatSessionVariableRepo(session).snapshot(session_id)
    return ChatSessionVariableMap(variables=variables)


@router.put(
    "/sessions/{session_id}/variables/{name}",
    response_model=ChatSessionVariableOut,
)
async def upsert_session_variable(
    session_id: str,
    name: str,
    body: ChatSessionVariableUpsert,
    session: SessionDep,
    _principal: EditorDep,
) -> ChatSessionVariableOut:
    if name != body.name:
        raise HTTPException(status_code=422, detail="name in path and body must match")
    row = await ChatSessionRepo(session).get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    var = await ChatSessionVariableRepo(session).upsert(
        session_id=session_id, name=name, value=body.value
    )
    await session.commit()
    return _variable_out(var)


@router.delete(
    "/sessions/{session_id}/variables/{name}",
    status_code=204,
)
async def delete_session_variable(
    session_id: str, name: str, session: SessionDep, _principal: EditorDep
) -> None:
    deleted = await ChatSessionVariableRepo(session).delete(session_id, name)
    if not deleted:
        raise HTTPException(status_code=404, detail="variable not found")
    await session.commit()


@router.get("/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    session: SessionDep,
    _principal: ViewerDep,
    format: ChatExportFormat = ChatExportFormat.json,
) -> Response:
    row = await ChatSessionRepo(session).get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    messages = await ChatMessageRepo(session).list_for_session(session_id, limit=10_000)
    variables = await ChatSessionVariableRepo(session).snapshot(session_id)
    title = row.title or "Chat export"

    if format is ChatExportFormat.markdown:
        lines: list[str] = [f"# {title}", ""]
        lines.append(f"- Session ID: `{row.id}`")
        if row.workflow_id:
            lines.append(f"- Workflow: `{row.workflow_id}`")
        lines.append(f"- Created: {row.created_at.isoformat() if row.created_at else ''}")
        lines.append(f"- Updated: {row.updated_at.isoformat() if row.updated_at else ''}")
        if variables:
            lines.append("")
            lines.append("## Variables")
            for key, value in variables.items():
                rendered = json.dumps(value, ensure_ascii=False, default=str)
                lines.append(f"- **{key}**: {rendered}")
        lines.append("")
        lines.append("## Transcript")
        lines.append("")
        for msg in messages:
            speaker = msg.role.capitalize()
            lines.append(f"### {speaker}")
            lines.append("")
            lines.append(msg.content or "")
            if msg.citations_json:
                lines.append("")
                lines.append("> Citations:")
                for cite in msg.citations_json:
                    source = cite.get("source") or cite.get("document") or cite.get("title")
                    lines.append(f"> - {source}")
            lines.append("")
        content = "\n".join(lines)
        filename = _export_filename(title, "md")
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    payload = {
        "session": {
            "id": row.id,
            "title": row.title,
            "workflow_id": row.workflow_id,
            "model_config_id": row.model_config_id,
            "app_id": row.app_id,
            "inputs": row.inputs_json or {},
            "variables": variables,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        },
        "messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "node_ref": msg.node_ref,
                "execution_id": msg.execution_id,
                "citations": msg.citations_json or [],
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            for msg in messages
        ],
    }
    filename = _export_filename(title, "json")
    return Response(
        content=json.dumps(payload, ensure_ascii=False, default=str, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _export_filename(title: str, ext: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in title)[:60].strip("-")
    return f"{safe or 'chat-export'}.{ext}"


@router.post("/sessions/{session_id}/regenerate")
async def regenerate_message(
    session_id: str,
    message_id: str,
    body: ChatRegenerateRequest,
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    _principal: EditorDep,
) -> StreamingResponse:
    """Re-run the workflow from the user message preceding ``message_id``.

    Drops the existing assistant reply (and any later messages) and starts a
    fresh execution with the original user turn.
    """
    return await _rerun_from_message(
        session_id=session_id,
        message_id=message_id,
        replacement_message=None,
        extra_inputs=body.inputs,
        request=request,
        container=container,
        session=session,
    )


@router.post("/sessions/{session_id}/messages/{message_id}/edit")
async def edit_and_rerun_message(
    session_id: str,
    message_id: str,
    body: ChatMessageEditRequest,
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    _principal: EditorDep,
) -> StreamingResponse:
    """Edit a user message, truncate later turns, and re-run the workflow."""
    return await _rerun_from_message(
        session_id=session_id,
        message_id=message_id,
        replacement_message=body.message,
        extra_inputs=body.inputs,
        request=request,
        container=container,
        session=session,
    )


async def _rerun_from_message(
    *,
    session_id: str,
    message_id: str,
    replacement_message: str | None,
    extra_inputs: dict[str, Any],
    request: Request,
    container: ServiceContainer,
    session: AsyncSession,
) -> StreamingResponse:
    repo = ChatSessionRepo(session)
    chat_session = await repo.get(session_id)
    if chat_session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not chat_session.workflow_id:
        raise HTTPException(status_code=400, detail="session has no bound workflow")

    msg_repo = ChatMessageRepo(session)
    target = await msg_repo.get(message_id)
    if target is None or target.session_id != session_id:
        raise HTTPException(status_code=404, detail="message not found")

    messages = await msg_repo.list_for_session(session_id, limit=10_000)
    # Locate the target by id and collect ids to drop after it (inclusive for
    # assistant regenerate; for user-edit we drop the target user message too).
    ordered = list(messages)
    drop_from_index: int | None = None
    user_query = ""
    for index, msg in enumerate(ordered):
        if msg.id == message_id:
            drop_from_index = index
            if replacement_message is not None:
                # editing a user message: use new text
                user_query = replacement_message
            else:
                # regenerating an assistant message: use preceding user turn
                for prev in reversed(ordered[:index]):
                    if prev.role == "user":
                        user_query = prev.content
                        break
            break

    if drop_from_index is None:
        raise HTTPException(status_code=404, detail="message not found in transcript")

    if replacement_message is None and target.role != "assistant":
        raise HTTPException(status_code=422, detail="can only regenerate assistant messages")
    if replacement_message is not None and target.role != "user":
        raise HTTPException(status_code=422, detail="can only edit user messages")

    # Truncate later messages in the same session.
    for msg in ordered[drop_from_index:]:
        await session.delete(msg)
    await session.flush()

    if replacement_message is not None:
        new_row = await msg_repo.append(
            session_id=session_id, role="user", content=replacement_message
        )
        user_query = replacement_message
        message_id_for_turn = new_row.id
    else:
        message_id_for_turn = message_id
    await repo.touch(chat_session)
    await session.commit()

    workflow_id = chat_session.workflow_id
    merged_inputs = {
        **(chat_session.inputs_json or {}),
        **extra_inputs,
        "user_query": user_query,
    }

    try:
        execution_id = await container.execution_engine.start(
            workflow_id, merged_inputs, session_id=session_id
        )
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExecutionConcurrencyLimit as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except ProjectQuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc

    async def event_stream():
        bus = container.event_bus
        queue = bus.subscribe(execution_id)
        try:
            from app.db.repositories import ExecutionRepo

            async with container.session_factory() as db:
                rows = await ExecutionRepo(db).list_events_after(execution_id, 0)
            citations: list[dict] = []
            for ev in rows:
                citations = merge_citations(citations, ev.payload_json, ev.node_id)
                yield _sse(
                    {
                        "event_type": ev.event_type,
                        "seq": ev.seq,
                        "node_id": ev.node_id,
                        "payload": ev.payload_json,
                        "ts": ev.ts.isoformat() if ev.ts else None,
                    }
                )

            terminal = {"workflow_finished", "workflow_failed", "workflow_cancelled"}
            assistant_text = ""
            final_output = None
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                payload = event.to_dict()
                yield _sse(payload)
                etype = payload.get("event_type") or payload.get("type")
                citations = merge_citations(
                    citations,
                    payload.get("payload"),
                    payload.get("node_id"),
                )
                if etype == "node_streaming" and payload.get("payload", {}).get("kind") == "text":
                    assistant_text += payload["payload"].get("delta", "")
                if etype == "workflow_finished":
                    final_output = payload.get("payload", {}).get("output")
                if etype in terminal:
                    break

            reply = _extract_reply(final_output, assistant_text)
            if reply:
                async with container.session_factory() as db:
                    await ChatMessageRepo(db).append(
                        session_id=session_id,
                        role="assistant",
                        content=reply,
                        execution_id=execution_id,
                        citations=citations,
                    )
                    await db.commit()
        finally:
            bus.unsubscribe(execution_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Execution-ID": execution_id, "X-Chat-Message-ID": message_id_for_turn},
    )
