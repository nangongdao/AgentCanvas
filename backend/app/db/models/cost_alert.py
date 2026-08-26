"""Durable execution budget and cost alerts (D2 cost governance)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CostAlert(Base):
    """One budget-ceiling observation for an execution.

    ``kind`` is ``calls``, ``tokens``, ``cost`` or ``concurrency``; ``severity``
    distinguishes a soft 80% threshold (``warning``) from a hard ceiling that
    stopped the execution (``critical``). ``limit_value``/``actual_value`` are
    exact strings so money never travels through float.
    """

    __tablename__ = "cost_alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    execution_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(32), default="critical")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    limit_value: Mapped[str] = mapped_column(String(60), nullable=False)
    actual_value: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = ["CostAlert"]
