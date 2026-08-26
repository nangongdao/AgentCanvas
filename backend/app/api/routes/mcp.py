"""MCP server registry, connection checks, and tool discovery."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ViewerDep, get_container, get_session
from app.api.tenant_deps import authorize_project_scope
from app.core.auth import Principal, Role
from app.core.container import ServiceContainer
from app.core.mcp_policy import StdioCommandDenied
from app.core.secret_providers import SecretProviderError
from app.db.models import McpServer
from app.db.repositories.mcp import McpServerRepo
from app.db.repositories.mcp_catalog import McpCatalogRepo
from app.mcphub import McpConnectionError
from app.schemas.api import (
    McpCatalogBindingUpdate,
    McpServerCreate,
    McpServerOut,
    McpServerUpdate,
    McpTestOut,
    McpToolOut,
    McpTransport,
)
from app.services.audit import record_audit
from app.services.mcp_catalog import (
    McpCatalogPolicyError,
    validate_catalog_binding,
)
from app.services.project_quotas import ProjectQuotaExceeded

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
MASKED_SECRET = "********"


def _secret_values(
    row: McpServer, encrypted_field: str, legacy_field: str, container: ServiceContainer
) -> dict[str, str]:
    encrypted = cast(str | None, getattr(row, encrypted_field))
    if encrypted:
        # Masked edits must carry the encrypted reference forward verbatim.
        # Resolving here would copy an env/Docker/external value into Fernet
        # storage and would also make an update fail when the provider is
        # temporarily unavailable.
        return container.secret_resolver.raw_mapping(encrypted)
    legacy = cast(dict[str, Any], getattr(row, legacy_field) or {})
    return {str(key): str(value) for key, value in legacy.items()}


def _secret_metadata(
    row: McpServer, encrypted_field: str, legacy_field: str, container: ServiceContainer
) -> tuple[dict[str, str], dict[str, str]]:
    encrypted = cast(str | None, getattr(row, encrypted_field))
    if encrypted:
        return container.secret_resolver.masked_mapping(encrypted)
    legacy = cast(dict[str, Any], getattr(row, legacy_field) or {})
    values = {str(key): str(value) for key, value in legacy.items()}
    return _masked(values), {key: "stored" for key in values}


def _masked(values: dict[str, str]) -> dict[str, str]:
    return {key: MASKED_SECRET for key in values}


def _to_out(row: McpServer, connected_ids: set[str], container: ServiceContainer) -> McpServerOut:
    env_masked, env_sources = _secret_metadata(row, "env_encrypted", "env_json", container)
    headers_masked, headers_sources = _secret_metadata(
        row, "headers_encrypted", "headers_json", container
    )
    return McpServerOut(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        transport=cast(McpTransport, row.transport),
        command=row.command,
        args=list(row.args_json or []),
        env=env_masked,
        env_sources=env_sources,
        url=row.url,
        headers=headers_masked,
        headers_sources=headers_sources,
        enabled=row.enabled,
        catalog_entry_id=row.catalog_entry_id,
        catalog_version_id=row.catalog_version_id,
        tools=[McpToolOut.model_validate(tool) for tool in row.tools_cache_json or []],
        tools_cached_at=row.tools_cached_at,
        last_status=row.last_status,
        connected=row.id in connected_ids,
        created_at=row.created_at,
    )


def _repo_fields(data: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "args": "args_json",
    }
    return {mapping.get(key, key): value for key, value in data.items()}


async def _require_admin_scope(
    session: AsyncSession, principal: Principal, project_id: str | None
) -> None:
    if project_id is None:
        if not principal.can(Role.ADMIN):
            raise HTTPException(status_code=403, detail="admin role required")
        return
    await authorize_project_scope(session, principal, project_id, required=Role.ADMIN)


async def _managed_row_or_404(
    session: AsyncSession, principal: Principal, server_id: str
) -> McpServer:
    row = await McpServerRepo(session).get(server_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await _require_admin_scope(session, principal, row.project_id)
    return row


def _merge_masked(existing: dict[str, str], provided: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for key, value in provided.items():
        if value == MASKED_SECRET:
            if key not in existing:
                raise HTTPException(
                    status_code=422,
                    detail=f"masked secret '{key}' has no stored value",
                )
            merged[key] = existing[key]
        else:
            merged[key] = value
    return merged


def _enforce_stdio_policy(
    container: ServiceContainer,
    transport: str,
    command: str | None,
    args: list[Any],
) -> None:
    if transport != "stdio":
        return
    try:
        container.mcp_stdio_policy.validate(command, tuple(str(value) for value in args))
    except StdioCommandDenied as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[McpServerOut])
async def list_servers(
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
    project_id: Annotated[str | None, Query()] = None,
) -> list[McpServerOut]:
    await authorize_project_scope(session, principal, project_id, required=Role.VIEWER)
    rows = await McpServerRepo(session).list_scoped(project_id)
    connected = set(container.mcp_manager.status())
    return [_to_out(row, connected, container) for row in rows]


@router.post("", response_model=McpServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(
    body: McpServerCreate,
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
) -> McpServerOut:
    await _require_admin_scope(session, principal, body.project_id)
    data = body.model_dump(exclude_none=True)
    env = cast(dict[str, str], data.pop("env", {}))
    headers = cast(dict[str, str], data.pop("headers", {}))
    _enforce_stdio_policy(
        container,
        str(data.get("transport", "stdio")),
        cast(str | None, data.get("command")),
        cast(list[Any], data.get("args", [])),
    )
    try:
        encrypted_env = container.secret_resolver.encrypt_mapping(env)
        encrypted_headers = container.secret_resolver.encrypt_mapping(headers)
    except SecretProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = await McpServerRepo(session).create(
        **_repo_fields(data),
        env_json={},
        headers_json={},
        env_encrypted=encrypted_env,
        headers_encrypted=encrypted_headers,
    )
    await record_audit(
        session,
        principal,
        action="mcp_server.created",
        resource_type="mcp_server",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={
            "transport": row.transport,
            "enabled": row.enabled,
            "configured_env_count": len(env),
            "configured_header_count": len(headers),
        },
    )
    return _to_out(row, set(container.mcp_manager.status()), container)


@router.put("/{server_id}", response_model=McpServerOut)
async def update_server(
    server_id: str,
    body: McpServerUpdate,
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
) -> McpServerOut:
    row = await _managed_row_or_404(session, principal, server_id)
    data = body.model_dump(exclude_unset=True)
    if "env" in data:
        provided_env = cast(dict[str, str], data.pop("env"))
        current_env = _secret_values(row, "env_encrypted", "env_json", container)
        updates_env = _merge_masked(current_env, provided_env)
        try:
            data["env_encrypted"] = container.secret_resolver.encrypt_mapping(updates_env)
        except SecretProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        data["env_json"] = {}
    if "headers" in data:
        provided_headers = cast(dict[str, str], data.pop("headers"))
        current_headers = _secret_values(row, "headers_encrypted", "headers_json", container)
        updates_headers = _merge_masked(current_headers, provided_headers)
        try:
            data["headers_encrypted"] = container.secret_resolver.encrypt_mapping(updates_headers)
        except SecretProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        data["headers_json"] = {}
    updates = _repo_fields(data)
    next_transport = updates.get("transport", row.transport)
    next_command = updates.get("command", row.command)
    next_url = updates.get("url", row.url)
    if next_transport == "stdio" and not (next_command or "").strip():
        raise HTTPException(status_code=422, detail="stdio transport requires command")
    if next_transport != "stdio" and not (next_url or "").strip():
        raise HTTPException(status_code=422, detail=f"{next_transport} transport requires url")
    next_args = cast(list[Any], updates.get("args_json", row.args_json or []))
    _enforce_stdio_policy(
        container,
        str(next_transport),
        cast(str | None, next_command),
        next_args,
    )
    updates["tools_cache_json"] = []
    updates["tools_cached_at"] = None
    updates["last_status"] = "configuration changed"
    await container.mcp_manager.disconnect(server_id)
    await McpServerRepo(session).update(row, **updates)
    await record_audit(
        session,
        principal,
        action="mcp_server.updated",
        resource_type="mcp_server",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={"changed_fields": sorted(body.model_fields_set)},
    )
    return _to_out(row, set(container.mcp_manager.status()), container)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str,
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
) -> None:
    row = await _managed_row_or_404(session, principal, server_id)
    resource_name = row.name
    project_id = row.project_id
    await container.mcp_manager.disconnect(server_id)
    await McpServerRepo(session).delete(row)
    await record_audit(
        session,
        principal,
        action="mcp_server.deleted",
        resource_type="mcp_server",
        resource_id=server_id,
        resource_name=resource_name,
        project_id=project_id,
    )


@router.put("/{server_id}/catalog", response_model=McpServerOut)
async def bind_catalog_version(
    server_id: str,
    body: McpCatalogBindingUpdate,
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
) -> McpServerOut:
    row = await _managed_row_or_404(session, principal, server_id)
    catalog_entry_id: str | None = None
    catalog_version_id: str | None = None
    if body.version_id is not None:
        version = await McpCatalogRepo(session).get_version(body.version_id)
        if version is None or version.status != "approved":
            raise HTTPException(status_code=409, detail="MCP server requires an approved catalog version")
        try:
            validate_catalog_binding(row, version)
        except McpCatalogPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        catalog_entry_id = version.entry_id
        catalog_version_id = version.id

    previous_version_id = row.catalog_version_id
    await container.mcp_manager.disconnect(server_id)
    await McpServerRepo(session).update(
        row,
        catalog_entry_id=catalog_entry_id,
        catalog_version_id=catalog_version_id,
        tools_cache_json=[],
        tools_cached_at=None,
        last_status="catalog binding changed",
    )
    await record_audit(
        session,
        principal,
        action="mcp_server.catalog_bound" if catalog_version_id else "mcp_server.catalog_unbound",
        resource_type="mcp_server",
        resource_id=row.id,
        resource_name=row.name,
        project_id=row.project_id,
        details={
            "previous_version_id": previous_version_id,
            "catalog_entry_id": catalog_entry_id,
            "catalog_version_id": catalog_version_id,
        },
    )
    return _to_out(row, set(container.mcp_manager.status()), container)


@router.get("/{server_id}/tools", response_model=list[McpToolOut])
async def list_server_tools(
    server_id: str,
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
    refresh: Annotated[bool, Query()] = False,
) -> list[McpToolOut]:
    row = await _managed_row_or_404(session, principal, server_id)
    project_id = row.project_id
    # Capture before rollback; ORM attributes may expire after the session ends.
    release_after = refresh or not (row.tools_cache_json or [])
    await session.rollback()
    # Cache hits stay connection-free. Live discovery is control-plane only and
    # must not leave long-lived execution sessions on the API process.
    try:
        if project_id is None:
            tools = await container.mcp_manager.list_tools(server_id, refresh=refresh)
        else:
            tools = await container.mcp_manager.list_tools(
                server_id, refresh=refresh, project_id=project_id
            )
    except ProjectQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except McpConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if release_after:
            await container.mcp_manager.disconnect(server_id)
    return [McpToolOut.model_validate(tool) for tool in tools]


@router.post("/{server_id}/test", response_model=McpTestOut)
async def test_server(
    server_id: str,
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
) -> McpTestOut:
    row = await _managed_row_or_404(session, principal, server_id)
    project_id = row.project_id
    await session.rollback()
    try:
        if project_id is None:
            tools = await container.mcp_manager.list_tools(server_id, refresh=True)
        else:
            tools = await container.mcp_manager.list_tools(
                server_id, refresh=True, project_id=project_id
            )
    except ProjectQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except McpConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        # Control-plane connectivity checks are short-lived; workers own runtime sessions.
        await container.mcp_manager.disconnect(server_id)
    return McpTestOut(tools=[McpToolOut.model_validate(tool) for tool in tools])


@router.get("/{server_id}/health")
async def health_server(
    server_id: str,
    session: SessionDep,
    container: ContainerDep,
    principal: ViewerDep,
) -> dict[str, Any]:
    """Probe an MCP server and return transport/tool health plus circuit state."""
    row = await McpServerRepo(session).get(server_id)
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    await authorize_project_scope(session, principal, row.project_id, required=Role.VIEWER)
    project_id = row.project_id
    await session.rollback()
    try:
        return await container.mcp_manager.health_check(server_id, project_id=project_id)
    finally:
        await container.mcp_manager.disconnect(server_id)
