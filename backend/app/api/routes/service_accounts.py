"""Service account and API token admin routes (D3 Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, get_container, get_session
from app.core.container import ServiceContainer
from app.db.models import ApiToken, ServiceAccount
from app.db.repositories.service_account import ApiTokenRepo, ServiceAccountRepo
from app.db.repositories.tenant import ProjectRepo
from app.schemas.api import (
    ApiTokenIssue,
    ApiTokenIssueOut,
    ApiTokenOut,
    ServiceAccountCreate,
    ServiceAccountOut,
    ServiceAccountUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/api/service-accounts", tags=["service-accounts"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _get_account(session: AsyncSession, account_id: str) -> ServiceAccount:
    account = await ServiceAccountRepo(session).get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="service account not found")
    return account


def _account_out(row: ServiceAccount) -> ServiceAccountOut:
    return ServiceAccountOut(
        id=row.id,
        name=row.name,
        description=row.description,
        project_id=row.project_id,
        role=row.role,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _token_out(row: ApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=row.id,
        service_account_id=row.service_account_id,
        name=row.name,
        prefix=row.prefix,
        scope=row.scope,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
    )


@router.post("", response_model=ServiceAccountOut, status_code=status.HTTP_201_CREATED)
async def create_service_account(
    body: ServiceAccountCreate, session: SessionDep, principal: AdminDep
) -> ServiceAccountOut:
    if body.project_id is not None and await ProjectRepo(session).get(body.project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        row = await ServiceAccountRepo(session).create(
            body.name,
            description=body.description,
            project_id=body.project_id,
            role=body.role,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="service account name already in use") from exc
    await record_audit(
        session,
        principal,
        action="service_account.created",
        resource_type="service_account",
        resource_id=row.id,
        resource_name=row.name,
        details={"role": row.role, "status": row.status},
    )
    return _account_out(row)


@router.get("", response_model=list[ServiceAccountOut])
async def list_service_accounts(
    session: SessionDep, _principal: AdminDep
) -> list[ServiceAccountOut]:
    rows = await ServiceAccountRepo(session).list()
    return [_account_out(row) for row in rows]


@router.put("/{account_id}", response_model=ServiceAccountOut)
async def update_service_account(
    account_id: str,
    body: ServiceAccountUpdate,
    session: SessionDep,
    principal: AdminDep,
) -> ServiceAccountOut:
    repo = ServiceAccountRepo(session)
    row = await _get_account(session, account_id)
    previous_role = row.role
    previous_status = row.status
    if body.description is not None:
        row.description = body.description
    if body.role is not None:
        row.role = body.role
    if body.status is not None:
        row.status = body.status
    await repo.touch(row)
    await record_audit(
        session,
        principal,
        action="service_account.updated",
        resource_type="service_account",
        resource_id=row.id,
        resource_name=row.name,
        details={
            "changed_fields": sorted(body.model_fields_set),
            "previous_role": previous_role,
            "new_role": row.role,
            "previous_status": previous_status,
            "new_status": row.status,
        },
    )
    return _account_out(row)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service_account(
    account_id: str, session: SessionDep, principal: AdminDep
) -> None:
    row = await _get_account(session, account_id)
    resource_name = row.name
    await ServiceAccountRepo(session).delete(account_id)
    await record_audit(
        session,
        principal,
        action="service_account.deleted",
        resource_type="service_account",
        resource_id=account_id,
        resource_name=resource_name,
    )


@router.post(
    "/{account_id}/tokens", response_model=ApiTokenIssueOut, status_code=status.HTTP_201_CREATED
)
async def issue_api_token(
    account_id: str,
    body: ApiTokenIssue,
    session: SessionDep,
    container: ContainerDep,
    principal: AdminDep,
) -> ApiTokenIssueOut:
    row = await _get_account(session, account_id)
    if row.status == "disabled":
        raise HTTPException(status_code=400, detail="service account is disabled")
    raw, token = await container.auth_service.issue_api_token(session, row, body.name)
    await record_audit(
        session,
        principal,
        action="api_token.issued",
        resource_type="api_token",
        resource_id=token.id,
        resource_name=token.name,
        details={"service_account_id": row.id, "service_account_name": row.name},
    )
    return ApiTokenIssueOut(token=_token_out(token), plaintext=raw)


@router.get("/{account_id}/tokens", response_model=list[ApiTokenOut])
async def list_api_tokens(
    account_id: str, session: SessionDep, _principal: AdminDep
) -> list[ApiTokenOut]:
    await _get_account(session, account_id)
    rows = await ApiTokenRepo(session).list_for_account(account_id)
    return [_token_out(row) for row in rows]


@router.post("/{account_id}/tokens/{token_id}/revoke", response_model=ApiTokenOut)
async def revoke_api_token(
    account_id: str, token_id: str, session: SessionDep, principal: AdminDep
) -> ApiTokenOut:
    account = await _get_account(session, account_id)
    token = await session.get(ApiToken, token_id)
    if token is None or token.service_account_id != account_id:
        raise HTTPException(status_code=404, detail="api token not found")
    if token.revoked_at is None:
        token.revoked_at = _utcnow()
        await session.flush()
        await record_audit(
            session,
            principal,
            action="api_token.revoked",
            resource_type="api_token",
            resource_id=token.id,
            resource_name=token.name,
            details={
                "service_account_id": account.id,
                "service_account_name": account.name,
            },
        )
    return _token_out(token)
