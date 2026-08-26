"""Published workflow APIs and their public execution endpoint."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    EditorDep,
    ViewerDep,
    get_api_token_principal,
    get_container,
    get_session,
)
from app.api.workflow_access import authorize_workflow
from app.core.auth import Principal, Role, _sha256
from app.core.container import ServiceContainer
from app.db.models import ApiToken, WorkflowApiPublication, WorkflowVersion
from app.db.repositories import (
    ApiTokenRepo,
    ServiceAccountRepo,
    WorkflowApiPublicationRepo,
    WorkflowVersionRepo,
)
from app.engine.execution_errors import EngineShuttingDown
from app.engine.executor import ExecutionConcurrencyLimit
from app.engine.input_validation import (
    WorkflowInputError,
    input_definitions,
    validate_execution_inputs,
)
from app.schemas.api import (
    WorkflowApiInvokeOut,
    WorkflowApiIssueOut,
    WorkflowApiPublicationCreate,
    WorkflowApiPublicationOut,
)
from app.schemas.dsl import VariableType, WorkflowDSL
from app.services.audit import record_audit
from app.services.project_quotas import ProjectQuotaExceeded

router = APIRouter(tags=["workflow APIs"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
ApiPrincipalDep = Annotated[Principal, Depends(get_api_token_principal)]


def _credential() -> str:
    return secrets.token_urlsafe(32)


def _json_type(variable_type: VariableType) -> dict[str, Any]:
    schemas: dict[VariableType, dict[str, Any]] = {
        VariableType.STRING: {"type": "string"},
        VariableType.NUMBER: {"type": "number"},
        VariableType.BOOLEAN: {"type": "boolean"},
        VariableType.OBJECT: {"type": "object"},
        VariableType.ARRAY: {"type": "array", "items": {}},
    }
    return schemas[variable_type]


def _openapi(workflow_id: str, version: WorkflowVersion) -> dict[str, Any]:
    definitions = input_definitions(WorkflowDSL.model_validate(version.dsl_json))
    properties: dict[str, dict[str, Any]] = {}
    for name, definition in definitions.items():
        properties[name] = _json_type(definition.type)
        if definition.default is not None:
            properties[name]["default"] = definition.default
    required = [name for name, definition in definitions.items() if definition.required and definition.default is None]
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return {
        "openapi": "3.1.0",
        "info": {"title": f"{version.name} API", "version": str(version.number)},
        "paths": {
            f"/api/workflow-apis/{workflow_id}/execute": {
                "post": {
                    "operationId": f"execute_{workflow_id}",
                    "security": [{"bearerAuth": []}],
                    "x-agentcanvas-output-schema": {"$ref": "#/components/schemas/WorkflowOutput"},
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WorkflowInput"}}},
                    },
                    "responses": {
                        "202": {
                            "description": "Execution queued",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WorkflowApiInvokeOut"}}},
                        }
                    },
                }
            }
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "WorkflowInput": schema,
                "WorkflowOutput": _output_schema(version),
                "WorkflowApiInvokeOut": {
                    "type": "object",
                    "required": ["execution_id", "status", "accepted_version_id", "input_json"],
                    "properties": {
                        "execution_id": {"type": "string"},
                        "status": {"type": "string", "const": "queued"},
                        "accepted_version_id": {"type": "string"},
                        "input_json": {"type": "object"},
                    },
                },
            },
        },
    }


def _output_schema(version: WorkflowVersion) -> dict[str, Any]:
    dsl = WorkflowDSL.model_validate(version.dsl_json)
    properties: dict[str, dict[str, Any]] = {}
    for node in dsl.nodes:
        if node.type != "end":
            continue
        template = dict((node.config or {}).get("output_template") or {})
        properties.update({key: {} for key in template})
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if properties:
        schema["required"] = list(properties)
    return schema


def _example_input(version: WorkflowVersion) -> dict[str, Any]:
    values = {
        VariableType.STRING: "string",
        VariableType.NUMBER: 0,
        VariableType.BOOLEAN: False,
        VariableType.OBJECT: {},
        VariableType.ARRAY: [],
    }
    return {
        name: definition.default if definition.default is not None else values[definition.type]
        for name, definition in input_definitions(
            WorkflowDSL.model_validate(version.dsl_json)
        ).items()
    }


def _examples(workflow_id: str, version: WorkflowVersion) -> dict[str, str]:
    endpoint = f"/api/workflow-apis/{workflow_id}/execute"
    payload = json.dumps(_example_input(version), separators=(",", ":"))
    return {
        "curl": f'curl -X POST "$BASE_URL{endpoint}" -H "Authorization: Bearer $AGENTCANVAS_API_KEY" -H "Content-Type: application/json" -d \'{payload}\'',
        "python": f"import requests\nrequests.post(base_url + \"{endpoint}\", headers={{\"Authorization\": \"Bearer \" + api_key}}, json={_example_input(version)!r})",
        "javascript": f"await fetch(baseUrl + \"{endpoint}\", {{ method: \"POST\", headers: {{ Authorization: `Bearer ${{apiKey}}`, \"Content-Type\": \"application/json\" }}, body: JSON.stringify({payload}) }});",
    }


def _out(row: WorkflowApiPublication, version: WorkflowVersion, token_prefix: str) -> WorkflowApiPublicationOut:
    return WorkflowApiPublicationOut(
        id=row.id,
        workflow_id=row.workflow_id,
        published_version_id=row.published_version_id,
        published_version_number=version.number,
        service_account_id=row.service_account_id,
        api_token_id=row.api_token_id,
        token_prefix=token_prefix,
        status=row.status,
        endpoint=f"/api/workflow-apis/{row.workflow_id}/execute",
        last_triggered_at=row.last_triggered_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _version(session: AsyncSession, row: WorkflowApiPublication) -> WorkflowVersion:
    version = await WorkflowVersionRepo(session).get(row.published_version_id)
    if version is None or version.status != "published":
        raise HTTPException(status_code=409, detail="published workflow version is unavailable")
    return version


@router.get("/api/workflows/{workflow_id}/api", response_model=WorkflowApiPublicationOut)
async def get_workflow_api(workflow_id: str, session: SessionDep, principal: ViewerDep) -> WorkflowApiPublicationOut:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    row = await WorkflowApiPublicationRepo(session).get_for_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow API is not published")
    version = await _version(session, row)
    token = await session.get(ApiToken, row.api_token_id)
    if token is None:
        raise HTTPException(status_code=500, detail="workflow API token is missing")
    return _out(row, version, token.prefix)


@router.get("/api/workflows/{workflow_id}/api/openapi")
async def get_workflow_api_openapi(workflow_id: str, session: SessionDep, principal: ViewerDep) -> dict[str, Any]:
    await authorize_workflow(session, principal, workflow_id, required=Role.VIEWER)
    row = await WorkflowApiPublicationRepo(session).get_for_workflow(workflow_id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow API is not published")
    return _openapi(workflow_id, await _version(session, row))


@router.post("/api/workflows/{workflow_id}/api", response_model=WorkflowApiIssueOut, status_code=201)
async def publish_workflow_api(
    workflow_id: str,
    body: WorkflowApiPublicationCreate,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowApiIssueOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    if workflow.project_id is None:
        raise HTTPException(status_code=409, detail="workflow API requires a project-scoped workflow")
    version = await WorkflowVersionRepo(session).get_by_number(workflow.id, workflow.version)
    if version is None or version.status != "published":
        raise HTTPException(status_code=409, detail="workflow must have a published version")
    accounts = ServiceAccountRepo(session)
    account = await accounts.get(body.service_account_id or "")
    if body.service_account_id is None:
        raise HTTPException(status_code=422, detail="service_account_id is required")
    if account is None or account.status != "active":
        raise HTTPException(status_code=404, detail="active service account not found")
    if account.project_id != workflow.project_id:
        raise HTTPException(status_code=403, detail="service account is outside workflow project")
    if account.role not in {"editor", "admin"}:
        raise HTTPException(status_code=403, detail="service account editor role required")
    repo = WorkflowApiPublicationRepo(session)
    existing = await repo.get_for_workflow(workflow.id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="workflow API already published")
    raw = _credential()
    token = await ApiTokenRepo(session).create(
        account.id,
        f"workflow-api:{workflow.id}",
        _sha256(raw),
        raw[:12],
        None,
        "execution",
    )
    row = await repo.create(workflow.id, version.id, account.id, token.id)
    await record_audit(
        session,
        principal,
        action="workflow_api.published",
        resource_type="workflow_api_publication",
        resource_id=row.id,
        resource_name=workflow.name,
        project_id=workflow.project_id,
        details={"workflow_id": workflow.id, "version": version.number, "service_account_id": account.id},
    )
    return WorkflowApiIssueOut(
        publication=_out(row, version, token.prefix),
        token=raw,
        endpoint=f"/api/workflow-apis/{workflow.id}/execute",
        openapi=_openapi(workflow.id, version),
        examples=_examples(workflow.id, version),
    )


@router.put("/api/workflows/{workflow_id}/api", response_model=WorkflowApiIssueOut)
async def rotate_workflow_api(
    workflow_id: str,
    body: WorkflowApiPublicationCreate,
    session: SessionDep,
    principal: EditorDep,
) -> WorkflowApiIssueOut:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    version = await WorkflowVersionRepo(session).get_by_number(workflow.id, workflow.version)
    if version is None or version.status != "published":
        raise HTTPException(status_code=409, detail="workflow must have a published version")
    row = await WorkflowApiPublicationRepo(session).get_for_workflow(workflow.id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow API is not published")
    account = await ServiceAccountRepo(session).get(body.service_account_id or row.service_account_id)
    if (
        account is None
        or account.status != "active"
        or account.project_id != workflow.project_id
        or account.role not in {"editor", "admin"}
    ):
        raise HTTPException(status_code=403, detail="active service account must belong to workflow project")
    old = await session.get(ApiToken, row.api_token_id)
    if old is not None:
        old.revoked_at = datetime.now(UTC)
    raw = _credential()
    token = await ApiTokenRepo(session).create(
        account.id,
        f"workflow-api:{workflow.id}",
        _sha256(raw),
        raw[:12],
        None,
        "execution",
    )
    await WorkflowApiPublicationRepo(session).rotate(
        row,
        published_version_id=version.id,
        service_account_id=account.id,
        api_token_id=token.id,
    )
    await record_audit(session, principal, action="workflow_api.rotated", resource_type="workflow_api_publication", resource_id=row.id, resource_name=workflow.name, project_id=workflow.project_id, details={"workflow_id": workflow.id, "version": version.number, "service_account_id": account.id})
    return WorkflowApiIssueOut(publication=_out(row, version, token.prefix), token=raw, endpoint=f"/api/workflow-apis/{workflow.id}/execute", openapi=_openapi(workflow.id, version), examples=_examples(workflow.id, version))


@router.delete("/api/workflows/{workflow_id}/api", status_code=204)
async def disable_workflow_api(workflow_id: str, session: SessionDep, principal: EditorDep) -> None:
    workflow = await authorize_workflow(session, principal, workflow_id, required=Role.EDITOR)
    row = await WorkflowApiPublicationRepo(session).get_for_workflow(workflow.id)
    if row is None:
        raise HTTPException(status_code=404, detail="workflow API is not published")
    token = await session.get(ApiToken, row.api_token_id)
    if token is not None:
        token.revoked_at = datetime.now(UTC)
    await WorkflowApiPublicationRepo(session).disable(row)
    await record_audit(session, principal, action="workflow_api.disabled", resource_type="workflow_api_publication", resource_id=row.id, resource_name=workflow.name, project_id=workflow.project_id, details={"workflow_id": workflow.id})


@router.post("/api/workflow-apis/{workflow_id}/execute", response_model=WorkflowApiInvokeOut, status_code=202)
async def invoke_workflow_api(
    workflow_id: str,
    request: Request,
    container: ContainerDep,
    session: SessionDep,
    principal: ApiPrincipalDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkflowApiInvokeOut:
    row = await WorkflowApiPublicationRepo(session).get_for_workflow(workflow_id)
    if row is None or row.status != "active" or row.api_token_id != principal.api_token_id:
        raise HTTPException(status_code=404, detail="workflow API not found")
    if principal.service_account_id != row.service_account_id or principal.project_id is None:
        raise HTTPException(status_code=403, detail="API token is not bound to this workflow")
    version = await _version(session, row)
    version_id = version.id
    try:
        raw = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="request body must be a JSON object")
    try:
        inputs = validate_execution_inputs(WorkflowDSL.model_validate(version.dsl_json), raw)
        await session.rollback()
        execution_id = await container.execution_engine.start_version(
            version_id,
            inputs,
            idempotency_key=idempotency_key,
            trigger_source="api",
        )
    except WorkflowInputError as exc:
        raise HTTPException(status_code=422, detail=exc.errors) from exc
    except EngineShuttingDown as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ExecutionConcurrencyLimit as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ProjectQuotaExceeded as exc:
        await container.workflow_callback_dispatcher.enqueue_quota_alert_safely(
            workflow_id=workflow_id,
            project_id=principal.project_id,
            metric=exc.metric,
            limit=exc.limit,
            usage=exc.usage,
            requested=exc.requested,
        )
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    async with container.session_factory() as touch_session:
        touched = await WorkflowApiPublicationRepo(touch_session).get_for_workflow(workflow_id)
        if touched is not None:
            await WorkflowApiPublicationRepo(touch_session).touch_triggered(touched)
            await touch_session.commit()
    return WorkflowApiInvokeOut(execution_id=execution_id, accepted_version_id=version_id, input_json=inputs)


__all__ = ["router"]
