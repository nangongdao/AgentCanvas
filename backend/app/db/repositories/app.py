"""Data access for application entities (C3-1)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import App
from app.db.pagination import PageSlice, PageSpec, paginate_select


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AppRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, app_id: str) -> App | None:
        return await self.session.get(App, app_id)

    async def get_for_project(self, project_id: str, app_id: str) -> App | None:
        result = await self.session.execute(
            select(App).where(App.id == app_id, App.project_id == project_id)
        )
        return result.scalars().first()

    async def list_for_project(
        self, project_id: str, spec: PageSpec
    ) -> PageSlice[App]:
        stmt = select(App).where(App.project_id == project_id)
        return await paginate_select(
            self.session,
            stmt,
            id_column=App.id,
            columns={
                "created_at": App.created_at,
                "updated_at": App.updated_at,
                "name": App.name,
                "id": App.id,
            },
            spec=spec,
            search_columns=(App.name, App.slug, App.id),
        )

    async def get_by_slug(self, project_id: str, slug: str) -> App | None:
        result = await self.session.execute(
            select(App).where(App.project_id == project_id, App.slug == slug)
        )
        return result.scalars().first()

    async def get_by_public_token_hash(self, token_hash: str) -> App | None:
        result = await self.session.execute(
            select(App).where(App.public_token_hash == token_hash)
        )
        return result.scalars().first()

    async def get_runtime_by_slug(self, slug: str) -> App | None:
        """Resolve a runnable app by slug for the public runtime.

        Only active apps with both a bound workflow and a published version are
        runnable; the caller enforces the visibility/token rule. Slug uniqueness
        is per-project, so when multiple projects share a slug the most
        recently created public/link app wins (public runtime reachability is
        expected to use project-distinct slugs in practice).
        """
        result = await self.session.execute(
            select(App)
            .where(
                App.slug == slug,
                App.status == "active",
                App.workflow_id.is_not(None),
                App.published_version_id.is_not(None),
            )
            .order_by(App.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def slug_exists(self, project_id: str, slug: str) -> bool:
        result = await self.session.execute(
            select(func.count()).select_from(App).where(
                App.project_id == project_id, App.slug == slug
            )
        )
        return int(result.scalar_one() or 0) > 0

    async def create(self, row: App) -> App:
        self.session.add(row)
        await self.session.flush()
        return row

    async def apply_version(
        self,
        row: App,
        *,
        published_version_id: str | None,
        input_form: list[dict[str, object]] | None,
    ) -> None:
        row.published_version_id = published_version_id
        row.input_form = input_form
        row.updated_at = _utcnow()
        await self.session.flush()

    async def update_fields(self, row: App, **fields: object) -> None:
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = _utcnow()
        await self.session.flush()

    async def delete(self, row: App) -> None:
        await self.session.delete(row)
        await self.session.flush()


__all__ = ["AppRepo"]
