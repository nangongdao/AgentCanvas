"""D3 rotating refresh-token lifecycle and replay-defence tests."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import AUTH_COOKIE_NAME
from app.core.config import Settings, validate_runtime_settings
from app.core.refresh_tokens import REFRESH_COOKIE_NAME
from app.db.models import RefreshToken, Session
from app.main import create_app

ADMIN_TOKEN = "admin-token-with-more-than-16-characters"
EMAIL = "refresh@example.com"
PASSWORD = "refresh-pass-word"


def auth_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        auth_session_ttl_seconds=900,
        auth_refresh_ttl_seconds=3600,
    )


def _register(client: TestClient) -> httpx2.Response:
    response = client.post(
        "/api/auth/register",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "display_name": "Refresh User",
            "role": "editor",
        },
    )
    assert response.status_code == 201, response.text
    return response


def _raw_cookie(response: httpx2.Response, name: str) -> str:
    value = response.cookies.get(name)
    assert value
    return value


def _cookie_header(name: str, value: str) -> dict[str, str]:
    return {"Cookie": f"{name}={value}"}


def test_user_login_sets_short_access_and_scoped_refresh_cookies(tmp_path: Path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        response = _register(client)

        headers = response.headers.get_list("set-cookie")
        access = next(item for item in headers if item.startswith(f"{AUTH_COOKIE_NAME}="))
        refresh = next(item for item in headers if item.startswith(f"{REFRESH_COOKIE_NAME}="))
        assert "HttpOnly" in access
        assert "Max-Age=900" in access
        assert "Path=/" in access
        assert "HttpOnly" in refresh
        assert "Path=/api/auth" in refresh
        assert "SameSite=strict" in refresh


def test_refresh_rotates_both_tokens_and_replay_revokes_the_family(tmp_path: Path) -> None:
    app = create_app(auth_settings(tmp_path))
    with TestClient(app) as client:
        registered = _register(client)
        old_access = _raw_cookie(registered, AUTH_COOKIE_NAME)
        old_refresh = _raw_cookie(registered, REFRESH_COOKIE_NAME)

        rotated = client.post("/api/auth/refresh")
        assert rotated.status_code == 200, rotated.text
        new_access = _raw_cookie(rotated, AUTH_COOKIE_NAME)
        new_refresh = _raw_cookie(rotated, REFRESH_COOKIE_NAME)
        assert new_access != old_access
        assert new_refresh != old_refresh
        assert client.get("/api/auth/me").status_code == 200
        assert (
            client.get(
                "/api/auth/me", headers=_cookie_header(AUTH_COOKIE_NAME, old_access)
            ).status_code
            == 401
        )

        client.cookies.clear()
        replay = client.post(
            "/api/auth/refresh",
            headers=_cookie_header(REFRESH_COOKIE_NAME, old_refresh),
        )
        assert replay.status_code == 401
        assert replay.json()["detail"] == "refresh token replay detected"
        cleared = replay.headers.get_list("set-cookie")
        assert any(
            item.startswith(f"{AUTH_COOKIE_NAME}=") and "Max-Age=0" in item for item in cleared
        )
        assert any(
            item.startswith(f"{REFRESH_COOKIE_NAME}=") and "Max-Age=0" in item for item in cleared
        )

        assert (
            client.get(
                "/api/auth/me", headers=_cookie_header(AUTH_COOKIE_NAME, new_access)
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/refresh",
                headers=_cookie_header(REFRESH_COOKIE_NAME, new_refresh),
            ).status_code
            == 401
        )


def test_logout_revokes_access_and_refresh_family(tmp_path: Path) -> None:
    with TestClient(create_app(auth_settings(tmp_path))) as client:
        registered = _register(client)
        access = _raw_cookie(registered, AUTH_COOKIE_NAME)
        refresh = _raw_cookie(registered, REFRESH_COOKIE_NAME)

        assert client.post("/api/auth/logout").status_code == 204
        assert (
            client.get("/api/auth/me", headers=_cookie_header(AUTH_COOKIE_NAME, access)).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/refresh",
                headers=_cookie_header(REFRESH_COOKIE_NAME, refresh),
            ).status_code
            == 401
        )


def test_database_stores_only_token_hashes_and_rotation_lineage(tmp_path: Path) -> None:
    app = create_app(auth_settings(tmp_path))
    with TestClient(app) as client:
        registered = _register(client)
        first_access = _raw_cookie(registered, AUTH_COOKIE_NAME)
        first_refresh = _raw_cookie(registered, REFRESH_COOKIE_NAME)
        rotated = client.post("/api/auth/refresh")
        second_access = _raw_cookie(rotated, AUTH_COOKIE_NAME)
        second_refresh = _raw_cookie(rotated, REFRESH_COOKIE_NAME)

        async def _assert_rows() -> None:
            async with app.state.container.session_factory() as db:
                refresh_rows = list(
                    (await db.execute(select(RefreshToken).order_by(RefreshToken.created_at)))
                    .scalars()
                    .all()
                )
                session_rows = list((await db.execute(select(Session))).scalars().all())
            assert len(refresh_rows) == 2
            assert refresh_rows[0].family_id == refresh_rows[1].family_id
            assert refresh_rows[0].consumed_at is not None
            assert refresh_rows[0].replaced_by_id == refresh_rows[1].id
            assert refresh_rows[0].token_hash == hashlib.sha256(first_refresh.encode()).hexdigest()
            assert refresh_rows[1].token_hash == hashlib.sha256(second_refresh.encode()).hexdigest()
            assert all(
                raw not in {row.token_hash for row in refresh_rows}
                for raw in (first_refresh, second_refresh)
            )
            assert {row.token_hash for row in session_rows} == {
                hashlib.sha256(first_access.encode()).hexdigest(),
                hashlib.sha256(second_access.encode()).hexdigest(),
            }

        asyncio.run(_assert_rows())


def test_refresh_ttl_configuration_is_bounded(tmp_path: Path) -> None:
    base = Settings(data_dir=tmp_path, environment="test")
    with pytest.raises(RuntimeError, match="AUTH_SESSION_TTL_SECONDS"):
        validate_runtime_settings(replace(base, auth_session_ttl_seconds=59))
    with pytest.raises(RuntimeError, match="AUTH_REFRESH_TTL_SECONDS"):
        validate_runtime_settings(
            replace(
                base,
                auth_session_ttl_seconds=900,
                auth_refresh_ttl_seconds=900,
            )
        )
