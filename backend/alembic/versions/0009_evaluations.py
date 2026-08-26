"""Add versioned datasets and evaluation run results.

Revision ID: 0009_evaluations
Revises: 0008_execution_reruns
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_evaluations"
down_revision = "0008_execution_reruns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_datasets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_datasets_name", "evaluation_datasets", ["name"])
    op.create_table(
        "evaluation_dataset_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("cases_json", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["evaluation_datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dataset_id", "number", name="uq_evaluation_dataset_version"),
    )
    op.create_index(
        "ix_evaluation_dataset_versions_dataset_id",
        "evaluation_dataset_versions",
        ["dataset_id"],
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_version_id", sa.String(length=32), nullable=False),
        sa.Column("evaluator_type", sa.String(length=32), nullable=False),
        sa.Column("evaluator_config_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["dataset_version_id"], ["evaluation_dataset_versions.id"]),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("dataset_version_id", "workflow_version_id", "evaluator_type", "status"):
        op.create_index(f"ix_evaluation_runs_{column}", "evaluation_runs", [column])
    op.create_table(
        "evaluation_case_results",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("case_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("expected_json", sa.JSON(), nullable=True),
        sa.Column("actual_json", sa.JSON(), nullable=True),
        sa.Column("execution_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "case_id", name="uq_evaluation_run_case"),
    )
    for column in ("run_id", "execution_id", "status"):
        op.create_index(
            f"ix_evaluation_case_results_{column}", "evaluation_case_results", [column]
        )


def downgrade() -> None:
    op.drop_table("evaluation_case_results")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_dataset_versions")
    op.drop_index("ix_evaluation_datasets_name", table_name="evaluation_datasets")
    op.drop_table("evaluation_datasets")
