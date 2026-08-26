"""Repository for online knowledge sources (C4-3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OnlineSource
from app.db.repositories.knowledge import DocumentRepo


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OnlineSourceRepo:
    """CRUD for URL-based knowledge sources."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, source_id: str) -> OnlineSource | None:
        return await self.session.get(OnlineSource, source_id)

    async def list_for_kb(self, kb_id: str) -> list[OnlineSource]:
        result = await self.session.execute(
            select(OnlineSource)
            .where(OnlineSource.kb_id == kb_id)
            .order_by(OnlineSource.created_at.asc(), OnlineSource.id.asc())
        )
        return list(result.scalars().all())

    async def list_due(
        self,
        now: datetime,
        *,
        stale_before: datetime,
        limit: int = 50,
    ) -> list[OnlineSource]:
        """Return scheduled sources that are due or whose prior claim expired."""
        result = await self.session.execute(
            select(OnlineSource)
            .where(
                OnlineSource.sync_interval_minutes.is_not(None),
                OnlineSource.next_sync_at.is_not(None),
                OnlineSource.next_sync_at <= now,
                or_(
                    OnlineSource.status != "syncing",
                    OnlineSource.sync_started_at.is_(None),
                    OnlineSource.sync_started_at <= stale_before,
                ),
            )
            .order_by(OnlineSource.next_sync_at.asc(), OnlineSource.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        kb_id: str,
        url: str,
        max_pages: int,
        depth: int,
        sync_interval_minutes: int | None = None,
        now: datetime | None = None,
    ) -> OnlineSource:
        created_at = now or _utcnow()
        row = OnlineSource(
            kb_id=kb_id,
            url=url,
            max_pages=max_pages,
            depth=depth,
            status="pending",
            sync_interval_minutes=sync_interval_minutes,
            next_sync_at=created_at if sync_interval_minutes is not None else None,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def configure(
        self,
        row: OnlineSource,
        *,
        max_pages: int,
        depth: int,
        sync_interval_minutes: int | None,
        schedule_changed: bool,
        now: datetime | None = None,
    ) -> OnlineSource | None:
        """Atomically update an idle source and make changed scheduled work due."""
        source_id = row.id
        updated_at = now or _utcnow()
        changed = row.max_pages != max_pages or row.depth != depth
        interval = row.sync_interval_minutes
        values: dict[str, object] = {
            "max_pages": max_pages,
            "depth": depth,
            "updated_at": updated_at,
        }
        if schedule_changed:
            changed = changed or row.sync_interval_minutes != sync_interval_minutes
            interval = sync_interval_minutes
            values["sync_interval_minutes"] = sync_interval_minutes
        if interval is None:
            values["next_sync_at"] = None
        elif changed:
            values["next_sync_at"] = updated_at
        configured = await self.session.execute(
            update(OnlineSource)
            .where(OnlineSource.id == source_id, OnlineSource.status != "syncing")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if int(getattr(configured, "rowcount", 0) or 0) != 1:
            return None
        self.session.expire_all()
        return await self.get(source_id)

    async def claim(
        self,
        source_id: str,
        *,
        now: datetime,
        stale_before: datetime,
        force: bool,
    ) -> OnlineSource | None:
        """Atomically claim a manual or due sync and advance its fence generation."""
        conditions = [
            OnlineSource.id == source_id,
            or_(
                OnlineSource.status != "syncing",
                OnlineSource.sync_started_at.is_(None),
                OnlineSource.sync_started_at <= stale_before,
            ),
        ]
        if not force:
            conditions.extend(
                [
                    OnlineSource.sync_interval_minutes.is_not(None),
                    OnlineSource.next_sync_at.is_not(None),
                    OnlineSource.next_sync_at <= now,
                ]
            )
        claimed = await self.session.execute(
            update(OnlineSource)
            .where(*conditions)
            .values(
                status="syncing",
                error=None,
                sync_started_at=now,
                sync_generation=OnlineSource.sync_generation + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if int(getattr(claimed, "rowcount", 0) or 0) != 1:
            return None
        self.session.expire_all()
        return await self.get(source_id)

    async def finish_success(
        self,
        source_id: str,
        *,
        generation: int,
        completed_at: datetime,
        document_id: str | None,
        content_sha256: str | None,
        retired_document_id: str | None = None,
    ) -> bool:
        """Complete one generation and schedule its next run."""
        values: dict[str, object] = {
            "status": "ready",
            "error": None,
            "last_synced_at": completed_at,
            "sync_started_at": None,
            "updated_at": completed_at,
        }
        if document_id is not None:
            values["document_id"] = document_id
        if content_sha256 is not None:
            values["content_sha256"] = content_sha256
        finished = await self.session.execute(
            update(OnlineSource)
            .where(
                OnlineSource.id == source_id,
                OnlineSource.status == "syncing",
                OnlineSource.sync_generation == generation,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if int(getattr(finished, "rowcount", 0) or 0) != 1:
            return False
        self.session.expire_all()
        row = await self.get(source_id)
        if row is None:
            return False
        if document_id is not None:
            staged = await DocumentRepo(self.session).get(document_id)
            if staged is None or staged.kb_id != row.kb_id or staged.status != "pending":
                return False
            staged.status = "ready"
            staged.error = None
            staged.updated_at = completed_at
        row.next_sync_at = (
            completed_at + timedelta(minutes=row.sync_interval_minutes)
            if row.sync_interval_minutes is not None
            else None
        )
        if retired_document_id and retired_document_id != row.document_id:
            retired = await DocumentRepo(self.session).get(retired_document_id)
            if retired is not None and retired.kb_id == row.kb_id:
                retired.status = "pending"
                retired.updated_at = completed_at
        await self.session.flush()
        return True

    async def finish_failure(
        self,
        source_id: str,
        *,
        generation: int,
        completed_at: datetime,
        error: str,
        retry_immediately: bool = False,
    ) -> bool:
        """Persist a fenced failure while retaining the prior successful document."""
        failed = await self.session.execute(
            update(OnlineSource)
            .where(
                OnlineSource.id == source_id,
                OnlineSource.status == "syncing",
                OnlineSource.sync_generation == generation,
            )
            .values(
                status="failed",
                error=error[:2000],
                sync_started_at=None,
                updated_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if int(getattr(failed, "rowcount", 0) or 0) != 1:
            return False
        self.session.expire_all()
        row = await self.get(source_id)
        if row is None:
            return False
        row.next_sync_at = (
            completed_at
            if retry_immediately and row.sync_interval_minutes is not None
            else completed_at + timedelta(minutes=row.sync_interval_minutes)
            if row.sync_interval_minutes is not None
            else None
        )
        await self.session.flush()
        return True

    async def update(
        self,
        row: OnlineSource,
        *,
        status: str | None = None,
        document_id: str | None = None,
        content_sha256: str | None = None,
        error: str | None = None,
        last_synced_at: datetime | None = None,
        max_pages: int | None = None,
        depth: int | None = None,
    ) -> OnlineSource:
        if status is not None:
            row.status = status
        if document_id is not None:
            row.document_id = document_id
        if content_sha256 is not None:
            row.content_sha256 = content_sha256
        if error is not None:
            row.error = error
        if last_synced_at is not None:
            row.last_synced_at = last_synced_at
        if max_pages is not None:
            row.max_pages = max_pages
        if depth is not None:
            row.depth = depth
        row.updated_at = _utcnow()
        await self.session.flush()
        return row

    async def delete(self, row: OnlineSource) -> None:
        await self.session.delete(row)
