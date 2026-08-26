"""Durable execution queue rows with fenced worker leases (I1 Phase 4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ExecutionQueueItem(Base):
    """One durable work unit for start/resume/rerun attempts."""

    __tablename__ = "execution_queue_items"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_execution_queue_execution"),
        Index(
            "ix_execution_queue_claim",
            "status",
            "available_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # start | resume | rerun
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="start")
    # queued | leased | retry_wait | dead_letter | done
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = ["ExecutionQueueItem"]
