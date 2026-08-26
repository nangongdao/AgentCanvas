"""ORM models for project quota limits, usage, and reservations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProjectQuota(Base):
    __tablename__ = "project_quotas"
    __table_args__ = (
        CheckConstraint(
            "concurrent_execution_limit IS NULL OR concurrent_execution_limit >= 0",
            name="ck_quota_concurrent_execution_limit",
        ),
        CheckConstraint(
            "storage_bytes_limit IS NULL OR storage_bytes_limit >= 0",
            name="ck_quota_storage_bytes_limit",
        ),
        CheckConstraint(
            "monthly_embedding_input_bytes_limit IS NULL "
            "OR monthly_embedding_input_bytes_limit >= 0",
            name="ck_quota_monthly_embedding_input_bytes_limit",
        ),
        CheckConstraint(
            "monthly_model_cost_units_limit IS NULL OR monthly_model_cost_units_limit >= 0",
            name="ck_quota_monthly_model_cost_units_limit",
        ),
        CheckConstraint(
            "stdio_mcp_process_limit IS NULL OR stdio_mcp_process_limit >= 0",
            name="ck_quota_stdio_mcp_process_limit",
        ),
        CheckConstraint(
            "embedding_overage_policy IN ('hard', 'soft')",
            name="ck_quota_embedding_overage_policy",
        ),
        CheckConstraint(
            "model_cost_overage_policy IN ('hard', 'soft')",
            name="ck_quota_model_cost_overage_policy",
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    concurrent_execution_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_bytes_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_embedding_input_bytes_limit: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    monthly_model_cost_units_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stdio_mcp_process_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # C7-2: overage behavior for the two monthly metered metrics, copied from
    # the bound plan. ``hard`` rejects the crossing charge, ``soft`` records
    # the overage plus one durable cost alert and continues. Realtime limits
    # (executions/storage/stdio processes) always reject.
    embedding_overage_policy: Mapped[str] = mapped_column(String(8), nullable=False, default="hard")
    model_cost_overage_policy: Mapped[str] = mapped_column(
        String(8), nullable=False, default="hard"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProjectQuotaCounter(Base):
    __tablename__ = "project_quota_counters"
    __table_args__ = (
        CheckConstraint(
            "concurrent_executions >= 0", name="ck_quota_counter_concurrent_executions"
        ),
        CheckConstraint("storage_bytes >= 0", name="ck_quota_counter_storage_bytes"),
        CheckConstraint("stdio_mcp_processes >= 0", name="ck_quota_counter_stdio_mcp_processes"),
    )

    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    concurrent_executions: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    stdio_mcp_processes: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProjectQuotaPeriodUsage(Base):
    __tablename__ = "project_quota_period_usage"
    __table_args__ = (
        CheckConstraint("embedding_input_bytes >= 0", name="ck_quota_usage_embedding_input_bytes"),
        CheckConstraint("model_cost_units >= 0", name="ck_quota_usage_model_cost_units"),
    )

    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    embedding_input_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    model_cost_units: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class ProjectQuotaReservation(Base):
    __tablename__ = "project_quota_reservations"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_quota_reservation_amount"),
        CheckConstraint(
            "kind IN ('execution', 'document_storage', 'stdio_mcp_process')",
            name="ck_quota_reservation_kind",
        ),
        UniqueConstraint("project_id", "kind", "resource_id", name="uq_quota_reservation_resource"),
        Index("ix_quota_reservations_project_kind", "project_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
