"""Immutable workflow snapshot persistence and lifecycle transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Workflow, WorkflowVersion

WorkflowVersionStatus = Literal["draft", "published", "archived"]


class WorkflowVersionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, version_id: str) -> WorkflowVersion | None:
        return await self.session.get(WorkflowVersion, version_id)

    async def get_for_workflow(self, workflow_id: str, version_id: str) -> WorkflowVersion | None:
        result = await self.session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.id == version_id,
                WorkflowVersion.workflow_id == workflow_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_number(self, workflow_id: str, number: int) -> WorkflowVersion | None:
        result = await self.session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.number == number,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_workflow(self, workflow_id: str) -> list[WorkflowVersion]:
        result = await self.session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.number.desc())
        )
        return list(result.scalars())

    async def ensure_current(self, workflow: Workflow) -> WorkflowVersion:
        current = await self.get_by_number(workflow.id, workflow.version)
        if current is not None:
            return current
        return await self.create_snapshot(workflow)

    async def create_snapshot(
        self,
        workflow: Workflow,
        *,
        status: WorkflowVersionStatus = "draft",
        change_summary: str = "",
    ) -> WorkflowVersion:
        row = WorkflowVersion(
            workflow_id=workflow.id,
            number=workflow.version,
            status=status,
            name=workflow.name,
            description=workflow.description or "",
            dsl_json=workflow.dsl_json,
            change_summary=change_summary,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def archive_draft(self, version: WorkflowVersion) -> None:
        if version.status != "draft":
            return
        version.status = "archived"
        version.archived_at = datetime.now(UTC)
        await self.session.flush()

    async def publish(self, workflow: Workflow) -> WorkflowVersion:
        current = await self.ensure_current(workflow)
        now = datetime.now(UTC)
        for version in await self.list_for_workflow(workflow.id):
            if version.status == "published" and version.id != current.id:
                version.status = "archived"
                version.archived_at = now
        current.status = "published"
        current.published_at = now
        current.archived_at = None
        await self.session.flush()
        return current

    async def rollback(
        self,
        workflow: Workflow,
        source: WorkflowVersion,
        *,
        change_summary: str = "",
    ) -> WorkflowVersion:
        current = await self.ensure_current(workflow)
        await self.archive_draft(current)
        workflow.version += 1
        workflow.name = source.name
        workflow.description = source.description
        workflow.dsl_json = source.dsl_json
        workflow.updated_at = datetime.now(UTC)
        summary = change_summary.strip() or f"Rollback to version {source.number}"
        return await self.create_snapshot(workflow, change_summary=summary)


__all__ = ["WorkflowVersionRepo", "WorkflowVersionStatus"]
