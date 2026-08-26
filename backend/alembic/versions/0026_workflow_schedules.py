"""Add durable workflow cron schedules.

Revision ID: 0026_workflow_schedules
Revises: 0025_webhook_triggers
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026_workflow_schedules"
down_revision = "0025_webhook_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_schedules",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("published_version_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("cron_expression", sa.String(length=120), nullable=False),
        sa.Column("timezone", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("misfire_policy", sa.String(length=16), nullable=False),
        sa.Column("failure_policy", sa.String(length=16), nullable=False),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pending_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_execution_id", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["published_version_id"], ["workflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["last_execution_id"], ["executions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "name", name="uq_workflow_schedules_name"),
    )
    op.create_index("ix_workflow_schedules_workflow_id", "workflow_schedules", ["workflow_id"])
    op.create_index("ix_workflow_schedules_status", "workflow_schedules", ["status"])
    op.create_index("ix_workflow_schedules_next_run_at", "workflow_schedules", ["next_run_at"])
    op.create_index(
        "ix_workflow_schedules_last_execution_id",
        "workflow_schedules",
        ["last_execution_id"],
    )
    op.create_index(
        "ix_workflow_schedules_due", "workflow_schedules", ["status", "next_run_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_schedules_due", table_name="workflow_schedules")
    op.drop_index("ix_workflow_schedules_last_execution_id", table_name="workflow_schedules")
    op.drop_index("ix_workflow_schedules_next_run_at", table_name="workflow_schedules")
    op.drop_index("ix_workflow_schedules_status", table_name="workflow_schedules")
    op.drop_index("ix_workflow_schedules_workflow_id", table_name="workflow_schedules")
    op.drop_table("workflow_schedules")
