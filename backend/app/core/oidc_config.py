"""Typed OIDC configuration loading and validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

EnvReader = Callable[[str, str], str]
CsvReader = Callable[[str, str], tuple[str, ...]]


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    post_login_redirect_url: str = "/"
    scopes: tuple[str, ...] = ("openid", "profile", "email")
    default_role: str = "viewer"
    http_timeout_seconds: float = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id and self.redirect_uri)

    def validate(
        self,
        *,
        environment: str,
        auth_mode: str,
        cors_origins: tuple[str, ...],
    ) -> None:
        values = (self.issuer, self.client_id, self.client_secret, self.redirect_uri)
        if not any(values):
            return
        if not self.enabled:
            raise RuntimeError(
                "OIDC_ISSUER, OIDC_CLIENT_ID, and OIDC_REDIRECT_URI must be configured together"
            )
        if auth_mode != "token":
            raise RuntimeError("OIDC requires AUTH_MODE=token")
        if "openid" not in self.scopes:
            raise RuntimeError("OIDC_SCOPES must include openid")
        if self.default_role not in {"viewer", "editor", "admin"}:
            raise RuntimeError("OIDC_DEFAULT_ROLE must be viewer, editor, or admin")
        if self.http_timeout_seconds <= 0:
            raise RuntimeError("OIDC_HTTP_TIMEOUT_SECONDS must be positive")
        for name, url in (("OIDC_ISSUER", self.issuer), ("OIDC_REDIRECT_URI", self.redirect_uri)):
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.query
                or parsed.fragment
            ):
                raise RuntimeError(f"{name} must be an absolute HTTP(S) URL")
            if environment == "production" and parsed.scheme != "https":
                raise RuntimeError(f"production {name} must use HTTPS")
        destination = self.post_login_redirect_url
        if destination.startswith("//"):
            raise RuntimeError("OIDC_POST_LOGIN_REDIRECT_URL must not be protocol-relative")
        if not destination.startswith("/"):
            parsed = urlsplit(destination)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in {allowed.rstrip("/") for allowed in cors_origins}:
                raise RuntimeError(
                    "OIDC_POST_LOGIN_REDIRECT_URL must be same-origin or in CORS_ORIGINS"
                )


def load_oidc_settings(env: EnvReader, csv: CsvReader) -> OIDCSettings:
    return OIDCSettings(
        issuer=env("OIDC_ISSUER", ""),
        client_id=env("OIDC_CLIENT_ID", ""),
        client_secret=env("OIDC_CLIENT_SECRET", ""),
        redirect_uri=env("OIDC_REDIRECT_URI", ""),
        post_login_redirect_url=env("OIDC_POST_LOGIN_REDIRECT_URL", "/"),
        scopes=csv("OIDC_SCOPES", "openid,profile,email"),
        default_role=env("OIDC_DEFAULT_ROLE", "viewer").lower(),
        http_timeout_seconds=float(env("OIDC_HTTP_TIMEOUT_SECONDS", "10")),
    )
