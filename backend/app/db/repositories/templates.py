"""Workflow template persistence and bounded search."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkflowTemplate


class WorkflowTemplateRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, template_id: str) -> WorkflowTemplate | None:
        return await self.session.get(WorkflowTemplate, template_id)

    async def search(
        self,
        *,
        search: str = "",
        tag: str = "",
        category: str = "",
        official: bool | None = None,
        limit: int = 200,
    ) -> list[WorkflowTemplate]:
        statement = select(WorkflowTemplate)
        normalized_search = search.strip()
        normalized_tag = tag.strip().casefold()
        if normalized_search:
            pattern = f"%{normalized_search}%"
            statement = statement.where(
                or_(
                    WorkflowTemplate.name.ilike(pattern),
                    WorkflowTemplate.description.ilike(pattern),
                )
            )
        if category.strip():
            statement = statement.where(WorkflowTemplate.category == category.strip())
        if official is not None:
            statement = statement.where(WorkflowTemplate.is_official.is_(official))
        statement = statement.order_by(
            WorkflowTemplate.is_official.desc(), WorkflowTemplate.updated_at.desc()
        ).limit(1000 if normalized_tag else limit)
        result = await self.session.execute(statement)
        rows = list(result.scalars())
        if normalized_tag:
            rows = [
                row
                for row in rows
                if normalized_tag in {value.casefold() for value in row.tags_json or []}
            ]
        return rows[:limit]

    async def create(
        self,
        *,
        name: str,
        description: str,
        category: str,
        tags: list[str],
        parameters: list[dict[str, Any]],
        dsl: dict[str, Any],
        is_official: bool = False,
        template_id: str | None = None,
    ) -> WorkflowTemplate:
        row = WorkflowTemplate(
            id=template_id,
            name=name,
            description=description,
            category=category,
            tags_json=tags,
            parameters_json=parameters,
            dsl_json=dsl,
            is_official=is_official,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def delete(self, row: WorkflowTemplate) -> None:
        await self.session.delete(row)
        await self.session.flush()


__all__ = ["WorkflowTemplateRepo"]
