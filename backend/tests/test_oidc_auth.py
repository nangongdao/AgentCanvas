"""D3 OIDC Authorization Code + PKCE integration tests with a local fake IdP."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.auth import AUTH_COOKIE_NAME
from app.core.config import Settings, validate_runtime_settings
from app.core.oidc import OIDC_STATE_COOKIE_NAME, OIDCClaims
from app.core.oidc_config import OIDCSettings
from app.core.refresh_tokens import REFRESH_COOKIE_NAME
from app.db.models import OIDCIdentity, User
from app.db.repositories import OIDCIdentityRepo, UserRepo
from app.main import create_app
from tests.oidc_support import (
    ADMIN_TOKEN,
    ISSUER,
    FakeOIDCProvider,
    oidc_settings,
)
from tests.oidc_support import (
    begin_oidc as _begin,
)
from tests.oidc_support import (
    install_provider as _install_provider,
)
from tests.oidc_support import (
    oidc_callback as _callback,
)


def test_oidc_pkce_login_provisions_user_and_refresh_session(tmp_path: Path) -> None:
    settings = oidc_settings(tmp_path)
    app = create_app(settings)
    provider = FakeOIDCProvider()
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        assert client.get("/api/meta").json()["oidc_enabled"] is True
        params = _begin(client, provider)

        callback = _callback(client, params["state"])
        assert callback.status_code == 303, callback.text
        assert callback.headers["location"] == "/"
        assert callback.cookies.get(AUTH_COOKIE_NAME)
        assert callback.cookies.get(REFRESH_COOKIE_NAME)
        assert OIDC_STATE_COOKIE_NAME not in client.cookies
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "viewer"
        assert me.json()["email"] == "alice@example.com"
        assert me.json()["display_name"] == "Alice Example"
        assert me.json()["user_id"]

        verifier = provider.last_token_form["code_verifier"][0]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == params["code_challenge"]

        async def _assert_identity() -> None:
            async with app.state.container.session_factory() as db:
                identity = (await db.execute(select(OIDCIdentity))).scalar_one()
                user = (await db.execute(select(User))).scalar_one()
            assert identity.issuer == ISSUER
            assert identity.subject == "external-user-1"
            assert identity.user_id == user.id
            assert user.password_hash is None
            assert user.role == "viewer"

        asyncio.run(_assert_identity())

        client.cookies.clear()
        local_login = client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "not-an-oidc-password"},
        )
        assert local_login.status_code == 401


def test_oidc_repeat_login_reuses_identity_and_user(tmp_path: Path) -> None:
    settings = oidc_settings(tmp_path)
    app = create_app(settings)
    provider = FakeOIDCProvider()
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        assert _callback(client, _begin(client, provider)["state"]).status_code == 303
        client.cookies.clear()
        assert _callback(client, _begin(client, provider)["state"]).status_code == 303

        async def _counts() -> tuple[int, int]:
            async with app.state.container.session_factory() as db:
                users = await db.scalar(select(func.count(User.id)))
                identities = await db.scalar(select(func.count(OIDCIdentity.id)))
            return int(users or 0), int(identities or 0)

        assert asyncio.run(_counts()) == (1, 1)


def test_oidc_refreshes_cached_jwks_when_provider_rotates_kid(tmp_path: Path) -> None:
    settings = oidc_settings(tmp_path)
    app = create_app(settings)
    provider = FakeOIDCProvider()
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        assert _callback(client, _begin(client, provider)["state"]).status_code == 303
        assert provider.jwks_requests == 1

        client.cookies.clear()
        params = _begin(client, provider)
        provider.rotate_key()
        assert _callback(client, params["state"]).status_code == 303
        assert provider.jwks_requests == 2


def test_oidc_preserves_trailing_slash_in_canonical_issuer(tmp_path: Path) -> None:
    base = oidc_settings(tmp_path)
    settings = replace(base, oidc=replace(base.oidc, issuer=f"{ISSUER}/"))
    app = create_app(settings)
    provider = FakeOIDCProvider()
    provider.issuer = f"{ISSUER}/"
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        params = _begin(client, provider)
        assert _callback(client, params["state"]).status_code == 303


def test_oidc_rejects_unadvertised_client_auth_method(tmp_path: Path) -> None:
    settings = oidc_settings(tmp_path)
    app = create_app(settings)
    provider = FakeOIDCProvider()
    provider.token_auth_methods = ["none"]
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        callback = _callback(client, _begin(client, provider)["state"])
        assert callback.status_code == 400
        assert callback.json()["detail"] == (
            "OIDC provider does not support the configured client auth"
        )
        assert provider.token_requests == 0


def test_oidc_public_client_rejects_missing_auth_method_metadata(tmp_path: Path) -> None:
    base = oidc_settings(tmp_path)
    settings = replace(base, oidc=replace(base.oidc, client_secret=""))
    app = create_app(settings)
    provider = FakeOIDCProvider()
    provider.token_auth_methods = None
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        callback = _callback(client, _begin(client, provider)["state"])
        assert callback.status_code == 400
        assert callback.json()["detail"] == "OIDC provider does not support public clients"
        assert provider.token_requests == 0


def test_oidc_provisioning_recovers_from_identity_insert_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = oidc_settings(tmp_path)
    app = create_app(settings)

    async def _exercise_race() -> tuple[str, str, int, int]:
        claims = OIDCClaims(
            issuer=ISSUER,
            subject="racing-subject",
            email="race@example.com",
            display_name="Race User",
        )
        async with app.state.container.session_factory() as seed:
            user = await UserRepo(seed).create(
                claims.email,
                None,
                display_name=claims.display_name,
                role="viewer",
            )
            await OIDCIdentityRepo(seed).create(
                issuer=claims.issuer,
                subject=claims.subject,
                user_id=user.id,
                email=claims.email,
            )
            await seed.commit()
            expected_user_id = user.id

        original_get = OIDCIdentityRepo.get
        get_calls = 0

        async def stale_then_current(
            self: OIDCIdentityRepo, issuer: str, subject: str
        ) -> OIDCIdentity | None:
            nonlocal get_calls
            get_calls += 1
            if get_calls == 1:
                return None
            return await original_get(self, issuer, subject)

        async def conflicting_create(self: OIDCIdentityRepo, **_kwargs: object) -> OIDCIdentity:
            raise IntegrityError("INSERT oidc_identities", {}, ValueError("duplicate"))

        monkeypatch.setattr(OIDCIdentityRepo, "get", stale_then_current)
        monkeypatch.setattr(OIDCIdentityRepo, "create", conflicting_create)
        async with app.state.container.session_factory() as db:
            resolved = await app.state.container.oidc_identity_service.resolve_or_provision(
                db, claims
            )
            await db.commit()
            users = await db.scalar(select(func.count(User.id)))
            identities = await db.scalar(select(func.count(OIDCIdentity.id)))
        return resolved.id, expected_user_id, int(users or 0), int(identities or 0)

    with TestClient(app):
        resolved_id, expected_id, users, identities = asyncio.run(_exercise_race())
        assert resolved_id == expected_id
        assert (users, identities) == (1, 1)


def test_oidc_state_mismatch_stops_before_token_exchange(tmp_path: Path) -> None:
    settings = oidc_settings(tmp_path)
    app = create_app(settings)
    provider = FakeOIDCProvider()
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        _begin(client, provider)
        callback = _callback(client, "attacker-state")
        assert callback.status_code == 400
        assert callback.json()["detail"] == "OIDC state mismatch"
        assert provider.token_requests == 0
        assert OIDC_STATE_COOKIE_NAME not in client.cookies


@pytest.mark.parametrize("failure", ["unverified_email", "invalid_signature"])
def test_oidc_rejects_untrusted_identity_claims(tmp_path: Path, failure: str) -> None:
    settings = oidc_settings(tmp_path)
    app = create_app(settings)
    provider = FakeOIDCProvider()
    if failure == "unverified_email":
        provider.email_verified = False
    else:
        provider.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        callback = _callback(client, _begin(client, provider)["state"])
        assert callback.status_code == 400
        expected = (
            "OIDC provider did not supply a verified email"
            if failure == "unverified_email"
            else "OIDC id_token validation failed"
        )
        assert callback.json()["detail"] == expected

        async def _user_count() -> int:
            async with app.state.container.session_factory() as db:
                value = await db.scalar(select(func.count(User.id)))
            return int(value or 0)

        assert asyncio.run(_user_count()) == 0


def test_oidc_disabled_and_configuration_validation(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get("/api/meta").json()["oidc_enabled"] is False
        assert client.get("/api/auth/oidc/start").status_code == 404

    with pytest.raises(RuntimeError, match="configured together"):
        validate_runtime_settings(
            Settings(
                data_dir=tmp_path,
                environment="test",
                auth_mode="token",
                admin_api_token=ADMIN_TOKEN,
                oidc=OIDCSettings(issuer=ISSUER),
            )
        )
    with pytest.raises(RuntimeError, match="protocol-relative"):
        validate_runtime_settings(
            replace(
                oidc_settings(tmp_path),
                oidc=replace(
                    oidc_settings(tmp_path).oidc,
                    post_login_redirect_url="//evil.example/steal",
                ),
            )
        )


def test_production_oidc_rejects_insecure_discovery_endpoints(tmp_path: Path) -> None:
    settings = replace(
        oidc_settings(tmp_path),
        environment="production",
        admin_api_token="a" * 32,
        cors_origins=("https://app.example.test",),
        oidc=replace(
            oidc_settings(tmp_path).oidc,
            redirect_uri="https://app.example.test/api/auth/oidc/callback",
        ),
    )
    app = create_app(settings)
    provider = FakeOIDCProvider()
    provider.insecure_endpoints = True
    with TestClient(app) as client:
        _install_provider(app, settings, provider)
        response = client.get("/api/auth/oidc/start")
        assert response.status_code == 502
        assert response.json()["detail"] == "OIDC provider is unavailable"
