"""Misc routes: node-types, models, meta."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.deps import AdminDep, ViewerDep, get_container, get_session
from app.api.etag import etag_json_response
from app.core.container import ServiceContainer
from app.core.provider_capabilities import ProviderCapabilities
from app.core.resilience import ResilienceBackendUnavailable
from app.core.secret_providers import SecretProviderError
from app.db.models import ModelConfig
from app.db.repositories import ModelConfigRepo
from app.engine.nodes import list_node_types
from app.providers import PROVIDERS
from app.schemas.api import (
    MetaOut,
    ModelConfigCreate,
    ModelConfigOut,
    ModelConfigUpdate,
)
from app.schemas.provider_capabilities import (
    ProviderCapabilitiesOut,
    ProviderDescriptorOut,
)
from app.services.audit import record_audit
from app.services.model_capabilities import (
    ModelCapabilityError,
    effective_model_capabilities,
    normalize_model_capability_overrides,
    provider_capabilities,
)
from app.services.model_dependencies import (
    changes_embedding_behavior,
    dependent_knowledge_base_ids,
    invalidate_knowledge_indexes,
)

router = APIRouter(prefix="/api", tags=["meta"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


@router.get("/node-types")
async def node_types(request: Request, _principal: ViewerDep, _container: ContainerDep) -> Response:
    # C6-4: static per-process payload polled by the canvas — 304 when unchanged.
    return etag_json_response(request, list_node_types())


def _to_out(row: ModelConfig, container: ServiceContainer) -> ModelConfigOut:
    capability_error: str | None = None
    try:
        capabilities = effective_model_capabilities(row)
    except ModelCapabilityError as exc:
        capabilities = ProviderCapabilities()
        capability_error = str(exc)
    return ModelConfigOut(
        id=row.id,
        name=row.name,
        provider=row.provider,
        model_name=row.model_name,
        base_url=row.base_url,
        kind=row.kind,
        is_default=row.is_default,
        has_api_key=bool(row.api_key_encrypted),
        api_key_source=container.secret_resolver.source(row.api_key_encrypted),
        prompt_price_per_million_usd=row.prompt_price_per_million_usd,
        completion_price_per_million_usd=row.completion_price_per_million_usd,
        pricing_version=row.pricing_version,
        capability_overrides=dict(row.capabilities_json or {}),
        capabilities=ProviderCapabilitiesOut(**capabilities.to_dict()),
        capability_error=capability_error,
    )


@router.get("/models", response_model=list[ModelConfigOut])
async def list_models(
    session: SessionDep, container: ContainerDep, _principal: ViewerDep
) -> list[ModelConfigOut]:
    rows = await ModelConfigRepo(session).list()
    return [_to_out(r, container) for r in rows]


@router.get("/models/providers", response_model=list[str])
async def list_providers(request: Request, _principal: ViewerDep) -> Response:
    return etag_json_response(request, sorted(PROVIDERS))


@router.get("/resilience")
async def resilience_status(
    container: ContainerDep,
    _principal: ViewerDep,
) -> dict[str, Any]:
    """Return provider/MCP circuit state without exposing configuration secrets."""
    try:
        resources = await container.resilience.snapshots_async()
    except ResilienceBackendUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "resources": resources,
        "summary": {
            "total": len(resources),
            "open": sum(item["state"] == "open" for item in resources),
            "half_open": sum(item["state"] == "half_open" for item in resources),
        },
    }


@router.get(
    "/models/provider-capabilities",
    response_model=list[ProviderDescriptorOut],
)
async def list_provider_capabilities(
    _principal: ViewerDep,
) -> list[ProviderDescriptorOut]:
    return [
        ProviderDescriptorOut(
            id=provider_name,
            capabilities=ProviderCapabilitiesOut(**provider_capabilities(provider_name).to_dict()),
        )
        for provider_name in sorted(PROVIDERS)
    ]


@router.post("/models", response_model=ModelConfigOut, status_code=201)
async def create_model(
    body: ModelConfigCreate,
    session: SessionDep,
    container: ContainerDep,
    principal: AdminDep,
) -> ModelConfigOut:
    if body.provider not in PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown provider '{body.provider}'. available: {sorted(PROVIDERS)}",
        )
    repo = ModelConfigRepo(session)
    model_id = body.id or uuid4().hex
    if await repo.get(model_id) is not None:
        raise HTTPException(status_code=409, detail=f"model '{model_id}' already exists")
    if body.is_default:
        await repo.unset_defaults(body.kind)
    try:
        capability_overrides = normalize_model_capability_overrides(
            body.provider,
            body.kind,
            body.capability_overrides.compact(),
        )
    except ModelCapabilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        api_key_encrypted = container.secret_resolver.encrypt(body.api_key or "")
    except SecretProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = ModelConfig(
        id=model_id,
        name=body.name,
        provider=body.provider,
        model_name=body.model_name,
        base_url=body.base_url,
        api_key_encrypted=api_key_encrypted,
        params_json=body.params,
        capabilities_json=capability_overrides,
        prompt_price_per_million_usd=(
            format(body.prompt_price_per_million_usd, "f")
            if body.prompt_price_per_million_usd is not None
            else None
        ),
        completion_price_per_million_usd=(
            format(body.completion_price_per_million_usd, "f")
            if body.completion_price_per_million_usd is not None
            else None
        ),
        pricing_version=body.pricing_version,
        kind=body.kind,
        is_default=body.is_default,
    )
    await repo.upsert(row)
    await record_audit(
        session,
        principal,
        action="model.created",
        resource_type="model",
        resource_id=row.id,
        resource_name=row.name,
        details={
            "provider": row.provider,
            "kind": row.kind,
            "has_key": bool(body.api_key),
            "is_default": row.is_default,
        },
    )
    await session.commit()
    return _to_out(row, container)


@router.put("/models/{model_id}", response_model=ModelConfigOut)
async def update_model(
    model_id: str,
    body: ModelConfigUpdate,
    session: SessionDep,
    container: ContainerDep,
    principal: AdminDep,
) -> ModelConfigOut:
    repo = ModelConfigRepo(session)
    row = await repo.get(model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="model not found")
    if body.provider is not None and body.provider not in PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown provider '{body.provider}'. available: {sorted(PROVIDERS)}",
        )
    if body.is_default:
        await repo.unset_defaults(body.kind or row.kind)
    knowledge_base_ids = await dependent_knowledge_base_ids(session, model_id)
    next_kind = body.kind or row.kind
    if knowledge_base_ids and next_kind != "embedding":
        raise HTTPException(
            status_code=409,
            detail="model is used by knowledge bases and must remain an embedding model",
        )
    invalidates_indexes = bool(knowledge_base_ids) and changes_embedding_behavior(
        current_kind=row.kind,
        next_kind=next_kind,
        changed_fields=body.model_fields_set,
    )
    requested_overrides = (
        body.capability_overrides.compact()
        if "capability_overrides" in body.model_fields_set and body.capability_overrides is not None
        else dict(row.capabilities_json or {})
    )
    try:
        capability_overrides = normalize_model_capability_overrides(
            body.provider or row.provider,
            next_kind,
            requested_overrides,
        )
    except ModelCapabilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for field in ("name", "provider", "model_name", "base_url", "kind", "is_default"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    if body.params is not None:
        row.params_json = body.params
    row.capabilities_json = capability_overrides
    for field in ("prompt_price_per_million_usd", "completion_price_per_million_usd"):
        if field in body.model_fields_set:
            value = getattr(body, field)
            setattr(row, field, format(value, "f") if value is not None else None)
    if "pricing_version" in body.model_fields_set:
        row.pricing_version = body.pricing_version or None
    if body.api_key is not None:
        try:
            row.api_key_encrypted = container.secret_resolver.encrypt(body.api_key)
        except SecretProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if invalidates_indexes:
        await invalidate_knowledge_indexes(session, knowledge_base_ids)
    await repo.upsert(row)
    await record_audit(
        session,
        principal,
        action="model.updated",
        resource_type="model",
        resource_id=row.id,
        resource_name=row.name,
        details={
            "changed_fields": sorted(body.model_fields_set),
            "key_changed": "api_key" in body.model_fields_set,
            "invalidated_index_count": len(knowledge_base_ids) if invalidates_indexes else 0,
        },
    )
    await session.commit()
    await container.execution_engine.invalidate_provider(model_id)
    if invalidates_indexes:
        for knowledge_base_id in knowledge_base_ids:
            await container.rag_service.vector_store.delete_knowledge_base(knowledge_base_id)
    return _to_out(row, container)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(
    model_id: str,
    session: SessionDep,
    container: ContainerDep,
    principal: AdminDep,
) -> None:
    if await dependent_knowledge_base_ids(session, model_id):
        raise HTTPException(status_code=409, detail="model is used by knowledge bases")
    repo = ModelConfigRepo(session)
    row = await repo.get(model_id)
    if row is None:
        raise HTTPException(status_code=404, detail="model not found")
    resource_name = row.name
    await repo.delete(model_id)
    await record_audit(
        session,
        principal,
        action="model.deleted",
        resource_type="model",
        resource_id=model_id,
        resource_name=resource_name,
    )
    await session.commit()
    await container.execution_engine.invalidate_provider(model_id)


@router.get("/meta", response_model=MetaOut)
async def meta(container: ContainerDep) -> MetaOut:
    db = container.settings.effective_database_url
    kind = "postgres" if db.startswith("postgresql") else "sqlite"
    memory_backend = getattr(container.memory_store, "backend_name", "none")
    vector_backend = getattr(
        getattr(container.rag_service, "vector_store", None),
        "backend_name",
        "none",
    )
    sandbox = container.sandbox.describe()
    return MetaOut(
        app="agentcanvas",
        version=__version__,
        memory_backend=memory_backend,
        vector_backend=vector_backend,
        database=kind,
        environment=container.settings.environment,
        auth_enabled=container.auth_service.enabled,
        oidc_enabled=container.oidc_client.enabled,
        sandbox_backend=str(sandbox.get("backend", "process_cleanup")),
        sandbox_degraded=bool(sandbox.get("degraded", True)),
    )
