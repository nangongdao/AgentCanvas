"""Rotating refresh-token families for database-backed user sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import RefreshToken, Session, User
from app.db.repositories import RefreshTokenRepo, SessionRepo, UserRepo

REFRESH_COOKIE_NAME = "agentcanvas_refresh"


class RefreshTokenError(ValueError):
    """A refresh credential is invalid, expired, revoked, or replayed."""


@dataclass(frozen=True)
class SessionBundle:
    user: User
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RefreshTokenService:
    def __init__(self, settings: Settings) -> None:
        self.access_ttl_seconds = settings.auth_session_ttl_seconds
        self.refresh_ttl_seconds = settings.auth_refresh_ttl_seconds

    async def issue(self, db: AsyncSession, user: User) -> SessionBundle:
        now = datetime.now(UTC)
        return await self._issue(
            db,
            user,
            family_id=uuid4().hex,
            refresh_expires_at=now + timedelta(seconds=self.refresh_ttl_seconds),
            now=now,
        )

    async def rotate(self, db: AsyncSession, raw_token: str) -> SessionBundle:
        repo = RefreshTokenRepo(db)
        row = await repo.get_by_token_hash(_sha256(raw_token))
        if row is None:
            raise RefreshTokenError("invalid refresh token")

        now = datetime.now(UTC)
        expires_at = row.expires_at.replace(tzinfo=UTC)
        if row.consumed_at is not None:
            await repo.revoke_family(row.family_id, now)
            raise RefreshTokenError("refresh token replay detected")
        if row.revoked_at is not None or expires_at <= now:
            await repo.revoke_family(row.family_id, now)
            raise RefreshTokenError("refresh token expired or revoked")

        user = await UserRepo(db).get(row.user_id)
        if user is None or user.status != "active":
            await repo.revoke_family(row.family_id, now)
            raise RefreshTokenError("refresh user is unavailable")

        if not await repo.consume_if_active(row.id, now):
            await repo.revoke_family(row.family_id, now)
            raise RefreshTokenError("refresh token replay detected")

        await SessionRepo(db).revoke_by_id(row.session_id, now)
        bundle = await self._issue(
            db,
            user,
            family_id=row.family_id,
            refresh_expires_at=expires_at,
            now=now,
        )
        replacement = await repo.get_by_token_hash(_sha256(bundle.refresh_token))
        if replacement is None:  # pragma: no cover - defensive persistence invariant
            raise RuntimeError("new refresh token was not persisted")
        await repo.link_replacement(row.id, replacement.id)
        return bundle

    async def revoke_family(self, db: AsyncSession, raw_token: str) -> None:
        if not raw_token:
            return
        repo = RefreshTokenRepo(db)
        row = await repo.get_by_token_hash(_sha256(raw_token))
        if row is not None:
            await repo.revoke_family(row.family_id, datetime.now(UTC))

    async def _issue(
        self,
        db: AsyncSession,
        user: User,
        *,
        family_id: str,
        refresh_expires_at: datetime,
        now: datetime,
    ) -> SessionBundle:
        access_raw = secrets.token_urlsafe(32)
        refresh_raw = secrets.token_urlsafe(48)
        access_expires_at = now + timedelta(seconds=self.access_ttl_seconds)
        access_row: Session = await SessionRepo(db).create(
            user.id,
            _sha256(access_raw),
            access_expires_at,
        )
        _: RefreshToken = await RefreshTokenRepo(db).create(
            token_hash=_sha256(refresh_raw),
            user_id=user.id,
            session_id=access_row.id,
            family_id=family_id,
            expires_at=refresh_expires_at,
        )
        return SessionBundle(
            user=user,
            access_token=access_raw,
            refresh_token=refresh_raw,
            access_expires_at=access_expires_at,
            refresh_expires_at=refresh_expires_at,
        )
