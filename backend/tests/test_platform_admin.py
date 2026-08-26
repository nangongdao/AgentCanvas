"""Platform admin console: tenants, users, queue replay, announcements (C7-3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.db.migrations import upgrade_database
from app.db.models import Execution, ExecutionQueueItem, PlatformAnnouncement, Workflow
from app.main import create_app

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="
ADMIN_TOKEN = "test-admin-token-platform-console"


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
            "password": "platform-password",
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


def test_organization_listing_and_disable_enforcement(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="OrgAdmin")

        # A non-platform-admin session cannot use the console.
        viewer = client.post(
            "/api/auth/register",
            json={
                "email": "org-viewer@example.test",
                "password": "platform-password",
                "display_name": "Org Viewer",
                "role": "viewer",
            },
        )
        assert viewer.status_code == 201, viewer.text
        viewer_login = client.post(
            "/api/auth/login",
            json={"email": "org-viewer@example.test", "password": "platform-password"},
        )
        assert viewer_login.status_code == 200
        assert client.get("/api/admin/organizations").status_code == 403
        # Back on the organization admin session.
        org_login = client.post(
            "/api/auth/login",
            json={"email": "orgadmin@example.test", "password": "platform-password"},
        )
        assert org_login.status_code == 200

        listed = client.get("/api/admin/organizations", headers=_admin_headers())
        assert listed.status_code == 200, listed.text
        entry = next(item for item in listed.json() if item["id"] == ids["org_id"])
        assert entry["status"] == "active"
        assert entry["project_count"] == 1
        assert entry["member_count"] == 1

        # The org admin can access their org before the disable.
        assert client.get(f"/api/organizations/{ids['org_id']}/members").status_code == 200
        # Give the plain viewer an org membership so enforcement can be
        # observed on a principal that is not a platform admin.
        added = client.post(
            f"/api/organizations/{ids['org_id']}/members",
            json={"email": "org-viewer@example.test", "role": "viewer"},
        )
        assert added.status_code == 201, added.text

        disabled = client.put(
            f"/api/admin/organizations/{ids['org_id']}/status",
            headers=_admin_headers(),
            json={"status": "disabled"},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["status"] == "disabled"

        # Members lose org- and project-scoped access...
        member_login = client.post(
            "/api/auth/login",
            json={"email": "org-viewer@example.test", "password": "platform-password"},
        )
        assert member_login.status_code == 200
        assert client.get(f"/api/organizations/{ids['org_id']}/members").status_code == 403
        assert client.get(f"/api/projects/{ids['project_id']}/quotas").status_code == 403
        # ...while the platform admin keeps access and can list the org.
        relisted = client.get("/api/admin/organizations", headers=_admin_headers())
        entry = next(item for item in relisted.json() if item["id"] == ids["org_id"])
        assert entry["status"] == "disabled"

        # Re-enabling restores member access.
        enabled = client.put(
            f"/api/admin/organizations/{ids['org_id']}/status",
            headers=_admin_headers(),
            json={"status": "active"},
        )
        assert enabled.status_code == 200
        assert client.get(f"/api/organizations/{ids['org_id']}/members").status_code == 200

        missing = client.put(
            "/api/admin/organizations/does-not-exist/status",
            headers=_admin_headers(),
            json={"status": "disabled"},
        )
        assert missing.status_code == 404


def test_user_listing_deactivate_and_revive(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        _setup_org(client, name="UserAdmin")

        # Register a second user (member of nothing) and log in.
        created = client.post(
            "/api/auth/register",
            json={
                "email": "deactivate-me@example.test",
                "password": "platform-password",
                "display_name": "Deactivate Me",
                "role": "viewer",
            },
        )
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]
        login = client.post(
            "/api/auth/login",
            json={"email": "deactivate-me@example.test", "password": "platform-password"},
        )
        assert login.status_code == 200, login.text
        # The session works before deactivation.
        assert client.get("/api/auth/me").status_code == 200

        listed = client.get("/api/admin/users", headers=_admin_headers())
        assert listed.status_code == 200
        entry = next(item for item in listed.json() if item["id"] == user_id)
        assert entry["status"] == "active"
        assert entry["email"] == "deactivate-me@example.test"

        deactivated = client.put(
            f"/api/admin/users/{user_id}/status",
            headers=_admin_headers(),
            json={"status": "disabled"},
        )
        assert deactivated.status_code == 200, deactivated.text
        assert deactivated.json()["status"] == "disabled"

        # Existing session cookie is dead and login is refused.
        assert client.get("/api/auth/me").status_code == 401
        relogin = client.post(
            "/api/auth/login",
            json={"email": "deactivate-me@example.test", "password": "platform-password"},
        )
        assert relogin.status_code == 401

        # A user-admin session cannot deactivate its own account. The static
        # platform token has no user row, so it is not subject to the guard.
        self_login = client.post(
            "/api/auth/login",
            json={
                "email": "useradmin@example.test",
                "password": "platform-password",
            },
        )
        assert self_login.status_code == 200
        self_id = client.get("/api/auth/me").json()["user_id"]
        self_disable = client.put(
            f"/api/admin/users/{self_id}/status",
            json={"status": "disabled"},
        )
        assert self_disable.status_code == 409

        # Reactivation allows login again.
        reactivated = client.put(
            f"/api/admin/users/{user_id}/status",
            headers=_admin_headers(),
            json={"status": "active"},
        )
        assert reactivated.status_code == 200
        revived = client.post(
            "/api/auth/login",
            json={"email": "deactivate-me@example.test", "password": "platform-password"},
        )
        assert revived.status_code == 200

        missing = client.put(
            "/api/admin/users/does-not-exist/status",
            headers=_admin_headers(),
            json={"status": "disabled"},
        )
        assert missing.status_code == 404


def _seed_queue_item(
    session_factory,
    *,
    item_id: str,
    execution_id: str,
    status: str,
    attempt: int,
    generation: int,
    last_error: str | None,
) -> None:
    async def _insert() -> None:
        async with session_factory() as session:
            session.add(
                Workflow(
                    id=f"wf-{item_id}",
                    name=f"Queue {item_id}",
                    dsl_json={"version": "1.0", "name": "queue probe", "nodes": [], "edges": []},
                )
            )
            await session.flush()
            from app.db.models import WorkflowVersion

            session.add(
                WorkflowVersion(
                    id=f"ver-{item_id}",
                    workflow_id=f"wf-{item_id}",
                    number=1,
                    status="published",
                    name=f"Queue {item_id} version",
                    dsl_json={"version": "1.0", "name": "queue probe", "nodes": [], "edges": []},
                )
            )
            await session.flush()
            session.add(
                Execution(
                    id=execution_id,
                    workflow_id=f"wf-{item_id}",
                    workflow_version_id=f"ver-{item_id}",
                    status="failed",
                    trigger_source="manual",
                    input_json={},
                    thread_id=execution_id,
                    started_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            await session.flush()
            session.add(
                ExecutionQueueItem(
                    id=item_id,
                    execution_id=execution_id,
                    kind="start",
                    status=status,
                    attempt=attempt,
                    owner_id=None,
                    lease_generation=generation,
                    last_error=last_error,
                )
            )
            await session.commit()

    import asyncio

    asyncio.run(_insert())


def test_queue_depth_dead_letters_and_replay(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container
        _seed_queue_item(
            container.session_factory,
            item_id="qi-dead-1",
            execution_id="exec-dead-1",
            status="dead_letter",
            attempt=3,
            generation=2,
            last_error="boom",
        )
        _seed_queue_item(
            container.session_factory,
            item_id="qi-queued-1",
            execution_id="exec-queued-1",
            status="queued",
            attempt=0,
            generation=0,
            last_error=None,
        )

        # Unauthenticated access is rejected before RBAC applies.
        assert client.get("/api/admin/queue").status_code == 401
        overview = client.get("/api/admin/queue", headers=_admin_headers())
        assert overview.status_code == 200, overview.text
        depth = overview.json()["depth"]
        assert depth.get("dead_letter") == 1
        assert depth.get("queued") == 1
        dead = overview.json()["dead_letters"]
        assert len(dead) == 1
        assert dead[0]["id"] == "qi-dead-1"
        assert dead[0]["last_error"] == "boom"
        assert dead[0]["attempt"] == 3

        # Replaying a non-dead-letter item is a conflict.
        conflict = client.post("/api/admin/queue/qi-queued-1/replay", headers=_admin_headers())
        assert conflict.status_code == 409
        missing = client.post("/api/admin/queue/does-not-exist/replay", headers=_admin_headers())
        assert missing.status_code == 404

        replayed = client.post("/api/admin/queue/qi-dead-1/replay", headers=_admin_headers())
        assert replayed.status_code == 200, replayed.text
        body = replayed.json()
        assert body["status"] == "queued"
        assert body["attempt"] == 0
        # Fencing is preserved: the generation survives until the next claim.
        assert body["lease_generation"] == 2

        overview = client.get("/api/admin/queue", headers=_admin_headers())
        # Depth keys exist only for statuses that currently have rows.
        assert overview.json()["depth"].get("dead_letter", 0) == 0
        assert overview.json()["depth"].get("queued") == 2
        assert overview.json()["dead_letters"] == []

        # A second replay of the same item now conflicts.
        assert (
            client.post("/api/admin/queue/qi-dead-1/replay", headers=_admin_headers()).status_code
            == 409
        )


def test_announcements_crud_and_active_listing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        _setup_org(client, name="Announce")
        viewer = client.post(
            "/api/auth/register",
            json={
                "email": "announce-viewer@example.test",
                "password": "platform-password",
                "display_name": "Announce Viewer",
                "role": "viewer",
            },
        )
        assert viewer.status_code == 201, viewer.text
        viewer_login = client.post(
            "/api/auth/login",
            json={
                "email": "announce-viewer@example.test",
                "password": "platform-password",
            },
        )
        assert viewer_login.status_code == 200
        assert client.get("/api/admin/announcements").status_code == 403
        client.post(
            "/api/auth/login",
            json={
                "email": "announce@example.test",
                "password": "platform-password",
            },
        )

        created = client.post(
            "/api/admin/announcements",
            headers=_admin_headers(),
            json={"message": "Scheduled maintenance tonight", "level": "warning"},
        )
        assert created.status_code == 201, created.text
        announcement = created.json()
        assert announcement["level"] == "warning"
        assert announcement["is_active"] is True

        created_info = client.post(
            "/api/admin/announcements",
            headers=_admin_headers(),
            json={"message": "Welcome to the platform"},
        )
        assert created_info.status_code == 201

        # Every authenticated principal sees active announcements.
        active = client.get("/api/announcements/active")
        assert active.status_code == 200
        messages = [item["message"] for item in active.json()]
        assert "Scheduled maintenance tonight" in messages
        assert "Welcome to the platform" in messages
        # Newest first.
        assert messages.index("Welcome to the platform") < messages.index(
            "Scheduled maintenance tonight"
        )

        deactivated = client.put(
            f"/api/admin/announcements/{announcement['id']}",
            headers=_admin_headers(),
            json={"is_active": False},
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["is_active"] is False
        messages = [item["message"] for item in client.get("/api/announcements/active").json()]
        assert "Scheduled maintenance tonight" not in messages

        invalid = client.post(
            "/api/admin/announcements",
            headers=_admin_headers(),
            json={"message": "x" * 1001},
        )
        assert invalid.status_code == 422

        deleted = client.delete(
            f"/api/admin/announcements/{announcement['id']}", headers=_admin_headers()
        )
        assert deleted.status_code == 204
        remaining = client.get("/api/admin/announcements", headers=_admin_headers()).json()
        assert all(item["id"] != announcement["id"] for item in remaining)


def test_admin_actions_write_audit_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _setup_org(client, name="AuditAdmin")
        client.put(
            f"/api/admin/organizations/{ids['org_id']}/status",
            headers=_admin_headers(),
            json={"status": "disabled"},
        )
        created = client.post(
            "/api/admin/announcements",
            headers=_admin_headers(),
            json={"message": "audit probe"},
        )
        assert created.status_code == 201

        container = client.app.state.container

        async def _actions() -> set[str]:
            from app.db.models import AuditLog

            async with container.session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(AuditLog).where(
                                AuditLog.resource_id.in_([ids["org_id"], created.json()["id"]])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                return {row.action for row in rows}

        import asyncio

        actions = asyncio.run(_actions())
        assert "organization.status.changed" in actions
        assert "announcement.created" in actions


async def test_migration_seeds_and_org_status_column(tmp_path: Path) -> None:
    settings = _settings(tmp_path, auth_mode="disabled")
    await upgrade_database(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            session.add(PlatformAnnouncement(message="probe", level="info", created_by="tester"))
            await session.flush()
            rows = list((await session.execute(select(PlatformAnnouncement))).scalars().all())
            assert len(rows) == 1
            assert rows[0].is_active is True
            await session.rollback()
    finally:
        await engine.dispose()
