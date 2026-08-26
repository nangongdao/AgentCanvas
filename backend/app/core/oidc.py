"""OIDC Authorization Code + PKCE client with strict ID-token validation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import jwt

from app.core.config import Settings
from app.core.security import SecretBox

OIDC_STATE_COOKIE_NAME = "agentcanvas_oidc_state"
OIDC_STATE_TTL_SECONDS = 10 * 60
_ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)


class OIDCError(ValueError):
    """The provider response or browser callback failed OIDC validation."""


@dataclass(frozen=True)
class OIDCLoginStart:
    authorization_url: str
    state_cookie: str


@dataclass(frozen=True)
class OIDCClaims:
    issuer: str
    subject: str
    email: str
    display_name: str


def _base64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _with_query(url: str, values: dict[str, str]) -> str:
    parts = urlsplit(url)
    query = [*parse_qsl(parts.query, keep_blank_values=True), *values.items()]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class OIDCClient:
    def __init__(
        self,
        settings: Settings,
        secret_box: SecretBox,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.enabled = settings.oidc_enabled
        self.issuer = settings.oidc.issuer
        self.client_id = settings.oidc.client_id
        self.client_secret = settings.oidc.client_secret
        self.redirect_uri = settings.oidc.redirect_uri
        self.scopes = settings.oidc.scopes
        self.timeout_seconds = settings.oidc.http_timeout_seconds
        self._require_https = settings.is_production
        self._secret_box = secret_box
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._cache_lock = asyncio.Lock()
        self._metadata: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._cache_expires_at = 0.0

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def begin(self) -> OIDCLoginStart:
        self._require_enabled()
        metadata = await self._discovery()
        methods = metadata.get("code_challenge_methods_supported")
        if isinstance(methods, list) and "S256" not in methods:
            raise OIDCError("OIDC provider does not support PKCE S256")

        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        cookie = self._secret_box.encrypt_mapping(
            {
                "state": state,
                "nonce": nonce,
                "verifier": verifier,
                "issuer": self.issuer,
                "issued_at": str(int(time.time())),
            }
        )
        authorization_url = _with_query(
            self._metadata_url(metadata, "authorization_endpoint"),
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": _base64url_sha256(verifier),
                "code_challenge_method": "S256",
            },
        )
        return OIDCLoginStart(authorization_url, cookie)

    async def complete(self, *, code: str, state: str, state_cookie: str) -> OIDCClaims:
        self._require_enabled()
        state_data = self._decode_state(state_cookie)
        if not secrets.compare_digest(state, state_data["state"]):
            raise OIDCError("OIDC state mismatch")

        metadata = await self._discovery()
        token_payload = await self._exchange_code(
            metadata, code=code, verifier=state_data["verifier"]
        )
        raw_id_token = token_payload.get("id_token")
        if not isinstance(raw_id_token, str) or not raw_id_token:
            raise OIDCError("OIDC token response omitted id_token")
        claims = await self._verify_id_token(
            metadata, raw_id_token, expected_nonce=state_data["nonce"]
        )
        return claims

    def _decode_state(self, encrypted: str) -> dict[str, str]:
        try:
            values = self._secret_box.decrypt_mapping(encrypted)
            issued_at = int(values["issued_at"])
            required = ("state", "nonce", "verifier", "issuer")
            if any(not values.get(key) for key in required):
                raise ValueError
        except (KeyError, ValueError, RuntimeError) as exc:
            raise OIDCError("invalid OIDC state cookie") from exc
        now = int(time.time())
        if issued_at > now + 60 or now - issued_at > OIDC_STATE_TTL_SECONDS:
            raise OIDCError("OIDC state expired")
        if values["issuer"] != self.issuer:
            raise OIDCError("OIDC state issuer mismatch")
        return values

    async def _exchange_code(
        self, metadata: dict[str, Any], *, code: str, verifier: str
    ) -> dict[str, Any]:
        methods = metadata.get("token_endpoint_auth_methods_supported")
        if methods is None:
            methods = ["client_secret_basic"]
        if not isinstance(methods, list) or not all(isinstance(method, str) for method in methods):
            raise OIDCError("OIDC provider advertised invalid client auth methods")
        supports_basic = "client_secret_basic" in methods
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
            "client_id": self.client_id,
        }
        auth: httpx.BasicAuth | None = None
        if self.client_secret:
            if supports_basic:
                auth = httpx.BasicAuth(self.client_id, self.client_secret)
                form.pop("client_id")
            elif "client_secret_post" in methods:
                form["client_secret"] = self.client_secret
            else:
                raise OIDCError("OIDC provider does not support the configured client auth")
        elif "none" not in methods:
            raise OIDCError("OIDC provider does not support public clients")
        try:
            request_options: dict[str, Any] = {
                "data": form,
                "headers": {"Accept": "application/json"},
            }
            if auth is not None:
                request_options["auth"] = auth
            response = await self._client().post(
                self._metadata_url(metadata, "token_endpoint"), **request_options
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("OIDC token exchange failed") from exc
        if not isinstance(payload, dict):
            raise OIDCError("OIDC token response is not an object")
        return payload

    async def _verify_id_token(
        self,
        metadata: dict[str, Any],
        raw_token: str,
        *,
        expected_nonce: str,
    ) -> OIDCClaims:
        try:
            header = jwt.get_unverified_header(raw_token)
        except jwt.PyJWTError as exc:
            raise OIDCError("OIDC id_token header is invalid") from exc
        algorithm = header.get("alg")
        if algorithm not in _ASYMMETRIC_ALGORITHMS:
            raise OIDCError("OIDC id_token uses an unsupported signing algorithm")
        advertised = metadata.get("id_token_signing_alg_values_supported")
        if isinstance(advertised, list) and algorithm not in advertised:
            raise OIDCError("OIDC id_token algorithm was not advertised")

        key = await self._signing_key(metadata, header.get("kid"))
        try:
            claims = jwt.decode(
                raw_token,
                key=key,
                algorithms=[algorithm],
                audience=self.client_id,
                issuer=self.issuer,
                leeway=60,
                options={"require": ["exp", "iat", "iss", "sub", "aud", "nonce"]},
            )
        except jwt.PyJWTError as exc:
            raise OIDCError("OIDC id_token validation failed") from exc

        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not secrets.compare_digest(nonce, expected_nonce):
            raise OIDCError("OIDC nonce mismatch")
        authorized_party = claims.get("azp")
        audience = claims.get("aud")
        if authorized_party is not None and authorized_party != self.client_id:
            raise OIDCError("OIDC authorized party mismatch")
        if isinstance(audience, list) and len(audience) > 1 and authorized_party != self.client_id:
            raise OIDCError("OIDC multi-audience token omitted the authorized party")
        email = claims.get("email")
        if (
            not isinstance(email, str)
            or not email.strip()
            or claims.get("email_verified") is not True
        ):
            raise OIDCError("OIDC provider did not supply a verified email")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OIDCError("OIDC subject is invalid")
        display_name = claims.get("name") or claims.get("preferred_username") or email
        if not isinstance(display_name, str):
            display_name = email
        return OIDCClaims(
            issuer=self.issuer,
            subject=subject,
            email=email.lower().strip(),
            display_name=display_name.strip()[:120],
        )

    async def _signing_key(self, metadata: dict[str, Any], key_id: object) -> Any:
        if key_id is not None and (not isinstance(key_id, str) or not key_id):
            raise OIDCError("OIDC signing key id is invalid")
        jwks = await self._jwks_document(metadata)
        candidates = self._signing_key_candidates(jwks, key_id)
        if not candidates and key_id is not None:
            jwks = await self._jwks_document(metadata, force_refresh=True)
            candidates = self._signing_key_candidates(jwks, key_id)
        if len(candidates) != 1:
            raise OIDCError("OIDC signing key is missing or ambiguous")
        try:
            return jwt.PyJWK.from_dict(candidates[0]).key
        except jwt.PyJWTError as exc:
            raise OIDCError("OIDC signing key is invalid") from exc

    async def _discovery(self) -> dict[str, Any]:
        if self._metadata is not None and time.monotonic() < self._cache_expires_at:
            return self._metadata
        async with self._cache_lock:
            if self._metadata is not None and time.monotonic() < self._cache_expires_at:
                return self._metadata
            payload = await self._get_json(
                f"{self.issuer.rstrip('/')}/.well-known/openid-configuration",
                "OIDC discovery failed",
            )
            discovered_issuer = payload.get("issuer")
            if not isinstance(discovered_issuer, str) or discovered_issuer != self.issuer:
                raise OIDCError("OIDC discovery issuer mismatch")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                self._metadata_url(payload, field)
            self._metadata = payload
            self._jwks = None
            self._cache_expires_at = time.monotonic() + 300
            return payload

    async def _jwks_document(
        self, metadata: dict[str, Any], *, force_refresh: bool = False
    ) -> dict[str, Any]:
        cached = self._jwks
        if cached is not None and not force_refresh:
            return cached
        async with self._cache_lock:
            if self._jwks is not None and (
                not force_refresh or self._jwks is not cached
            ):
                return self._jwks
            self._jwks = await self._get_json(
                self._metadata_url(metadata, "jwks_uri"), "OIDC JWKS request failed"
            )
            return self._jwks

    @staticmethod
    def _signing_key_candidates(jwks: dict[str, Any], key_id: str | None) -> list[dict[str, Any]]:
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise OIDCError("OIDC JWKS has no keys")
        return [
            item
            for item in keys
            if isinstance(item, dict)
            and item.get("kty") != "oct"
            and item.get("use") in {None, "sig"}
            and (key_id is None or item.get("kid") == key_id)
        ]

    async def _get_json(self, url: str, failure: str) -> dict[str, Any]:
        try:
            response = await self._client().get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError(failure) from exc
        if not isinstance(payload, dict):
            raise OIDCError(failure)
        return payload

    def _metadata_url(self, metadata: dict[str, Any], field: str) -> str:
        value = metadata.get(field)
        if not isinstance(value, str):
            raise OIDCError(f"OIDC discovery omitted {field}")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OIDCError(f"OIDC discovery returned an invalid {field}")
        if self._require_https and parsed.scheme != "https":
            raise OIDCError(f"OIDC discovery returned an insecure {field}")
        return value

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        return self._http

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise OIDCError("OIDC login is not configured")
