"""Global-admin query surface for append-only audit history."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, get_session
from app.api.pagination import PageParams, PageResult, page_result
from app.db.models.audit import AuditLog
from app.db.repositories.audit import AuditLogFilters, AuditLogRepo
from app.schemas.audit import AuditLogOut

router = APIRouter(prefix="/api/audit-logs", tags=["audit logs"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
IdFilter = Annotated[str | None, Query(min_length=1, max_length=128)]


def _to_out(row: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=row.id,
        organization_id=row.organization_id,
        project_id=row.project_id,
        actor_user_id=row.actor_user_id,
        actor_key=row.actor_key,
        actor_subject=row.actor_subject,
        auth_method=row.auth_method,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        resource_name=row.resource_name,
        details=row.details_json or {},
        created_at=row.created_at,
    )


@router.get("", response_model=PageResult[AuditLogOut])
async def list_audit_logs(
    session: SessionDep,
    _principal: AdminDep,
    params: Annotated[PageParams, Depends()],
    organization_id: IdFilter = None,
    project_id: IdFilter = None,
    actor_key: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    action: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    resource_type: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> PageResult[AuditLogOut]:
    filters = AuditLogFilters(
        organization_id=organization_id,
        project_id=project_id,
        actor_key=actor_key,
        action=action,
        resource_type=resource_type,
    )
    scope = "audit:" + json.dumps(
        asdict(filters), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    spec = params.to_spec(
        allowed_sorts={"created_at", "action", "resource_type", "actor_subject", "id"},
        default_sort="created_at",
        scope=scope,
    )
    return page_result(await AuditLogRepo(session).list_page(spec, filters), _to_out)


__all__ = ["router"]
