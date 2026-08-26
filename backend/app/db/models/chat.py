"""ORM models for chat sessions and messages (P5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ChatSession(Base):
    """A persisted chat conversation, optionally bound to a workflow."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    model_config_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When a session is started from a published app (C3-2), app_id ties it to
    # the app for usage attribution (C3-5) and isolation. SET NULL if the app
    # is deleted so the conversation transcript survives.
    app_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("apps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    inputs_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    messages: Mapped[list[ChatMessageRow]] = relationship(
        back_populates="session", order_by="ChatMessageRow.created_at", cascade="all, delete-orphan"
    )


class ChatMessageRow(Base):
    """A single message within a chat session."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    node_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
    feedback: Mapped[ChatMessageFeedback | None] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class ChatMessageFeedback(Base):
    """End-user feedback (👍/👎) on an assistant message (C3-4).

    A ``negative`` rating is the canonical signal for promoting the
    conversation turn into an evaluation-dataset candidate (C3-4 ↔ D2).
    One row per message (unique on ``message_id``).
    """

    __tablename__ = "chat_message_feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    session_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rating: Mapped[str] = mapped_column(String(16), nullable=False)  # positive | negative
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    promoted_dataset_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    message: Mapped[ChatMessageRow] = relationship(back_populates="feedback")


class ChatSessionVariable(Base):
    """A per-session key/value store for multi-turn memory (C3-4).

    Nodes can read/write these via ``{{vars.<name>}}`` template references,
    decoupling conversational state from the static ``inputs`` snapshot.
    Unique on ``(session_id, name)``.
    """

    __tablename__ = "chat_session_variables"
    __table_args__ = (
        UniqueConstraint("session_id", "name", name="uq_chat_session_variable"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


__all__ = [
    "ChatMessageFeedback",
    "ChatMessageRow",
    "ChatSession",
    "ChatSessionVariable",
]
