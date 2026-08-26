"""Durable cursor for the execution event relay (I1 Phase 5)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExecutionEventRelayCursor(Base):
    """One row per relay stream tracking the last published execution_events.id."""

    __tablename__ = "execution_event_relay_cursors"

    stream_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


__all__ = ["ExecutionEventRelayCursor"]
