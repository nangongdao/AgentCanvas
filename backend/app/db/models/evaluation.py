"""Versioned evaluation datasets and auditable run results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


def _uuid() -> str:
    return uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EvaluationDataset(Base):
    __tablename__ = "evaluation_datasets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    versions: Mapped[list[EvaluationDatasetVersion]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        order_by="EvaluationDatasetVersion.number",
    )


class EvaluationDatasetVersion(Base):
    __tablename__ = "evaluation_dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "number", name="uq_evaluation_dataset_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("evaluation_datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    cases_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    dataset: Mapped[EvaluationDataset] = relationship(back_populates="versions")
    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="dataset_version")
    comparisons: Mapped[list[EvaluationComparison]] = relationship(
        back_populates="dataset_version", cascade="all, delete-orphan"
    )


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    dataset_version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("evaluation_dataset_versions.id"), nullable=False, index=True
    )
    workflow_version_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("workflow_versions.id"), nullable=False, index=True
    )
    evaluator_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    evaluator_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dataset_version: Mapped[EvaluationDatasetVersion] = relationship(back_populates="runs")
    workflow_version: Mapped[Any] = relationship("WorkflowVersion")
    case_results: Mapped[list[EvaluationCaseResult]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EvaluationCaseResult.case_index",
    )
    as_variant_a: Mapped[list[EvaluationComparison]] = relationship(
        back_populates="variant_a_run", foreign_keys="EvaluationComparison.variant_a_run_id"
    )
    as_variant_b: Mapped[list[EvaluationComparison]] = relationship(
        back_populates="variant_b_run", foreign_keys="EvaluationComparison.variant_b_run_id"
    )


class EvaluationComparison(Base):
    """Side-by-side report over two evaluation runs on one dataset snapshot."""

    __tablename__ = "evaluation_comparisons"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    dataset_version_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("evaluation_dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_a_run_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_b_run_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dataset_version: Mapped[EvaluationDatasetVersion] = relationship(
        back_populates="comparisons"
    )
    variant_a_run: Mapped[EvaluationRun] = relationship(
        foreign_keys=[variant_a_run_id], back_populates="as_variant_a"
    )
    variant_b_run: Mapped[EvaluationRun] = relationship(
        foreign_keys=[variant_b_run_id], back_populates="as_variant_b"
    )


class EvaluationCaseResult(Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_evaluation_run_case"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    case_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    actual_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    execution_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("executions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cost_known: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_error_bound_usd: Mapped[str | None] = mapped_column(String(40), nullable=True)
    price_versions_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[EvaluationRun] = relationship(back_populates="case_results")
    execution: Mapped[Any] = relationship("Execution")


__all__ = [
    "EvaluationCaseResult",
    "EvaluationComparison",
    "EvaluationDataset",
    "EvaluationDatasetVersion",
    "EvaluationRun",
]
