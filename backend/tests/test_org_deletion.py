"""Tenant data compliance: two-phase organization deletion (C7-5)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.core.config import Settings
from app.db.base import create_engine
from app.db.migrations import CURRENT_REVISION, upgrade_database
from app.db.models import (
    App,
    AuditLog,
    ChatSession,
    CostAlert,
    Execution,
    ExecutionEventRow,
    ExecutionQueueItem,
    KnowledgeBase,
    Membership,
    Organization,
    Project,
    ServiceAccount,
    UsageDailyFact,
    Workflow,
    WorkflowVersion,
)
from app.main import create_app

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="
ADMIN_TOKEN = "test-admin-token-org-deletion"


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        secret_key=FERNET_KEY,
        local_embedding_dimensions=64,
        rate_limit_execution_requests=100,
        execution_max_concurrent=32,
        model_max_concurrent=32,
        log_level="WARNING",
        rate_limit_default_requests=1000,
        org_deletion_grace_days=30,
    )
    base.update(overrides)
    return Settings(**base)


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _setup_org(client: TestClient, *, name: str) -> dict[str, str]:
    registered = client.post(
        "/api/auth/register",
        json={
            "email": f"{name.lower()}@example.test",
            "password": "deletion-password",
            "display_name": name,
            "role": "admin",
        },
    )
    assert registered.status_code == 201, registered.text
    org = client.post("/api/organizations", json={"name": f"{name} Org"})
    assert org.status_code == 201, org.text
    project = client.post(
        f"/api/organizations/{org.json()['id']}/projects", json={"name": f"{name} Project"}
    )
    assert project.status_code == 201, project.text
    return {"org_id": org.json()["id"], "project_id": project.json()["id"]}


def _login(client: TestClient, name: str) -> None:
    login = client.post(
        "/api/auth/login",
        json={"email": f"{name.lower()}@example.test", "password": "deletion-password"},
    )
    assert login.status_code == 200, login.text


def _seed_full_org(session_factory, org_id: str, project_id: str) -> dict[str, str]:
    async def _insert() -> dict[str, str]:
        async with session_factory() as session:
            ids: dict[str, str] = {"project_id": project_id}
            wf = Workflow(
                id=f"wf-purge-{project_id[:6]}",
                name="Purge Wf",
                project_id=project_id,
                dsl_json={"version": "1.0", "name": "purge", "nodes": [], "edges": []},
            )
            session.add(wf)
            await session.flush()
            version = WorkflowVersion(
                id=f"ver-purge-{project_id[:6]}",
                workflow_id=wf.id,
                number=1,
                status="published",
                name="Purge version",
                dsl_json=wf.dsl_json,
            )
            session.add(version)
            await session.flush()
            execution = Execution(
                id=f"exec-purge-{project_id[:6]}",
                workflow_id=wf.id,
                workflow_version_id=version.id,
                status="succeeded",
                trigger_source="manual",
                input_json={},
                thread_id=f"exec-purge-{project_id[:6]}",
                started_at=datetime.now(UTC) - timedelta(hours=1),
                finished_at=datetime.now(UTC),
            )
            session.add(execution)
            await session.flush()
            session.add(
                ExecutionEventRow(
                    execution_id=execution.id,
                    seq=1,
                    event_type="workflow_started",
                    payload_json={},
                )
            )
            session.add(
                ExecutionQueueItem(
                    id=f"qi-purge-{project_id[:6]}",
                    execution_id=execution.id,
                    kind="start",
                    status="done",
                    attempt=1,
                    lease_generation=1,
                )
            )
            session.add(
                ChatSession(
                    id=f"chat-purge-{project_id[:6]}",
                    title="Purge Chat",
                    workflow_id=wf.id,
                )
            )
            session.add(
                CostAlert(
                    id=f"alert-purge-{project_id[:6]}",
                    execution_id=execution.id,
                    workflow_id=wf.id,
                    kind="cost",
                    severity="critical",
                    limit_value="1.00",
                    actual_value="1.50",
                )
            )
            session.add(
                UsageDailyFact(
                    organization_id=org_id,
                    project_id=project_id,
                    app_id=None,
                    model_config_id=None,
                    day=datetime.now(UTC).date(),
                    executions=1,
                )
            )
            session.add(
                ServiceAccount(
                    id=f"sa-purge-{project_id[:6]}",
                    name=f"sa-purge-{project_id[:6]}",
                    project_id=project_id,
                    role="editor",
                )
            )
            session.add(
                App(
                    id=f"app-purge-{project_id[:6]}",
                    project_id=project_id,
                    name="Purge App",
                    slug="purge-app",
                    type="chatbot",
                    visibility="project",
                    status="active",
                )
            )
            session.add(
                KnowledgeBase(
                    id=f"kb-purge-{project_id[:6]}",
                    project_id=project_id,
                    name="Purge KB",
                    embedding_model_id="default-embedding",
                )
            )
            await session.commit()
            ids.update(
                {
                    "workflow_id": wf.id,
                    "version_id": version.id,
                    "execution_id": execution.id,
                }
            )
        return ids

    return asyncio.run(_insert())


def _row_counts(session_factory, org_id: str) -> dict[str, int]:
    async def _count() -> dict[str, int]:
        async with session_factory() as session:
            async def _c(model, *filters):
                stmt = select(model)
                for f in filters:
                    stmt = stmt.where(f)
                result = await session.execute(stmt)
                return len(result.scalars().all())

            project_ids_q = select(Project.id).where(Project.organization_id == org_id)
            project_ids = [r for r in (await session.execute(project_ids_q)).scalars().all()]
            return {
                "org": await _c(Organization, Organization.id == org_id),
                "projects": len(project_ids),
                "workflows": await _c(
                    Workflow, Workflow.project_id.in_(project_ids)
                ) if project_ids else 0,
                "executions": await _c(
                    Execution, Execution.workflow_id.in_(
                        select(Workflow.id).where(Workflow.project_id.in_(project_ids))
                    )
                ) if project_ids else 0,
                "memberships": await _c(Membership, Membership.organization_id == org_id),
                "usage_facts": await _c(UsageDailyFact, UsageDailyFact.organization_id == org_id),
            }

    return asyncio.run(_count())


def test_request_freezes_org_and_cancel_restores(tmp_path: Path) -> None:
    settings = _settings(tmp_path, org_deletion_grace_days=30)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="Freeze")

        # A second member to observe the freeze.
        client.post(
            "/api/auth/register",
            json={
                "email": "freeze-member@example.test",
                "password": "deletion-password",
                "display_name": "Freeze Member",
                "role": "viewer",
            },
        )
        client.post(
            f"/api/organizations/{ids['org_id']}/members",
            json={"email": "freeze-member@example.test", "role": "viewer"},
        )
        client.post(
            "/api/auth/login",
            json={"email": "freeze-member@example.test", "password": "deletion-password"},
        )
        # Members can access the org before the freeze.
        assert client.get(f"/api/organizations/{ids['org_id']}").status_code == 200

        _login(client, "Freeze")
        requested = client.post(
            f"/api/organizations/{ids['org_id']}/deletion-request"
        )
        assert requested.status_code == 200, requested.text
        body = requested.json()
        assert body["deletion"]["status"] == "requested"
        assert body["deletion"]["purge_due_at"] is not None

        # The frozen org is invisible to non-admin members.
        client.post(
            "/api/auth/login",
            json={"email": "freeze-member@example.test", "password": "deletion-password"},
        )
        assert client.get(f"/api/organizations/{ids['org_id']}").status_code == 403

        # The platform admin still sees the org and the deletion status.
        listed = client.get("/api/admin/organizations", headers=_admin_headers())
        assert listed.status_code == 200, listed.text
        entry = next(item for item in listed.json() if item["id"] == ids["org_id"])
        assert entry["deletion"]["status"] == "requested"

        # Cancelling within the grace window restores member access.
        _login(client, "Freeze")
        cancelled = client.delete(
            f"/api/organizations/{ids['org_id']}/deletion-request"
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["deletion"]["status"] == "none"

        client.post(
            "/api/auth/login",
            json={"email": "freeze-member@example.test", "password": "deletion-password"},
        )
        assert client.get(f"/api/organizations/{ids['org_id']}").status_code == 200


def test_duplicate_request_is_conflict(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="Conflict")
        first = client.post(f"/api/organizations/{ids['org_id']}/deletion-request")
        assert first.status_code == 200
        second = client.post(f"/api/organizations/{ids['org_id']}/deletion-request")
        assert second.status_code == 409


def test_purge_zeros_all_org_data(tmp_path: Path) -> None:
    settings = _settings(tmp_path, org_deletion_grace_days=0)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="Purge")
        container = client.app.state.container
        _seed_full_org(container.session_factory, ids["org_id"], ids["project_id"])

        before = _row_counts(container.session_factory, ids["org_id"])
        assert before["projects"] == 1
        assert before["workflows"] == 1
        assert before["memberships"] == 1
        assert before["usage_facts"] == 1

        # Request deletion (grace_days=0 → due immediately) then purge via admin.
        requested = client.post(f"/api/organizations/{ids['org_id']}/deletion-request")
        assert requested.status_code == 200

        purged = client.post(
            f"/api/admin/organizations/{ids['org_id']}/deletion/purge",
            headers=_admin_headers(),
        )
        assert purged.status_code == 200, purged.text
        summary = purged.json()
        assert summary.get("Project", 0) == 1
        assert summary.get("Workflow", 0) == 1
        assert summary.get("Execution", 0) == 1

        after = _row_counts(container.session_factory, ids["org_id"])
        assert after["org"] == 0
        assert after["projects"] == 0
        assert after["workflows"] == 0
        assert after["executions"] == 0
        assert after["memberships"] == 0
        assert after["usage_facts"] == 0

        # The org row is gone → 404 on subsequent admin access.
        assert (
            client.get(
                f"/api/admin/organizations/{ids['org_id']}/deletion",
                headers=_admin_headers(),
            ).status_code
            == 404
        )


def test_purge_clears_checkpoint_threads(tmp_path: Path) -> None:
    settings = _settings(tmp_path, org_deletion_grace_days=0)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="Checkpoint")
        container = client.app.state.container
        seeded = _seed_full_org(container.session_factory, ids["org_id"], ids["project_id"])
        thread_id = seeded["execution_id"]

        # Seed a checkpoint thread that should be removed by the purge.
        import aiosqlite

        async def _seed_checkpoint() -> None:
            path = settings.checkpoint_db_path
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(str(path))
            try:
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

                from app.engine.checkpoint import make_strict_serde

                saver = AsyncSqliteSaver(conn, serde=make_strict_serde())
                await saver.setup()
                await saver.aput(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_ns": "",
                        }
                    },
                    {
                        "v": 1,
                        "ts": "2026-08-23T00:00:00+00:00",
                        "id": "n-1",
                        "channel_values": {},
                        "channel_versions": {},
                        "versions_seen": {},
                    },
                    {"source": "input", "step": 0, "writes": {}},
                    {},
                )
                await conn.commit()
            finally:
                await conn.close()

        asyncio.run(_seed_checkpoint())

        # Sanity: the checkpoint thread exists before purge.
        async def _count_before() -> int:
            conn = await aiosqlite.connect(str(settings.checkpoint_db_path))
            try:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (thread_id,)
                )
                row = await cursor.fetchone()
                return int(row[0] or 0)
            finally:
                await conn.close()

        assert asyncio.run(_count_before()) == 1

        client.post(f"/api/organizations/{ids['org_id']}/deletion-request")
        purged = client.post(
            f"/api/admin/organizations/{ids['org_id']}/deletion/purge",
            headers=_admin_headers(),
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["checkpoint_rows"] >= 1

        # The thread is gone after the purge.
        assert asyncio.run(_count_before()) == 0


def test_audit_rows_survive_purge(tmp_path: Path) -> None:
    settings = _settings(tmp_path, org_deletion_grace_days=0)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="AuditPurge")
        container = client.app.state.container

        client.post(f"/api/organizations/{ids['org_id']}/deletion-request")
        purged = client.post(
            f"/api/admin/organizations/{ids['org_id']}/deletion/purge",
            headers=_admin_headers(),
        )
        assert purged.status_code == 200, purged.text

        async def _actions() -> set[str]:
            async with container.session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(AuditLog)
                            .where(AuditLog.organization_id == ids["org_id"])
                            .order_by(AuditLog.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )
                return {row.action for row in rows}

        actions = asyncio.run(_actions())
        assert "organization.deletion.requested" in actions
        assert "organization.deletion.purged" in actions


def test_export_returns_organization_bundle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="Export")
        container = client.app.state.container
        _seed_full_org(container.session_factory, ids["org_id"], ids["project_id"])

        exported = client.get(f"/api/organizations/{ids['org_id']}/export")
        assert exported.status_code == 200, exported.text
        assert "application/json" in exported.headers.get("content-type", "")
        assert "attachment" in exported.headers.get("content-disposition", "")
        payload = exported.json()
        assert payload["schema"] == "agentcanvas.org-export/v1"
        assert payload["organization"]["id"] == ids["org_id"]
        assert len(payload["projects"]) == 1
        assert payload["meta"]["project_count"] == 1
        assert len(payload["workflows"]) == 1
        assert payload["workflows"][0]["dsl"]["name"] == "purge"
        # Service-account export must not carry credential material.
        assert payload["service_accounts"] == [] or all(
            "token_hash" not in sa for sa in payload["service_accounts"]
        )


def test_migration_adds_deletion_columns(tmp_path: Path) -> None:
    settings = _settings(tmp_path, auth_mode="disabled")
    asyncio.run(upgrade_database(settings))
    engine = create_engine(settings)

    async def _cols() -> set[str]:
        async with engine.connect() as connection:

            def _inspect(sync_conn) -> set[str]:
                return {c["name"] for c in inspect(sync_conn).get_columns("organizations")}

            return await connection.run_sync(_inspect)

    cols = asyncio.run(_cols())
    asyncio.run(engine.dispose())
    assert "deletion_status" in cols
    assert "deletion_requested_at" in cols
    assert "deletion_requested_by" in cols
    assert "purge_due_at" in cols
    assert CURRENT_REVISION == "0047_evaluation_policy"
