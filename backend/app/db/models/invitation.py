"""Organization membership invitation model (C7-4)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OrganizationInvitation(Base):
    """A single-use, expiring token invitation to an organization.

    Only the SHA-256 hash of the raw token is persisted; the raw value is
    returned exactly once at creation. ``accepted_at`` marks the invitation
    burned; ``accepted_by_user_id`` records who consumed it (not necessarily
    the invited email — invitations are bearer URLs by design).
    """

    __tablename__ = "organization_invitations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer', 'editor', 'admin')",
            name="ck_org_invitations_role",
        ),
        Index("ix_org_invitations_org_pending", "organization_id", "accepted_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    organization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    invited_by: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


__all__ = ["OrganizationInvitation"]
