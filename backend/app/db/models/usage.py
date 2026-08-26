"""Usage daily fact model (C7-1)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UsageDailyFact(Base):
    """One metered bucket per (organization, project, app, model, day).

    Aggregate snapshot rows: intentionally no FK constraints so they survive
    deletion of the underlying source entities — tenant-wide cleanup of aged
    facts belongs to C7-5's explicit two-phase org-deletion flow. NULL
    ``app_id`` / ``model_config_id`` are the "no app" / "no model" buckets.
    """

    __tablename__ = "usage_daily_facts"
    __table_args__ = (
        Index("ix_usage_daily_facts_org_day", "organization_id", "day"),
        Index("ix_usage_daily_facts_day", "day"),
    )

    organization_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    app_id: Mapped[str | None] = mapped_column(String(32), primary_key=True)
    model_config_id: Mapped[str | None] = mapped_column(String(32), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)

    executions: Mapped[int] = mapped_column(BigInteger, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_unknown_executions: Mapped[int] = mapped_column(BigInteger, default=0)
    storage_bytes_delta: Mapped[int] = mapped_column(BigInteger, default=0)
    # Placeholder until a retrieval counter is instrumented (documented gap).
    retrievals: Mapped[int] = mapped_column(BigInteger, default=0)
    # COST_QUANTUM_USD-formatted decimal string; NULL when every contributing
    # execution had unknown pricing.
    estimated_cost_usd: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = ["UsageDailyFact"]
