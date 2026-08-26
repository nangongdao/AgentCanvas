"""Token and session authentication plus role-based authorization primitives."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum, StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.service_account import ApiToken, ServiceAccount
from app.db.repositories.identity import SessionRepo, UserRepo
from app.db.repositories.service_account import ApiTokenRepo, ServiceAccountRepo

AUTH_COOKIE_NAME = "agentcanvas_session"

class Role(StrEnum):
    VIEWER = "viewer"
    EDITOR = "editor"
    ADMIN = "admin"


class RoleLevel(IntEnum):
    VIEWER = 10
    EDITOR = 20
    ADMIN = 30


ROLE_LEVEL = {
    Role.VIEWER: RoleLevel.VIEWER,
    Role.EDITOR: RoleLevel.EDITOR,
    Role.ADMIN: RoleLevel.ADMIN,
}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    auth_method: str
    user_id: str | None = None
    service_account_id: str | None = None
    api_token_id: str | None = None
    project_id: str | None = None
    token_scope: str | None = None

    def can(self, required: Role) -> bool:
        return ROLE_LEVEL[self.role] >= ROLE_LEVEL[required]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.auth_mode == "token"
        self._tokens = (
            (Role.ADMIN, settings.admin_api_token),
            (Role.EDITOR, settings.editor_api_token),
            (Role.VIEWER, settings.viewer_api_token),
        )

    def authenticate(self, token: str | None) -> Principal | None:
        """Resolve a static API token into a principal (no database access)."""
        if not self.enabled:
            return Principal("local-development", Role.ADMIN, "disabled")
        candidate = token or ""
        matched: Role | None = None
        for role, configured in self._tokens:
            valid = bool(configured) and secrets.compare_digest(candidate, configured)
            if valid and (matched is None or ROLE_LEVEL[role] > ROLE_LEVEL[matched]):
                matched = role
        if matched is None:
            return None
        return Principal(f"token:{matched.value}", matched, "token")

    async def authenticate_session(
        self, credentials: str, session: AsyncSession
    ) -> Principal | None:
        """Resolve an opaque session token into a real user principal."""
        row = await SessionRepo(session).get_by_token_hash(_sha256(credentials))
        if row is None:
            return None
        now = datetime.now(UTC)
        if row.revoked_at is not None or row.expires_at.replace(tzinfo=UTC) <= now:
            return None
        user = await UserRepo(session).get(row.user_id)
        if user is None or user.status != "active":
            return None
        try:
            role = Role(user.role)
        except ValueError:
            return None
        return Principal(user.email, role, "session", user_id=user.id)

    async def authenticate_api_token(
        self, credentials: str, session: AsyncSession
    ) -> Principal | None:
        """Resolve a service-account API token into a principal.

        Tokens are stored as SHA-256 hashes; only the plaintext credential the
        caller presents is hashed for the lookup, and revocations/expiry stop
        it even when the hash matches.
        """
        token = await ApiTokenRepo(session).get_by_hash(_sha256(credentials))
        if token is None:
            return None
        now = datetime.now(UTC)
        if token.revoked_at is not None:
            return None
        if token.expires_at is not None and token.expires_at.replace(tzinfo=UTC) <= now:
            return None
        account = await ServiceAccountRepo(session).get(token.service_account_id)
        if account is None or account.status != "active":
            return None
        try:
            role = Role(account.role)
        except ValueError:
            return None
        await ApiTokenRepo(session).touch_used(token)
        return Principal(
            f"service:{account.name}",
            role,
            "api_token",
            service_account_id=account.id,
            api_token_id=token.id,
            project_id=account.project_id,
            token_scope=token.scope,
        )

    async def issue_api_token(
        self, session: AsyncSession, account: ServiceAccount, name: str
    ) -> tuple[str, ApiToken]:
        """Create a token row and return (one-time raw, row); hash stored only."""
        raw = secrets.token_urlsafe(32)
        repo = ApiTokenRepo(session)
        row = await repo.create(
            account.id,
            name,
            _sha256(raw),
            raw[:12],
            None,
            "management",
        )
        return raw, row

    async def revoke_session(self, session: AsyncSession, credentials: str) -> None:
        """Revoke a session row if the credential matches one (no-op otherwise)."""
        if not credentials:
            return
        row = await SessionRepo(session).get_by_token_hash(_sha256(credentials))
        if row is not None:
            await SessionRepo(session).revoke(row)
