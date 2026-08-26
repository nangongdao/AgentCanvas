"""Daily usage fact table for metered billing exports (C7-1).

Revision ID: 0042_usage_daily_facts
Revises: 0041_execution_event_retention
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0042_usage_daily_facts"
down_revision = "0041_execution_event_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_daily_facts",
        # Dimension primary key. app_id/model_config_id NULL means the
        # "no app" / "no model" bucket. Facts are aggregate snapshots: they
        # intentionally have no FK constraints so they survive source-row
        # deletion (tenant cleanup is C7-5's explicit two-phase flow).
        sa.Column("organization_id", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(32), nullable=False),
        sa.Column("app_id", sa.String(32), nullable=True),
        sa.Column("model_config_id", sa.String(32), nullable=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("executions", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "completion_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "cost_unknown_executions",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "storage_bytes_delta", sa.BigInteger(), nullable=False, server_default="0"
        ),
        # Placeholder until a retrieval counter exists (documented gap).
        sa.Column("retrievals", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.String(40), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "project_id",
            "app_id",
            "model_config_id",
            "day",
            name="pk_usage_daily_facts",
        ),
    )
    op.create_index(
        "ix_usage_daily_facts_org_day",
        "usage_daily_facts",
        ["organization_id", "day"],
        unique=False,
    )
    op.create_index(
        "ix_usage_daily_facts_day",
        "usage_daily_facts",
        ["day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_usage_daily_facts_day", table_name="usage_daily_facts")
    op.drop_index("ix_usage_daily_facts_org_day", table_name="usage_daily_facts")
    op.drop_table("usage_daily_facts")
