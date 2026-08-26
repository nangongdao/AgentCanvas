"""Browser-facing OIDC Authorization Code + PKCE login routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.responses import Response

from app.api.auth_cookies import set_user_session_cookies
from app.api.deps import get_container
from app.core.container import ServiceContainer
from app.core.oidc import (
    OIDC_STATE_COOKIE_NAME,
    OIDC_STATE_TTL_SECONDS,
    OIDCError,
)
from app.services.oidc_identity import OIDCIdentityError

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]


def _clear_state_cookie(response: Response) -> None:
    response.delete_cookie(
        OIDC_STATE_COOKIE_NAME,
        path="/api/auth/oidc/callback",
        samesite="lax",
    )


def _error_response(detail: str, code: int = 400) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=code)
    _clear_state_cookie(response)
    return response


@router.get("/start")
async def start_oidc(container: ContainerDep) -> Response:
    if not container.oidc_client.enabled:
        return _error_response("OIDC login is not configured", status.HTTP_404_NOT_FOUND)
    try:
        login = await container.oidc_client.begin()
    except OIDCError:
        return _error_response("OIDC provider is unavailable", status.HTTP_502_BAD_GATEWAY)
    response = RedirectResponse(login.authorization_url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        OIDC_STATE_COOKIE_NAME,
        login.state_cookie,
        httponly=True,
        secure=container.settings.is_production,
        samesite="lax",
        max_age=OIDC_STATE_TTL_SECONDS,
        path="/api/auth/oidc/callback",
    )
    return response


@router.get("/callback")
async def oidc_callback(
    request: Request,
    container: ContainerDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    if error:
        return _error_response("OIDC provider rejected the login")
    state_cookie = request.cookies.get(OIDC_STATE_COOKIE_NAME, "")
    if not code or not state or not state_cookie:
        return _error_response("OIDC callback is incomplete")
    try:
        claims = await container.oidc_client.complete(
            code=code,
            state=state,
            state_cookie=state_cookie,
        )
        async with container.session_factory() as db:
            user = await container.oidc_identity_service.resolve_or_provision(db, claims)
            bundle = await container.refresh_token_service.issue(db, user)
            await db.commit()
    except (OIDCError, OIDCIdentityError) as exc:
        return _error_response(str(exc))

    response = RedirectResponse(
        container.settings.oidc.post_login_redirect_url,
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _clear_state_cookie(response)
    set_user_session_cookies(
        response,
        bundle,
        access_max_age=container.settings.auth_session_ttl_seconds,
        secure=container.settings.is_production,
    )
    return response
