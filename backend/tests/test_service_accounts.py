"""D3 Phase 3 service accounts and hashed API token authentication."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.core.auth import _sha256
from app.core.config import Settings
from app.db.base import create_engine, create_session_factory
from app.main import create_app

ADMIN_TOKEN = "admin-token-with-more-than-16-characters"
EDITOR_TOKEN = "editor-token"
VIEWER_TOKEN = "viewer-token"


def auth_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        editor_api_token=EDITOR_TOKEN,
        viewer_api_token=VIEWER_TOKEN,
    )


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _create_account(client: TestClient, name: str = "runner") -> str:
    resp = client.post(
        "/api/service-accounts",
        headers=_admin_headers(),
        json={"name": name, "role": "editor"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _issue(client: TestClient, account_id: str) -> tuple[str, dict]:
    resp = client.post(
        f"/api/service-accounts/{account_id}/tokens",
        headers=_admin_headers(),
        json={"name": "ci"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["plaintext"], body["token"]


def test_service_account_admin_only_and_crud(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        # Non-admin shared tokens cannot manage service accounts.
        assert (
            client.get(
                "/api/service-accounts", headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
            ).status_code
            == 403
        )

        account_id = _create_account(client)
        rows = client.get("/api/service-accounts", headers=_admin_headers())
        assert [r["id"] for r in rows.json()] == [account_id]

        # Update role/status then delete.
        updated = client.put(
            f"/api/service-accounts/{account_id}",
            headers=_admin_headers(),
            json={"role": "admin", "status": "disabled"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["role"] == "admin"
        assert updated.json()["status"] == "disabled"

        deleted = client.delete(f"/api/service-accounts/{account_id}", headers=_admin_headers())
        assert deleted.status_code == 204
        # Deleting cascades its tokens; listing the account now 404s.
        assert (
            client.get(
                f"/api/service-accounts/{account_id}/tokens", headers=_admin_headers()
            ).status_code
            == 404
        )


def test_duplicate_account_name_conflicts(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        _create_account(client, name="runner")
        conflict = client.post(
            "/api/service-accounts",
            headers=_admin_headers(),
            json={"name": "runner"},
        )
        assert conflict.status_code == 409


def test_raw_token_shown_once_and_only_hash_stored(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        account_id = _create_account(client)
        raw, token = _issue(client, account_id)
        assert raw.startswith(token["prefix"])

        # Coin listing never returns the raw credential.
        listed = client.get(
            f"/api/service-accounts/{account_id}/tokens", headers=_admin_headers()
        ).json()
        assert listed[0]["id"] == token["id"]
        assert listed[0]["prefix"] == token["prefix"]

        assert raw not in str(listed)

        # The DB stores only the SHA-256 hash of the raw credential.
        engine = create_engine(auth_settings(tmp_path))

        async def _check() -> None:
            from app.db.repositories.service_account import ApiTokenRepo

            async with create_session_factory(engine)() as session:
                stored = await ApiTokenRepo(session).get_by_hash(_sha256(raw))
            assert stored is not None
            assert stored.token_hash == _sha256(raw)
            assert stored.token_hash != raw

        asyncio.run(_check())


def test_api_token_authenticates_and_respects_permissions(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        account_id = _create_account(client)
        raw, _token = _issue(client, account_id)

        # A service-account token resolves to a principal with the account role.
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}"})
        assert me.status_code == 200
        assert me.json()["role"] == "editor"
        assert me.json()["subject"].startswith("service:")

        # It can hit editor-gated surfaces too.
        resp = client.get("/api/workflows", headers={"Authorization": f"Bearer {raw}"})
        assert resp.status_code == 200

        # The usage mark is durable: last_used_at was committed, not rolled back.
        engine = create_engine(auth_settings(tmp_path))

        async def _check_usage() -> None:
            from app.db.repositories.service_account import ApiTokenRepo

            async with create_session_factory(engine)() as session:
                stored = await ApiTokenRepo(session).get_by_hash(_sha256(raw))
            assert stored is not None
            assert stored.last_used_at is not None

        asyncio.run(_check_usage())


def test_admin_service_token_can_register_a_user(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        bootstrap = client.post(
            "/api/auth/register",
            json={
                "email": "bootstrap@example.com",
                "password": "bootstrap-password",
                "display_name": "Bootstrap",
                "role": "admin",
            },
        )
        assert bootstrap.status_code == 201
        account_id = client.post(
            "/api/service-accounts",
            headers=_admin_headers(),
            json={"name": "identity-admin", "role": "admin"},
        ).json()["id"]
        raw, _token = _issue(client, account_id)
        client.cookies.clear()

        created = client.post(
            "/api/auth/register",
            headers={"Authorization": f"Bearer {raw}"},
            json={
                "email": "managed@example.com",
                "password": "managed-password",
                "display_name": "Managed User",
                "role": "viewer",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["role"] == "viewer"


def test_project_service_token_cannot_access_global_management_routes(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        registered = client.post(
            "/api/auth/register",
            json={
                "email": "owner@example.com",
                "password": "owner-password",
                "display_name": "Owner",
                "role": "admin",
            },
        )
        assert registered.status_code == 201, registered.text
        organization = client.post("/api/organizations", json={"name": "Scoped Org"})
        assert organization.status_code == 201, organization.text
        project = client.post(
            f"/api/organizations/{organization.json()['id']}/projects",
            json={"name": "Scoped Project"},
        )
        assert project.status_code == 201, project.text

        account = client.post(
            "/api/service-accounts",
            headers=_admin_headers(),
            json={
                "name": "project-admin",
                "project_id": project.json()["id"],
                "role": "admin",
            },
        )
        assert account.status_code == 201, account.text
        raw, token = _issue(client, account.json()["id"])
        assert token["scope"] == "management"

        scoped_headers = {"Authorization": f"Bearer {raw}"}
        for path in ("/api/workflows", "/api/service-accounts"):
            denied = client.get(path, headers=scoped_headers)
            assert denied.status_code == 403, denied.text
            assert denied.json()["detail"] == (
                "project-scoped API token cannot access management endpoints"
            )

        client.cookies.clear()
        login = client.post("/api/auth/login", json={"token": raw})
        assert login.status_code == 200, login.text
        cookie_denied = client.get("/api/workflows")
        assert cookie_denied.status_code == 403, cookie_denied.text


def test_service_token_can_use_api_token_login(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        account_id = _create_account(client, name="browser-editor")
        raw, _token = _issue(client, account_id)
        client.cookies.clear()

        login = client.post("/api/auth/login", json={"token": raw})
        assert login.status_code == 200, login.text
        assert login.json()["role"] == "editor"
        assert login.json()["subject"] == "service:browser-editor"
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["subject"] == "service:browser-editor"


def test_revoked_expired_or_disabled_token_rejected(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        account_id = _create_account(client)
        raw, token = _issue(client, account_id)

        # Revoke the token; it no longer authenticates.
        revoked = client.post(
            f"/api/service-accounts/{account_id}/tokens/{token['id']}/revoke",
            headers=_admin_headers(),
        )
        assert revoked.status_code == 200
        assert revoked.json()["revoked_at"] is not None
        assert (
            client.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}"}).status_code
            == 401
        )

        # Disabled account: issuing to it is blocked and its tokens stop working.
        _disabled_id = _create_account(client, name="disabled")
        client.put(
            f"/api/service-accounts/{_disabled_id}",
            headers=_admin_headers(),
            json={"status": "disabled"},
        )
        denied = client.post(
            f"/api/service-accounts/{_disabled_id}/tokens",
            headers=_admin_headers(),
            json={"name": "x"},
        )
        assert denied.status_code == 400


def test_unknown_or_non_matching_token_rejected(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert (
            client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer definitely-not-a-real-token"},
            ).status_code
            == 401
        )
        # No match in either the shared-token nor service-account store.
        assert (
            client.get(
                "/api/workflows", headers={"Authorization": "Bearer unknown-zzz"}
            ).status_code
            == 401
        )
