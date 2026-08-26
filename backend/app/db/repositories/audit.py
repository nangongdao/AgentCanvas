"""Append and query operations for administrative audit history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit import AuditLog
from app.db.pagination import PageSlice, PageSpec, paginate_select


@dataclass(frozen=True, slots=True)
class AuditLogFilters:
    organization_id: str | None = None
    project_id: str | None = None
    actor_key: str | None = None
    action: str | None = None
    resource_type: str | None = None


class AuditLogRepo:
    """Expose creation and bounded reads, but no history mutation methods."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        organization_id: str | None,
        project_id: str | None,
        actor_user_id: str | None,
        actor_key: str,
        actor_subject: str,
        auth_method: str,
        action: str,
        resource_type: str,
        resource_id: str,
        resource_name: str | None,
        details: dict[str, Any],
    ) -> AuditLog:
        row = AuditLog(
            organization_id=organization_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            actor_key=actor_key,
            actor_subject=actor_subject,
            auth_method=auth_method,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            details_json=details,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_page(
        self, spec: PageSpec, filters: AuditLogFilters
    ) -> PageSlice[AuditLog]:
        statement = select(AuditLog)
        for column, value in (
            (AuditLog.organization_id, filters.organization_id),
            (AuditLog.project_id, filters.project_id),
            (AuditLog.actor_key, filters.actor_key),
            (AuditLog.action, filters.action),
            (AuditLog.resource_type, filters.resource_type),
        ):
            if value is not None:
                statement = statement.where(column == value)
        return await paginate_select(
            self.session,
            statement,
            id_column=AuditLog.id,
            columns={
                "created_at": AuditLog.created_at,
                "action": AuditLog.action,
                "resource_type": AuditLog.resource_type,
                "actor_subject": AuditLog.actor_subject,
                "id": AuditLog.id,
            },
            spec=spec,
            search_columns=(
                AuditLog.actor_subject,
                AuditLog.action,
                AuditLog.resource_type,
                AuditLog.resource_id,
                AuditLog.resource_name,
            ),
        )


__all__ = ["AuditLogFilters", "AuditLogRepo"]
