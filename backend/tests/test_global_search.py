"""C5-2 tenant-safe global resource search contracts."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_tenancy import (
    ADMIN_TOKEN,
    _login_as,
    _register,
    _workflow_body,
    auth_settings,
)


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _create_project(client: TestClient, name: str) -> str:
    organization = client.post("/api/organizations", json={"name": f"{name} Org"})
    assert organization.status_code == 201, organization.text
    project = client.post(
        f"/api/organizations/{organization.json()['id']}/projects",
        json={"name": name},
    )
    assert project.status_code == 201, project.text
    return project.json()["id"]


def test_global_search_finds_ranked_resources_with_navigation_context(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "search-admin@example.com")
        project_id = _create_project(client, "Atlas Project")

        exact = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json=_workflow_body("Atlas"),
        )
        prefix = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json=_workflow_body("Atlas Workflow"),
        )
        contains = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json=_workflow_body("Field Atlas Notes"),
        )
        description_only = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json={**_workflow_body("Reference Manual"), "description": "Atlas"},
        )
        assert (
            exact.status_code
            == prefix.status_code
            == contains.status_code
            == description_only.status_code
            == 201
        )

        app = client.post(
            "/api/apps",
            headers=_admin_headers(),
            json={"project_id": project_id, "name": "Atlas Operator"},
        )
        assert app.status_code == 201, app.text

        knowledge_base = client.post(
            "/api/knowledge-bases",
            headers=_admin_headers(),
            json={"project_id": project_id, "name": "Atlas Knowledge"},
        )
        assert knowledge_base.status_code == 201, knowledge_base.text
        kb_id = knowledge_base.json()["id"]
        document = client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            headers=_admin_headers(),
            files={"file": ("atlas-runbook.txt", b"operator notes", "text/plain")},
        )
        assert document.status_code == 201, document.text

        response = client.get(
            "/api/search",
            params={"q": "  ATLAS  ", "limit": 20},
            headers=_admin_headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["query"] == "ATLAS"
        assert body["items"][0]["id"] == exact.json()["id"]
        assert [row["id"] for row in body["items"][:3]] == [
            exact.json()["id"],
            prefix.json()["id"],
            app.json()["app"]["id"],
        ]

        by_kind = {row["kind"]: row for row in body["items"]}
        assert set(by_kind) == {"workflow", "app", "knowledge_base", "document"}
        assert by_kind["workflow"]["project_id"] is None
        assert by_kind["app"]["project_id"] == project_id
        assert by_kind["knowledge_base"]["id"] == kb_id
        assert by_kind["document"]["id"] == document.json()["id"]
        assert by_kind["document"]["parent_id"] == kb_id
        assert by_kind["document"]["subtitle"] == "Atlas Knowledge"


def test_global_search_is_bounded_and_excludes_archived_workflows(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        created_ids: list[str] = []
        for index in range(5):
            created = client.post(
                "/api/workflows",
                headers=_admin_headers(),
                json=_workflow_body(f"Bounded Search {index}"),
            )
            assert created.status_code == 201, created.text
            created_ids.append(created.json()["id"])
        archived = client.delete(
            f"/api/workflows/{created_ids[0]}", headers=_admin_headers()
        )
        assert archived.status_code == 204, archived.text

        response = client.get(
            "/api/search",
            params={"q": "Bounded", "limit": 3},
            headers=_admin_headers(),
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 3
        assert created_ids[0] not in {row["id"] for row in response.json()["items"]}

        assert (
            client.get(
                "/api/search", params={"q": "Bounded", "limit": 31}, headers=_admin_headers()
            ).status_code
            == 422
        )
        assert client.get("/api/search", params={"q": "Bounded"}).status_code == 401
        assert client.get(
            "/api/search", params={"q": "%_"}, headers=_admin_headers()
        ).json()["items"] == []
        assert client.get(
            "/api/search", params={"q": "   "}, headers=_admin_headers()
        ).status_code == 422


def test_global_search_never_leaks_resources_outside_memberships(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "owner@example.com")
        project_id = _create_project(client, "Private Project")
        private = client.post(
            "/api/workflows",
            json={**_workflow_body("Needle Private Workflow"), "project_id": project_id},
        )
        assert private.status_code == 201, private.text
        global_row = client.post(
            "/api/workflows",
            headers=_admin_headers(),
            json=_workflow_body("Needle Global Workflow"),
        )
        assert global_row.status_code == 201, global_row.text

        owner_results = client.get("/api/search", params={"q": "Needle"})
        assert owner_results.status_code == 200, owner_results.text
        assert {row["id"] for row in owner_results.json()["items"]} == {
            private.json()["id"],
            global_row.json()["id"],
        }

        _register(client, "outsider@example.com", role="viewer")
        _login_as(client, "outsider@example.com")
        outsider_results = client.get("/api/search", params={"q": "Needle"})
        assert outsider_results.status_code == 200, outsider_results.text
        assert [row["id"] for row in outsider_results.json()["items"]] == [
            global_row.json()["id"]
        ]
