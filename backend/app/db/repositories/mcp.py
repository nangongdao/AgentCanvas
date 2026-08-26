"""Repository for MCP server rows."""

from __future__ import annotations

import builtins
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import McpServer

__all__ = ["McpServerRepo"]


class McpServerRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, server_id: str) -> McpServer | None:
        return await self.session.get(McpServer, server_id)

    async def list(self, *, enabled_only: bool = False) -> builtins.list[McpServer]:
        stmt = select(McpServer).order_by(McpServer.name)
        if enabled_only:
            stmt = stmt.where(McpServer.enabled.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_scoped(
        self, project_id: str | None, *, enabled_only: bool = False
    ) -> builtins.list[McpServer]:
        stmt = select(McpServer).order_by(McpServer.name)
        if project_id is None:
            stmt = stmt.where(McpServer.project_id.is_(None))
        else:
            stmt = stmt.where(McpServer.project_id == project_id)
        if enabled_only:
            stmt = stmt.where(McpServer.enabled.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **kwargs: Any) -> McpServer:
        row = McpServer(**{k: v for k, v in kwargs.items() if v is not None})
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, row: McpServer, **kwargs: Any) -> McpServer:
        for key, value in kwargs.items():
            if hasattr(row, key):
                setattr(row, key, value)
        await self.session.flush()
        return row

    async def delete(self, row: McpServer) -> None:
        await self.session.delete(row)
        await self.session.flush()
