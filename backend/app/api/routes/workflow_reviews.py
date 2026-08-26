"""Tenant-aware durable comments and reviews for workflow versions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, ViewerDep, get_session
from app.api.pagination import PageParams, PageResult, page_result
from app.api.workflow_access import authorize_workflow, workflow_version_or_404
from app.core.actor import ActorIdentity
from app.core.auth import Role
from app.core.workflow_reviews import ReviewStatus
from app.db.models import WorkflowComment, WorkflowReview
from app.db.repositories import WorkflowCommentRepo, WorkflowReviewRepo
from app.schemas.workflow_reviews import (
    WorkflowCommentCreate,
    WorkflowCommentOut,
    WorkflowCommentResolution,
    WorkflowReviewCreate,
    WorkflowReviewDecision,
    WorkflowReviewOut,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/api/workflows/{workflow_id}", tags=["workflow reviews"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
VersionFilter = Annotated[str | None, Query(min_length=1, max_length=32)]


def _comment_out(row: WorkflowComment) -> WorkflowCommentOut:
    return WorkflowCommentOut(
        id=row.id,
        workflow_id=row.workflow_id,
        version_id=row.version_id,
        parent_comment_id=row.parent_comment_id,
        node_id=row.node_id,
        author_subject=row.author_subject,
        body=row.body,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        resolved_by_subject=row.resolved_by_subject,
    )


def _review_out(row: WorkflowReview, actor: ActorIdentity) -> WorkflowReviewOut:
    return WorkflowReviewOut(
        id=row.id,
        workflow_id=row.workflow_id,
        version_id=row.version_id,
        status=ReviewStatus(row.status),
        summary=row.summary or "",
        requested_by_subject=row.requested_by_subject,
        requester_is_current_actor=row.requested_by_key == actor.key,
        created_at=row.created_at,
        decision_summary=row.decision_summary or "",
        decided_by_subject=row.decided_by_subject,
        decided_at=row.decided_at,
    )


@router.get("/comments", response_model=PageResult[WorkflowCommentOut])
async def list_workflow_comments(
    workflow_id: str,
    session: SessionDep,
    principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
    version_id: VersionFilter = None,
) -> PageResult[WorkflowCommentOut]:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    if version_id is not None:
        await workflow_version_or_404(session, workflow_id, version_id)
    spec = params.to_spec(
        allowed_sorts={"created_at", "author_subject", "id"},
        default_sort="created_at",
        scope=f"workflow-comments:{workflow_id}:{version_id or '*'}",
    )
    page = await WorkflowCommentRepo(session).list_page(workflow_id, spec, version_id=version_id)
    return page_result(page, _comment_out)


@router.post(
    "/comments",
    response_model=WorkflowCommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_comment(
    workflow_id: str,
    body: WorkflowCommentCreate,
    session: SessionDep,
    principal: ViewerDep,
) -> WorkflowCommentOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    version = await workflow_version_or_404(session, workflow_id, body.version_id)
    if body.node_id is not None and not any(
        isinstance(node, dict) and node.get("id") == body.node_id
        for node in version.dsl_json.get("nodes", [])
    ):
        raise HTTPException(status_code=404, detail="workflow version node not found")
    repo = WorkflowCommentRepo(session)
    if body.parent_comment_id is not None:
        parent = await repo.get_for_workflow(workflow_id, body.parent_comment_id)
        if parent is None or parent.version_id != body.version_id:
            raise HTTPException(status_code=404, detail="parent comment not found")
    row = await repo.create(
        workflow_id=workflow_id,
        version_id=body.version_id,
        parent_comment_id=body.parent_comment_id,
        node_id=body.node_id,
        body=body.body,
        author=ActorIdentity.from_principal(principal),
    )
    return _comment_out(row)


@router.put(
    "/comments/{comment_id}/resolution",
    response_model=WorkflowCommentOut,
)
async def resolve_workflow_comment(
    workflow_id: str,
    comment_id: str,
    body: WorkflowCommentResolution,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowCommentOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    repo = WorkflowCommentRepo(session)
    row = await repo.get_for_workflow(workflow_id, comment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow comment not found")
    await repo.set_resolved(
        row,
        resolved=body.resolved,
        actor=ActorIdentity.from_principal(principal),
    )
    return _comment_out(row)


@router.get("/reviews", response_model=PageResult[WorkflowReviewOut])
async def list_workflow_reviews(
    workflow_id: str,
    session: SessionDep,
    principal: ViewerDep,
    params: Annotated[PageParams, Depends()],
    version_id: VersionFilter = None,
) -> PageResult[WorkflowReviewOut]:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    if version_id is not None:
        await workflow_version_or_404(session, workflow_id, version_id)
    spec = params.to_spec(
        allowed_sorts={"created_at", "status", "id"},
        default_sort="created_at",
        scope=f"workflow-reviews:{workflow_id}:{version_id or '*'}",
    )
    page = await WorkflowReviewRepo(session).list_page(workflow_id, spec, version_id=version_id)
    actor = ActorIdentity.from_principal(principal)
    return page_result(page, lambda row: _review_out(row, actor))


@router.post(
    "/reviews",
    response_model=WorkflowReviewOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_review(
    workflow_id: str,
    body: WorkflowReviewCreate,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowReviewOut:
    workflow = await authorize_workflow(
        session, principal, workflow_id, required=Role.EDITOR
    )
    await workflow_version_or_404(session, workflow_id, body.version_id)
    repo = WorkflowReviewRepo(session)
    if await repo.get_for_version(body.version_id) is not None:
        raise HTTPException(status_code=409, detail="workflow version already has a review")
    try:
        row = await repo.create(
            workflow_id=workflow_id,
            version_id=body.version_id,
            summary=body.summary.strip(),
            requester=ActorIdentity.from_principal(principal),
        )
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409, detail="workflow version already has a review"
        ) from exc
    await record_audit(
        session,
        principal,
        action="workflow_review.requested",
        resource_type="workflow_review",
        resource_id=row.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id, "version_id": row.version_id},
    )
    return _review_out(row, ActorIdentity.from_principal(principal))


@router.put(
    "/reviews/{review_id}/decision",
    response_model=WorkflowReviewOut,
)
async def decide_workflow_review(
    workflow_id: str,
    review_id: str,
    body: WorkflowReviewDecision,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowReviewOut:
    workflow = await authorize_workflow(
        session, principal, workflow_id, required=Role.EDITOR
    )
    repo = WorkflowReviewRepo(session)
    row = await repo.get_for_workflow(workflow_id, review_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow review not found")
    try:
        await repo.decide(
            row,
            decision=body.decision,
            summary=body.summary.strip(),
            actor=ActorIdentity.from_principal(principal),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await record_audit(
        session,
        principal,
        action="workflow_review.decided",
        resource_type="workflow_review",
        resource_id=row.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={
            "workflow_id": workflow.id,
            "version_id": row.version_id,
            "outcome": body.decision.value,
        },
    )
    return _review_out(row, ActorIdentity.from_principal(principal))


__all__ = ["router"]
