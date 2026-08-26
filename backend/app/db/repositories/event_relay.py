"""Repository for the durable execution event relay cursor."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExecutionEventRelayCursor


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExecutionEventRelayRepo:
    """Track the highest execution_events.id published by a relay instance."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self,
        stream_key: str,
        *,
        lock: bool = False,
    ) -> ExecutionEventRelayCursor:
        values = {
            "stream_key": stream_key,
            "last_event_id": 0,
            "updated_at": _utcnow(),
        }
        insert = (
            postgresql_insert(ExecutionEventRelayCursor)
            if self.session.get_bind().dialect.name == "postgresql"
            else sqlite_insert(ExecutionEventRelayCursor)
        )
        await self.session.execute(
            insert.values(**values).on_conflict_do_nothing(index_elements=("stream_key",))
        )
        stmt = select(ExecutionEventRelayCursor).where(
            ExecutionEventRelayCursor.stream_key == stream_key
        )
        if lock:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        row = result.scalar_one()
        return row

    async def advance(self, stream_key: str, last_event_id: int) -> ExecutionEventRelayCursor:
        row = await self.get_or_create(stream_key)
        if last_event_id > int(row.last_event_id):
            row.last_event_id = int(last_event_id)
            row.updated_at = _utcnow()
            await self.session.flush()
        return row


__all__ = ["ExecutionEventRelayRepo"]
