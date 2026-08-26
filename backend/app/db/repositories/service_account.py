"""Data-access repositories for service accounts and API tokens."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiToken, ServiceAccount


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ServiceAccountRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, account_id: str) -> ServiceAccount | None:
        return await self.session.get(ServiceAccount, account_id)

    async def get_by_name(self, name: str) -> ServiceAccount | None:
        stmt = select(ServiceAccount).where(ServiceAccount.name == name.strip())
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list(self) -> list[ServiceAccount]:
        stmt = select(ServiceAccount).order_by(ServiceAccount.created_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        name: str,
        *,
        description: str = "",
        project_id: str | None = None,
        role: str = "editor",
    ) -> ServiceAccount:
        row = ServiceAccount(
            name=name.strip(),
            description=description,
            project_id=project_id,
            role=role,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_status(self, account_id: str, status: str) -> None:
        row = await self.session.get(ServiceAccount, account_id)
        if row is not None:
            row.status = status
            row.updated_at = _utcnow()
            await self.session.flush()

    async def touch(self, account: ServiceAccount) -> None:
        account.updated_at = _utcnow()
        await self.session.flush()

    async def delete(self, account_id: str) -> bool:
        row = await self.session.get(ServiceAccount, account_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True


class ApiTokenRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        service_account_id: str,
        name: str,
        token_hash: str,
        prefix: str,
        expires_at: datetime | None,
        scope: str = "management",
    ) -> ApiToken:
        row = ApiToken(
            service_account_id=service_account_id,
            name=name.strip(),
            token_hash=token_hash,
            prefix=prefix,
            scope=scope,
            expires_at=expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_by_hash(self, token_hash: str) -> ApiToken | None:
        stmt = select(ApiToken).where(ApiToken.token_hash == token_hash)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_for_account(self, account_id: str) -> list[ApiToken]:
        stmt = (
            select(ApiToken)
            .where(ApiToken.service_account_id == account_id)
            .order_by(ApiToken.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def touch_used(self, token: ApiToken) -> None:
        token.last_used_at = _utcnow()
        await self.session.flush()
