"""Published workflow API bindings backed by service-account tokens."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowApiPublication(Base):
    __tablename__ = "workflow_api_publications"
    __table_args__ = (
        UniqueConstraint("workflow_id", name="uq_workflow_api_publications_workflow"),
        UniqueConstraint("api_token_id", name="uq_workflow_api_publications_token"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    published_version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflow_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service_account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("service_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_token_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("api_tokens.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["WorkflowApiPublication"]
