"""Durable comments and reviews attached to immutable workflow versions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkflowComment(Base):
    __tablename__ = "workflow_comments"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "workflow_id",
            "version_id",
            name="uq_workflow_comments_aggregate",
        ),
        ForeignKeyConstraint(
            ["version_id", "workflow_id"],
            ["workflow_versions.id", "workflow_versions.workflow_id"],
            name="fk_workflow_comments_version_workflow",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["parent_comment_id", "workflow_id", "version_id"],
            [
                "workflow_comments.id",
                "workflow_comments.workflow_id",
                "workflow_comments.version_id",
            ],
            name="fk_workflow_comments_parent_aggregate",
            ondelete="CASCADE",
        ),
        Index(
            "ix_workflow_comments_workflow_version_created",
            "workflow_id",
            "version_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_comment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    author_user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_key: Mapped[str] = mapped_column(String(512), nullable=False)
    author_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resolved_by_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WorkflowReview(Base):
    __tablename__ = "workflow_reviews"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_workflow_reviews_version"),
        ForeignKeyConstraint(
            ["version_id", "workflow_id"],
            ["workflow_versions.id", "workflow_versions.workflow_id"],
            name="fk_workflow_reviews_version_workflow",
            ondelete="CASCADE",
        ),
        Index("ix_workflow_reviews_workflow_created", "workflow_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    requested_by_user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_key: Mapped[str] = mapped_column(String(512), nullable=False)
    requested_by_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    decision_summary: Mapped[str] = mapped_column(Text, default="")
    decided_by_user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    decided_by_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["WorkflowComment", "WorkflowReview"]
