"""Durable cron schedules bound to immutable workflow versions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowSchedule(Base):
    """One independently managed cron trigger for a published workflow."""

    __tablename__ = "workflow_schedules"
    __table_args__ = (
        UniqueConstraint("workflow_id", "name", name="uq_workflow_schedules_name"),
        Index("ix_workflow_schedules_due", "status", "next_run_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    published_version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(120), nullable=False, default="UTC")
    # active | disabled | error
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # skip collapses missed slots; catch_up advances one original slot per dispatch.
    misfire_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="skip")
    # skip advances, retry retains the slot, alert pauses in error state.
    failure_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="skip")
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    pending_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_execution_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("executions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = ["WorkflowSchedule"]
