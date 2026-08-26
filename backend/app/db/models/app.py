"""Published application entities binding a workflow version for end users (C3-1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class App(Base):
    """A tenant-scoped application that exposes a published workflow version
    to end users as a chatbot / completion / API surface.

    The app binds a single ``published_version_id`` (switched explicitly so
    deployments do not silently drift on re-publish) and carries an
    ``input_form`` snapshot regenerated whenever the bound version changes,
    so the editor can render the form without re-reading the version DSL on
    every request. Public ``link``/``public`` apps carry an unguessable
    ``public_token_hash`` (SHA-256 of the raw token, never the raw token).
    """

    __tablename__ = "apps"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_apps_project_slug"),
        UniqueConstraint("public_token_hash", name="uq_apps_public_token_hash"),
        CheckConstraint("type IN ('chatbot', 'completion', 'api')", name="ck_apps_type"),
        CheckConstraint(
            "visibility IN ('project', 'link', 'public')", name="ck_apps_visibility"
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_apps_status"),
        CheckConstraint(
            "(visibility IN ('link', 'public') AND public_token_hash IS NOT NULL) "
            "OR (visibility = 'project')",
            name="ck_apps_visibility_token",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True, index=True
    )
    published_version_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("workflow_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # chatbot | completion | api
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="chatbot", index=True)
    welcome_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_questions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Snapshot of the bound version's input definitions; regenerated on switch.
    input_form: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    # project | link | public
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="project", index=True
    )
    # active | disabled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    public_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    token_prefix: Mapped[str | None] = mapped_column(String(12), nullable=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    # C3-3: optional brand theme color (#rrggbb) applied on the runtime page.
    theme_color: Mapped[str | None] = mapped_column(String(9), nullable=True)
    # C3-3: allow-list of origins permitted to embed the app via iframe/bubble.
    # NULL/empty disables embedding (frame-ancestors 'none').
    embed_allowed_origins: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    project: Mapped[Any] = relationship("Project", back_populates="apps")
    workflow: Mapped[Any] = relationship("Workflow")
    version: Mapped[Any] = relationship("WorkflowVersion")


__all__ = ["App"]
