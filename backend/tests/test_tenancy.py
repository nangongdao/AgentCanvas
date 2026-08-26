"""D3 Phase 2 tenancy: orgs, projects, memberships, and project-scoped RBAC."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ADMIN_TOKEN = "admin-token-with-more-than-16-characters"
EDITOR_TOKEN = "editor-token"
VIEWER_TOKEN = "viewer-token"

PASSWORD = "s3cret-pass-word"


def auth_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
    )


def _register(client: TestClient, email: str, *, role: str = "editor") -> dict:
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
    client.cookies.clear()
    resp = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text


def _workflow_body(name: str = "Tenant Workflow") -> dict:
    return {
        "name": name,
        "dsl": {
            "version": "1.0",
            "name": name,
            "variables": [],
            "settings": {
                "max_loop_iterations": 20,
                "timeout_seconds": 30,
                "recursion_limit": 50,
            },
            "nodes": [
                {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
                {"id": "end", "type": "end", "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "edge", "source": "start", "target": "end"}],
        },
    }


def test_org_create_list_and_admin_gate(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        admin = _register(client, "admin@example.com")
        assert admin["role"] == "admin"

        org = client.post("/api/organizations", json={"name": "Acme"})
        assert org.status_code == 201, org.text
        org_id = org.json()["id"]
        assert org.json()["slug"] == "acme"

        members = client.get(f"/api/organizations/{org_id}/members")
        assert members.status_code == 200
        assert [m["user_id"] for m in members.json()] == [admin["id"]]
        assert members.json()[0]["role"] == "admin"

        orgs = client.get("/api/organizations")
        assert [o["id"] for o in orgs.json()] == [org_id]

        # A registered non-admin cannot create organizations.
        _register(client, "editor@example.com")
        _login_as(client, "editor@example.com")
        denied = client.post("/api/organizations", json={"name": "Sneaky"})
        assert denied.status_code == 403

        # A token-only admin has no user identity and cannot own an organization.
        token_login = client.post("/api/auth/login", json={"token": ADMIN_TOKEN})
        assert token_login.status_code == 200
        token_denied = client.post("/api/organizations", json={"name": "Token"})
        assert token_denied.status_code == 400


def test_project_membership_and_workflow_flow(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        org = client.post("/api/organizations", json={"name": "Acme"}).json()
        org_id = org["id"]

        _register(client, "bob@example.com")
        _login_as(client, "admin@example.com")
        added = client.post(
            f"/api/organizations/{org_id}/members",
            json={"email": "bob@example.com", "role": "editor"},
        )
        assert added.status_code == 201, added.text
        assert added.json()["role"] == "editor"

        _login_as(client, "bob@example.com")
        orgs = client.get("/api/organizations")
        assert [o["id"] for o in orgs.json()] == [org_id]

        project = client.post(
            f"/api/organizations/{org_id}/projects", json={"name": "App"}
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        assert project.json()["slug"] == "app"

        wf = client.post(
            "/api/workflows", json={**_workflow_body(), "project_id": project_id}
        )
        assert wf.status_code == 201, wf.text
        assert wf.json()["project_id"] == project_id

        listed = client.get(f"/api/workflows?project_id={project_id}")
        assert listed.status_code == 200
        assert [w["id"] for w in listed.json()["items"]] == [wf.json()["id"]]

        # The default list only shows unscoped workflows, not project ones.
        default = client.get("/api/workflows")
        assert default.status_code == 200
        default_ids = [w["id"] for w in default.json()["items"]]
        assert wf.json()["id"] not in default_ids
        assert all(w["project_id"] is None for w in default.json()["items"])


def test_cross_tenant_isolation(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        org1 = client.post("/api/organizations", json={"name": "Acme"}).json()
        org2 = client.post("/api/organizations", json={"name": "Globex"}).json()

        bob = _register(client, "bob@example.com")
        _login_as(client, "admin@example.com")
        _register(client, "carol@example.com")
        _login_as(client, "admin@example.com")
        client.post(
            f"/api/organizations/{org1['id']}/members",
            json={"email": "bob@example.com", "role": "editor"},
        )
        client.post(
            f"/api/organizations/{org2['id']}/members",
            json={"email": "carol@example.com", "role": "viewer"},
        )

        _login_as(client, "bob@example.com")
        project1 = client.post(
            f"/api/organizations/{org1['id']}/projects", json={"name": "App"}
        ).json()
        wf = client.post(
            "/api/workflows", json={**_workflow_body(), "project_id": project1["id"]}
        )
        assert wf.status_code == 201, wf.text
        wf_id = wf.json()["id"]

        # Carol (org2 viewer) must not reach org1's project or its workflows.
        _login_as(client, "carol@example.com")
        assert client.get(f"/api/workflows?project_id={project1['id']}").status_code == 403
        assert client.get(f"/api/workflows/{wf_id}").status_code == 403
        assert client.get(f"/api/organizations/{org1['id']}").status_code == 403
        assert client.get(f"/api/organizations/{org1['id']}/projects").status_code == 403
        assert client.get(f"/api/organizations/{org1['id']}/members").status_code == 403

        # Revoking Bob's membership strips his access to the tenant workflow.
        _login_as(client, "admin@example.com")
        assert client.delete(
            f"/api/organizations/{org1['id']}/members/{bob['id']}"
        ).status_code == 204
        _login_as(client, "bob@example.com")
        assert client.get(f"/api/workflows/{wf_id}").status_code == 403


def test_project_rbac_viewer_cannot_create(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        org = client.post("/api/organizations", json={"name": "Acme"}).json()
        _register(client, "dave@example.com")
        _login_as(client, "admin@example.com")
        client.post(
            f"/api/organizations/{org['id']}/members",
            json={"email": "dave@example.com", "role": "viewer"},
        )
        project = client.post(
            f"/api/organizations/{org['id']}/projects", json={"name": "App"}
        ).json()

        _login_as(client, "dave@example.com")
        assert (
            client.get(f"/api/workflows?project_id={project['id']}").status_code == 200
        )
        denied = client.post(
            "/api/workflows", json={**_workflow_body(), "project_id": project["id"]}
        )
        assert denied.status_code == 403


def test_token_global_admin_bypass_and_unscoped_regression(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        org = client.post("/api/organizations", json={"name": "Acme"}).json()
        project = client.post(
            f"/api/organizations/{org['id']}/projects", json={"name": "App"}
        ).json()

        admin_headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        wf = client.post(
            "/api/workflows",
            headers=admin_headers,
            json={**_workflow_body(), "project_id": project["id"]},
        )
        assert wf.status_code == 201, wf.text
        wf_id = wf.json()["id"]
        assert (
            client.get(f"/api/workflows/{wf_id}", headers=admin_headers).status_code == 200
        )

        # A global viewer token without a membership is denied tenant workflows.
        viewer_headers = {"Authorization": f"Bearer {VIEWER_TOKEN}"}
        assert client.get(f"/api/workflows/{wf_id}", headers=viewer_headers).status_code == 403
        assert (
            client.get(f"/api/workflows?project_id={project['id']}", headers=viewer_headers).status_code
            == 403
        )

        # Unscoped workflows stay fully available to shared tokens (regression).
        editor_headers = {"Authorization": f"Bearer {EDITOR_TOKEN}"}
        unscoped = client.post("/api/workflows", headers=editor_headers, json=_workflow_body())
        assert unscoped.status_code == 201
        default = client.get("/api/workflows", headers=viewer_headers)
        assert default.status_code == 200
        default_ids = [w["id"] for w in default.json()["items"]]
        assert unscoped.json()["id"] in default_ids
        assert wf_id not in default_ids


def test_slug_conflicts_are_rejected(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        assert client.post("/api/organizations", json={"name": "Acme"}).status_code == 201
        conflict = client.post("/api/organizations", json={"name": "Acme"})
        assert conflict.status_code == 409

        org = client.post("/api/organizations", json={"name": "Globex"}).json()
        assert client.post(
            f"/api/organizations/{org['id']}/projects", json={"name": "App"}
        ).status_code == 201
        project_conflict = client.post(
            f"/api/organizations/{org['id']}/projects", json={"name": "App"}
        )
        assert project_conflict.status_code == 409


def test_membership_role_demotion_revokes_admin_actions(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        org = client.post("/api/organizations", json={"name": "Acme"}).json()
        bob = _register(client, "bob@example.com")
        _login_as(client, "admin@example.com")
        _register(client, "carol@example.com")
        _login_as(client, "admin@example.com")
        client.post(
            f"/api/organizations/{org['id']}/members",
            json={"email": "bob@example.com", "role": "admin"},
        )

        # Bob (org admin) can manage members and projects.
        _login_as(client, "bob@example.com")
        assert client.post(
            f"/api/organizations/{org['id']}/members",
            json={"email": "carol@example.com", "role": "viewer"},
        ).status_code == 201
        assert client.post(
            f"/api/organizations/{org['id']}/projects", json={"name": "App"}
        ).status_code == 201

        # Demote Bob to viewer; admin-only actions now fail.
        _login_as(client, "admin@example.com")
        assert client.put(
            f"/api/organizations/{org['id']}/members/{bob['id']}",
            json={"role": "viewer"},
        ).status_code == 200

        _login_as(client, "bob@example.com")
        assert client.post(
            f"/api/organizations/{org['id']}/members",
            json={"email": "carol@example.com", "role": "viewer"},
        ).status_code == 403
        assert client.post(
            f"/api/organizations/{org['id']}/projects", json={"name": "Denied"}
        ).status_code == 403


def test_cross_tenant_versions_and_execution_access_blocked(tmp_path) -> None:
    settings = replace(auth_settings(tmp_path), rate_limit_execution_requests=100)
    with TestClient(create_app(settings)) as client:
        _register(client, "admin@example.com")
        org1 = client.post("/api/organizations", json={"name": "Acme"}).json()
        org2 = client.post("/api/organizations", json={"name": "Globex"}).json()

        _register(client, "bob@example.com")
        _login_as(client, "admin@example.com")
        client.post(
            f"/api/organizations/{org1['id']}/members",
            json={"email": "bob@example.com", "role": "editor"},
        )
        _register(client, "carol@example.com")
        _login_as(client, "admin@example.com")
        client.post(
            f"/api/organizations/{org2['id']}/members",
            json={"email": "carol@example.com", "role": "viewer"},
        )

        # Bob (org1 editor) creates a project workflow and runs it.
        _login_as(client, "bob@example.com")
        project = client.post(
            f"/api/organizations/{org1['id']}/projects", json={"name": "App"}
        ).json()
        wf = client.post(
            "/api/workflows", json={**_workflow_body(), "project_id": project["id"]}
        )
        assert wf.status_code == 201, wf.text
        wf_id = wf.json()["id"]
        run = client.post(f"/api/workflows/{wf_id}/run", json={})
        assert run.status_code == 201, run.text
        execution_id = run.json()["id"]

        # Carol (other-org viewer) must be denied every version/execution surface,
        # including reads that expose potentially sensitive snapshot data.
        _login_as(client, "carol@example.com")
        assert client.get(f"/api/workflows/{wf_id}/versions").status_code == 403
        assert (
            client.get(
                f"/api/workflows/{wf_id}/versions/diff",
                params={"base_id": "x", "target_id": "y"},
            ).status_code
            == 403
        )
        assert client.get(f"/api/workflows/{wf_id}/executions").status_code == 403
        assert client.get(f"/api/executions/{execution_id}").status_code == 403
        assert client.get(f"/api/executions/{execution_id}/inspection").status_code == 403
        assert client.get(f"/api/executions/{execution_id}/events").status_code == 403
        assert client.post(f"/api/executions/{execution_id}/cancel").status_code == 403
        assert client.post(
            f"/api/executions/{execution_id}/rerun", json={"node_id": "start"}
        ).status_code == 403
        assert client.post(
            f"/api/executions/{execution_id}/resume", json={"decision": {}}
        ).status_code == 403


def test_org_must_retain_at_least_one_admin(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        admin_user = _register(client, "admin@example.com")
        org = client.post("/api/organizations", json={"name": "Acme"}).json()
        org_id = org["id"]

        # The creator is the only admin; demotion and removal are blocked.
        assert client.put(
            f"/api/organizations/{org_id}/members/{admin_user['id']}",
            json={"role": "viewer"},
        ).status_code == 409
        assert client.delete(
            f"/api/organizations/{org_id}/members/{admin_user['id']}"
        ).status_code == 409

        # Adding a second admin unlocks demotion/removal of the first.
        _register(client, "carol@example.com")
        _login_as(client, "admin@example.com")
        assert client.post(
            f"/api/organizations/{org_id}/members",
            json={"email": "carol@example.com", "role": "admin"},
        ).status_code == 201
        assert client.put(
            f"/api/organizations/{org_id}/members/{admin_user['id']}",
            json={"role": "viewer"},
        ).status_code == 200
