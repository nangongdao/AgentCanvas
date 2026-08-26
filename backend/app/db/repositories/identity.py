"""Data-access repositories for users and sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdentityBootstrap, OIDCIdentity, RefreshToken, Session, User


def _uuid() -> str:
    return uuid4().hex


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: str) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower().strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create(
        self,
        email: str,
        password_hash: str | None,
        *,
        display_name: str = "",
        role: str = "editor",
        user_id: str | None = None,
    ) -> User:
        row = User(
            **({"id": user_id} if user_id is not None else {}),
            email=email.lower().strip(),
            password_hash=password_hash,
            display_name=display_name,
            role=role,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(User.id)))
        return int(result.scalar_one())

    async def list_all(self, *, limit: int = 200) -> list[User]:
        """Newest-first account listing for the platform admin console (C7-3)."""
        result = await self.session.execute(
            select(User).order_by(User.created_at.desc(), User.id.asc()).limit(limit)
        )
        return list(result.scalars().all())

    async def set_status(self, user: User, status: str) -> User:
        user.status = status
        await self.session.flush()
        return user

    async def touch_login(self, user: User) -> None:
        user.last_login_at = datetime.now(UTC)
        await self.session.flush()

    async def set_language(self, user: User, language: str) -> User:
        """Persist an account-level UI language preference (C5-9)."""
        user.language = language
        await self.session.flush()
        return user


class IdentityBootstrapRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim(self, user_id: str) -> bool:
        """Claim the singleton first-admin slot in the current transaction."""
        try:
            async with self.session.begin_nested():
                self.session.add(IdentityBootstrap(id=1, claimed_by_user_id=user_id))
                await self.session.flush()
        except IntegrityError:
            return False
        return True


class SessionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, user_id: str, token_hash: str, expires_at: datetime) -> Session:
        row = Session(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        stmt = select(Session).where(Session.token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def revoke(self, session_row: Session) -> None:
        session_row.revoked_at = datetime.now(UTC)
        await self.session.flush()

    async def revoke_by_id(self, session_id: str, now: datetime) -> None:
        await self.session.execute(
            update(Session)
            .where(Session.id == session_id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_all_for_user(self, user_id: str, now: datetime) -> int:
        """Kill every live session of an account (C7-3 user deactivation)."""
        result = await self.session.execute(
            update(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class RefreshTokenRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        token_hash: str,
        user_id: str,
        session_id: str,
        family_id: str,
        expires_at: datetime,
    ) -> RefreshToken:
        row = RefreshToken(
            token_hash=token_hash,
            user_id=user_id,
            session_id=session_id,
            family_id=family_id,
            expires_at=expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_by_token_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self.session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalars().first()

    async def consume_if_active(self, token_id: str, now: datetime) -> bool:
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.id == token_id,
                RefreshToken.consumed_at.is_(None),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )
        result = await self.session.execute(statement)
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def link_replacement(self, token_id: str, replacement_id: str) -> None:
        await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(replaced_by_id=replacement_id)
        )

    async def revoke_family(self, family_id: str, now: datetime) -> None:
        session_ids = select(RefreshToken.session_id).where(RefreshToken.family_id == family_id)
        await self.session.execute(
            update(Session)
            .where(Session.id.in_(session_ids), Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self.session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def revoke_all_for_user(self, user_id: str, now: datetime) -> int:
        """Revoke every refresh token of an account (C7-3 user deactivation)."""
        result = await self.session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class OIDCIdentityRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, issuer: str, subject: str) -> OIDCIdentity | None:
        result = await self.session.execute(
            select(OIDCIdentity).where(
                OIDCIdentity.issuer == issuer,
                OIDCIdentity.subject == subject,
            )
        )
        return result.scalars().first()

    async def create(self, *, issuer: str, subject: str, user_id: str, email: str) -> OIDCIdentity:
        row = OIDCIdentity(
            issuer=issuer,
            subject=subject,
            user_id=user_id,
            email=email.lower().strip(),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def touch_login(self, identity: OIDCIdentity, email: str) -> None:
        identity.email = email.lower().strip()
        identity.last_login_at = datetime.now(UTC)
        await self.session.flush()
