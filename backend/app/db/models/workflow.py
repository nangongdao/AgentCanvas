"""ORM models for workflows, executions, events and model configs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    dsl_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Optional eval gate: {"dataset_version_id": str, "threshold": float}.
    # When set, publishing requires the latest completed run on that dataset
    # version to meet the pass-rate threshold (Backlog: eval gate on publish).
    evaluation_policy: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    project_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    versions: Mapped[list[WorkflowVersion]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        order_by="WorkflowVersion.number",
    )
    executions: Mapped[list[Execution]] = relationship(back_populates="workflow")
    project: Mapped[Any | None] = relationship("Project", back_populates="workflows")


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "number", name="uq_workflow_version_number"),
        UniqueConstraint("id", "workflow_id", name="uq_workflow_versions_id_workflow"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    dsl_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[Workflow] = relationship(back_populates="versions")
    executions: Mapped[list[Execution]] = relationship(back_populates="workflow_version")


class Execution(Base):
    __tablename__ = "executions"
    __table_args__ = (
        CheckConstraint(
            "trigger_source IN ('manual', 'webhook', 'schedule', 'api')",
            name="ck_executions_trigger_source",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflows.id"), nullable=False, index=True
    )
    workflow_version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflow_versions.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    trigger_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", index=True
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_id: Mapped[str] = mapped_column(String(32), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_execution_id: Mapped[str | None] = mapped_column(
        String(32),
        ForeignKey("executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rerun_from_node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[Workflow] = relationship(back_populates="executions")
    workflow_version: Mapped[WorkflowVersion] = relationship(
        back_populates="executions", lazy="selectin"
    )
    events: Mapped[list[ExecutionEventRow]] = relationship(
        back_populates="execution", order_by="ExecutionEventRow.seq"
    )


class ExecutionEventRow(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        UniqueConstraint("execution_id", "seq", name="uq_exec_seq"),
        Index("ix_execution_events_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("executions.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    execution: Mapped[Execution] = relationship(back_populates="events")


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # openai_compat/...
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities_json: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False, default=dict)
    prompt_price_per_million_usd: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completion_price_per_million_usd: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="chat")  # chat | embedding
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
