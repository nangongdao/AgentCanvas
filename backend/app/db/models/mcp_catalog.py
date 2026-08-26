"""Versioned, approved MCP catalog records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class McpCatalogEntry(Base):
    """A stable catalog identity whose versions can be approved independently."""

    __tablename__ = "mcp_catalog_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class McpCatalogVersion(Base):
    """One immutable manifest and source reference for a catalog entry."""

    __tablename__ = "mcp_catalog_versions"
    __table_args__ = (UniqueConstraint("entry_id", "version", name="uq_mcp_catalog_entry_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    entry_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("mcp_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class McpCatalogUpgradeHistory(Base):
    """Append-only approval and upgrade transitions for catalog entries."""

    __tablename__ = "mcp_catalog_upgrade_history"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    entry_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("mcp_catalog_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_version: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_key: Mapped[str] = mapped_column(String(160), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
