"""Production loader that resolves a published subworkflow version from DB.

Used by the runtime ExecutionEngine: the runner calls it during the async
pre-resolution pass to pull a referenced version's immutable ``dsl_json`` so
the sync compiler can inline the child graph. Only ``published`` versions are
resolvable — subworkflow nodes always reference an immutable snapshot.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.repositories import WorkflowVersionRepo
from app.engine.subworkflow_resolver import SubworkflowLoader
from app.schemas.dsl import WorkflowDSL

logger = logging.getLogger(__name__)


def build_subworkflow_loader(
    session_factory: async_sessionmaker[AsyncSession],
) -> SubworkflowLoader:
    """Return an async loader that fetches published versions by id.

    An empty ``version_id`` resolves the workflow's current published version
    (the one with ``status='published'``), so canvas seeds and templates can
    reference a workflow by id without pinning an opaque UUID.
    """

    async def load(workflow_id: str, version_id: str, _ancestors: frozenset[str]) -> WorkflowDSL | None:
        async with session_factory() as session:
            repo = WorkflowVersionRepo(session)
            if version_id:
                version = await repo.get(version_id)
            else:
                versions = await repo.list_for_workflow(workflow_id)
                version = next((v for v in versions if v.status == "published"), None)
        if version is None:
            logger.warning(
                "subworkflow version not found: workflow=%s version=%s",
                workflow_id,
                version_id or "<current>",
            )
            return None
        if version.workflow_id != workflow_id:
            logger.warning(
                "subworkflow version %s belongs to workflow %s, not %s",
                version.id,
                version.workflow_id,
                workflow_id,
            )
            return None
        if version.status != "published":
            logger.warning(
                "subworkflow version %s is not published (status=%s)",
                version.id,
                version.status,
            )
            return None
        try:
            return WorkflowDSL.model_validate(version.dsl_json)
        except Exception:  # noqa: BLE001 - surface as missing, not a crash
            logger.exception(
                "subworkflow version %s has an invalid DSL payload", version.id
            )
            return None

    return load


__all__ = ["build_subworkflow_loader"]
