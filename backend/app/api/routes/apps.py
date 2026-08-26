"""Application entity CRUD and version binding routes (C3-1)."""

from __future__ import annotations

import re
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, EditorDep, ViewerDep, get_session
from app.api.pagination import PageParams, PageResult, page_result
from app.api.tenant_deps import authorize_project
from app.core.auth import Principal, Role, _sha256
from app.db.models import App, WorkflowVersion
from app.db.repositories import AppRepo, WorkflowRepo, WorkflowVersionRepo
from app.engine.input_validation import input_definitions
from app.schemas.api import (
    AppCreate,
    AppIssueOut,
    AppOut,
    AppStatus,
    AppType,
    AppUpdate,
    AppUsageDayOut,
    AppUsageOut,
    AppVersionSwitch,
    AppVisibility,
    InputFieldDef,
)
from app.schemas.dsl import WorkflowDSL
from app.services.app_usage import collect_app_usage
from app.services.audit import record_audit

router = APIRouter(prefix="/api/apps", tags=["apps"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_SUFFIX_TRIES = 32


def _slugify(name: str) -> str:
    slug = _SLUG_NON_ALNUM.sub("-", name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


async def _unique_slug(repo: AppRepo, project_id: str, name: str) -> str:
    """Resolve a project-unique slug for ``name`` before the row is flushed.

    A collision appends an incrementing ``-2``, ``-3`` suffix. Uniqueness is
    still enforced by ``uq_apps_project_slug``; this probe keeps the happy path
    collision-free so a single flush satisfies the constraint.
    """
    base = _slugify(name) or "app"
    candidate = base
    for attempt in range(_MAX_SLUG_SUFFIX_TRIES):
        if not await repo.slug_exists(project_id, candidate):
            return candidate
        candidate = f"{base}-{attempt + 2}"
    # Exhausted readable suffixes; fall back to a random slug whose uniqueness
    # is guaranteed by the unique constraint itself.
    return f"app-{secrets.token_hex(8)}"


def _new_public_token() -> tuple[str, str, str]:
    """Return (raw_token, token_hash, token_prefix)."""
    raw = secrets.token_urlsafe(32)
    return raw, _sha256(raw), raw[:12]


def _input_form_snapshot(version: WorkflowVersion | None) -> list[dict[str, Any]]:
    if version is None:
        return []
    dsl = WorkflowDSL.model_validate(version.dsl_json)
    definitions = input_definitions(dsl)
    return [
        {
            "name": definition.name,
            "type": definition.type.value,
            "required": definition.required,
            "default": definition.default,
        }
        for definition in definitions.values()
    ]


def _public_url(row: App, raw_token: str | None) -> str | None:
    if row.visibility not in (AppVisibility.LINK, AppVisibility.PUBLIC):
        return None
    # The public runtime page (C3-2) resolves by project slug + token; until
    # then expose the API-facing resolve path so callers can verify reachability.
    if raw_token is None:
        return f"/api/apps/p/{row.slug}"
    return f"/api/apps/p/{row.slug}?t={raw_token}"


def _out(row: App, version: WorkflowVersion | None, raw_token: str | None = None) -> AppOut:
    published_number = version.number if version is not None else None
    has_public = row.visibility in (AppVisibility.LINK, AppVisibility.PUBLIC)
    return AppOut(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        published_version_id=row.published_version_id,
        published_version_number=published_number,
        name=row.name,
        icon=row.icon,
        type=AppType(row.type),
        welcome_message=row.welcome_message,
        suggested_questions=list(row.suggested_questions or []),
        input_form=[InputFieldDef(**field) for field in (row.input_form or [])],
        visibility=AppVisibility(row.visibility),
        status=AppStatus(row.status),
        has_public_access=has_public,
        token_prefix=row.token_prefix,
        slug=row.slug,
        theme_color=row.theme_color,
        # Preserve an explicit empty list (embedding disabled) distinct from
        # None (never configured); both disable embedding but the editor UI
        # relies on the distinction for its textarea state.
        embed_allowed_origins=list(row.embed_allowed_origins)
        if row.embed_allowed_origins is not None
        else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _resolve_version(session: AsyncSession, row: App) -> WorkflowVersion | None:
    if row.published_version_id is None:
        return None
    version = await WorkflowVersionRepo(session).get(row.published_version_id)
    if version is None or version.status != "published":
        return None
    return version


async def _resolve_workflow_binding(
    session: AsyncSession, workflow_id: str | None, project_id: str
) -> str | None:
    """Validate a workflow belongs to ``project_id`` before binding it to an app.

    Returns the workflow id when the workflow exists and is owned by the project,
    ``None`` when no workflow was supplied, or raises 409 otherwise. A workflow
    scoped to a different project (or a global/template workflow) is rejected so
    an app never republishes another tenant's DSL.
    """
    if workflow_id is None:
        return None
    workflow = await WorkflowRepo(session).get(workflow_id)
    if workflow is None or workflow.project_id != project_id:
        raise HTTPException(
            status_code=409,
            detail="workflow does not belong to the app's project",
        )
    return workflow.id


@router.get("", response_model=PageResult[AppOut])
async def list_apps(
    project_id: Annotated[str, Query(min_length=1, max_length=32)],
    session: SessionDep,
    principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
) -> PageResult[AppOut]:
    await authorize_project(session, principal, project_id, required=Role.VIEWER)
    spec = params.to_spec(
        allowed_sorts={"created_at", "updated_at", "name", "id"},
        default_sort="created_at",
        scope=project_id,
    )
    page = await AppRepo(session).list_for_project(project_id, spec)
    versions = await _bulk_versions(session, page.rows)
    version_by_id: dict[str, WorkflowVersion] = {v.id: v for v in versions}
    return page_result(
        page,
        lambda row: _out(
            row,
            version_by_id.get(row.published_version_id)
            if row.published_version_id is not None
            else None,
        ),
    )


async def _bulk_versions(session: AsyncSession, rows: list[App]) -> list[WorkflowVersion]:
    repo = WorkflowVersionRepo(session)
    versions: list[WorkflowVersion] = []
    for row in rows:
        if row.published_version_id is None:
            continue
        version = await repo.get(row.published_version_id)
        if version is not None and version.status == "published":
            versions.append(version)
    return versions


@router.post("", response_model=AppIssueOut, status_code=201)
async def create_app(
    body: AppCreate,
    session: SessionDep,
    principal: EditorDep,
) -> AppIssueOut:
    project = await authorize_project(session, principal, body.project_id, required=Role.EDITOR)
    repo = AppRepo(session)
    workflow_id = await _resolve_workflow_binding(session, body.workflow_id, project.id)
    slug = await _unique_slug(repo, project.id, body.name)

    raw_token: str | None = None
    token_hash: str | None = None
    token_prefix: str | None = None
    if body.visibility in (AppVisibility.LINK, AppVisibility.PUBLIC):
        raw_token, token_hash, token_prefix = _new_public_token()

    row = App(
        project_id=project.id,
        workflow_id=workflow_id,
        published_version_id=None,
        name=body.name,
        icon=body.icon,
        type=body.type.value,
        welcome_message=body.welcome_message,
        suggested_questions=list(body.suggested_questions),
        input_form=None,
        visibility=body.visibility.value,
        status=AppStatus.ACTIVE.value,
        slug=slug,
        public_token_hash=token_hash,
        token_prefix=token_prefix,
        theme_color=body.theme_color,
        embed_allowed_origins=list(body.embed_allowed_origins)
        if body.embed_allowed_origins is not None
        else None,
    )
    await repo.create(row)

    await record_audit(
        session,
        principal,
        action="app.created",
        resource_type="app",
        resource_id=row.id,
        resource_name=row.name,
        project_id=project.id,
        details={
            "type": row.type,
            "visibility": row.visibility,
            "workflow_id": row.workflow_id,
        },
    )
    return AppIssueOut(
        app=_out(row, None, raw_token), public_url=_public_url(row, raw_token), token=raw_token
    )


@router.get("/{app_id}", response_model=AppOut)
async def get_app(app_id: str, session: SessionDep, principal: ViewerDep) -> AppOut:
    row = await _load_app(session, principal, app_id, required=Role.VIEWER)
    return _out(row, await _resolve_version(session, row))


@router.get("/{app_id}/usage", response_model=AppUsageOut)
async def get_app_usage(
    app_id: str,
    session: SessionDep,
    principal: ViewerDep,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> AppUsageOut:
    """Aggregate sessions, messages, tokens/cost, and feedback for an app (C3-5)."""
    row = await _load_app(session, principal, app_id, required=Role.VIEWER)
    usage = await collect_app_usage(session, row, days=days)
    return AppUsageOut(
        app_id=usage.app_id,
        days=usage.days,
        since=usage.since.isoformat(),
        sessions=usage.sessions,
        user_messages=usage.user_messages,
        assistant_messages=usage.assistant_messages,
        executions=usage.executions,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost_usd=usage.estimated_cost_usd,
        cost_known=usage.cost_known,
        positive_feedback=usage.positive_feedback,
        negative_feedback=usage.negative_feedback,
        feedback_rate=usage.feedback_rate,
        available_citations=usage.available_citations,
        referenced_citations=usage.referenced_citations,
        citation_coverage=usage.citation_coverage,
        daily=[
            AppUsageDayOut(
                date=day.date,
                sessions=day.sessions,
                messages=day.messages,
                executions=day.executions,
                total_tokens=day.total_tokens,
                estimated_cost_usd=day.estimated_cost_usd,
            )
            for day in usage.daily
        ],
    )


@router.put("/{app_id}", response_model=AppIssueOut)
async def update_app(
    app_id: str,
    body: AppUpdate,
    session: SessionDep,
    principal: EditorDep,
) -> AppIssueOut:
    row = await _load_app(session, principal, app_id, required=Role.EDITOR)
    repo = AppRepo(session)
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.icon is not None:
        fields["icon"] = body.icon
    if body.type is not None:
        fields["type"] = body.type.value
    if body.welcome_message is not None:
        fields["welcome_message"] = body.welcome_message
    if body.suggested_questions is not None:
        fields["suggested_questions"] = list(body.suggested_questions)
    if body.status is not None:
        fields["status"] = body.status.value
    # C3-3: embed/theme config. An explicit empty list disables embedding;
    # ``None`` means "not provided" and is skipped (no change).
    if body.theme_color is not None:
        fields["theme_color"] = body.theme_color
    if body.embed_allowed_origins is not None:
        fields["embed_allowed_origins"] = list(body.embed_allowed_origins)

    raw_token: str | None = None
    if body.visibility is not None and body.visibility.value != row.visibility:
        visibility_fields = _visibility_transition_fields(row, body.visibility)
        if body.visibility in (AppVisibility.LINK, AppVisibility.PUBLIC):
            raw_token, token_hash, token_prefix = _new_public_token()
            visibility_fields["public_token_hash"] = token_hash
            visibility_fields["token_prefix"] = token_prefix
        await repo.update_fields(row, **visibility_fields)

    if fields:
        await repo.update_fields(row, **fields)
    else:
        await session.flush()

    await record_audit(
        session,
        principal,
        action="app.updated",
        resource_type="app",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={"changed": sorted(fields.keys() | ({"visibility"} if body.visibility else set()))},
    )
    version = await _resolve_version(session, row)
    return AppIssueOut(
        app=_out(row, version, raw_token), public_url=_public_url(row, raw_token), token=raw_token
    )


def _visibility_transition_fields(row: App, visibility: AppVisibility) -> dict[str, Any]:
    """Compute the column delta for a visibility change as a single flush.

    Promoting to ``project`` clears the public token; promoting to
    ``link``/``public`` keeps any existing token (the caller overlays a fresh
    token if rotating). Both deltas land in one ``update_fields`` call so the
    visibility/token check constraint never sees an inconsistent midpoint.
    """
    if visibility == AppVisibility.PROJECT:
        return {"visibility": visibility.value, "public_token_hash": None, "token_prefix": None}
    return {"visibility": visibility.value}


@router.post("/{app_id}/version", response_model=AppOut)
async def switch_app_version(
    app_id: str,
    body: AppVersionSwitch,
    session: SessionDep,
    principal: EditorDep,
) -> AppOut:
    row = await _load_app(session, principal, app_id, required=Role.EDITOR)
    if row.workflow_id is None:
        raise HTTPException(status_code=409, detail="app has no bound workflow")
    version = await WorkflowVersionRepo(session).get_for_workflow(row.workflow_id, body.version_id)
    if version is None or version.status != "published":
        raise HTTPException(status_code=409, detail="published workflow version not found")
    snapshot = _input_form_snapshot(version)
    await AppRepo(session).apply_version(
        row,
        published_version_id=version.id,
        input_form=snapshot,
    )
    await record_audit(
        session,
        principal,
        action="app.version.changed",
        resource_type="app",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={"version_id": version.id, "version_number": version.number},
    )
    return _out(row, version)


@router.post("/{app_id}/token/rotate", response_model=AppIssueOut)
async def rotate_app_token(
    app_id: str,
    session: SessionDep,
    principal: EditorDep,
) -> AppIssueOut:
    row = await _load_app(session, principal, app_id, required=Role.EDITOR)
    if row.visibility not in (AppVisibility.LINK, AppVisibility.PUBLIC):
        raise HTTPException(status_code=409, detail="app is not publicly accessible")
    raw_token, token_hash, token_prefix = _new_public_token()
    await AppRepo(session).update_fields(
        row,
        public_token_hash=token_hash,
        token_prefix=token_prefix,
    )
    await record_audit(
        session,
        principal,
        action="app.token.rotated",
        resource_type="app",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
    )
    version = await _resolve_version(session, row)
    return AppIssueOut(
        app=_out(row, version, raw_token), public_url=_public_url(row, raw_token), token=raw_token
    )


@router.delete("/{app_id}", status_code=204)
async def delete_app(app_id: str, session: SessionDep, principal: AdminDep) -> None:
    row = await _load_app(session, principal, app_id, required=Role.ADMIN)
    await AppRepo(session).delete(row)
    await record_audit(
        session,
        principal,
        action="app.deleted",
        resource_type="app",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
    )


async def _load_app(
    session: AsyncSession,
    principal: Principal,
    app_id: str,
    *,
    required: Role,
) -> App:
    repo = AppRepo(session)
    row = await repo.get(app_id)
    if row is None:
        raise HTTPException(status_code=404, detail="app not found")
    await authorize_project(session, principal, row.project_id, required=required)
    return row


__all__ = ["router"]
