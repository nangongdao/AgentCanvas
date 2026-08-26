"""HTTP, RBAC, and tenant isolation contracts for workflow collaboration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.collaboration import CollaborationUnavailable
from app.core.config import Settings
from app.main import create_app

ADMIN_TOKEN = "admin-token-with-more-than-16-characters"
EDITOR_TOKEN = "editor-token"
VIEWER_TOKEN = "viewer-token"
PASSWORD = "s3cret-pass-word"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workflow_body(name: str = "Collaborative workflow") -> dict[str, Any]:
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


def test_presence_lock_takeover_and_private_lease(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        created = client.post(
            "/api/workflows",
            headers=_headers(EDITOR_TOKEN),
            json=_workflow_body(),
        )
        workflow_id = created.json()["id"]
        base = f"/api/workflows/{workflow_id}/collaboration"

        viewer_presence = client.post(
            f"{base}/heartbeat",
            headers=_headers(VIEWER_TOKEN),
            json={"client_id": "viewer-tab"},
        )
        assert viewer_presence.status_code == 200
        assert viewer_presence.json()["participants"][0]["subject"] == "token:viewer"
        assert (
            client.put(
                f"{base}/lock",
                headers=_headers(VIEWER_TOKEN),
                json={"client_id": "viewer-tab"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"{base}/heartbeat",
                headers=_headers(VIEWER_TOKEN),
                json={"client_id": "viewer-tab", "lease_id": "x" * 43},
            ).status_code
            == 403
        )

        acquired = client.put(
            f"{base}/lock",
            headers=_headers(EDITOR_TOKEN),
            json={"client_id": "editor-one"},
        )
        assert acquired.status_code == 200, acquired.text
        lease_id = acquired.json()["lease_id"]
        assert lease_id and acquired.json()["lock"]["owned_by_self"] is True

        other_view = client.get(
            base,
            headers=_headers(VIEWER_TOKEN),
            params={"client_id": "viewer-tab"},
        )
        assert other_view.status_code == 200
        assert other_view.json()["lease_id"] is None
        assert lease_id not in other_view.text

        blocked = client.put(
            f"{base}/lock",
            headers=_headers(EDITOR_TOKEN),
            json={"client_id": "editor-two"},
        )
        assert blocked.status_code == 409
        takeover = client.put(
            f"{base}/lock",
            headers=_headers(EDITOR_TOKEN),
            json={"client_id": "editor-two", "takeover": True},
        )
        assert takeover.status_code == 200
        replacement = takeover.json()["lease_id"]
        assert replacement and replacement != lease_id

        stale = client.request(
            "DELETE",
            f"{base}/lock",
            headers=_headers(EDITOR_TOKEN),
            json={"client_id": "editor-one", "lease_id": lease_id},
        )
        assert stale.status_code == 409
        released = client.request(
            "DELETE",
            f"{base}/lock",
            headers=_headers(EDITOR_TOKEN),
            json={"client_id": "editor-two", "lease_id": replacement},
        )
        assert released.status_code == 200
        assert released.json()["lock"] is None


def test_snapshot_maps_shared_backend_outage_to_service_unavailable(tmp_path: Path) -> None:
    class UnavailableSnapshotHub:
        async def snapshot(self, *_args: object) -> None:
            raise CollaborationUnavailable(
                "shared collaboration backend unavailable; editing is read-only"
            )

        async def close(self) -> None:
            pass

    with TestClient(create_app(_settings(tmp_path))) as client:
        created = client.post(
            "/api/workflows",
            headers=_headers(EDITOR_TOKEN),
            json=_workflow_body(),
        )
        workflow_id = created.json()["id"]
        app = cast(FastAPI, client.app)
        app.state.container.collaboration_hub = UnavailableSnapshotHub()

        response = client.get(
            f"/api/workflows/{workflow_id}/collaboration",
            headers=_headers(VIEWER_TOKEN),
            params={"client_id": "viewer-client"},
        )

        assert response.status_code == 503
        assert response.json() == {
            "detail": "shared collaboration backend unavailable; editing is read-only"
        }


def test_project_viewer_capability_overrides_global_editor_role(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _register(client, "admin@example.com")
        organization = client.post("/api/organizations", json={"name": "One"}).json()
        project = client.post(
            f"/api/organizations/{organization['id']}/projects",
            json={"name": "App"},
        ).json()
        workflow = client.post(
            "/api/workflows",
            json={**_workflow_body(), "project_id": project["id"]},
        ).json()
        _register(client, "viewer@example.com")
        _login(client, "admin@example.com")
        membership = client.post(
            f"/api/organizations/{organization['id']}/members",
            json={"email": "viewer@example.com", "role": "viewer"},
        )
        assert membership.status_code == 201, membership.text

        _login(client, "viewer@example.com")
        base = f"/api/workflows/{workflow['id']}/collaboration"
        heartbeat = client.post(
            f"{base}/heartbeat",
            json={"client_id": "viewer-client"},
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["can_edit"] is False
        assert client.put(f"{base}/lock", json={"client_id": "viewer-client"}).status_code == 403


def test_every_collaboration_surface_enforces_tenant_scope(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
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
        workflow = client.post(
            "/api/workflows",
            json={**_workflow_body(), "project_id": project["id"]},
        ).json()
        base = f"/api/workflows/{workflow['id']}/collaboration"

        _login(client, "bob@example.com")
        lease = "x" * 43
        requests = [
            client.get(base, params={"client_id": "bob-client"}),
            client.post(f"{base}/heartbeat", json={"client_id": "bob-client"}),
            client.put(f"{base}/lock", json={"client_id": "bob-client"}),
            client.request(
                "DELETE",
                f"{base}/lock",
                json={"client_id": "bob-client", "lease_id": lease},
            ),
            client.delete(f"{base}/presence/bob-client"),
            client.get(f"{base}/stream", params={"client_id": "bob-client"}),
        ]
        assert [response.status_code for response in requests] == [403] * 6


def test_collaboration_ttl_configuration_boundaries(tmp_path: Path) -> None:
    invalid = replace(_settings(tmp_path), collaboration_sweep_interval_seconds=31)
    with (
        pytest.raises(RuntimeError, match="COLLABORATION_SWEEP_INTERVAL_SECONDS"),
        TestClient(create_app(invalid)),
    ):
        pass
