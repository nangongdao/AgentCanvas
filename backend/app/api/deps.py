"""FastAPI dependency helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AUTH_COOKIE_NAME, Principal, Role
from app.core.container import ServiceContainer


def get_container(request: Request) -> ServiceContainer:
    return request.app.state.container


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    container: ServiceContainer = request.app.state.container
    async with container.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def request_credentials(request: Request) -> str | None:
    """Extract the bearer token or session cookie from an HTTP request."""
    authorization = request.headers.get("Authorization", "")
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() == "bearer" and credentials:
        return credentials.strip()
    return request.cookies.get(AUTH_COOKIE_NAME)


async def get_principal(request: Request) -> Principal:
    container: ServiceContainer = request.app.state.container
    credentials = request_credentials(request)
    principal = container.auth_service.authenticate(credentials)
    if principal is None and credentials:
        async with container.session_factory() as session:
            principal = await container.auth_service.authenticate_api_token(credentials, session)
            if principal is not None:
                # Persist the last_used_at usage mark; the request-session commits
                # only the accessed resource, so commit the auth write explicitly.
                await session.commit()
    if principal is None and credentials:
        async with container.session_factory() as session:
            principal = await container.auth_service.authenticate_session(credentials, session)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def get_api_token_principal(request: Request) -> Principal:
    """Authenticate a bearer service-account token without static-token fallback."""
    container: ServiceContainer = request.app.state.container
    scheme, _, credentials = request.headers.get("Authorization", "").partition(" ")
    credentials = credentials.strip()
    if scheme.lower() != "bearer" or not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    async with container.session_factory() as session:
        principal = await container.auth_service.authenticate_api_token(credentials, session)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or revoked API token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        await session.commit()
    return principal


def require_role(required: Role):
    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if principal.token_scope == "execution":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="execution-only API token cannot access management endpoints",
            )
        if principal.auth_method == "api_token" and principal.project_id is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="project-scoped API token cannot access management endpoints",
            )
        if not principal.can(required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{required.value} role required",
            )
        return principal

    return dependency


ViewerDep = Annotated[Principal, Depends(require_role(Role.VIEWER))]
EditorDep = Annotated[Principal, Depends(require_role(Role.EDITOR))]
AdminDep = Annotated[Principal, Depends(require_role(Role.ADMIN))]
