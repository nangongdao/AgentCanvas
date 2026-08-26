"""Add side-by-side A/B evaluation comparison reports.

Revision ID: 0010_evaluation_comparisons
Revises: 0009_evaluations
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_evaluation_comparisons"
down_revision = "0009_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_configs") as batch:
        batch.add_column(sa.Column("prompt_price_per_million_usd", sa.String(40)))
        batch.add_column(sa.Column("completion_price_per_million_usd", sa.String(40)))
        batch.add_column(sa.Column("pricing_version", sa.String(120)))

    with op.batch_alter_table("evaluation_case_results") as batch:
        batch.add_column(
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("estimated_cost_usd", sa.String(40)))
        batch.add_column(
            sa.Column("cost_known", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("cost_error_bound_usd", sa.String(40)))
        batch.add_column(sa.Column("price_versions_json", sa.JSON()))

    op.create_table(
        "evaluation_comparisons",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=32), nullable=False),
        sa.Column("variant_a_run_id", sa.String(length=32), nullable=False),
        sa.Column("variant_b_run_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["evaluation_dataset_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_a_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["variant_b_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_comparisons_dataset_version_id",
        "evaluation_comparisons",
        ["dataset_version_id"],
    )
    op.create_index(
        "ix_evaluation_comparisons_status", "evaluation_comparisons", ["status"]
    )


def downgrade() -> None:
    op.drop_table("evaluation_comparisons")
    with op.batch_alter_table("evaluation_case_results") as batch:
        batch.drop_column("price_versions_json")
        batch.drop_column("cost_error_bound_usd")
        batch.drop_column("cost_known")
        batch.drop_column("estimated_cost_usd")
        batch.drop_column("total_tokens")
        batch.drop_column("completion_tokens")
        batch.drop_column("prompt_tokens")
    with op.batch_alter_table("model_configs") as batch:
        batch.drop_column("pricing_version")
        batch.drop_column("completion_price_per_million_usd")
        batch.drop_column("prompt_price_per_million_usd")
