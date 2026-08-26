"""Workflow aggregate persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Workflow
from app.db.pagination import PageSlice, PageSpec, paginate_select
from app.db.repositories.workflow_versions import WorkflowVersionRepo


class WorkflowRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        name: str,
        dsl: dict[str, Any],
        description: str = "",
        project_id: str | None = None,
    ) -> Workflow:
        row = Workflow(
            name=name,
            description=description,
            dsl_json=dsl,
            version=1,
            project_id=project_id,
        )
        self.session.add(row)
        await self.session.flush()
        await WorkflowVersionRepo(self.session).create_snapshot(row)
        return row

    async def get(self, workflow_id: str) -> Workflow | None:
        return await self.session.get(Workflow, workflow_id)

    async def list(self, include_archived: bool = False) -> list[Workflow]:
        stmt = select(Workflow).order_by(Workflow.updated_at.desc())
        if not include_archived:
            stmt = stmt.where(Workflow.is_archived.is_(False))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_page(
        self,
        spec: PageSpec,
        *,
        include_archived: bool = False,
        project_id: str | None = None,
    ) -> PageSlice[Workflow]:
        stmt = select(Workflow)
        if not include_archived:
            stmt = stmt.where(Workflow.is_archived.is_(False))
        if project_id is not None:
            stmt = stmt.where(Workflow.project_id == project_id)
        else:
            stmt = stmt.where(Workflow.project_id.is_(None))
        return await paginate_select(
            self.session,
            stmt,
            id_column=Workflow.id,
            columns={
                "created_at": Workflow.created_at,
                "updated_at": Workflow.updated_at,
                "name": Workflow.name,
                "id": Workflow.id,
            },
            spec=spec,
            search_columns=(Workflow.name, Workflow.description, Workflow.id),
        )

    async def update(
        self,
        workflow: Workflow,
        *,
        name: str | None = None,
        dsl: dict[str, Any] | None = None,
        description: str | None = None,
        expected_version: int | None = None,
        change_summary: str = "",
    ) -> Workflow:
        changed = name is not None or description is not None or dsl is not None
        values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if dsl is not None:
            values["dsl_json"] = dsl
        if changed:
            values["version"] = Workflow.version + 1

        statement = update(Workflow).where(Workflow.id == workflow.id)
        if expected_version is not None:
            statement = statement.where(Workflow.version == expected_version)
        result = await self.session.execute(
            statement.values(**values).execution_options(synchronize_session=False)
        )
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            actual_result = await self.session.execute(
                select(Workflow.version).where(Workflow.id == workflow.id)
            )
            actual_version = actual_result.scalar_one_or_none()
            if expected_version is not None:
                raise ValueError(
                    f"version conflict: expected {expected_version}, got {actual_version}"
                )
            raise ValueError("workflow no longer exists")

        await self.session.refresh(workflow)
        if changed:
            versions = WorkflowVersionRepo(self.session)
            previous = await versions.get_by_number(workflow.id, workflow.version - 1)
            if previous is not None:
                await versions.archive_draft(previous)
            await WorkflowVersionRepo(self.session).create_snapshot(
                workflow, change_summary=change_summary
            )
        return workflow

    async def archive(self, workflow: Workflow) -> None:
        workflow.is_archived = True
        workflow.updated_at = datetime.now(UTC)
        await self.session.flush()
