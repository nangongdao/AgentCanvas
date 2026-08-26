"""Fake OIDC provider and browser-protocol helpers for integration tests."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.oidc import OIDC_STATE_COOKIE_NAME, OIDCClient
from app.core.oidc_config import OIDCSettings

ADMIN_TOKEN = "admin-token-with-more-than-16-characters"
SECRET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
ISSUER = "https://idp.example.test"
CLIENT_ID = "agentcanvas-client"
CLIENT_SECRET = "oidc-client-secret"


def oidc_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        environment="test",
        auth_mode="token",
        admin_api_token=ADMIN_TOKEN,
        secret_key=SECRET_KEY,
        cors_origins=("http://testserver",),
        oidc=OIDCSettings(
            issuer=ISSUER,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri="http://testserver/api/auth/oidc/callback",
            post_login_redirect_url="/",
        ),
    )


def _b64_integer(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


class FakeOIDCProvider:
    def __init__(self) -> None:
        self.issuer = ISSUER
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = self._public_jwk("test-key")
        self.nonce = ""
        self.email_verified = True
        self.signing_key = self.private_key
        self.token_requests = 0
        self.jwks_requests = 0
        self.last_token_form: dict[str, list[str]] = {}
        self.last_authorization: str | None = None
        self.insecure_endpoints = False
        self.token_auth_methods: list[str] | None = ["client_secret_basic"]

    def _public_jwk(self, key_id: str) -> dict[str, str]:
        public = self.private_key.public_key().public_numbers()
        return {
            "kty": "RSA",
            "use": "sig",
            "kid": key_id,
            "alg": "RS256",
            "n": _b64_integer(public.n),
            "e": _b64_integer(public.e),
        }

    def rotate_key(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.signing_key = self.private_key
        self.jwk = self._public_jwk("rotated-key")

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            endpoint_origin = (
                "http://idp.example.test" if self.insecure_endpoints else self.issuer.rstrip("/")
            )
            metadata: dict[str, object] = {
                "issuer": self.issuer,
                "authorization_endpoint": f"{endpoint_origin}/authorize",
                "token_endpoint": f"{endpoint_origin}/token",
                "jwks_uri": f"{endpoint_origin}/jwks",
                "code_challenge_methods_supported": ["S256"],
                "id_token_signing_alg_values_supported": ["RS256"],
            }
            if self.token_auth_methods is not None:
                metadata["token_endpoint_auth_methods_supported"] = self.token_auth_methods
            return httpx.Response(200, json=metadata)
        if request.url.path == "/jwks":
            self.jwks_requests += 1
            return httpx.Response(200, json={"keys": [self.jwk]})
        if request.url.path == "/token":
            self.token_requests += 1
            expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            assert request.headers["Authorization"] == f"Basic {expected}"
            self.last_token_form = parse_qs(request.content.decode())
            assert self.last_token_form["grant_type"] == ["authorization_code"]
            assert self.last_token_form["code"] == ["good-code"]
            assert self.last_token_form["redirect_uri"] == [
                "http://testserver/api/auth/oidc/callback"
            ]
            now = int(time.time())
            token = jwt.encode(
                {
                    "iss": self.issuer,
                    "sub": "external-user-1",
                    "aud": CLIENT_ID,
                    "iat": now,
                    "exp": now + 300,
                    "nonce": self.nonce,
                    "email": "alice@example.com",
                    "email_verified": self.email_verified,
                    "name": "Alice Example",
                },
                self.signing_key,
                algorithm="RS256",
                headers={"kid": self.jwk["kid"]},
            )
            return httpx.Response(
                200,
                json={"access_token": "provider-access", "token_type": "Bearer", "id_token": token},
            )
        return httpx.Response(404)


def install_provider(
    app: FastAPI, settings: Settings, provider: FakeOIDCProvider
) -> None:
    app.state.container.oidc_client = OIDCClient(
        settings,
        app.state.container.secret_box,
        transport=httpx.MockTransport(provider.handle),
    )


def begin_oidc(client: TestClient, provider: FakeOIDCProvider) -> dict[str, str]:
    response = client.get("/api/auth/oidc/start", follow_redirects=False)
    assert response.status_code == 302, response.text
    assert response.cookies.get(OIDC_STATE_COOKIE_NAME)
    location = response.headers["location"]
    provider.last_authorization = location
    params = {key: values[0] for key, values in parse_qs(urlsplit(location).query).items()}
    provider.nonce = params["nonce"]
    assert params["response_type"] == "code"
    assert params["client_id"] == CLIENT_ID
    assert params["code_challenge_method"] == "S256"
    assert "openid" in params["scope"].split()
    return params


def oidc_callback(client: TestClient, state: str) -> httpx2.Response:
    return client.get(
        "/api/auth/oidc/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
