"""MCP catalog version diff, preflight, and controlled rollout routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminDep, ViewerDep, get_container, get_session
from app.core.container import ServiceContainer
from app.db.models import McpCatalogVersion, McpServer
from app.db.repositories.mcp import McpServerRepo
from app.db.repositories.mcp_catalog import McpCatalogRepo
from app.schemas.api import (
    McpCatalogRolloutOut,
    McpCatalogRolloutPreviewOut,
    McpCatalogRolloutRequest,
    McpCatalogRolloutServerOut,
    McpCatalogVersionDiffOut,
)
from app.services.audit import record_audit
from app.services.mcp_catalog import (
    McpCatalogPolicyError,
    catalog_version_diff,
    validate_catalog_binding,
)

router = APIRouter(prefix="/api/mcp/catalog", tags=["mcp catalog"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


@router.get(
    "/{entry_id}/versions/{version_id}/diff",
    response_model=McpCatalogVersionDiffOut,
)
async def diff_catalog_version(
    entry_id: str,
    version_id: str,
    session: SessionDep,
    principal: ViewerDep,
    from_version_id: Annotated[str | None, Query()] = None,
) -> McpCatalogVersionDiffOut:
    del principal
    repo = McpCatalogRepo(session)
    target = await repo.get_version(version_id)
    if target is None or target.entry_id != entry_id:
        raise HTTPException(status_code=404, detail="MCP catalog version not found")
    previous = None
    versions = await repo.list_versions(entry_id)
    if from_version_id is not None:
        previous = await repo.get_version(from_version_id)
        if previous is None or previous.entry_id != entry_id:
            raise HTTPException(status_code=404, detail="MCP catalog base version not found")
    else:
        target_index = next(index for index, version in enumerate(versions) if version.id == target.id)
        if target_index + 1 < len(versions):
            previous = versions[target_index + 1]
    return McpCatalogVersionDiffOut.model_validate(catalog_version_diff(previous, target))


async def _rollout_server_status(
    server: McpServer,
    target: McpCatalogVersion,
    session: AsyncSession,
) -> McpCatalogRolloutServerOut:
    current_version = None
    if server.catalog_version_id is not None:
        current_version = await session.get(McpCatalogVersion, server.catalog_version_id)
    compatible = True
    reason: str | None = None
    if server.catalog_entry_id != target.entry_id:
        compatible = False
        reason = "server is not bound to this catalog entry"
    else:
        try:
            validate_catalog_binding(server, target)
        except McpCatalogPolicyError as exc:
            compatible = False
            reason = str(exc)
    return McpCatalogRolloutServerOut(
        server_id=server.id,
        name=server.name,
        project_id=server.project_id,
        current_version_id=server.catalog_version_id,
        current_version=current_version.version if current_version is not None else None,
        compatible=compatible,
        reason=reason,
    )


async def _rollout_targets(
    repo: McpCatalogRepo,
    target: McpCatalogVersion,
    server_ids: list[str],
) -> list[McpServer]:
    servers = await repo.list_bound_servers(target.entry_id)
    if not server_ids:
        return servers
    by_id = {server.id: server for server in servers}
    missing = [server_id for server_id in server_ids if server_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"rollout targets are not bound to catalog entry: {', '.join(missing)}",
        )
    return [by_id[server_id] for server_id in server_ids]


async def _load_rollout_target(
    entry_id: str,
    body: McpCatalogRolloutRequest,
    session: AsyncSession,
) -> McpCatalogVersion:
    target = await McpCatalogRepo(session).get_version(body.version_id)
    if target is None or target.entry_id != entry_id:
        raise HTTPException(status_code=404, detail="MCP catalog version not found")
    if target.status != "approved":
        raise HTTPException(status_code=409, detail="rollout requires an approved catalog version")
    return target


@router.post(
    "/{entry_id}/rollout/preview",
    response_model=McpCatalogRolloutPreviewOut,
)
async def preview_catalog_rollout(
    entry_id: str,
    body: McpCatalogRolloutRequest,
    session: SessionDep,
    principal: AdminDep,
) -> McpCatalogRolloutPreviewOut:
    del principal
    repo = McpCatalogRepo(session)
    target = await _load_rollout_target(entry_id, body, session)
    servers = await _rollout_targets(repo, target, body.server_ids)
    statuses = [await _rollout_server_status(server, target, session) for server in servers]
    compatible_count = sum(status.compatible for status in statuses)
    return McpCatalogRolloutPreviewOut(
        entry_id=entry_id,
        version_id=target.id,
        version=target.version,
        servers=statuses,
        compatible_count=compatible_count,
        incompatible_count=len(statuses) - compatible_count,
    )


@router.post(
    "/{entry_id}/rollout",
    response_model=McpCatalogRolloutOut,
)
async def rollout_catalog_version(
    entry_id: str,
    body: McpCatalogRolloutRequest,
    session: SessionDep,
    container: ContainerDep,
    principal: AdminDep,
) -> McpCatalogRolloutOut:
    repo = McpCatalogRepo(session)
    target = await _load_rollout_target(entry_id, body, session)
    servers = await _rollout_targets(repo, target, body.server_ids)
    statuses = [await _rollout_server_status(server, target, session) for server in servers]
    skipped = [status for status in statuses if not status.compatible]
    updated_server_ids: list[str] = []
    unchanged_server_ids: list[str] = []
    for server, status_row in zip(servers, statuses, strict=True):
        if not status_row.compatible:
            continue
        previous_version = status_row.current_version
        if server.catalog_version_id == target.id:
            unchanged_server_ids.append(server.id)
            continue
        await container.mcp_manager.disconnect(server.id)
        await McpServerRepo(session).update(
            server,
            catalog_entry_id=entry_id,
            catalog_version_id=target.id,
            tools_cache_json=[],
            tools_cached_at=None,
            last_status="catalog rollout applied",
        )
        await repo.add_history(
            entry_id=entry_id,
            from_version=previous_version,
            to_version=target.version,
            action="rollout",
            actor_key=principal.subject,
            details_json={"server_id": server.id, "project_id": server.project_id},
        )
        await record_audit(
            session,
            principal,
            action="mcp_server.catalog_rollout",
            resource_type="mcp_server",
            resource_id=server.id,
            resource_name=server.name,
            project_id=server.project_id,
            details={
                "entry_id": entry_id,
                "from_version": previous_version,
                "to_version": target.version,
            },
        )
        updated_server_ids.append(server.id)
    await record_audit(
        session,
        principal,
        action="mcp_catalog.rollout",
        resource_type="mcp_catalog_version",
        resource_id=target.id,
        resource_name=f"{entry_id}@{target.version}",
        details={
            "entry_id": entry_id,
            "updated_count": len(updated_server_ids),
            "unchanged_count": len(unchanged_server_ids),
            "skipped_count": len(skipped),
        },
    )
    return McpCatalogRolloutOut(
        entry_id=entry_id,
        version_id=target.id,
        version=target.version,
        updated_server_ids=updated_server_ids,
        unchanged_server_ids=unchanged_server_ids,
        skipped=skipped,
    )
