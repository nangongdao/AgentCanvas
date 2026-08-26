"""Approved MCP catalog entry and version administration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, ViewerDep, get_session
from app.db.models import McpCatalogEntry, McpCatalogVersion
from app.db.repositories.mcp_catalog import McpCatalogRepo
from app.schemas.api import (
    McpCatalogEntryCreate,
    McpCatalogEntryOut,
    McpCatalogHistoryOut,
    McpCatalogVersionCreate,
    McpCatalogVersionOut,
)
from app.services.audit import record_audit
from app.services.mcp_catalog import catalog_version_is_usable

router = APIRouter(prefix="/api/mcp/catalog", tags=["mcp catalog"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _version_out(row: McpCatalogVersion) -> McpCatalogVersionOut:
    return McpCatalogVersionOut.model_validate(
        {
            "id": row.id,
            "entry_id": row.entry_id,
            "version": row.version,
            "source_ref": row.source_ref,
            "manifest": row.manifest_json,
            "status": row.status,
            "approved_at": row.approved_at,
            "approved_by": row.approved_by,
            "created_at": row.created_at,
        }
    )


async def _entry_out(repo: McpCatalogRepo, row: McpCatalogEntry) -> McpCatalogEntryOut:
    versions = await repo.list_versions(row.id)
    return McpCatalogEntryOut(
        id=row.id,
        name=row.name,
        description=row.description,
        source_url=row.source_url,
        versions=[_version_out(version) for version in versions],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[McpCatalogEntryOut])
async def list_catalog(
    session: SessionDep,
    principal: ViewerDep,
) -> list[McpCatalogEntryOut]:
    del principal
    repo = McpCatalogRepo(session)
    return [await _entry_out(repo, row) for row in await repo.list_entries()]


@router.post("", response_model=McpCatalogEntryOut, status_code=status.HTTP_201_CREATED)
async def create_catalog_entry(
    body: McpCatalogEntryCreate,
    session: SessionDep,
    principal: AdminDep,
) -> McpCatalogEntryOut:
    repo = McpCatalogRepo(session)
    if await repo.get_entry(body.id) is not None:
        raise HTTPException(status_code=409, detail="MCP catalog entry already exists")
    entry = await repo.create_entry(
        id=body.id,
        name=body.name,
        description=body.description,
        source_url=body.source_url,
    )
    await repo.create_version(
        entry_id=entry.id,
        version=body.version,
        source_ref=body.source_ref,
        manifest_json=body.manifest.model_dump(mode="json"),
        status="draft",
    )
    await repo.add_history(
        entry_id=entry.id,
        to_version=body.version,
        action="created",
        actor_key=principal.subject,
        details_json={"source_ref": body.source_ref},
    )
    await record_audit(
        session,
        principal,
        action="mcp_catalog.created",
        resource_type="mcp_catalog",
        resource_id=entry.id,
        resource_name=entry.name,
        details={"version": body.version, "status": "draft"},
    )
    return await _entry_out(repo, entry)


@router.get("/{entry_id}", response_model=McpCatalogEntryOut)
async def get_catalog_entry(
    entry_id: str,
    session: SessionDep,
    principal: ViewerDep,
) -> McpCatalogEntryOut:
    repo = McpCatalogRepo(session)
    entry = await repo.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="MCP catalog entry not found")
    del principal
    return await _entry_out(repo, entry)


@router.post(
    "/{entry_id}/versions",
    response_model=McpCatalogVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_catalog_version(
    entry_id: str,
    body: McpCatalogVersionCreate,
    session: SessionDep,
    principal: AdminDep,
) -> McpCatalogVersionOut:
    repo = McpCatalogRepo(session)
    entry = await repo.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="MCP catalog entry not found")
    if any(version.version == body.version for version in await repo.list_versions(entry_id)):
        raise HTTPException(status_code=409, detail="MCP catalog version already exists")
    version = await repo.create_version(
        entry_id=entry_id,
        version=body.version,
        source_ref=body.source_ref,
        manifest_json=body.manifest.model_dump(mode="json"),
        status="draft",
    )
    await repo.add_history(
        entry_id=entry_id,
        to_version=version.version,
        action="created",
        actor_key=principal.subject,
        details_json={"source_ref": body.source_ref},
    )
    await record_audit(
        session,
        principal,
        action="mcp_catalog.version_created",
        resource_type="mcp_catalog_version",
        resource_id=version.id,
        resource_name=f"{entry.id}@{version.version}",
        details={"entry_id": entry.id, "version": version.version},
    )
    return _version_out(version)


@router.get("/{entry_id}/history", response_model=list[McpCatalogHistoryOut])
async def list_catalog_history(
    entry_id: str,
    session: SessionDep,
    principal: ViewerDep,
) -> list[McpCatalogHistoryOut]:
    repo = McpCatalogRepo(session)
    if await repo.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail="MCP catalog entry not found")
    del principal
    return [
        McpCatalogHistoryOut(
            id=row.id,
            entry_id=row.entry_id,
            from_version=row.from_version,
            to_version=row.to_version,
            action=row.action,
            actor_key=row.actor_key,
            details=row.details_json,
            created_at=row.created_at,
        )
        for row in await repo.list_history(entry_id)
    ]


@router.post(
    "/{entry_id}/versions/{version_id}/approve",
    response_model=McpCatalogVersionOut,
)
async def approve_catalog_version(
    entry_id: str,
    version_id: str,
    session: SessionDep,
    principal: AdminDep,
) -> McpCatalogVersionOut:
    repo = McpCatalogRepo(session)
    version = await repo.get_version(version_id)
    if version is None or version.entry_id != entry_id:
        raise HTTPException(status_code=404, detail="MCP catalog version not found")
    if version.status == "revoked":
        raise HTTPException(status_code=409, detail="revoked catalog versions cannot be approved")
    if version.status == "approved":
        return _version_out(version)
    previous = next(
        (candidate for candidate in await repo.list_versions(entry_id) if candidate.status == "approved"),
        None,
    )
    if previous is not None and previous.id != version.id:
        previous.status = "superseded"
    version.status = "approved"
    version.approved_at = datetime.now(UTC)
    version.approved_by = principal.subject
    await repo.add_history(
        entry_id=entry_id,
        from_version=previous.version if previous is not None else None,
        to_version=version.version,
        action="upgraded" if previous is not None else "approved",
        actor_key=principal.subject,
        details_json={"version_id": version.id},
    )
    await record_audit(
        session,
        principal,
        action="mcp_catalog.version_approved",
        resource_type="mcp_catalog_version",
        resource_id=version.id,
        resource_name=f"{entry_id}@{version.version}",
        details={
            "entry_id": entry_id,
            "version": version.version,
            "previous_version": previous.version if previous else None,
        },
    )
    return _version_out(version)


@router.post(
    "/{entry_id}/versions/{version_id}/revoke",
    response_model=McpCatalogVersionOut,
)
async def revoke_catalog_version(
    entry_id: str,
    version_id: str,
    session: SessionDep,
    principal: AdminDep,
) -> McpCatalogVersionOut:
    repo = McpCatalogRepo(session)
    version = await repo.get_version(version_id)
    if version is None or version.entry_id != entry_id:
        raise HTTPException(status_code=404, detail="MCP catalog version not found")
    if not catalog_version_is_usable(version):
        raise HTTPException(status_code=409, detail="only approved catalog versions can be revoked")
    version.status = "revoked"
    await repo.add_history(
        entry_id=entry_id,
        from_version=version.version,
        to_version=version.version,
        action="revoked",
        actor_key=principal.subject,
        details_json={"version_id": version.id},
    )
    await record_audit(
        session,
        principal,
        action="mcp_catalog.version_revoked",
        resource_type="mcp_catalog_version",
        resource_id=version.id,
        resource_name=f"{entry_id}@{version.version}",
        details={"entry_id": entry_id, "version": version.version},
    )
    return _version_out(version)
