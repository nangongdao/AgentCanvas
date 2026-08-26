"""Named quota plan model (C7-2)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OrgPlan(Base):
    """A named bundle of the five project quota limits plus overage policies.

    Binding a plan to an organization copies these values onto every project
    quota row of that organization (batch adjustment). ``is_system`` marks
    the seeded example plans, which cannot be deleted.
    """

    __tablename__ = "org_plans"
    __table_args__ = (
        CheckConstraint(
            "embedding_overage_policy IN ('hard', 'soft')",
            name="ck_org_plans_embedding_overage_policy",
        ),
        CheckConstraint(
            "model_cost_overage_policy IN ('hard', 'soft')",
            name="ck_org_plans_model_cost_overage_policy",
        ),
        CheckConstraint(
            "concurrent_execution_limit IS NULL OR concurrent_execution_limit >= 0",
            name="ck_org_plans_concurrent_execution_limit",
        ),
        CheckConstraint(
            "storage_bytes_limit IS NULL OR storage_bytes_limit >= 0",
            name="ck_org_plans_storage_bytes_limit",
        ),
        CheckConstraint(
            "monthly_embedding_input_bytes_limit IS NULL "
            "OR monthly_embedding_input_bytes_limit >= 0",
            name="ck_org_plans_monthly_embedding_input_bytes_limit",
        ),
        CheckConstraint(
            "monthly_model_cost_units_limit IS NULL OR monthly_model_cost_units_limit >= 0",
            name="ck_org_plans_monthly_model_cost_units_limit",
        ),
        CheckConstraint(
            "stdio_mcp_process_limit IS NULL OR stdio_mcp_process_limit >= 0",
            name="ck_org_plans_stdio_mcp_process_limit",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    concurrent_execution_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    storage_bytes_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_embedding_input_bytes_limit: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    monthly_model_cost_units_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stdio_mcp_process_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    embedding_overage_policy: Mapped[str] = mapped_column(String(8), nullable=False, default="hard")
    model_cost_overage_policy: Mapped[str] = mapped_column(
        String(8), nullable=False, default="hard"
    )
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = ["OrgPlan"]
