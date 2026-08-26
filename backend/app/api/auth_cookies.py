"""Cookie policy shared by local, refresh, and OIDC authentication routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Response

from app.core.auth import AUTH_COOKIE_NAME
from app.core.refresh_tokens import REFRESH_COOKIE_NAME, SessionBundle


def set_access_cookie(response: Response, token: str, *, max_age: int, secure: bool) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=max_age,
        path="/",
    )


def set_user_session_cookies(
    response: Response, bundle: SessionBundle, *, access_max_age: int, secure: bool
) -> None:
    set_access_cookie(
        response,
        bundle.access_token,
        max_age=access_max_age,
        secure=secure,
    )
    refresh_max_age = max(
        0,
        int((bundle.refresh_expires_at - datetime.now(UTC)).total_seconds()),
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        bundle.refresh_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=refresh_max_age,
        path="/api/auth",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path="/api/auth",
        samesite="strict",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(AUTH_COOKIE_NAME, path="/", samesite="strict")
    clear_refresh_cookie(response)
