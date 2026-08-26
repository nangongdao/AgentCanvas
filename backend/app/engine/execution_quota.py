"""Project quota helpers shared by execution launch paths."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.project_quotas import ProjectQuotaService


class ExecutionQuotaMixin:
    async def _reserve_project_execution(
        self: Any,
        session: AsyncSession,
        project_id: str | None,
        execution_id: str,
    ) -> bool:
        if project_id is None:
            return False
        return await ProjectQuotaService(session).reserve(project_id, "execution", execution_id)

    async def _release_project_execution(
        self: Any,
        session: AsyncSession,
        project_id: str | None,
        execution_id: str,
    ) -> bool:
        if project_id is None:
            return False
        return await ProjectQuotaService(session).release(project_id, "execution", execution_id)


__all__ = ["ExecutionQuotaMixin"]
