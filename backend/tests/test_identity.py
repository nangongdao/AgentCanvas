"""D3 identity: local user accounts, sessions, register/login/logout/me, RBAC gates."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from app.core.auth import AUTH_COOKIE_NAME, Principal, Role
from app.core.config import Settings
from app.core.security import hash_password, verify_password
from app.db.repositories import SessionRepo, UserRepo
from app.main import create_app

ADMIN_TOKEN = "admin-token-with-more-than-16-characters"
EDITOR_TOKEN = "editor-token"
VIEWER_TOKEN = "viewer-token"

EMAIL = "alice@example.com"
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


def _register_payload(email: str = EMAIL, *, role: str = "editor") -> dict:
    return {
        "email": email,
        "password": PASSWORD,
        "display_name": "Alice",
        "role": role,
    }


def _raw_token(response) -> str:
    """Extract the raw session token from a Set-Cookie login/register response."""
    header = response.headers.get("set-cookie") or ""
    prefix = f"{AUTH_COOKIE_NAME}="
    for part in header.split(";"):
        part = part.strip()
        if part.startswith(prefix):
            return part[len(prefix) :]
    raise AssertionError(f"no session cookie in {header!r}")


def _cookie_header(raw: str) -> dict[str, str]:
    """Send a raw session token explicitly, bypassing the httpx cookie jar."""
    return {"Cookie": f"{AUTH_COOKIE_NAME}={raw}"}


def test_password_hash_roundtrip_and_malformed() -> None:
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored) is True
    assert verify_password("wrong guess", stored) is False

    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "pbkdf2_sha256$100000$deadbeef") is False
    assert verify_password("x", "pbkdf2_sha256$50000$deadbeef$cafe") is False
    assert verify_password("x", "pbkdf2_sha256$50000$00$00") is False
    assert verify_password("x", "") is False


def test_bootstrap_first_user_is_admin_and_auto_login(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        resp = client.post("/api/auth/register", json=_register_payload())
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["role"] == "admin"
        assert data["status"] == "active"
        assert data["email"] == EMAIL

        me = client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["role"] == "admin"
        assert body["email"] == EMAIL
        assert body["subject"] == EMAIL
        assert body["user_id"] == data["id"]


def test_duplicate_email_register_conflict(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert client.post("/api/auth/register", json=_register_payload()).status_code == 201
        conflict = client.post("/api/auth/register", json=_register_payload())
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "email already registered"


def test_concurrent_bootstrap_creates_exactly_one_admin(tmp_path) -> None:
    first_app = create_app(auth_settings(tmp_path))
    second_app = create_app(auth_settings(tmp_path))

    async def _register(app, email: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/api/auth/register", json=_register_payload(email))

    async def _user_count() -> int:
        async with first_app.state.container.session_factory() as session:
            return await UserRepo(session).count()

    async def _register_both() -> tuple[httpx.Response, httpx.Response]:
        first, second = await asyncio.gather(
            _register(first_app, "bootstrap-one@example.com"),
            _register(second_app, "bootstrap-two@example.com"),
        )
        return first, second

    with TestClient(first_app), TestClient(second_app):
        first, second = asyncio.run(_register_both())
        assert sorted((first.status_code, second.status_code)) == [201, 403]
        successful = first if first.status_code == 201 else second
        assert successful.json()["role"] == "admin"
        assert asyncio.run(_user_count()) == 1


def test_registration_requires_admin_after_bootstrap(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert client.post("/api/auth/register", json=_register_payload()).status_code == 201

        # Anonymous registration is denied once a user exists.
        client.cookies.clear()
        denied = client.post("/api/auth/register", json=_register_payload(email="bob@example.com"))
        assert denied.status_code == 403
        assert denied.json()["detail"] == "admin role required to register users"

        # Admin can mint an editor.
        login = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 200, login.text
        assert login.json()["role"] == "admin"
        created = client.post("/api/auth/register", json=_register_payload(email="bob@example.com"))
        assert created.status_code == 201
        assert created.json()["role"] == "editor"
        assert created.headers.get_list("set-cookie") == []
        assert client.get("/api/auth/me").json()["email"] == EMAIL

        # A non-admin (editor) session cannot register more users.
        client.cookies.clear()
        editor_login = client.post(
            "/api/auth/login",
            json={"email": "bob@example.com", "password": PASSWORD},
        )
        assert editor_login.json()["role"] == "editor"
        denied_again = client.post(
            "/api/auth/register", json=_register_payload(email="carol@example.com")
        )
        assert denied_again.status_code == 403


def test_login_requires_valid_credentials(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert client.post("/api/auth/register", json=_register_payload()).status_code == 201
        wrong_password = client.post(
            "/api/auth/login", json={"email": EMAIL, "password": "not-the-password"}
        )
        assert wrong_password.status_code == 401
        assert wrong_password.json()["detail"] == "invalid email or password"

        unknown = client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": PASSWORD},
        )
        assert unknown.status_code == 401


def test_register_login_logout_me_lifecycle(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        registered = client.post("/api/auth/register", json=_register_payload())
        old_raw = _raw_token(registered)

        assert client.get("/api/auth/me").status_code == 200

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 204
        # The delete-cookie clears the jar; an unauthenticated caller is rejected.
        assert client.get("/api/auth/me").status_code == 401

        # A revoked raw token must not authenticate even if replayed.
        replay = client.get("/api/auth/me", headers=_cookie_header(old_raw))
        assert replay.status_code == 401

        # A fresh login issues a new session and works again.
        login = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 200, login.text
        new_raw = _raw_token(login)
        assert new_raw != old_raw
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"


def test_expired_session_is_rejected(tmp_path) -> None:
    app = create_app(auth_settings(tmp_path))
    with TestClient(app) as client:
        assert client.post("/api/auth/register", json=_register_payload()).status_code == 201
        raw = "already-expired-session-raw"

        async def _insert_expired_session() -> None:
            async with app.state.container.session_factory() as session:
                user = await UserRepo(session).get_by_email(EMAIL)
                assert user is not None
                await SessionRepo(session).create(
                    user.id,
                    hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    datetime.now(UTC) - timedelta(hours=1),
                )
                await session.commit()

        asyncio.run(_insert_expired_session())

        assert client.get("/api/auth/me", headers=_cookie_header(raw)).status_code == 401


def test_token_login_me_and_invalid_token(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        login = client.post("/api/auth/login", json={"token": ADMIN_TOKEN})
        assert login.status_code == 200
        assert login.json()["role"] == "admin"
        assert login.json()["subject"] == "token:admin"
        set_cookies = login.headers.get_list("set-cookie")
        assert sum(item.startswith(f"{AUTH_COOKIE_NAME}=") for item in set_cookies) == 1
        assert any(item.startswith("agentcanvas_refresh=") for item in set_cookies)

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"
        assert me.json()["user_id"] is None

        assert client.post("/api/auth/logout").status_code == 204

        invalid = client.post("/api/auth/login", json={"token": "wrong-token"})
        assert invalid.status_code == 401
        assert invalid.json()["detail"] == "invalid API token"


def test_token_path_regression_after_users_exist(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert client.post("/api/auth/register", json=_register_payload()).status_code == 201
        headers = {"Authorization": f"Bearer {EDITOR_TOKEN}"}
        assert client.get("/api/workflows", headers=headers).status_code == 200
        assert (
            client.post(
                "/api/workflows",
                headers=headers,
                json={
                    "name": "Token user",
                    "dsl": {
                        "version": "1.0",
                        "name": "Token user",
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
                },
            ).status_code
            == 201
        )


def test_login_rejects_ambiguous_or_empty_credentials(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        both = client.post(
            "/api/auth/login",
            json={"token": ADMIN_TOKEN, "email": EMAIL, "password": PASSWORD},
        )
        assert both.status_code == 422

        neither = client.post("/api/auth/login", json={})
        assert neither.status_code == 422


def test_role_hierarchy_used_for_register_gate(tmp_path) -> None:
    viewer = Principal("v", Role.VIEWER, "test")
    editor = Principal("e", Role.EDITOR, "test")
    admin = Principal("a", Role.ADMIN, "test")
    assert viewer.can(Role.VIEWER) is True
    assert viewer.can(Role.EDITOR) is False
    assert editor.can(Role.VIEWER) is True
    assert editor.can(Role.ADMIN) is False
    assert admin.can(Role.VIEWER) is True
    assert admin.can(Role.ADMIN) is True


def test_language_preference_roundtrip(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert client.post("/api/auth/register", json=_register_payload()).status_code == 201

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["language"] is None

        patched = client.patch("/api/auth/me", json={"language": "en"})
        assert patched.status_code == 200, patched.text
        assert patched.json()["language"] == "en"

        # The preference survives a logout + fresh login (server-side row).
        assert client.post("/api/auth/logout").status_code == 204
        login = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert login.status_code == 200
        assert client.get("/api/auth/me").json()["language"] == "en"

        # Switching back and invalid values behave symmetrically.
        assert client.patch("/api/auth/me", json={"language": "zh"}).json()["language"] == "zh"
        assert client.patch("/api/auth/me", json={"language": "fr"}).status_code == 422


def test_language_preference_rejected_for_token_subject(tmp_path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        assert client.post("/api/auth/login", json={"token": ADMIN_TOKEN}).status_code == 200
        assert client.get("/api/auth/me").json()["language"] is None
        rejected = client.patch("/api/auth/me", json={"language": "en"})
        assert rejected.status_code == 403
        assert rejected.json()["detail"] == "language preference requires an account session"
