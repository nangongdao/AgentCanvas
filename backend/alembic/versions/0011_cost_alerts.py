"""Add durable execution cost/budget alerts.

Revision ID: 0011_cost_alerts
Revises: 0010_evaluation_comparisons
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_cost_alerts"
down_revision = "0010_evaluation_comparisons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_alerts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("execution_id", sa.String(length=32), nullable=True),
        sa.Column("workflow_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("limit_value", sa.String(length=60), nullable=False),
        sa.Column("actual_value", sa.String(length=60), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["executions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cost_alerts_execution_id", "cost_alerts", ["execution_id"])
    op.create_index("ix_cost_alerts_workflow_id", "cost_alerts", ["workflow_id"])
    op.create_index("ix_cost_alerts_kind", "cost_alerts", ["kind"])
    op.create_index("ix_cost_alerts_status", "cost_alerts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_cost_alerts_status", table_name="cost_alerts")
    op.drop_index("ix_cost_alerts_kind", table_name="cost_alerts")
    op.drop_index("ix_cost_alerts_workflow_id", table_name="cost_alerts")
    op.drop_index("ix_cost_alerts_execution_id", table_name="cost_alerts")
    op.drop_table("cost_alerts")
