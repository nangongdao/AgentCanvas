"""Persistence operations for version comments and change reviews."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actor import ActorIdentity
from app.core.workflow_reviews import ReviewDecision, ReviewStatus
from app.db.models import WorkflowComment, WorkflowReview
from app.db.pagination import PageSlice, PageSpec, paginate_select


class WorkflowCommentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_workflow(self, workflow_id: str, comment_id: str) -> WorkflowComment | None:
        result = await self.session.execute(
            select(WorkflowComment).where(
                WorkflowComment.id == comment_id,
                WorkflowComment.workflow_id == workflow_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_page(
        self,
        workflow_id: str,
        spec: PageSpec,
        *,
        version_id: str | None = None,
    ) -> PageSlice[WorkflowComment]:
        statement = select(WorkflowComment).where(WorkflowComment.workflow_id == workflow_id)
        if version_id is not None:
            statement = statement.where(WorkflowComment.version_id == version_id)
        return await paginate_select(
            self.session,
            statement,
            id_column=WorkflowComment.id,
            columns={
                "created_at": WorkflowComment.created_at,
                "author_subject": WorkflowComment.author_subject,
                "id": WorkflowComment.id,
            },
            spec=spec,
            search_columns=(
                WorkflowComment.body,
                WorkflowComment.author_subject,
                WorkflowComment.node_id,
                WorkflowComment.id,
            ),
        )

    async def create(
        self,
        *,
        workflow_id: str,
        version_id: str,
        body: str,
        author: ActorIdentity,
        parent_comment_id: str | None = None,
        node_id: str | None = None,
    ) -> WorkflowComment:
        row = WorkflowComment(
            workflow_id=workflow_id,
            version_id=version_id,
            parent_comment_id=parent_comment_id,
            node_id=node_id,
            author_user_id=author.user_id,
            author_key=author.key,
            author_subject=author.subject,
            body=body,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def set_resolved(
        self,
        row: WorkflowComment,
        *,
        resolved: bool,
        actor: ActorIdentity,
    ) -> WorkflowComment:
        if resolved:
            if row.resolved_at is None:
                row.resolved_at = datetime.now(UTC)
                row.resolved_by_key = actor.key
                row.resolved_by_subject = actor.subject
        else:
            row.resolved_at = None
            row.resolved_by_key = None
            row.resolved_by_subject = None
        await self.session.flush()
        return row


class WorkflowReviewRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_workflow(self, workflow_id: str, review_id: str) -> WorkflowReview | None:
        result = await self.session.execute(
            select(WorkflowReview).where(
                WorkflowReview.id == review_id,
                WorkflowReview.workflow_id == workflow_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_version(self, version_id: str) -> WorkflowReview | None:
        result = await self.session.execute(
            select(WorkflowReview).where(WorkflowReview.version_id == version_id)
        )
        return result.scalar_one_or_none()

    async def list_page(
        self,
        workflow_id: str,
        spec: PageSpec,
        *,
        version_id: str | None = None,
    ) -> PageSlice[WorkflowReview]:
        statement = select(WorkflowReview).where(WorkflowReview.workflow_id == workflow_id)
        if version_id is not None:
            statement = statement.where(WorkflowReview.version_id == version_id)
        return await paginate_select(
            self.session,
            statement,
            id_column=WorkflowReview.id,
            columns={
                "created_at": WorkflowReview.created_at,
                "status": WorkflowReview.status,
                "id": WorkflowReview.id,
            },
            spec=spec,
            search_columns=(
                WorkflowReview.summary,
                WorkflowReview.decision_summary,
                WorkflowReview.requested_by_subject,
                WorkflowReview.decided_by_subject,
                WorkflowReview.id,
            ),
        )

    async def create(
        self,
        *,
        workflow_id: str,
        version_id: str,
        summary: str,
        requester: ActorIdentity,
    ) -> WorkflowReview:
        row = WorkflowReview(
            workflow_id=workflow_id,
            version_id=version_id,
            status=ReviewStatus.OPEN,
            summary=summary,
            requested_by_key=requester.key,
            requested_by_subject=requester.subject,
            requested_by_user_id=requester.user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def decide(
        self,
        row: WorkflowReview,
        *,
        decision: ReviewDecision,
        summary: str,
        actor: ActorIdentity,
    ) -> WorkflowReview:
        if row.status != ReviewStatus.OPEN:
            raise ValueError("review has already reached a terminal decision")
        if decision != ReviewDecision.DISMISSED and row.requested_by_key == actor.key:
            raise ValueError("review requester cannot decide their own review")
        result = await self.session.execute(
            update(WorkflowReview)
            .where(
                WorkflowReview.id == row.id,
                WorkflowReview.status == ReviewStatus.OPEN,
            )
            .values(
                status=decision,
                decision_summary=summary,
                decided_by_key=actor.key,
                decided_by_subject=actor.subject,
                decided_by_user_id=actor.user_id,
                decided_at=datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            raise ValueError("review has already reached a terminal decision")
        await self.session.refresh(row)
        return row


__all__ = ["WorkflowCommentRepo", "WorkflowReviewRepo"]
