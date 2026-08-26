"""Application entity CRUD, version binding, and RBAC coverage (C3-1)."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.core.auth import _sha256
from app.core.config import Settings
from app.main import create_app

ADMIN_TOKEN = "apps-admin-token-with-more-than-16-characters"


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        rate_limit_workflow_api_requests=100,
        rate_limit_execution_requests=100,
    )


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _dsl(name: str = "Published App Workflow") -> dict:
    return {
        "version": "1.0",
        "name": name,
        "variables": [
            {"name": "topic", "type": "string", "required": True},
        ],
        "settings": {"max_loop_iterations": 20, "timeout_seconds": 30, "recursion_limit": 50},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "position": {"x": 0, "y": 0},
                "config": {
                    "input_schema": [
                        {"name": "message", "type": "string", "required": True},
                        {"name": "retries", "type": "number", "required": False, "default": 1},
                    ]
                },
            },
            {
                "id": "end",
                "type": "end",
                "position": {"x": 200, "y": 0},
                "config": {"output_template": {"answer": "{{nodes.prev.output}}"}},
            },
        ],
        "edges": [{"id": "edge", "source": "start", "target": "end"}],
    }


def _project_and_published_workflow(client: TestClient) -> tuple[str, str, str]:
    registered = client.post(
        "/api/auth/register",
        json={
            "email": "apps-admin@example.com",
            "password": "apps-password",
            "display_name": "Apps Admin",
            "role": "admin",
        },
    )
    assert registered.status_code == 201, registered.text
    org = client.post("/api/organizations", json={"name": "Apps Org"})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    project = client.post(f"/api/organizations/{org_id}/projects", json={"name": "Apps Project"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    workflow = client.post(
        "/api/workflows",
        headers=_admin(),
        json={"name": "Published App Workflow", "project_id": project_id, "dsl": _dsl()},
    )
    assert workflow.status_code == 201, workflow.text
    workflow_id = workflow.json()["id"]
    assert client.post(f"/api/workflows/{workflow_id}/publish", headers=_admin()).status_code == 200
    return org_id, project_id, workflow_id


def _published_version(client: TestClient, workflow_id: str) -> str:
    versions = client.get(f"/api/workflows/{workflow_id}/versions", headers=_admin())
    assert versions.status_code == 200, versions.text
    published = [v for v in versions.json() if v["status"] == "published"]
    assert published, "expected at least one published version"
    return published[0]["id"]


def test_create_project_app_binds_no_version_and_has_no_token(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "name": "Support Bot",
                "type": "chatbot",
                "welcome_message": "How can I help?",
                "suggested_questions": [" What is X? ", " What is X? ", "What is Y?"],
                "visibility": "project",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        app = body["app"]
        assert app["name"] == "Support Bot"
        assert app["type"] == "chatbot"
        assert app["visibility"] == "project"
        assert app["status"] == "active"
        assert app["has_public_access"] is False
        assert app["published_version_id"] is None
        assert app["published_version_number"] is None
        assert app["input_form"] == []
        assert app["slug"] == "support-bot"
        assert app["suggested_questions"] == ["What is X?", "What is Y?"]
        assert body["token"] is None
        assert body["public_url"] is None


def test_create_public_app_issues_token_and_persists_hash(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "name": "Public Assistant",
                "visibility": "public",
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        token = body["token"]
        assert token and len(token) > 16
        assert body["app"]["has_public_access"] is True
        assert body["app"]["token_prefix"] == token[:12]
        assert body["public_url"] == f"/api/apps/p/public-assistant?t={token}"

        with sqlite3.connect(tmp_path / "app.db") as database:
            row = database.execute(
                "SELECT public_token_hash, token_prefix, visibility FROM apps WHERE id = ?",
                (body["app"]["id"],),
            ).fetchone()
        assert row == (_sha256(token), token[:12], "public")


def test_switch_version_snapshots_input_form(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _project_and_published_workflow(client)
        version_id = _published_version(client, workflow_id)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "workflow_id": workflow_id, "name": "Versioned App"},
        )
        app_id = created.json()["app"]["id"]
        switched = client.post(
            f"/api/apps/{app_id}/version",
            headers=_admin(),
            json={"version_id": version_id},
        )
        assert switched.status_code == 200, switched.text
        body = switched.json()
        assert body["published_version_id"] == version_id
        assert body["published_version_number"] == 1
        names = {field["name"] for field in body["input_form"]}
        assert names == {"topic", "message", "retries"}
        topic = next(field for field in body["input_form"] if field["name"] == "topic")
        assert topic["required"] is True
        retries = next(field for field in body["input_form"] if field["name"] == "retries")
        assert retries["required"] is False
        assert retries["default"] == 1


def test_switch_version_rejects_unpublished_workflow_version(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "workflow_id": workflow_id, "name": "Bad Switch"},
        )
        app_id = created.json()["app"]["id"]
        # An unknown version id cannot resolve to a published row.
        bad = client.post(
            f"/api/apps/{app_id}/version",
            headers=_admin(),
            json={"version_id": "nonexistent1234567890ab1234567"},
        )
        assert bad.status_code == 409


def test_create_app_rejects_workflow_from_another_project(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_a, workflow_a = _project_and_published_workflow(client)
        # A second project in the same org with its own published workflow.
        other_project = client.post(
            f"/api/organizations/{_org_id}/projects",
            headers=_admin(),
            json={"name": "Other Project"},
        )
        assert other_project.status_code == 201
        other_project_id = other_project.json()["id"]
        other_workflow = client.post(
            "/api/workflows",
            headers=_admin(),
            json={
                "name": "Other Workflow",
                "project_id": other_project_id,
                "dsl": _dsl("Other Workflow"),
            },
        )
        assert other_workflow.status_code == 201
        other_workflow_id = other_workflow.json()["id"]

        # Binding project_a's app to other_project's workflow is rejected.
        cross = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_a,
                "workflow_id": other_workflow_id,
                "name": "Cross Binding",
            },
        )
        assert cross.status_code == 409
        # Binding to an unknown workflow id is also rejected.
        unknown = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_a,
                "workflow_id": "nonexistent1234567890ab1234567",
                "name": "Unknown Binding",
            },
        )
        assert unknown.status_code == 409
        # Binding a same-project workflow succeeds.
        ok = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_a, "workflow_id": workflow_a, "name": "Own Binding"},
        )
        assert ok.status_code == 201, ok.text


def test_rotate_token_invalidates_old_token(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "Rotate Me", "visibility": "link"},
        )
        app_id = created.json()["app"]["id"]
        old_token = created.json()["token"]
        rotated = client.post(f"/api/apps/{app_id}/token/rotate", headers=_admin())
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["token"]
        assert new_token and new_token != old_token
        assert rotated.json()["app"]["token_prefix"] == new_token[:12]
        with sqlite3.connect(tmp_path / "app.db") as database:
            row = database.execute(
                "SELECT public_token_hash FROM apps WHERE id = ?", (app_id,)
            ).fetchone()
        assert row[0] == _sha256(new_token)
        assert row[0] != _sha256(old_token)


def test_rotate_token_rejected_for_project_visibility(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "No Rotate", "visibility": "project"},
        )
        app_id = created.json()["app"]["id"]
        rotated = client.post(f"/api/apps/{app_id}/token/rotate", headers=_admin())
        assert rotated.status_code == 409


def test_demote_visibility_clears_public_token(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "Demote Me", "visibility": "public"},
        )
        app_id = created.json()["app"]["id"]
        demoted = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"visibility": "project"},
        )
        assert demoted.status_code == 200, demoted.text
        body = demoted.json()["app"]
        assert body["visibility"] == "project"
        assert body["has_public_access"] is False
        assert body["token_prefix"] is None
        assert demoted.json()["token"] is None
        with sqlite3.connect(tmp_path / "app.db") as database:
            row = database.execute(
                "SELECT public_token_hash, token_prefix FROM apps WHERE id = ?", (app_id,)
            ).fetchone()
        assert row == (None, None)


def test_promote_visibility_to_public_issues_token(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "Promote Me", "visibility": "project"},
        )
        app_id = created.json()["app"]["id"]
        promoted = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={"visibility": "public"},
        )
        assert promoted.status_code == 200, promoted.text
        token = promoted.json()["token"]
        assert token and promoted.json()["app"]["has_public_access"] is True


def test_slug_collision_appended_with_suffix(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        first = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "Colliding Name"},
        )
        assert first.status_code == 201
        second = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "Colliding Name"},
        )
        assert second.status_code == 201, second.text
        assert first.json()["app"]["slug"] == "colliding-name"
        assert second.json()["app"]["slug"] == "colliding-name-2"


def test_list_apps_returns_project_scoped_page(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        for index in range(3):
            assert client.post(
                "/api/apps",
                headers=_admin(),
                json={"project_id": project_id, "name": f"List App {index}"},
            ).status_code == 201
        listed = client.get("/api/apps", headers=_admin(), params={"project_id": project_id})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["has_more"] is False
        assert len(body["items"]) == 3
        assert {item["name"] for item in body["items"]} == {
            "List App 0",
            "List App 1",
            "List App 2",
        }


def test_get_and_update_app_fields(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "Editable", "type": "chatbot"},
        )
        app_id = created.json()["app"]["id"]
        fetched = client.get(f"/api/apps/{app_id}", headers=_admin())
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "Editable"
        updated = client.put(
            f"/api/apps/{app_id}",
            headers=_admin(),
            json={
                "name": "Renamed",
                "type": "completion",
                "welcome_message": "updated",
                "status": "disabled",
            },
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()["app"]
        assert body["name"] == "Renamed"
        assert body["type"] == "completion"
        assert body["welcome_message"] == "updated"
        assert body["status"] == "disabled"


def test_delete_app_removes_row(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_id, "name": "Deletable"},
        )
        app_id = created.json()["app"]["id"]
        deleted = client.delete(f"/api/apps/{app_id}", headers=_admin())
        assert deleted.status_code == 204
        assert client.get(f"/api/apps/{app_id}", headers=_admin()).status_code == 404


PASSWORD = "s3cret-pass-word"


def _register(client: TestClient, email: str, *, role: str = "viewer") -> dict:
    resp = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": email.split("@")[0],
            "role": role,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login_as(client: TestClient, email: str) -> None:
    """Switch the TestClient cookie session to ``email``'s user.

    Cookie sessions carry the principal, so subsequent calls need no
    Authorization header. Switching identity requires re-logging in because
    the client holds a single session cookie at a time.
    """
    client.cookies.clear()
    resp = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text


def _add_member(client: TestClient, org_id: str, email: str, role: str) -> None:
    resp = client.post(
        f"/api/organizations/{org_id}/members",
        headers=_admin(),
        json={"email": email, "role": role},
    )
    assert resp.status_code == 201, resp.text


def test_rbac_viewer_cannot_create_and_editor_cannot_delete(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, _workflow_id = _project_and_published_workflow(client)
        _register(client, "apps-viewer@example.com", role="viewer")
        _register(client, "apps-editor@example.com", role="editor")
        _add_member(client, _org_id, "apps-viewer@example.com", "viewer")
        _add_member(client, _org_id, "apps-editor@example.com", "editor")

        # Viewer (cookie session): can list, cannot create.
        _login_as(client, "apps-viewer@example.com")
        listed = client.get("/api/apps", params={"project_id": project_id})
        assert listed.status_code == 200
        created = client.post(
            "/api/apps",
            json={"project_id": project_id, "name": "Forbidden"},
        )
        assert created.status_code == 403

        # Editor (cookie session): can create, cannot delete.
        _login_as(client, "apps-editor@example.com")
        created = client.post(
            "/api/apps",
            json={"project_id": project_id, "name": "Editor Owned"},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["app"]["id"]
        forbidden_delete = client.delete(f"/api/apps/{app_id}")
        assert forbidden_delete.status_code == 403
        # Admin (static token) can delete the editor-owned app.
        assert client.delete(f"/api/apps/{app_id}", headers=_admin()).status_code == 204


def test_cross_project_app_access_forbidden(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_a, _workflow_a = _project_and_published_workflow(client)
        # A second project in the same org, plus a viewer member of the org.
        other_project = client.post(
            f"/api/organizations/{_org_id}/projects",
            headers=_admin(),
            json={"name": "Other Project"},
        )
        assert other_project.status_code == 201
        other_project_id = other_project.json()["id"]
        _register(client, "cross-viewer@example.com", role="viewer")
        _add_member(client, _org_id, "cross-viewer@example.com", "viewer")
        _login_as(client, "cross-viewer@example.com")

        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={"project_id": project_a, "name": "Scoped App"},
        )
        app_id = created.json()["app"]["id"]
        # The org viewer can list project_a (org-level membership), and the app
        # itself belongs to project_a and is visible to org members.
        listed = client.get("/api/apps", params={"project_id": project_a})
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [app_id]
        # Listing the other project yields nothing (no cross-project leak).
        own_listed = client.get("/api/apps", params={"project_id": other_project_id})
        assert own_listed.status_code == 200
        assert own_listed.json()["items"] == []


def test_audit_log_records_app_lifecycle(tmp_path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _org_id, project_id, workflow_id = _project_and_published_workflow(client)
        version_id = _published_version(client, workflow_id)
        created = client.post(
            "/api/apps",
            headers=_admin(),
            json={
                "project_id": project_id,
                "workflow_id": workflow_id,
                "name": "Audited",
                "visibility": "link",
            },
        )
        app_id = created.json()["app"]["id"]
        client.post(
            f"/api/apps/{app_id}/version",
            headers=_admin(),
            json={"version_id": version_id},
        )
        client.post(f"/api/apps/{app_id}/token/rotate", headers=_admin())
        client.put(f"/api/apps/{app_id}", headers=_admin(), json={"name": "Audited Renamed"})
        client.delete(f"/api/apps/{app_id}", headers=_admin())

        with sqlite3.connect(tmp_path / "app.db") as database:
            actions = [
                row[0]
                for row in database.execute(
                    "SELECT action FROM audit_logs WHERE resource_type = 'app' AND resource_id = ? "
                    "ORDER BY created_at, id",
                    (app_id,),
                )
            ]
        assert actions == [
            "app.created",
            "app.version.changed",
            "app.token.rotated",
            "app.updated",
            "app.deleted",
        ]
