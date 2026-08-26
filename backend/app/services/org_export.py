"""Organization-level data export for compliance/offboarding (C7-5).

Streams a single JSON document containing the organization's workflows
(DSL + version history), recent executions (status + event detail), and
knowledge-base metadata. Knowledge-base *document bytes* are not embedded —
they can be large and are already individually downloadable via the KB API;
only their metadata (filename, mime, size, hash, status) is exported so the
package stays bounded.

The export is read-only and runs against a short-lived session; it never
mutates state. A frozen (deletion-requested) organization can still be
exported by a platform admin or org admin so the data is recoverable before
the purge window closes.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    App,
    Document,
    KnowledgeBase,
    OnlineSource,
    Organization,
    Project,
    ServiceAccount,
    Workflow,
)
from app.db.models.evaluation import (
    EvaluationRun,
)
from app.db.repositories.executions import ExecutionRepo
from app.db.repositories.workflow_versions import WorkflowVersionRepo

# Cap on execution event rows exported per execution, so a runaway event log
# cannot make the export unbounded.
_MAX_EVENTS_PER_EXECUTION = 500
# Cap on executions exported per workflow.
_MAX_EXECUTIONS_PER_WORKFLOW = 50


def _dt(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def _export_projects(session: AsyncSession, org_id: str) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                select(Project).where(Project.organization_id == org_id).order_by(Project.name)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "created_at": _dt(p.created_at),
            "updated_at": _dt(p.updated_at),
        }
        for p in rows
    ]


async def _export_apps(session: AsyncSession, project_ids: list[str]) -> list[dict[str, Any]]:
    if not project_ids:
        return []
    rows = list(
        (
            await session.execute(
                select(App).where(App.project_id.in_(project_ids)).order_by(App.name)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": a.id,
            "project_id": a.project_id,
            "name": a.name,
            "slug": a.slug,
            "type": a.type,
            "visibility": a.visibility,
            "status": a.status,
            "workflow_id": a.workflow_id,
            "published_version_id": a.published_version_id,
            "created_at": _dt(a.created_at),
            "updated_at": _dt(a.updated_at),
        }
        for a in rows
    ]


async def _export_workflows(
    session: AsyncSession, project_ids: list[str]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    if not project_ids:
        return [], [], []
    wf_rows = list(
        (
            await session.execute(
                select(Workflow)
                .where(Workflow.project_id.in_(project_ids))
                .order_by(Workflow.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    workflow_ids = [w.id for w in wf_rows]
    version_repo = WorkflowVersionRepo(session)
    execution_repo = ExecutionRepo(session)
    out: list[dict[str, Any]] = []
    version_ids: list[str] = []
    for wf in wf_rows:
        versions = await version_repo.list_for_workflow(wf.id)
        version_ids.extend(v.id for v in versions)
        executions = await execution_repo.list_for_workflow(
            wf.id, limit=_MAX_EXECUTIONS_PER_WORKFLOW
        )
        exec_payload: list[dict[str, Any]] = []
        for ex in executions:
            events = await execution_repo.list_events_after(ex.id, after_seq=0)
            exec_payload.append(
                {
                    "id": ex.id,
                    "status": ex.status,
                    "trigger_source": ex.trigger_source,
                    "workflow_version_id": ex.workflow_version_id,
                    "input_json": ex.input_json,
                    "output_json": ex.output_json,
                    "error": ex.error,
                    "started_at": _dt(ex.started_at),
                    "finished_at": _dt(ex.finished_at),
                    "events": [
                        {
                            "seq": ev.seq,
                            "event_type": ev.event_type,
                            "node_id": ev.node_id,
                            "payload_json": ev.payload_json,
                            "ts": _dt(ev.ts),
                        }
                        for ev in events[:_MAX_EVENTS_PER_EXECUTION]
                    ],
                }
            )
        out.append(
            {
                "id": wf.id,
                "project_id": wf.project_id,
                "name": wf.name,
                "description": wf.description,
                "dsl": wf.dsl_json,
                "version": wf.version,
                "is_archived": wf.is_archived,
                "created_at": _dt(wf.created_at),
                "updated_at": _dt(wf.updated_at),
                "versions": [
                    {
                        "id": v.id,
                        "number": v.number,
                        "status": v.status,
                        "name": v.name,
                        "description": v.description,
                        "dsl": v.dsl_json,
                        "change_summary": v.change_summary,
                        "published_at": _dt(v.published_at),
                        "archived_at": _dt(v.archived_at),
                        "created_at": _dt(v.created_at),
                    }
                    for v in versions
                ],
                "executions": exec_payload,
            }
        )
    return out, workflow_ids, version_ids


async def _export_knowledge(session: AsyncSession, project_ids: list[str]) -> list[dict[str, Any]]:
    if not project_ids:
        return []
    kb_rows = list(
        (
            await session.execute(
                select(KnowledgeBase)
                .where(KnowledgeBase.project_id.in_(project_ids))
                .order_by(KnowledgeBase.name)
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for kb in kb_rows:
        docs = list(
            (
                await session.execute(
                    select(Document).where(Document.kb_id == kb.id).order_by(Document.created_at)
                )
            )
            .scalars()
            .all()
        )
        sources = list(
            (
                await session.execute(
                    select(OnlineSource).where(OnlineSource.kb_id == kb.id)
                )
            )
            .scalars()
            .all()
        )
        out.append(
            {
                "id": kb.id,
                "project_id": kb.project_id,
                "name": kb.name,
                "description": kb.description,
                "embedding_model_id": kb.embedding_model_id,
                "chunk_size": kb.chunk_size,
                "chunk_overlap": kb.chunk_overlap,
                "split_strategy": kb.split_strategy,
                "parent_chunk": kb.parent_chunk,
                "retrieval_mode": kb.retrieval_mode,
                "rerank_enabled": kb.rerank_enabled,
                "rerank_model_id": kb.rerank_model_id,
                "created_at": _dt(kb.created_at),
                "updated_at": _dt(kb.updated_at),
                "documents": [
                    {
                        "id": d.id,
                        "filename": d.filename,
                        "mime_type": d.mime_type,
                        "size_bytes": d.size_bytes,
                        "content_sha256": d.content_sha256,
                        "status": d.status,
                        "chunk_count": d.chunk_count,
                        "error": d.error,
                        "created_at": _dt(d.created_at),
                        "updated_at": _dt(d.updated_at),
                    }
                    for d in docs
                ],
                "online_sources": [
                    {
                        "id": s.id,
                        "url": s.url,
                        "status": s.status,
                        "last_synced_at": _dt(s.last_synced_at),
                    }
                    for s in sources
                ],
            }
        )
    return out


async def _export_service_accounts(
    session: AsyncSession, project_ids: list[str]
) -> list[dict[str, Any]]:
    if not project_ids:
        return []
    rows = list(
        (
            await session.execute(
                select(ServiceAccount)
                .where(ServiceAccount.project_id.in_(project_ids))
                .order_by(ServiceAccount.name)
            )
        )
        .scalars()
        .all()
    )
    # Tokens are never exported — only the account metadata. Hashes/prefixes
    # are omitted too so the export carries no credential material.
    return [
        {
            "id": sa.id,
            "name": sa.name,
            "description": sa.description,
            "project_id": sa.project_id,
            "role": sa.role,
            "status": sa.status,
            "created_at": _dt(sa.created_at),
            "updated_at": _dt(sa.updated_at),
        }
        for sa in rows
    ]


async def _export_evaluations(
    session: AsyncSession, version_ids: list[str]
) -> list[dict[str, Any]]:
    if not version_ids:
        return []
    run_rows = list(
        (
            await session.execute(
                select(EvaluationRun)
                .where(EvaluationRun.workflow_version_id.in_(version_ids))
                .order_by(EvaluationRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "dataset_version_id": r.dataset_version_id,
            "workflow_version_id": r.workflow_version_id,
            "evaluator_type": r.evaluator_type,
            "status": r.status,
            "summary": r.summary_json,
            "error": r.error,
            "created_at": _dt(r.created_at),
            "started_at": _dt(r.started_at),
            "finished_at": _dt(r.finished_at),
        }
        for r in run_rows
    ]


async def build_org_export(session: AsyncSession, org: Organization) -> dict[str, Any]:
    """Assemble the full organization export document."""
    projects = await _export_projects(session, org.id)
    project_ids = [p["id"] for p in projects]
    workflows, workflow_ids, version_ids = await _export_workflows(session, project_ids)
    return {
        "schema": "agentcanvas.org-export/v1",
        "organization": {
            "id": org.id,
            "name": org.name,
            "slug": org.slug,
            "status": org.status,
            "deletion_status": org.deletion_status,
            "created_at": _dt(org.created_at),
            "updated_at": _dt(org.updated_at),
        },
        "projects": projects,
        "workflows": workflows,
        "knowledge_bases": await _export_knowledge(session, project_ids),
        "apps": await _export_apps(session, project_ids),
        "service_accounts": await _export_service_accounts(session, project_ids),
        "evaluation_runs": await _export_evaluations(session, version_ids),
        "meta": {
            "workflow_count": len(workflow_ids),
            "version_count": len(version_ids),
            "project_count": len(project_ids),
        },
    }


def serialize_org_export(payload: dict[str, Any]) -> bytes:
    """Serialize the export document as UTF-8 JSON."""
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


__all__ = ["build_org_export", "serialize_org_export"]
