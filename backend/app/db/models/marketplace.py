"""Marketplace models."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.identity import User
    from app.db.models.workflow import Workflow


class MarketplaceWorkflow(Base):
    """Published workflow in the marketplace."""

    __tablename__ = "marketplace_workflows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(
        String, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False
    )
    author_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Marketplace metadata
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    icon_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    # Version information
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    changelog: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Dependency declaration
    dependencies: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Statistics
    downloads: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    rating: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, index=True)
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Moderation status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )  # pending, approved, rejected
    moderator_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    published_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    # Relationships
    workflow: Mapped["Workflow"] = relationship(back_populates="marketplace_entry")
    author: Mapped["User"] = relationship(back_populates="published_workflows")
    reviews: Mapped[list["MarketplaceReview"]] = relationship(
        back_populates="marketplace_workflow", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<MarketplaceWorkflow(id={self.id!r}, "
            f"display_name={self.display_name!r}, "
            f"version={self.version!r})>"
        )


class MarketplaceReview(Base):
    """User review for a marketplace workflow."""

    __tablename__ = "marketplace_reviews"

    __table_args__ = (
        UniqueConstraint(
            "marketplace_workflow_id",
            "user_id",
            name="uq_one_review_per_user"
        ),
        CheckConstraint("rating >= 1 AND rating <= 5", name="ck_rating_range"),
        Index("ix_marketplace_reviews_workflow_id", "marketplace_workflow_id"),
        Index("ix_marketplace_reviews_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    marketplace_workflow_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("marketplace_workflows.id", ondelete="CASCADE"),
        nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Review content
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5 stars
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)

    # Relationships
    marketplace_workflow: Mapped[MarketplaceWorkflow] = relationship(
        back_populates="reviews"
    )
    user: Mapped["User"] = relationship(back_populates="marketplace_reviews")

    def __repr__(self) -> str:
        return (
            f"<MarketplaceReview(id={self.id!r}, "
            f"rating={self.rating}, "
            f"user_id={self.user_id!r})>"
        )
