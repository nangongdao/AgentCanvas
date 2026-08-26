"""ORM model for registered MCP servers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class McpServer(Base):
    """A registered MCP server (stdio / sse / streamable_http transport)."""

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    catalog_entry_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("mcp_catalog_entries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    catalog_version_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("mcp_catalog_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="stdio"
    )  # stdio | sse | streamable_http
    # stdio transport
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    env_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    env_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # http transports
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    headers_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    headers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tools_cache_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tools_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
