"""Authentication session endpoints (API token and multi-user login)."""

from __future__ import annotations

from typing import Annotated, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.auth_cookies import (
    clear_auth_cookies,
    clear_refresh_cookie,
    set_access_cookie,
    set_user_session_cookies,
)
from app.api.deps import get_container, get_principal, request_credentials
from app.core.auth import Principal, Role
from app.core.container import ServiceContainer
from app.core.refresh_tokens import REFRESH_COOKIE_NAME, RefreshTokenError
from app.core.security import hash_password, verify_password
from app.db.repositories import IdentityBootstrapRepo, UserRepo
from app.schemas.api import (
    AuthLogin,
    AuthPreferencesUpdate,
    AuthRegister,
    AuthSessionOut,
    UserOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
ContainerDep = Annotated[ServiceContainer, Depends(get_container)]
PrincipalDep = Annotated[Principal, Depends(get_principal)]
RoleLiteral = Literal["viewer", "editor", "admin"]


@router.post("/login", response_model=AuthSessionOut)
async def login(
    body: AuthLogin,
    response: Response,
    container: ContainerDep,
) -> AuthSessionOut:
    """Sign in with an API token or, for multi-user accounts, email+password."""
    if body.token:
        principal = container.auth_service.authenticate(body.token)
        if principal is None:
            async with container.session_factory() as session:
                principal = await container.auth_service.authenticate_api_token(body.token, session)
                if principal is not None:
                    await session.commit()
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid API token",
            )
        clear_refresh_cookie(response)
        set_access_cookie(
            response,
            body.token,
            max_age=86_400,
            secure=container.settings.is_production,
        )
        return AuthSessionOut(
            role=principal.role.value,
            auth_enabled=container.auth_service.enabled,
            subject=principal.subject,
        )

    if body.email is None or body.password is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="email and password are required for user login",
        )
    async with container.session_factory() as session:
        repo = UserRepo(session)
        user = await repo.get_by_email(body.email)
        if (
            user is None
            or user.status != "active"
            or not user.password_hash
            or not verify_password(body.password, user.password_hash)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid email or password",
            )
        bundle = await container.refresh_token_service.issue(session, user)
        await repo.touch_login(user)
        await session.commit()

    set_user_session_cookies(
        response,
        bundle,
        access_max_age=container.settings.auth_session_ttl_seconds,
        secure=container.settings.is_production,
    )
    return AuthSessionOut(
        role=cast(RoleLiteral, user.role),
        auth_enabled=container.auth_service.enabled,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        subject=user.email,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: AuthRegister,
    response: Response,
    request: Request,
) -> UserOut:
    """Create a user account.

    The first account bootstraps as admin; afterwards an authenticated admin is
    required, so a fresh deployment never locks itself out and a shared-token
    deployment cannot silently mint admins.
    """
    container: ServiceContainer = request.app.state.container
    async with container.session_factory() as session:
        repo = UserRepo(session)
        if await repo.get_by_email(body.email) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email already registered",
            )
        user_id = uuid4().hex
        is_bootstrap = await IdentityBootstrapRepo(session).claim(user_id)
        role = body.role
        if is_bootstrap:
            role = "admin"
        else:
            credentials = request_credentials(request)
            principal = container.auth_service.authenticate(credentials)
            if principal is None and credentials:
                principal = await container.auth_service.authenticate_api_token(
                    credentials, session
                )
            if principal is None and credentials:
                principal = await container.auth_service.authenticate_session(credentials, session)
            if principal is None or not principal.can(Role.ADMIN):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="admin role required to register users",
                )
        try:
            user = await repo.create(
                body.email,
                hash_password(body.password),
                display_name=body.display_name,
                role=role,
                user_id=user_id,
            )
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email already registered",
            ) from exc
        bundle = (
            await container.refresh_token_service.issue(session, user) if is_bootstrap else None
        )
        await session.commit()

    if bundle is not None:
        set_user_session_cookies(
            response,
            bundle,
            access_max_age=container.settings.auth_session_ttl_seconds,
            secure=container.settings.is_production,
        )
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    container: ContainerDep,
) -> None:
    credentials = request_credentials(request)
    refresh_credentials = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if credentials or refresh_credentials:
        async with container.session_factory() as session:
            if credentials:
                await container.auth_service.revoke_session(session, credentials)
            await container.refresh_token_service.revoke_family(session, refresh_credentials)
            await session.commit()
    clear_auth_cookies(response)


@router.post("/refresh", response_model=AuthSessionOut)
async def refresh(
    request: Request,
    response: Response,
    container: ContainerDep,
) -> AuthSessionOut | JSONResponse:
    raw = request.cookies.get(REFRESH_COOKIE_NAME, "")
    if not raw:
        rejected = JSONResponse(
            {"detail": "refresh token required"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
        clear_auth_cookies(rejected)
        return rejected
    async with container.session_factory() as session:
        try:
            bundle = await container.refresh_token_service.rotate(session, raw)
        except RefreshTokenError as exc:
            await session.commit()
            rejected = JSONResponse(
                {"detail": str(exc)},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
            clear_auth_cookies(rejected)
            return rejected
        await session.commit()
    set_user_session_cookies(
        response,
        bundle,
        access_max_age=container.settings.auth_session_ttl_seconds,
        secure=container.settings.is_production,
    )
    user = bundle.user
    return AuthSessionOut(
        role=cast(RoleLiteral, user.role),
        auth_enabled=container.auth_service.enabled,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        subject=user.email,
    )


@router.get("/me", response_model=AuthSessionOut)
async def me(principal: PrincipalDep, request: Request) -> AuthSessionOut:
    container: ServiceContainer = request.app.state.container
    result = AuthSessionOut(
        role=principal.role.value,
        auth_enabled=container.auth_service.enabled,
        subject=principal.subject,
    )
    if principal.user_id:
        async with container.session_factory() as session:
            user = await UserRepo(session).get(principal.user_id)
        if user is not None:
            result.user_id = user.id
            result.email = user.email
            result.display_name = user.display_name
            if user.language is not None:
                result.language = cast(Literal["zh", "en"], user.language)
    return result


@router.patch("/me", response_model=AuthSessionOut)
async def update_preferences(
    payload: AuthPreferencesUpdate,
    principal: PrincipalDep,
    request: Request,
) -> AuthSessionOut:
    """Persist account-level UI preferences (C5-9 language).

    Token subjects have no user row to anchor a preference; they keep the
    browser-local choice instead of a server-side one.
    """
    container: ServiceContainer = request.app.state.container
    if not principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="language preference requires an account session",
        )
    async with container.session_factory() as session, session.begin():
        repo = UserRepo(session)
        user = await repo.get(principal.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="user not found"
            )
        await repo.set_language(user, payload.language)
    return await me(principal, request)
