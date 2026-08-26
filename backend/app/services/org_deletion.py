"""Tenant data compliance: two-phase organization deletion (C7-5).

State machine driven by ``organizations.deletion_status``:

* ``none`` — default; the org is fully usable.
* ``requested`` — frozen (members lose access via ``_ensure_org_active``); all
  tenant data is retained for the grace window so an admin can cancel.
* ``purging`` — the sweep is in progress; rows are being removed.
* ``purged`` — terminal marker; the org row itself has been removed.

Cancelling within the grace window returns the org to ``none``. The purge
sweeps every tenant-owned row in dependency order so NO-ACTION and RESTRICT
foreign keys never block, then drops the organization itself.

``AuditLog`` rows survive by design: they carry no FK and record the purge
for compliance review. External state — pgvector/SQL chunks, checkpoint
threads, Redis collaboration/stream keys — is cleaned alongside the DB rows.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.actor import ActorIdentity
from app.core.auth import Principal, Role
from app.core.config import Settings
from app.db.models import (
    App,
    ChatSession,
    CostAlert,
    EvaluationRun,
    Execution,
    ExecutionEventRow,
    KnowledgeBase,
    Membership,
    Organization,
    OrganizationInvitation,
    Project,
    ServiceAccount,
    UsageDailyFact,
    WebhookTrigger,
    Workflow,
    WorkflowApiPublication,
    WorkflowCallback,
    WorkflowComment,
    WorkflowReview,
    WorkflowSchedule,
    WorkflowVersion,
)
from app.rag import RagService
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

ACTION_REQUESTED = "organization.deletion.requested"
ACTION_CANCELLED = "organization.deletion.cancelled"
ACTION_PURGED = "organization.deletion.purged"

# System principal used to attribute the purge audit row when the scheduler
# drives the sweep (no interactive principal is available).
_SYSTEM_PRINCIPAL = Principal("system:scheduler", Role.ADMIN, "system")

# Tables that reference a published workflow version via RESTRICT and would
# therefore block workflow/version deletion until they are removed first.
_WORKFLOW_DEPENDENT_ROWS: tuple[tuple[type, Any], ...] = (
    (WebhookTrigger, WebhookTrigger.workflow_id),
    (WorkflowApiPublication, WorkflowApiPublication.workflow_id),
    (WorkflowSchedule, WorkflowSchedule.workflow_id),
    (WorkflowCallback, WorkflowCallback.workflow_id),
    (WorkflowComment, WorkflowComment.workflow_id),
    (WorkflowReview, WorkflowReview.workflow_id),
)


class OrgDeletionConflict(ValueError):
    """Raised when a deletion transition is not allowed in the current state."""


def _chunks(items: list[str], size: int) -> list[list[str]]:
    size = max(1, int(size))
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _delete_in(
    session: AsyncSession,
    model: type,
    column: Any,
    ids: list[str],
    batch_size: int,
) -> int:
    """Delete rows where ``column`` matches any id, in bounded batches."""
    total = 0
    for chunk in _chunks(ids, batch_size):
        result = await session.execute(delete(model).where(column.in_(chunk)))
        total += int(getattr(result, "rowcount", 0) or 0)
    return total


async def _collect_org_resource_ids(
    session: AsyncSession, org_id: str
) -> dict[str, list[str]]:
    """Snapshot every tenant-owned id set before mutation begins.

    Captured up-front so the purge never depends on a project_id that a
    SET-NULL foreign key might null mid-sweep.
    """
    project_ids = list(
        (await session.execute(select(Project.id).where(Project.organization_id == org_id)))
        .scalars()
        .all()
    )
    workflow_ids: list[str] = []
    version_ids: list[str] = []
    execution_ids: list[str] = []
    app_ids: list[str] = []
    kb_ids: list[str] = []
    if project_ids:
        workflow_ids = list(
            (
                await session.execute(select(Workflow.id).where(Workflow.project_id.in_(project_ids)))
            )
            .scalars()
            .all()
        )
        app_ids = list(
            (await session.execute(select(App.id).where(App.project_id.in_(project_ids))))
            .scalars()
            .all()
        )
        kb_ids = list(
            (
                await session.execute(
                    select(KnowledgeBase.id).where(KnowledgeBase.project_id.in_(project_ids))
                )
            )
            .scalars()
            .all()
        )
    if workflow_ids:
        version_ids = list(
            (
                await session.execute(
                    select(WorkflowVersion.id).where(WorkflowVersion.workflow_id.in_(workflow_ids))
                )
            )
            .scalars()
            .all()
        )
        execution_ids = list(
            (
                await session.execute(
                    select(Execution.id).where(Execution.workflow_id.in_(workflow_ids))
                )
            )
            .scalars()
            .all()
        )
    return {
        "project_ids": project_ids,
        "workflow_ids": workflow_ids,
        "version_ids": version_ids,
        "execution_ids": execution_ids,
        "app_ids": app_ids,
        "kb_ids": kb_ids,
    }


async def request_org_deletion(
    session: AsyncSession,
    principal: Principal,
    org: Organization,
    *,
    grace_days: int,
) -> Organization:
    """Freeze an organization and schedule its purge for ``grace_days`` later."""
    if org.deletion_status != "none":
        raise OrgDeletionConflict(
            f"organization deletion already {org.deletion_status}"
        )
    now = datetime.now(UTC)
    days = max(0, int(grace_days))
    org.deletion_status = "requested"
    org.deletion_requested_at = now
    org.deletion_requested_by = ActorIdentity.from_principal(principal).key
    org.purge_due_at = now + timedelta(days=days)
    await session.flush()
    await record_audit(
        session,
        principal,
        action=ACTION_REQUESTED,
        resource_type="organization",
        resource_id=org.id,
        resource_name=org.name,
        organization_id=org.id,
        details={"grace_days": days, "purge_due_at": org.purge_due_at.isoformat()},
    )
    return org


async def cancel_org_deletion(
    session: AsyncSession,
    principal: Principal,
    org: Organization,
) -> Organization:
    """Revert a pending deletion; the org becomes fully usable again."""
    if org.deletion_status not in ("requested", "purging"):
        raise OrgDeletionConflict("organization is not pending deletion")
    previous = org.deletion_status
    org.deletion_status = "none"
    org.deletion_requested_at = None
    org.deletion_requested_by = None
    org.purge_due_at = None
    await session.flush()
    await record_audit(
        session,
        principal,
        action=ACTION_CANCELLED,
        resource_type="organization",
        resource_id=org.id,
        resource_name=org.name,
        organization_id=org.id,
        details={"previous_status": previous},
    )
    return org


async def _purge_checkpoints(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    thread_ids: list[str],
    batch_size: int,
) -> int:
    """Delete checkpoint thread state for the given executions (best-effort).

    SQLite stores checkpoints in a separate file (``checkpoints.db``);
    PostgreSQL stores the LangGraph tables in the main database. Either way
    ``thread_id`` equals the execution id, so a precise IN-clause delete
    clears the state without touching unrelated threads.
    """
    if not thread_ids:
        return 0
    try:
        if settings.database_backend == "postgresql":
            return await _purge_checkpoints_postgres(session_factory, thread_ids, batch_size)
        return await _purge_checkpoints_sqlite(settings, thread_ids, batch_size)
    except Exception:
        logger.exception("checkpoint cleanup failed for %d threads", len(thread_ids))
        return 0


async def _purge_checkpoints_postgres(
    session_factory: async_sessionmaker[AsyncSession],
    thread_ids: list[str],
    batch_size: int,
) -> int:
    # The LangGraph checkpoint tables live in the main PostgreSQL database;
    # reuse the ORM engine rather than opening a separate pool.
    engine = session_factory.kw.get("bind")
    if engine is None:
        return 0
    total = 0
    async with engine.begin() as conn:
        for chunk in _chunks(thread_ids, batch_size):
            for table_name in ("checkpoints", "checkpoint_writes"):
                stmt = text(
                    f"DELETE FROM {table_name} WHERE thread_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True))
                result = await conn.execute(stmt, {"ids": chunk})
                total += int(getattr(result, "rowcount", 0) or 0)
    return total


async def _purge_checkpoints_sqlite(
    settings: Settings,
    thread_ids: list[str],
    batch_size: int,
) -> int:
    import aiosqlite

    path = settings.checkpoint_db_path
    if not path.exists():
        return 0
    total = 0
    conn = await aiosqlite.connect(str(path))
    try:
        for chunk in _chunks(thread_ids, batch_size):
            placeholders = ",".join("?" for _ in chunk)
            for table_name in ("checkpoints", "checkpoint_writes"):
                try:
                    cursor = await conn.execute(
                        f"DELETE FROM {table_name} WHERE thread_id IN ({placeholders})",
                        chunk,
                    )
                    total += int(cursor.rowcount or 0)
                except aiosqlite.OperationalError as exc:
                    if "no such table" in str(exc).lower():
                        continue
                    raise
        await conn.commit()
    finally:
        await conn.close()
    return total


async def _purge_redis(
    settings: Settings,
    workflow_ids: list[str],
    execution_ids: list[str],
) -> int:
    """Delete collaboration and execution-stream Redis keys (best-effort)."""
    if not settings.redis_url:
        return 0
    if not workflow_ids and not execution_ids:
        return 0
    from app.core.collaboration_redis import (
        LOCK_PREFIX,
        PRESENCE_PREFIX,
        REVISION_PREFIX,
    )
    from app.engine.event_stream import (
        STREAM_PREFIX,
        close_redis_client,
        connect_event_stream_client,
    )

    client = await connect_event_stream_client(settings.redis_url)
    if client is None:
        return 0
    keys: list[str] = []
    for wid in workflow_ids:
        keys.append(f"{PRESENCE_PREFIX}{wid}")
        keys.append(f"{LOCK_PREFIX}{wid}")
        keys.append(f"{REVISION_PREFIX}{wid}")
    for eid in execution_ids:
        keys.append(f"{STREAM_PREFIX}{eid}")
    deleted = 0
    try:
        for chunk in _chunks(keys, 500):
            deleted += int(await client.delete(*chunk))
    except Exception:
        logger.exception("redis cleanup failed for org purge")
    finally:
        await close_redis_client(client)
    return deleted


async def _purge_db(
    session_factory: async_sessionmaker[AsyncSession],
    ids: dict[str, list[str]],
    org_id: str,
    batch_size: int,
) -> dict[str, int]:
    """Delete every tenant-owned row in dependency order.

    The order respects every NO-ACTION and RESTRICT foreign key so the sweep
    never deadlocks: workflow trigger family → evaluation runs → execution
    events → executions → chat sessions → workflow versions → workflows →
    service accounts → usage facts → cost alerts → projects → memberships →
    invitations. The org row itself is dropped by the caller after auditing.
    """
    summary: dict[str, int] = {}
    workflow_ids = ids["workflow_ids"]
    version_ids = ids["version_ids"]
    execution_ids = ids["execution_ids"]
    app_ids = ids["app_ids"]
    project_ids = ids["project_ids"]

    async with session_factory() as session:
        # Workflow-attached rows that would otherwise RESTRICT/NO-ACTION the
        # version or workflow deletion.
        for model, column in _WORKFLOW_DEPENDENT_ROWS:
            if workflow_ids:
                summary[model.__name__] = await _delete_in(
                    session, model, column, workflow_ids, batch_size
                )
        # Evaluation runs reference workflow versions via NO-ACTION.
        if version_ids:
            summary["EvaluationRun"] = await _delete_in(
                session, EvaluationRun, EvaluationRun.workflow_version_id, version_ids, batch_size
            )
        # Execution events reference executions via NO-ACTION.
        if execution_ids:
            summary["ExecutionEventRow"] = await _delete_in(
                session, ExecutionEventRow, ExecutionEventRow.execution_id, execution_ids, batch_size
            )
        # Executions: queue items, case_results.execution_id and
        # cost_alerts.execution_id cascade/SET-NULL automatically.
        if execution_ids:
            summary["Execution"] = await _delete_in(
                session, Execution, Execution.id, execution_ids, batch_size
            )
        # Chat sessions: workflow_id and app_id are bare/SET-NULL columns;
        # their child message/feedback/variable rows cascade on session delete.
        if workflow_ids:
            summary["ChatSessionByWorkflow"] = await _delete_in(
                session, ChatSession, ChatSession.workflow_id, workflow_ids, batch_size
            )
        if app_ids:
            summary["ChatSessionByApp"] = await _delete_in(
                session, ChatSession, ChatSession.app_id, app_ids, batch_size
            )
        # Workflow versions are now unreferenced (executions/evals gone).
        if version_ids:
            summary["WorkflowVersion"] = await _delete_in(
                session, WorkflowVersion, WorkflowVersion.id, version_ids, batch_size
            )
        # Workflows: versions cascade via ORM; trigger family already removed.
        if workflow_ids:
            summary["Workflow"] = await _delete_in(
                session, Workflow, Workflow.id, workflow_ids, batch_size
            )
        # Service accounts: api_tokens cascade.
        if project_ids:
            summary["ServiceAccount"] = await _delete_in(
                session, ServiceAccount, ServiceAccount.project_id, project_ids, batch_size
            )
        # Usage facts: no FK, by organization_id.
        usage_result = await session.execute(
            delete(UsageDailyFact).where(UsageDailyFact.organization_id == org_id)
        )
        summary["UsageDailyFact"] = int(getattr(usage_result, "rowcount", 0) or 0)
        # Cost alerts: workflow_id is a bare string (no FK).
        if workflow_ids:
            summary["CostAlert"] = await _delete_in(
                session, CostAlert, CostAlert.workflow_id, workflow_ids, batch_size
            )
        await session.commit()

    # Projects (apps + quota tables cascade), memberships, invitations.
    async with session_factory() as session:
        if project_ids:
            summary["Project"] = await _delete_in(
                session, Project, Project.id, project_ids, batch_size
            )
        membership_result = await session.execute(
            delete(Membership).where(Membership.organization_id == org_id)
        )
        summary["Membership"] = int(getattr(membership_result, "rowcount", 0) or 0)
        invitation_result = await session.execute(
            delete(OrganizationInvitation).where(OrganizationInvitation.organization_id == org_id)
        )
        summary["OrganizationInvitation"] = int(
            getattr(invitation_result, "rowcount", 0) or 0
        )
        await session.commit()
    return summary


async def purge_organization_data(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    org_id: str,
    *,
    rag_service: RagService,
    batch_size: int,
    audit_principal: Principal | None = None,
) -> dict[str, int]:
    """Hard-purge every row belonging to an organization, then drop it.

    Returns a summary of deleted-row counts per table plus external cleanup
    counts (``checkpoint_rows``, ``redis_keys``) so callers and tests can
    verify the org's data has been fully zeroed.
    """
    principal = audit_principal or _SYSTEM_PRINCIPAL
    # 1. Mark purging so concurrent requests see the frozen state immediately.
    async with session_factory() as session:
        org = await session.get(Organization, org_id)
        if org is None:
            raise OrgDeletionConflict(f"organization {org_id} not found")
        if org.deletion_status == "purged":
            raise OrgDeletionConflict("organization already purged")
        org.deletion_status = "purging"
        org_name = org.name
        await session.commit()

    # 2. Snapshot id sets while the project_id links are still intact.
    async with session_factory() as session:
        ids = await _collect_org_resource_ids(session, org_id)

    # 3. External cleanup (checkpoints + Redis) before DB rows go away.
    checkpoint_rows = await _purge_checkpoints(
        settings, session_factory, ids["execution_ids"], batch_size
    )
    redis_keys = await _purge_redis(
        settings, ids["workflow_ids"], ids["execution_ids"]
    )

    # 4. Knowledge bases via RagService (vectors + uploads + quotas + rows).
    for kb_id in ids["kb_ids"]:
        try:
            await rag_service.delete_knowledge_base(kb_id)
        except Exception:
            logger.exception("knowledge base %s cleanup failed during org purge", kb_id)
    summary: dict[str, int] = {"knowledge_bases": len(ids["kb_ids"])}

    # 5. DB sweep in dependency order.
    summary.update(await _purge_db(session_factory, ids, org_id, batch_size))
    summary["checkpoint_rows"] = checkpoint_rows
    summary["redis_keys"] = redis_keys

    # 6. Audit the purge, then drop the org row itself. AuditLog carries no
    # FK so the row survives the org deletion for compliance review.
    async with session_factory() as session:
        await record_audit(
            session,
            principal,
            action=ACTION_PURGED,
            resource_type="organization",
            resource_id=org_id,
            resource_name=org_name,
            organization_id=org_id,
            details={
                "projects": len(ids["project_ids"]),
                "workflows": len(ids["workflow_ids"]),
                "executions": len(ids["execution_ids"]),
                "checkpoint_rows": checkpoint_rows,
                "redis_keys": redis_keys,
            },
        )
        await session.execute(delete(Organization).where(Organization.id == org_id))
        await session.commit()
    return summary


__all__ = [
    "ACTION_CANCELLED",
    "ACTION_PURGED",
    "ACTION_REQUESTED",
    "OrgDeletionConflict",
    "cancel_org_deletion",
    "purge_organization_data",
    "request_org_deletion",
]
