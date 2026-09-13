"""Workflow AI Copilot route — turn a natural-language request into a draft."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import EditorDep, get_container, get_session
from app.api.tenant_deps import authorize_project_scope
from app.core.auth import Role
from app.core.container import ServiceContainer
from app.db.repositories import ModelConfigRepo
from app.schemas.api import CopilotDraftOut, CopilotDraftRequest, CopilotUsageOut
from app.services.workflow_copilot import WorkflowCopilotError, draft_workflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows/copilot", tags=["copilot"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


@router.post("/draft", response_model=CopilotDraftOut)
async def draft_workflow_endpoint(
    body: CopilotDraftRequest,
    session: SessionDep,
    container: ContainerDep,
    principal: EditorDep,
) -> CopilotDraftOut:
    """Draft a workflow from a prompt and return it with the validator's verdict.

    The draft is never persisted: the editor applies it (or not) through the
    normal save path, so the copilot cannot bypass validation or audit.
    """
    await authorize_project_scope(session, principal, body.project_id, required=Role.EDITOR)
    try:
        provider = await container.execution_engine.load_provider(body.model_config_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"model '{body.model_config_id}' not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    chat_model_ids = {
        row.id for row in await ModelConfigRepo(session).list() if row.kind == "chat"
    }
    try:
        draft = await draft_workflow(
            provider,
            prompt=body.prompt,
            base_dsl=body.base_dsl,
            available_model_ids=chat_model_ids,
        )
    except WorkflowCopilotError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - an upstream Provider outage is 502, not 500
        logger.exception("copilot provider call failed")
        raise HTTPException(
            status_code=502, detail=f"copilot provider call failed: {type(exc).__name__}"
        ) from exc

    return CopilotDraftOut(
        dsl=draft.dsl,
        name=draft.name,
        valid=draft.valid,
        errors=draft.errors,
        warnings=draft.warnings,
        attempts=draft.attempts,
        provider=draft.provider,
        model=draft.model,
        usage=CopilotUsageOut(
            prompt_tokens=draft.prompt_tokens,
            completion_tokens=draft.completion_tokens,
        ),
    )
