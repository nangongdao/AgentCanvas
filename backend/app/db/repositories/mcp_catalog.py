"""Repository helpers for the versioned MCP catalog."""

from __future__ import annotations

import builtins
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    McpCatalogEntry,
    McpCatalogUpgradeHistory,
    McpCatalogVersion,
    McpServer,
)

__all__ = ["McpCatalogRepo"]


class McpCatalogRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_entry(self, entry_id: str) -> McpCatalogEntry | None:
        return await self.session.get(McpCatalogEntry, entry_id)

    async def list_entries(self) -> builtins.list[McpCatalogEntry]:
        result = await self.session.execute(
            select(McpCatalogEntry).order_by(McpCatalogEntry.name, McpCatalogEntry.id)
        )
        return list(result.scalars().all())

    async def get_version(self, version_id: str) -> McpCatalogVersion | None:
        return await self.session.get(McpCatalogVersion, version_id)

    async def list_versions(self, entry_id: str) -> builtins.list[McpCatalogVersion]:
        result = await self.session.execute(
            select(McpCatalogVersion)
            .where(McpCatalogVersion.entry_id == entry_id)
            .order_by(McpCatalogVersion.created_at.desc(), McpCatalogVersion.id)
        )
        return list(result.scalars().all())

    async def list_history(self, entry_id: str) -> builtins.list[McpCatalogUpgradeHistory]:
        result = await self.session.execute(
            select(McpCatalogUpgradeHistory)
            .where(McpCatalogUpgradeHistory.entry_id == entry_id)
            .order_by(McpCatalogUpgradeHistory.created_at.desc(), McpCatalogUpgradeHistory.id)
        )
        return list(result.scalars().all())

    async def list_bound_servers(self, entry_id: str) -> builtins.list[McpServer]:
        result = await self.session.execute(
            select(McpServer)
            .where(McpServer.catalog_entry_id == entry_id)
            .order_by(McpServer.name, McpServer.id)
        )
        return list(result.scalars().all())

    async def create_entry(self, **values: Any) -> McpCatalogEntry:
        row = McpCatalogEntry(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_version(self, **values: Any) -> McpCatalogVersion:
        row = McpCatalogVersion(**values)
        self.session.add(row)
        await self.session.flush()
        return row

    async def add_history(self, **values: Any) -> McpCatalogUpgradeHistory:
        row = McpCatalogUpgradeHistory(**values)
        self.session.add(row)
        await self.session.flush()
        return row
