"""Idempotent official workflow template seeds."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import WorkflowTemplateRepo
from app.services.seeds import SEED_WORKFLOWS

_METADATA: tuple[dict[str, Any], ...] = (
    {
        "id": "official-linear",
        "category": "starter",
        "tags": ["agent", "linear", "starter"],
        "parameters": [
            {
                "name": "system_prompt",
                "label": "System prompt",
                "type": "string",
                "required": True,
                "default": "你是简洁的助手,用两句话回答。",
                "description": "Agent behavior for the generated workflow",
            }
        ],
    },
    {
        "id": "official-condition",
        "category": "routing",
        "tags": ["agent", "condition", "routing"],
        "parameters": [],
    },
    {
        "id": "official-supervisor",
        "category": "multi-agent",
        "tags": ["agent", "supervisor", "multi-agent"],
        "parameters": [],
    },
    {
        "id": "official-iteration",
        "category": "data-processing",
        "tags": ["agent", "iteration", "batch"],
        "parameters": [],
    },
    {
        "id": "official-http",
        "category": "integration",
        "tags": ["http", "integration", "outbound"],
        "parameters": [],
    },
    {
        "id": "official-code",
        "category": "data-processing",
        "tags": ["code", "transform", "sandbox"],
        "parameters": [],
    },
    {
        "id": "official-switch",
        "category": "routing",
        "tags": ["switch", "routing", "multi-branch"],
        "parameters": [],
    },
    {
        "id": "official-subworkflow",
        "category": "orchestration",
        "tags": ["subworkflow", "orchestration", "composition"],
        "parameters": [
            {
                "name": "child_workflow_id",
                "label": "Child workflow id",
                "type": "string",
                "required": True,
                "default": "",
                "description": "Published workflow id to embed as a child graph.",
            }
        ],
    },
)


async def seed_workflow_templates(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    created = 0
    async with session_factory() as session:
        repo = WorkflowTemplateRepo(session)
        for seed, metadata in zip(SEED_WORKFLOWS, _METADATA, strict=True):
            template_id = str(metadata["id"])
            if await repo.get(template_id) is not None:
                continue
            dsl = deepcopy(seed["dsl"])
            if template_id == "official-linear":
                dsl["nodes"][1]["config"]["system_prompt"] = "${parameter.system_prompt}"
            elif template_id == "official-subworkflow":
                sub_node = next(n for n in dsl["nodes"] if n["type"] == "subworkflow")
                sub_node["config"]["workflow_id"] = "${parameter.child_workflow_id}"
                sub_node["config"]["version_id"] = ""
            await repo.create(
                template_id=template_id,
                name=str(seed["name"]),
                description=str(seed.get("description", "")),
                category=str(metadata["category"]),
                tags=list(metadata["tags"]),
                parameters=list(metadata["parameters"]),
                dsl=dsl,
                is_official=True,
            )
            created += 1
        await session.commit()
    return created


__all__ = ["seed_workflow_templates"]
