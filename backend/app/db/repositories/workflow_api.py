"""Data access for published workflow API bindings."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkflowApiPublication


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowApiPublicationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_workflow(self, workflow_id: str) -> WorkflowApiPublication | None:
        result = await self.session.execute(
            select(WorkflowApiPublication).where(
                WorkflowApiPublication.workflow_id == workflow_id
            )
        )
        return result.scalars().first()

    async def get_for_token(self, token_id: str) -> WorkflowApiPublication | None:
        result = await self.session.execute(
            select(WorkflowApiPublication).where(WorkflowApiPublication.api_token_id == token_id)
        )
        return result.scalars().first()

    async def create(
        self,
        workflow_id: str,
        published_version_id: str,
        service_account_id: str,
        api_token_id: str,
    ) -> WorkflowApiPublication:
        row = WorkflowApiPublication(
            workflow_id=workflow_id,
            published_version_id=published_version_id,
            service_account_id=service_account_id,
            api_token_id=api_token_id,
            status="active",
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def rotate(
        self,
        row: WorkflowApiPublication,
        *,
        published_version_id: str,
        service_account_id: str,
        api_token_id: str,
    ) -> None:
        row.published_version_id = published_version_id
        row.service_account_id = service_account_id
        row.api_token_id = api_token_id
        row.status = "active"
        row.updated_at = _utcnow()
        await self.session.flush()

    async def bind_version(
        self, row: WorkflowApiPublication, published_version_id: str
    ) -> None:
        row.published_version_id = published_version_id
        row.updated_at = _utcnow()
        await self.session.flush()

    async def disable(self, row: WorkflowApiPublication) -> None:
        row.status = "disabled"
        row.updated_at = _utcnow()
        await self.session.flush()

    async def touch_triggered(self, row: WorkflowApiPublication) -> None:
        now = _utcnow()
        row.last_triggered_at = now
        row.updated_at = now
        await self.session.flush()


__all__ = ["WorkflowApiPublicationRepo"]
