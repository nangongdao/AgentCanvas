"""Cross-project resource ownership transfer (C7-4).

Moving a workflow, knowledge base, or application to another project is an
ownership change executed by an admin of both the source and target
organizations. The platform's project-scoping invariants must survive the
move, so each resource type refuses transfers that would strand references:

- workflow: every knowledge base referenced by its DSL (rag nodes and agent
  context scopes) must be global or already owned by the target project.
- knowledge base: no workflow in the source project may reference it.
- app: the app's bound workflow must already live in the target project
  (move the workflow first, then the app).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import App, KnowledgeBase, Project, Workflow
from app.schemas.dsl import NodeType, WorkflowDSL

ResourceType = Literal["workflow", "knowledge_base", "app"]


class TransferError(RuntimeError):
    """A transfer that would break project-scoping invariants."""


@dataclass(frozen=True)
class TransferResult:
    resource_type: ResourceType
    resource_id: str
    from_project_id: str | None
    to_project_id: str


def _validated_dsl(raw: object) -> WorkflowDSL | None:
    if not isinstance(raw, dict):
        return None
    try:
        return WorkflowDSL.model_validate(raw)
    except Exception:  # noqa: BLE001 - a broken snapshot cannot be scanned
        return None


def _referenced_kb_ids(dsl: WorkflowDSL) -> set[str]:
    kb_ids: set[str] = set()
    for node in dsl.nodes:
        config = node.config or {}
        if node.type == NodeType.RAG:
            kb_id = str(config.get("kb_id") or "")
            if kb_id:
                kb_ids.add(kb_id)
        elif node.type == NodeType.AGENT:
            scopes = config.get("context_nodes")
            if isinstance(scopes, list):
                kb_ids.update(str(item) for item in scopes if item)
    return kb_ids


async def _kb_ownership(session: AsyncSession, kb_ids: set[str]) -> dict[str, str | None]:
    rows = (
        await session.execute(
            select(KnowledgeBase.id, KnowledgeBase.project_id).where(
                KnowledgeBase.id.in_(kb_ids or {"-"})
            )
        )
    ).all()
    return {kb_id: project_id for kb_id, project_id in rows}


async def transfer_resource(
    session: AsyncSession,
    *,
    resource_type: ResourceType,
    resource_id: str,
    target_project_id: str,
) -> TransferResult:
    target = await session.get(Project, target_project_id)
    if target is None:
        raise KeyError(target_project_id)

    if resource_type == "workflow":
        workflow = await session.get(Workflow, resource_id)
        if workflow is None:
            raise KeyError(resource_id)
        source_project_id = workflow.project_id
        if source_project_id == target_project_id:
            raise TransferError("workflow already belongs to the target project")
        dsl = _validated_dsl(workflow.dsl_json)
        if dsl is not None:
            referenced = _referenced_kb_ids(dsl)
            ownership = await _kb_ownership(session, referenced)
            stranded = sorted(
                kb_id
                for kb_id, project_id in ownership.items()
                if project_id is not None and project_id != target_project_id
            )
            if stranded:
                raise TransferError(
                    "workflow references project-scoped knowledge bases that do not "
                    "belong to the target project: " + ", ".join(stranded)
                )
        workflow.project_id = target_project_id
        await session.flush()
        return TransferResult("workflow", resource_id, source_project_id, target_project_id)

    if resource_type == "knowledge_base":
        kb = await session.get(KnowledgeBase, resource_id)
        if kb is None:
            raise KeyError(resource_id)
        source_project_id = kb.project_id
        if source_project_id == target_project_id:
            raise TransferError("knowledge base already belongs to the target project")
        referencing: list[str] = []
        workflows = (
            await session.scalars(
                select(Workflow).where(
                    Workflow.project_id == source_project_id,
                    Workflow.is_archived.is_(False),
                )
            )
        ).all()
        for workflow in workflows:
            dsl = _validated_dsl(workflow.dsl_json)
            if dsl is None:
                # An unparseable snapshot might reference the KB; refuse to
                # move rather than risk stranding it silently.
                referencing.append(workflow.name)
                continue
            if resource_id in _referenced_kb_ids(dsl):
                referencing.append(workflow.name)
        if referencing:
            raise TransferError(
                "knowledge base is referenced by source-project workflows: "
                + ", ".join(sorted(set(referencing)))
            )
        kb.project_id = target_project_id
        await session.flush()
        return TransferResult("knowledge_base", resource_id, source_project_id, target_project_id)

    app = await session.get(App, resource_id)
    if app is None:
        raise KeyError(resource_id)
    source_project_id = app.project_id
    if source_project_id == target_project_id:
        raise TransferError("app already belongs to the target project")
    if app.workflow_id is not None:
        workflow = await session.get(Workflow, app.workflow_id)
        if workflow is not None and workflow.project_id != target_project_id:
            raise TransferError(
                "app's bound workflow does not belong to the target project; "
                "transfer the workflow first"
            )
    app.project_id = target_project_id
    await session.flush()
    return TransferResult("app", resource_id, source_project_id, target_project_id)


__all__ = ["ResourceType", "TransferError", "TransferResult", "transfer_resource"]
