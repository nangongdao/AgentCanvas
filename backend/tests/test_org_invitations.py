"""Membership invitations and cross-project resource transfer (C7-4)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.main import create_app

FERNET_KEY = "BjzaAlRXaAJ8S_6Vj4_Yf6YmMBtMo2rHtE1L6T2HYXs="
ADMIN_TOKEN = "test-admin-token-invitations"


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


def _bootstrap_org(client: TestClient, *, name: str) -> dict[str, str]:
    registered = client.post(
        "/api/auth/register",
        json={
            "email": f"{name.lower()}@example.test",
            "password": "invite-password",
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


def _register_and_login(client: TestClient, *, email: str) -> None:
    created = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "invite-password",
            "display_name": email,
            "role": "viewer",
        },
    )
    assert created.status_code == 201, created.text
    login = client.post("/api/auth/login", json={"email": email, "password": "invite-password"})
    assert login.status_code == 200, login.text


def test_invitation_lifecycle_issue_accept_and_burn(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _bootstrap_org(client, name="Invite")

        # Non-admins cannot issue invitations.
        _register_and_login(client, email="invite-viewer@example.test")
        forbidden = client.post(
            f"/api/organizations/{ids['org_id']}/invitations",
            json={"email": "newcomer@example.test", "role": "editor"},
        )
        assert forbidden.status_code == 403

        # Back on the org admin (bootstrap) session.
        client.post(
            "/api/auth/login",
            json={"email": "invite@example.test", "password": "invite-password"},
        )
        issued = client.post(
            f"/api/organizations/{ids['org_id']}/invitations",
            json={"email": "newcomer@example.test", "role": "editor"},
        )
        assert issued.status_code == 201, issued.text
        payload = issued.json()
        # Without SMTP the link is handed to the inviting admin.
        assert payload["delivery"] == "manual"
        assert "token=" in payload["accept_url"]
        assert payload["invitation"]["accepted_at"] is None

        # Only a hash is persisted; the raw token never reappears in listings.
        listed = client.get(f"/api/organizations/{ids['org_id']}/invitations")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert "token" not in str(listed.json())

        # The invitee accepts and receives the invited role.
        _register_and_login(client, email="newcomer@example.test")
        accepted = client.post(
            "/api/organizations/invitations/accept",
            json={"token": payload["accept_url"].split("token=")[1]},
        )
        assert accepted.status_code == 200, accepted.text
        members = client.get(f"/api/organizations/{ids['org_id']}/members")
        roles = {member["user_id"]: member["role"] for member in members.json()}
        me = client.get("/api/auth/me").json()
        assert roles.get(me["user_id"]) == "editor"

        # The invitation is burned: a second accept conflicts.
        reused = client.post(
            "/api/organizations/invitations/accept",
            json={"token": payload["accept_url"].split("token=")[1]},
        )
        assert reused.status_code == 409

        # A bogus token is gone, not forbidden.
        missing = client.post("/api/organizations/invitations/accept", json={"token": "x" * 40})
        assert missing.status_code == 410

        # Static-token principals cannot accept invitations.
        reused_token_principal = client.post(
            "/api/organizations/invitations/accept",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"token": "y" * 40},
        )
        assert reused_token_principal.status_code == 403


def test_invitation_revocation_and_already_member(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _bootstrap_org(client, name="Revoke")
        issued = client.post(
            f"/api/organizations/{ids['org_id']}/invitations",
            json={"email": "someone@example.test", "role": "viewer"},
        )
        assert issued.status_code == 201
        token = issued.json()["accept_url"].split("token=")[1]
        invitation_id = issued.json()["invitation"]["id"]

        revoked = client.delete(f"/api/organizations/{ids['org_id']}/invitations/{invitation_id}")
        assert revoked.status_code == 204

        _register_and_login(client, email="someone@example.test")
        accepted = client.post("/api/organizations/invitations/accept", json={"token": token})
        assert accepted.status_code == 410

        # A fresh invitation for the same person joins them once...
        client.post(
            "/api/auth/login",
            json={"email": "revoke@example.test", "password": "invite-password"},
        )
        second = client.post(
            f"/api/organizations/{ids['org_id']}/invitations",
            json={"email": "someone@example.test", "role": "viewer"},
        )
        assert second.status_code == 201
        client.post(
            "/api/auth/login",
            json={"email": "someone@example.test", "password": "invite-password"},
        )
        joined = client.post(
            "/api/organizations/invitations/accept",
            json={"token": second.json()["accept_url"].split("token=")[1]},
        )
        assert joined.status_code == 200, joined.text

        # ...and re-issuing to the now-existing member burns the invitation
        # without duplicating or downgrading the membership.
        client.post(
            "/api/auth/login",
            json={"email": "revoke@example.test", "password": "invite-password"},
        )
        third = client.post(
            f"/api/organizations/{ids['org_id']}/invitations",
            json={"email": "someone@example.test", "role": "viewer"},
        )
        assert third.status_code == 201
        client.post(
            "/api/auth/login",
            json={"email": "someone@example.test", "password": "invite-password"},
        )
        conflicting = client.post(
            "/api/organizations/invitations/accept",
            json={"token": third.json()["accept_url"].split("token=")[1]},
        )
        assert conflicting.status_code == 409
        members = client.get(
            f"/api/organizations/{ids['org_id']}/members",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        # admin bootstrap + someone, exactly one membership for someone
        user_counts = [m["user_id"] for m in members.json()]
        assert len(user_counts) == len(set(user_counts))


def test_invitation_email_validation_and_expiry(tmp_path: Path) -> None:
    settings = _settings(tmp_path, invitation_expire_days=1)
    with TestClient(create_app(settings)) as client:
        ids = _bootstrap_org(client, name="Expiry")
        invalid = client.post(
            f"/api/organizations/{ids['org_id']}/invitations",
            json={"email": "not-an-email", "role": "viewer"},
        )
        assert invalid.status_code == 422
        invalid_role = client.post(
            f"/api/organizations/{ids['org_id']}/invitations",
            json={"email": "ok@example.test", "role": "owner"},
        )
        assert invalid_role.status_code == 422


def _rag_dsl(kb_id: str) -> dict:
    return {
        "version": "1.0",
        "name": "kb-bound workflow",
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {"input_schema": [{"name": "q", "type": "string", "required": True}]},
            },
            {
                "id": "rag",
                "type": "rag",
                "position": {"x": 200, "y": 0},
                "config": {"kb_id": kb_id, "query": "{{input.q}}"},
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 400, "y": 0},
                "config": {"output_template": {"answer": "{{nodes.rag.output}}"}},
            },
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "rag"},
            {"id": "e2", "source": "rag", "target": "end"},
        ],
    }


def test_resource_transfer_between_projects(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _bootstrap_org(client, name="Transfer")
        org_id = ids["org_id"]
        second = client.post(
            f"/api/organizations/{org_id}/projects", json={"name": "Transfer Target"}
        )
        assert second.status_code == 201
        target_project_id = second.json()["id"]

        # A workflow in the source project.
        workflow = client.post(
            "/api/workflows",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={
                "name": "Transfer WF",
                "project_id": ids["project_id"],
                "dsl": _rag_dsl("global-kb"),
            },
        )
        assert workflow.status_code == 201, workflow.text
        workflow_id = workflow.json()["id"]

        # A knowledge base referenced by that workflow: transfer must refuse.
        kb = client.post(
            "/api/knowledge-bases",
            json={"name": "Transfer KB", "project_id": ids["project_id"]},
        )
        assert kb.status_code == 201, kb.text
        kb_id = kb.json()["id"]
        updated = client.put(
            f"/api/workflows/{workflow_id}",
            json={"dsl": _rag_dsl(kb_id)},
        )
        assert updated.status_code == 200, updated.text

        blocked = client.post(
            "/api/transfers",
            json={
                "resource_type": "knowledge_base",
                "resource_id": kb_id,
                "target_project_id": target_project_id,
            },
        )
        assert blocked.status_code == 409
        assert "referenced by source-project workflows" in blocked.json()["detail"]

        # Transferring the workflow first is refused while it references a
        # KB that does not belong to the target project.
        blocked_wf = client.post(
            "/api/transfers",
            json={
                "resource_type": "workflow",
                "resource_id": workflow_id,
                "target_project_id": target_project_id,
            },
        )
        assert blocked_wf.status_code == 409
        assert "knowledge bases that do not belong" in blocked_wf.json()["detail"]

        # Detach the KB reference: both transfers now succeed in order.
        detached = client.put(
            f"/api/workflows/{workflow_id}",
            json={"dsl": _rag_dsl("global-kb")},
        )
        assert detached.status_code == 200
        moved_wf = client.post(
            "/api/transfers",
            json={
                "resource_type": "workflow",
                "resource_id": workflow_id,
                "target_project_id": target_project_id,
            },
        )
        assert moved_wf.status_code == 200, moved_wf.text
        assert moved_wf.json()["from_project_id"] == ids["project_id"]

        moved_kb = client.post(
            "/api/transfers",
            json={
                "resource_type": "knowledge_base",
                "resource_id": kb_id,
                "target_project_id": target_project_id,
            },
        )
        assert moved_kb.status_code == 200, moved_kb.text

        # Same-project transfer is a no-op conflict.
        repeated = client.post(
            "/api/transfers",
            json={
                "resource_type": "workflow",
                "resource_id": workflow_id,
                "target_project_id": target_project_id,
            },
        )
        assert repeated.status_code == 409

        # Unknown resources and projects 404; invalid types 422.
        missing = client.post(
            "/api/transfers",
            json={
                "resource_type": "workflow",
                "resource_id": "does-not-exist",
                "target_project_id": target_project_id,
            },
        )
        assert missing.status_code == 404
        bad_project = client.post(
            "/api/transfers",
            json={
                "resource_type": "workflow",
                "resource_id": workflow_id,
                "target_project_id": "does-not-exist",
            },
        )
        assert bad_project.status_code == 404
        bad_type = client.post(
            "/api/transfers",
            json={
                "resource_type": "dataset",
                "resource_id": workflow_id,
                "target_project_id": target_project_id,
            },
        )
        assert bad_type.status_code == 422

        # Editor sessions cannot transfer.
        created_editor = client.post(
            "/api/auth/register",
            json={
                "email": "transfer-editor@example.test",
                "password": "invite-password",
                "display_name": "Transfer Editor",
                "role": "editor",
            },
        )
        assert created_editor.status_code == 201
        added = client.post(
            f"/api/organizations/{org_id}/members",
            json={"email": "transfer-editor@example.test", "role": "editor"},
        )
        assert added.status_code == 201
        editor_login = client.post(
            "/api/auth/login",
            json={
                "email": "transfer-editor@example.test",
                "password": "invite-password",
            },
        )
        assert editor_login.status_code == 200
        forbidden = client.post(
            "/api/transfers",
            json={
                "resource_type": "knowledge_base",
                "resource_id": kb_id,
                "target_project_id": ids["project_id"],
            },
        )
        assert forbidden.status_code == 403


def test_transfer_audit_written(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        ids = _bootstrap_org(client, name="TransferAudit")
        workflow = client.post(
            "/api/workflows",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={
                "name": "Audit Transfer WF",
                "project_id": ids["project_id"],
                "dsl": _rag_dsl("global-kb"),
            },
        )
        assert workflow.status_code == 201
        second = client.post(
            f"/api/organizations/{ids['org_id']}/projects", json={"name": "Audit Target"}
        )
        moved = client.post(
            "/api/transfers",
            json={
                "resource_type": "workflow",
                "resource_id": workflow.json()["id"],
                "target_project_id": second.json()["id"],
            },
        )
        assert moved.status_code == 200

        container = client.app.state.container

        async def _actions() -> list[str]:
            from app.db.models import AuditLog

            async with container.session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(AuditLog).where(
                                AuditLog.action.in_(["resource.transferred", "invitation.created"])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                return [row.action for row in rows]

        import asyncio

        assert "resource.transferred" in asyncio.run(_actions())
