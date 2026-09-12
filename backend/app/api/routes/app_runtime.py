"""Public application runtime: resolve, session, and streaming send (C3-2).

These endpoints back the standalone WebApp at ``/apps/p/{slug}``. They never
require a platform session; access is gated by the app's visibility policy:

* ``project`` apps are not reachable from the public runtime (the resolve
  endpoint returns 404 for them) — members must use the platform chat surface.
* ``link`` apps require a ``?t=<raw_token>`` query parameter whose SHA-256
  matches the stored ``public_token_hash``.
* ``public`` apps are open to anyone with the slug.

The raw token is never persisted; only its SHA-256 hash is stored, so the
lookup compares hashes, never plaintext.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_container, get_session
from app.core.auth import _sha256
from app.core.container import ServiceContainer
from app.db.models import App
from app.db.repositories import (
    AppRepo,
    ChatFeedbackRepo,
    ChatMessageRepo,
    ChatSessionRepo,
    DocumentRepo,
    KnowledgeBaseRepo,
)
from app.engine.executor import EngineShuttingDown, ExecutionConcurrencyLimit
from app.schemas.api import (
    AppRuntimeOut,
    AppRuntimeSend,
    AppRuntimeSessionCreate,
    AppStatus,
    AppType,
    AppVisibility,
    ChatFeedbackOut,
    ChatFeedbackUpsert,
    ChatMessageOut,
    InputFieldDef,
)
from app.services.chat_citations import find_citation, merge_citations
from app.services.project_quotas import ProjectQuotaExceeded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/apps/p", tags=["app-runtime"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]

_CITATION_SOURCE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "Original cited document",
        "content": {
            "text/plain": {"schema": {"type": "string"}},
            "text/markdown": {"schema": {"type": "string"}},
            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
        },
    }
}


class _RuntimeAccess:
    """A resolved app plus whether the caller supplied a valid token."""

    __slots__ = ("app", "authenticated")

    def __init__(self, app: App, authenticated: bool) -> None:
        self.app = app
        self.authenticated = authenticated


async def _resolve_runtime_app(
    session: AsyncSession, slug: str, raw_token: str | None
) -> _RuntimeAccess:
    """Load a runnable app by slug and enforce the visibility/token rule."""
    app = await AppRepo(session).get_runtime_by_slug(slug)
    if app is None:
        raise HTTPException(status_code=404, detail="app not found")
    if app.visibility == AppVisibility.PROJECT:
        # Project-scoped apps are intentionally unreachable from the public
        # runtime; members use the platform chat surface instead.
        raise HTTPException(status_code=404, detail="app not found")
    if app.visibility == AppVisibility.PUBLIC:
        return _RuntimeAccess(app, authenticated=False)
    # link: require a token whose SHA-256 matches the stored hash.
    if not raw_token:
        raise HTTPException(status_code=401, detail="app access token required")
    if app.public_token_hash is None or _sha256(raw_token) != app.public_token_hash:
        raise HTTPException(status_code=401, detail="invalid app access token")
    return _RuntimeAccess(app, authenticated=True)


def _runtime_out(app: App) -> AppRuntimeOut:
    requires_token = app.visibility == AppVisibility.LINK
    origins = app.embed_allowed_origins or []
    return AppRuntimeOut(
        slug=app.slug,
        name=app.name,
        icon=app.icon,
        type=AppType(app.type),
        welcome_message=app.welcome_message,
        suggested_questions=list(app.suggested_questions or []),
        input_form=[InputFieldDef(**field) for field in (app.input_form or [])],
        visibility=AppVisibility(app.visibility),
        status=AppStatus(app.status),
        requires_token=requires_token,
        theme_color=app.theme_color,
        embed_enabled=len(origins) > 0,
    )


def _frame_ancestors_header(app: App) -> str:
    """Build a ``Content-Security-Policy: frame-ancestors`` directive.

    When ``embed_allowed_origins`` is empty/None, embedding is disabled
    (``frame-ancestors 'none'``). Otherwise the listed origins are joined
    with spaces. This prevents clickjacking: a browser will refuse to
    render the runtime page in an iframe whose parent origin is not
    explicitly allow-listed.
    """
    origins = app.embed_allowed_origins or []
    if not origins:
        return "frame-ancestors 'none'"
    return "frame-ancestors " + " ".join(origins)


def _harden(response: Response, app: App | None = None) -> None:
    """Set defensive headers so a link app's ``?t=`` token is not leaked via
    Referer to third-party resources embedded on the runtime page.

    When an app is provided, also set a ``Content-Security-Policy:
    frame-ancestors`` header to control which origins may embed the
    runtime page in an iframe (C3-3).
    """
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if app is not None:
        response.headers["Content-Security-Policy"] = _frame_ancestors_header(app)


@router.get("/{slug}", response_model=AppRuntimeOut)
async def resolve_runtime_app(
    slug: str,
    session: SessionDep,
    response: Response,
    t: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> AppRuntimeOut:
    access = await _resolve_runtime_app(session, slug, t)
    _harden(response, access.app)
    return _runtime_out(access.app)


@router.post("/{slug}/sessions", response_model=ChatMessageOut, status_code=201)
async def create_runtime_session(
    slug: str,
    body: AppRuntimeSessionCreate,
    session: SessionDep,
    response: Response,
    t: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> ChatMessageOut:
    """Create a chat session bound to the app's published workflow."""
    access = await _resolve_runtime_app(session, slug, t)
    _harden(response, access.app)
    row = await ChatSessionRepo(session).create(
        title=body.title or access.app.name,
        workflow_id=access.app.workflow_id,
        inputs=body.inputs,
        app_id=access.app.id,
    )
    await session.commit()
    return ChatMessageOut(
        id=row.id,
        session_id=row.id,
        role="system",
        content=access.app.welcome_message or "",
        node_ref=None,
        execution_id=None,
        citations=[],
        created_at=row.created_at,
    )


@router.post("/{slug}/sessions/{session_id}/send")
async def send_runtime_message(
    slug: str,
    session_id: str,
    body: AppRuntimeSend,
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    t: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> StreamingResponse:
    """Run the app's bound workflow and stream the assistant reply via SSE."""
    access = await _resolve_runtime_app(session, slug, t)
    repo = ChatSessionRepo(session)
    chat_session = await repo.get(session_id)
    if chat_session is None or chat_session.app_id != access.app.id:
        raise HTTPException(status_code=404, detail="session not found")
    if not chat_session.workflow_id:
        raise HTTPException(status_code=400, detail="session has no bound workflow")

    workflow_id = chat_session.workflow_id
    merged_inputs = {
        **(chat_session.inputs_json or {}),
        **body.inputs,
        "user_query": body.message,
    }

    msg_repo = ChatMessageRepo(session)
    # End-user send budget per runtime session (C3-5): counted from durable
    # message rows so the limit holds across API replicas and survives restarts.
    settings = container.settings
    if settings is not None:
        window = timedelta(seconds=settings.rate_limit_window_seconds)
        recent_sends = await msg_repo.count_recent(session_id, "user", datetime.now(UTC) - window)
        if recent_sends >= settings.rate_limit_app_runtime_requests:
            raise HTTPException(
                status_code=429,
                detail="send rate limit exceeded for this conversation",
                headers={"Retry-After": str(settings.rate_limit_window_seconds)},
            )
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
            from app.db.repositories import ExecutionRepo

            async with container.session_factory() as db:
                rows = await ExecutionRepo(db).list_events_after(execution_id, 0)
            # Track the highest replayed seq so live events received via the
            # bus queue that were already persisted are not streamed twice
            # (subscribe races with the worker's commit for in-flight events).
            last_replayed_seq = 0
            citations: list[dict] = []
            for ev in rows:
                last_replayed_seq = max(last_replayed_seq, ev.seq)
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
            seen_terminal = False
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
                # Skip live events already covered by the DB replay above.
                live_seq = payload.get("seq")
                if isinstance(live_seq, int) and live_seq <= last_replayed_seq:
                    if (payload.get("event_type") or payload.get("type")) in terminal:
                        seen_terminal = True
                    continue
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
                    seen_terminal = True
                    break

            # If we exited via disconnect before a terminal event, still try to
            # persist any reply already captured in durable events (best-effort).
            if not seen_terminal:
                async with container.session_factory() as db:
                    rows = await ExecutionRepo(db).list_events_after(
                        execution_id, last_replayed_seq
                    )
                for ev in rows:
                    etype = ev.event_type
                    payload = ev.payload_json or {}
                    citations = merge_citations(citations, payload, ev.node_id)
                    if (
                        etype == "node_streaming"
                        and payload.get("kind") == "text"
                        and isinstance(payload.get("delta"), str)
                    ):
                        assistant_text += payload["delta"]
                    if etype == "workflow_finished":
                        final_output = payload.get("output")
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
        headers={
            "X-Execution-ID": execution_id,
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": _frame_ancestors_header(access.app),
        },
    )


# C3-3 floating-bubble bootstrap script. Deliberately static: it derives the
# slug and platform origin from its own <script src> URL, so no server data is
# interpolated into the JavaScript body (zero injection surface). The server
# only decides whether the app exists and embedding is enabled.
_EMBED_SCRIPT = """\
(function () {
  "use strict";
  var script = document.currentScript || (function () {
    var candidates = document.querySelectorAll('script[src$="/embed.js"]');
    return candidates.length ? candidates[candidates.length - 1] : null;
  })();
  if (!script || !script.src) { return; }
  var src;
  try { src = new URL(script.src, window.location.href); } catch (err) { return; }
  var match = src.pathname.match(/^\\/api\\/apps\\/p\\/([a-z0-9-]{1,64})\\/embed\\.js$/);
  if (!match) { return; }
  var slug = match[1];
  var position = script.getAttribute("data-position") === "left" ? "left" : "right";
  var color = script.getAttribute("data-color") || "#3b82f6";
  var label = script.getAttribute("data-title") || "Chat";
  var frameUrl = src.origin + "/apps/p/" + slug + "?embed=1";
  var token = script.getAttribute("data-token");
  if (token) { frameUrl += "&t=" + encodeURIComponent(token); }

  var btn = document.createElement("button");
  btn.type = "button";
  btn.setAttribute("aria-label", label);
  btn.setAttribute("aria-expanded", "false");
  btn.style.cssText = [
    "position:fixed", "bottom:20px", position + ":20px", "width:56px", "height:56px",
    "border-radius:50%", "border:0", "cursor:pointer", "z-index:2147483000",
    "color:#fff", "box-shadow:0 4px 14px rgba(0,0,0,0.25)", "display:flex",
    "align-items:center", "justify-content:center", "padding:0"
  ].join(";");
  btn.style.backgroundColor = color;
  btn.innerHTML =
    '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5' +
    'H9.4L5 20.2V5.5Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/>' +
    '<circle cx="9" cy="9.5" r="1" fill="currentColor"/>' +
    '<circle cx="12" cy="9.5" r="1" fill="currentColor"/>' +
    '<circle cx="15" cy="9.5" r="1" fill="currentColor"/></svg>';

  var panel = document.createElement("div");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", label);
  panel.style.cssText = [
    "position:fixed", "bottom:92px", position + ":20px", "width:380px",
    "max-width:calc(100vw - 32px)", "height:600px", "max-height:calc(100vh - 130px)",
    "border-radius:12px", "overflow:hidden", "z-index:2147483000",
    "box-shadow:0 12px 40px rgba(0,0,0,0.3)", "background:#0b0d12", "display:none"
  ].join(";");

  var frame = null;
  function ensureFrame() {
    if (frame) { return; }
    frame = document.createElement("iframe");
    frame.title = label;
    frame.setAttribute("allow", "clipboard-write");
    frame.src = frameUrl;
    frame.style.cssText = "width:100%;height:100%;border:0;display:block";
    panel.appendChild(frame);
  }
  function setOpen(next) {
    panel.style.display = next ? "block" : "none";
    btn.setAttribute("aria-expanded", next ? "true" : "false");
    if (next) { ensureFrame(); }
  }
  btn.addEventListener("click", function () {
    setOpen(panel.style.display === "none");
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && panel.style.display !== "none") { setOpen(false); }
  });

  function mount() {
    if (!document.body) { return; }
    document.body.appendChild(panel);
    document.body.appendChild(btn);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
"""


@router.get("/{slug}/embed.js")
async def embed_script(slug: str, session: SessionDep) -> Response:
    """Serve the floating-bubble bootstrap script for an embeddable app (C3-3).

    The script body is static (see ``_EMBED_SCRIPT``); this endpoint only
    decides whether a runnable app exists and embedding is enabled, so a
    disabled or unknown app yields no bubble instead of a broken one. The
    response is never framed, so no ``frame-ancestors`` applies here; the
    runtime page the bubble opens keeps its own embed allow-list policy.
    """
    app = await AppRepo(session).get_runtime_by_slug(slug)
    if app is None or app.visibility == AppVisibility.PROJECT:
        raise HTTPException(status_code=404, detail="app not found")
    if not app.embed_allowed_origins:
        raise HTTPException(status_code=403, detail="embedding is disabled for this app")
    # The body is static, but the 200/403/404 decision follows live app
    # config; no-store keeps disable/rotate actions effective immediately.
    # CORP is declared cross-origin on purpose: the bootstrap is meant to be
    # loaded by allow-listed external host pages, and the C8-3 middleware's
    # same-origin default would otherwise make browsers block the request
    # (ERR_BLOCKED_BY_RESPONSE) — which silently broke embeds.
    return Response(
        content=_EMBED_SCRIPT,
        media_type="text/javascript",
        headers={
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


@router.get("/{slug}/sessions/{session_id}/messages", response_model=list[ChatMessageOut])
async def list_runtime_messages(
    slug: str,
    session_id: str,
    session: SessionDep,
    response: Response,
    t: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> list[ChatMessageOut]:
    """Replay a runtime session's transcript (for reconnect/history)."""
    access = await _resolve_runtime_app(session, slug, t)
    _harden(response, access.app)
    chat_session = await ChatSessionRepo(session).get(session_id)
    if chat_session is None or chat_session.app_id != access.app.id:
        raise HTTPException(status_code=404, detail="session not found")
    rows = await ChatMessageRepo(session).list_for_session(session_id)
    ratings = await ChatFeedbackRepo(session).ratings_for_messages([row.id for row in rows])
    return [_message_out(row, ratings.get(row.id)) for row in rows]


async def _runtime_message_or_404(
    session: AsyncSession, app: App, session_id: str, message_id: str, *, response: Response
):
    """Resolve a message that belongs to a chat session bound to this app."""
    _harden(response, app)
    chat_session = await ChatSessionRepo(session).get(session_id)
    if chat_session is None or chat_session.app_id != app.id:
        raise HTTPException(status_code=404, detail="session not found")
    message = await ChatMessageRepo(session).get(message_id)
    if message is None or message.session_id != session_id:
        raise HTTPException(status_code=404, detail="message not found")
    return message


@router.get(
    "/{slug}/sessions/{session_id}/messages/{message_id}/citations/{citation_id}/source",
    response_class=FileResponse,
    responses=_CITATION_SOURCE_RESPONSES,
)
async def get_runtime_citation_source(
    slug: str,
    session_id: str,
    message_id: str,
    citation_id: str,
    session: SessionDep,
    container: ContainerDep,
    response: Response,
    t: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> FileResponse:
    """Serve only a source explicitly cited by this app message."""
    access = await _resolve_runtime_app(session, slug, t)
    message = await _runtime_message_or_404(
        session, access.app, session_id, message_id, response=response
    )
    citation = find_citation(message.citations_json, citation_id)
    if citation is None:
        raise HTTPException(status_code=404, detail="citation not found")

    document_id = str(citation.get("document_id") or "")
    kb_id = str(citation.get("kb_id") or "")
    document = await DocumentRepo(session).get(document_id) if document_id else None
    kb = await KnowledgeBaseRepo(session).get(kb_id) if kb_id else None
    if (
        document is None
        or kb is None
        or document.kb_id != kb.id
        or kb.project_id != access.app.project_id
        or str(citation.get("filename") or "") != document.filename
    ):
        raise HTTPException(status_code=404, detail="citation source not found")
    try:
        source_path = container.rag_service.storage.resolve(document.file_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="citation source not found") from exc

    source = FileResponse(
        source_path,
        media_type=document.mime_type,
        filename=document.filename,
        content_disposition_type="inline",
    )
    _harden(source, access.app)
    source.headers["Cache-Control"] = "private, no-store"
    return source


@router.post(
    "/{slug}/sessions/{session_id}/messages/{message_id}/feedback",
    response_model=ChatFeedbackOut,
)
async def upsert_runtime_feedback(
    slug: str,
    session_id: str,
    message_id: str,
    body: ChatFeedbackUpsert,
    session: SessionDep,
    response: Response,
    t: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> ChatFeedbackOut:
    """End-user 👍/👎 on a runtime assistant reply (C3-4).

    Feedback from the public surface lands in the same durable store as the
    platform chat so editors can promote 👎 turns into evaluation datasets.
    """
    access = await _resolve_runtime_app(session, slug, t)
    message = await _runtime_message_or_404(
        session, access.app, session_id, message_id, response=response
    )
    if message.role != "assistant":
        raise HTTPException(status_code=422, detail="feedback only allowed on assistant messages")
    row = await ChatFeedbackRepo(session).upsert(
        message=message, rating=body.rating, comment=body.comment
    )
    await session.commit()
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


@router.get(
    "/{slug}/sessions/{session_id}/messages/{message_id}/feedback",
    response_model=ChatFeedbackOut,
)
async def get_runtime_feedback(
    slug: str,
    session_id: str,
    message_id: str,
    session: SessionDep,
    response: Response,
    t: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> ChatFeedbackOut:
    """Read end-user feedback for a runtime assistant reply."""
    access = await _resolve_runtime_app(session, slug, t)
    await _runtime_message_or_404(session, access.app, session_id, message_id, response=response)
    row = await ChatFeedbackRepo(session).get_for_message(message_id)
    if row is None or row.session_id != session_id:
        raise HTTPException(status_code=404, detail="feedback not found")
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


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _extract_reply(final_output: object, streamed: str) -> str:
    """Pick the best text to persist as the assistant message."""
    if isinstance(final_output, dict):
        for key in ("answer", "result", "text", "output"):
            value = final_output.get(key)
            if isinstance(value, str) and value:
                return value
    return streamed or ""


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


__all__ = ["router"]
