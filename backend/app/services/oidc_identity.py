"""Map verified external identities onto local AgentCanvas users."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.oidc import OIDCClaims
from app.db.models import User
from app.db.repositories import OIDCIdentityRepo, UserRepo


class OIDCIdentityError(ValueError):
    """A verified external identity cannot use its mapped local account."""


class OIDCIdentityService:
    def __init__(self, default_role: str) -> None:
        self.default_role = default_role

    async def resolve_or_provision(self, db: AsyncSession, claims: OIDCClaims) -> User:
        try:
            async with db.begin_nested():
                return await self._resolve_or_provision(db, claims)
        except IntegrityError:
            pass

        try:
            async with db.begin_nested():
                return await self._resolve_or_provision(db, claims)
        except IntegrityError as exc:
            raise OIDCIdentityError("OIDC account provisioning conflict") from exc

    async def _resolve_or_provision(self, db: AsyncSession, claims: OIDCClaims) -> User:
        identities = OIDCIdentityRepo(db)
        identity = await identities.get(claims.issuer, claims.subject)
        users = UserRepo(db)

        if identity is not None:
            user = await users.get(identity.user_id)
            if user is None or user.status != "active":
                raise OIDCIdentityError("OIDC account is unavailable")
            await identities.touch_login(identity, claims.email)
            await users.touch_login(user)
            return user

        user = await users.get_by_email(claims.email)
        if user is None:
            user = await users.create(
                claims.email,
                None,
                display_name=claims.display_name,
                role=self.default_role,
            )
        elif user.status != "active":
            raise OIDCIdentityError("OIDC account is unavailable")

        identity = await identities.create(
            issuer=claims.issuer,
            subject=claims.subject,
            user_id=user.id,
            email=claims.email,
        )
        await identities.touch_login(identity, claims.email)
        await users.touch_login(user)
        return user
