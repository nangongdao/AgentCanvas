"""Add provenance for failed-node rerun executions.

Revision ID: 0008_execution_reruns
Revises: 0007_workflow_templates
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_execution_reruns"
down_revision = "0007_workflow_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("executions") as batch_op:
        batch_op.add_column(
            sa.Column("parent_execution_id", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(
            sa.Column("rerun_from_node_id", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_executions_parent_execution_id",
            "executions",
            ["parent_execution_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_executions_parent_execution_id", ["parent_execution_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("executions") as batch_op:
        batch_op.drop_index("ix_executions_parent_execution_id")
        batch_op.drop_constraint(
            "fk_executions_parent_execution_id", type_="foreignkey"
        )
        batch_op.drop_column("rerun_from_node_id")
        batch_op.drop_column("parent_execution_id")
