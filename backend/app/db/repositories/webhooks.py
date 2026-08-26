"""Data access for inbound workflow webhook triggers."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WebhookTrigger


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WebhookTriggerRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, trigger_id: str) -> WebhookTrigger | None:
        return await self.session.get(WebhookTrigger, trigger_id)

    async def get_for_workflow(self, workflow_id: str) -> WebhookTrigger | None:
        result = await self.session.execute(
            select(WebhookTrigger).where(WebhookTrigger.workflow_id == workflow_id)
        )
        return result.scalar_one_or_none()

    async def get_by_token_hash(self, token_hash: str) -> WebhookTrigger | None:
        result = await self.session.execute(
            select(WebhookTrigger).where(WebhookTrigger.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        workflow_id: str,
        published_version_id: str,
        *,
        token_hash: str,
        token_prefix: str,
        secret_encrypted: str,
        ip_allowlist: list[str],
    ) -> WebhookTrigger:
        row = WebhookTrigger(
            id=uuid4().hex,
            workflow_id=workflow_id,
            published_version_id=published_version_id,
            token_hash=token_hash,
            token_prefix=token_prefix,
            secret_encrypted=secret_encrypted,
            status="active",
            ip_allowlist=list(ip_allowlist),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def rotate(
        self,
        row: WebhookTrigger,
        *,
        published_version_id: str,
        token_hash: str,
        token_prefix: str,
        secret_encrypted: str,
        ip_allowlist: list[str] | None = None,
    ) -> WebhookTrigger:
        row.published_version_id = published_version_id
        row.token_hash = token_hash
        row.token_prefix = token_prefix
        row.secret_encrypted = secret_encrypted
        if ip_allowlist is not None:
            row.ip_allowlist = list(ip_allowlist)
        row.status = "active"
        row.updated_at = _utcnow()
        await self.session.flush()
        return row

    async def set_ip_allowlist(self, row: WebhookTrigger, values: list[str]) -> None:
        row.ip_allowlist = list(values)
        row.updated_at = _utcnow()
        await self.session.flush()

    async def bind_version(self, row: WebhookTrigger, published_version_id: str) -> None:
        row.published_version_id = published_version_id
        row.updated_at = _utcnow()
        await self.session.flush()

    async def disable(self, row: WebhookTrigger) -> None:
        row.status = "disabled"
        row.updated_at = _utcnow()
        await self.session.flush()

    async def touch_triggered(self, row: WebhookTrigger) -> None:
        row.last_triggered_at = _utcnow()
        row.updated_at = _utcnow()
        await self.session.flush()


__all__ = ["WebhookTriggerRepo"]
