"""Durable workflow comment/review behavior and authorization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_auth import (
    ADMIN_TOKEN,
    EDITOR_TOKEN,
    VIEWER_TOKEN,
    auth_settings,
    bearer,
    workflow_body,
)
from tests.test_tenancy import PASSWORD


def _version(client: TestClient, workflow_id: str) -> dict[str, Any]:
    response = client.get(f"/api/workflows/{workflow_id}/versions", headers=bearer(VIEWER_TOKEN))
    assert response.status_code == 200, response.text
    return response.json()[0]


def _register(client: TestClient, email: str) -> dict[str, Any]:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": email.split("@")[0],
            "role": "editor",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str) -> None:
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def test_comment_threads_resolution_and_review_decisions(tmp_path: Path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        workflow = client.post(
            "/api/workflows", headers=bearer(EDITOR_TOKEN), json=workflow_body()
        ).json()
        version = _version(client, workflow["id"])
        base = f"/api/workflows/{workflow['id']}"

        root = client.post(
            f"{base}/comments",
            headers=bearer(VIEWER_TOKEN),
            json={
                "version_id": version["id"],
                "node_id": "end",
                "body": "Please verify this output.",
            },
        )
        assert root.status_code == 201, root.text
        assert root.json()["author_subject"] == "token:viewer"
        assert root.json()["node_id"] == "end"

        missing_node = client.post(
            f"{base}/comments",
            headers=bearer(VIEWER_TOKEN),
            json={
                "version_id": version["id"],
                "node_id": "missing-node",
                "body": "This anchor must not be accepted.",
            },
        )
        assert missing_node.status_code == 404
        assert missing_node.json()["detail"] == "workflow version node not found"

        reply = client.post(
            f"{base}/comments",
            headers=bearer(EDITOR_TOKEN),
            json={
                "version_id": version["id"],
                "parent_comment_id": root.json()["id"],
                "body": "Verified.",
            },
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["parent_comment_id"] == root.json()["id"]

        listed = client.get(
            f"{base}/comments",
            headers=bearer(VIEWER_TOKEN),
            params={"version_id": version["id"], "order": "asc"},
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["items"]] == [
            root.json()["id"],
            reply.json()["id"],
        ]
        assert (
            client.put(
                f"{base}/comments/{root.json()['id']}/resolution",
                headers=bearer(VIEWER_TOKEN),
                json={"resolved": True},
            ).status_code
            == 403
        )
        resolved = client.put(
            f"{base}/comments/{root.json()['id']}/resolution",
            headers=bearer(EDITOR_TOKEN),
            json={"resolved": True},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["resolved_at"]
        assert resolved.json()["resolved_by_subject"] == "token:editor"
        reopened = client.put(
            f"{base}/comments/{root.json()['id']}/resolution",
            headers=bearer(EDITOR_TOKEN),
            json={"resolved": False},
        )
        assert reopened.status_code == 200
        assert reopened.json()["resolved_at"] is None

        requested = client.post(
            f"{base}/reviews",
            headers=bearer(EDITOR_TOKEN),
            json={"version_id": version["id"], "summary": "Ready for review"},
        )
        assert requested.status_code == 201, requested.text
        review = requested.json()
        assert review["status"] == "open"
        assert review["requester_is_current_actor"] is True
        assert (
            client.post(
                f"{base}/reviews",
                headers=bearer(EDITOR_TOKEN),
                json={"version_id": version["id"]},
            ).status_code
            == 409
        )
        self_decision = client.put(
            f"{base}/reviews/{review['id']}/decision",
            headers=bearer(EDITOR_TOKEN),
            json={"decision": "approved", "summary": "Self approve"},
        )
        assert self_decision.status_code == 409

        approved = client.put(
            f"{base}/reviews/{review['id']}/decision",
            headers=bearer(ADMIN_TOKEN),
            json={"decision": "approved", "summary": "Looks good"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved"
        assert approved.json()["decided_by_subject"] == "token:admin"
        assert approved.json()["requester_is_current_actor"] is False
        assert (
            client.put(
                f"{base}/reviews/{review['id']}/decision",
                headers=bearer(ADMIN_TOKEN),
                json={"decision": "changes_requested"},
            ).status_code
            == 409
        )


def test_foreign_version_parent_and_cursor_scope_are_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        first = client.post(
            "/api/workflows", headers=bearer(EDITOR_TOKEN), json=workflow_body()
        ).json()
        second = client.post(
            "/api/workflows", headers=bearer(EDITOR_TOKEN), json=workflow_body()
        ).json()
        first_version = _version(client, first["id"])
        second_version = _version(client, second["id"])
        first_base = f"/api/workflows/{first['id']}"
        second_base = f"/api/workflows/{second['id']}"

        foreign_version = client.post(
            f"{first_base}/comments",
            headers=bearer(VIEWER_TOKEN),
            json={"version_id": second_version["id"], "body": "Wrong workflow"},
        )
        assert foreign_version.status_code == 404
        assert (
            client.post(
                f"{first_base}/reviews",
                headers=bearer(EDITOR_TOKEN),
                json={"version_id": second_version["id"]},
            ).status_code
            == 404
        )

        other_comment = client.post(
            f"{second_base}/comments",
            headers=bearer(VIEWER_TOKEN),
            json={"version_id": second_version["id"], "body": "Other"},
        ).json()
        other_review = client.post(
            f"{second_base}/reviews",
            headers=bearer(EDITOR_TOKEN),
            json={"version_id": second_version["id"], "summary": "Other review"},
        ).json()
        foreign_parent = client.post(
            f"{first_base}/comments",
            headers=bearer(VIEWER_TOKEN),
            json={
                "version_id": first_version["id"],
                "parent_comment_id": other_comment["id"],
                "body": "Wrong parent",
            },
        )
        assert foreign_parent.status_code == 404
        foreign_review = client.put(
            f"{first_base}/reviews/{other_review['id']}/decision",
            headers=bearer(EDITOR_TOKEN),
            json={"decision": "dismissed"},
        )
        assert foreign_review.status_code == 404

        for index in range(2):
            response = client.post(
                f"{first_base}/comments",
                headers=bearer(VIEWER_TOKEN),
                json={"version_id": first_version["id"], "body": f"Comment {index}"},
            )
            assert response.status_code == 201
        page = client.get(
            f"{first_base}/comments",
            headers=bearer(VIEWER_TOKEN),
            params={"version_id": first_version["id"], "limit": 1},
        ).json()
        assert page["next_cursor"]
        mismatched = client.get(
            f"{first_base}/comments",
            headers=bearer(VIEWER_TOKEN),
            params={"cursor": page["next_cursor"], "limit": 1},
        )
        assert mismatched.status_code == 422

        first_review = client.post(
            f"{first_base}/reviews",
            headers=bearer(EDITOR_TOKEN),
            json={"version_id": first_version["id"], "summary": "First version"},
        )
        assert first_review.status_code == 201
        updated = client.put(
            f"/api/workflows/{first['id']}",
            headers=bearer(EDITOR_TOKEN),
            json={"name": "Second version", "version": 1},
        )
        assert updated.status_code == 200, updated.text
        next_version = _version(client, first["id"])
        assert next_version["number"] == 2
        second_review = client.post(
            f"{first_base}/reviews",
            headers=bearer(EDITOR_TOKEN),
            json={"version_id": next_version["id"], "summary": "Second version"},
        )
        assert second_review.status_code == 201
        review_page = client.get(
            f"{first_base}/reviews",
            headers=bearer(VIEWER_TOKEN),
            params={"limit": 1},
        ).json()
        assert review_page["next_cursor"]
        mismatched_review_cursor = client.get(
            f"{first_base}/reviews",
            headers=bearer(VIEWER_TOKEN),
            params={
                "version_id": first_version["id"],
                "cursor": review_page["next_cursor"],
                "limit": 1,
            },
        )
        assert mismatched_review_cursor.status_code == 422


def test_every_review_and_merge_surface_enforces_tenant_scope(tmp_path: Path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        org_one = client.post("/api/organizations", json={"name": "One"}).json()
        org_two = client.post("/api/organizations", json={"name": "Two"}).json()
        _register(client, "alice@example.com")
        _login(client, "admin@example.com")
        _register(client, "bob@example.com")
        _login(client, "admin@example.com")
        client.post(
            f"/api/organizations/{org_one['id']}/members",
            json={"email": "alice@example.com", "role": "editor"},
        )
        client.post(
            f"/api/organizations/{org_two['id']}/members",
            json={"email": "bob@example.com", "role": "editor"},
        )

        _login(client, "alice@example.com")
        project = client.post(
            f"/api/organizations/{org_one['id']}/projects", json={"name": "App"}
        ).json()
        body = workflow_body()
        workflow = client.post("/api/workflows", json={**body, "project_id": project["id"]}).json()
        versions = client.get(f"/api/workflows/{workflow['id']}/versions").json()
        version = versions[0]
        base = f"/api/workflows/{workflow['id']}"
        comment = client.post(
            f"{base}/comments",
            json={"version_id": version["id"], "body": "Tenant comment"},
        ).json()
        review = client.post(
            f"{base}/reviews",
            json={"version_id": version["id"], "summary": "Tenant review"},
        ).json()

        _login(client, "bob@example.com")
        merge_body = {
            "base_version_id": version["id"],
            "remote_version": 1,
            "local_name": workflow["name"],
            "local_dsl": workflow["dsl"],
        }
        requests = [
            client.get(f"{base}/comments", params={"version_id": version["id"]}),
            client.post(
                f"{base}/comments",
                json={"version_id": version["id"], "body": "Denied"},
            ),
            client.put(
                f"{base}/comments/{comment['id']}/resolution",
                json={"resolved": True},
            ),
            client.get(f"{base}/reviews", params={"version_id": version["id"]}),
            client.post(f"{base}/reviews", json={"version_id": version["id"]}),
            client.put(
                f"{base}/reviews/{review['id']}/decision",
                json={"decision": "approved"},
            ),
            client.post(f"{base}/versions/merge", json=merge_body),
        ]
        assert [response.status_code for response in requests] == [403] * 7
