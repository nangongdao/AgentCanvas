"""Platform announcement banner model (C7-3)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PlatformAnnouncement(Base):
    """One durable banner message shown to every authenticated user.

    ``level`` drives severity styling; ``is_active`` toggles visibility
    without deleting the historical row. ``created_by`` records the admin
    principal subject that published the message.
    """

    __tablename__ = "platform_announcements"
    __table_args__ = (
        CheckConstraint(
            "level IN ('info', 'warning', 'critical')",
            name="ck_platform_announcements_level",
        ),
        Index("ix_platform_announcements_active_created", "is_active", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = ["PlatformAnnouncement"]
