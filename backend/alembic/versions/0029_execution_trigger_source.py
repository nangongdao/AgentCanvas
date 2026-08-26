"""Record the origin of each workflow execution."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029_execution_trigger_source"
down_revision = "0028_workflow_callbacks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("executions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "trigger_source",
                sa.String(length=16),
                nullable=False,
                server_default="manual",
            )
        )
        batch_op.create_check_constraint(
            "ck_executions_trigger_source",
            "trigger_source IN ('manual', 'webhook', 'schedule', 'api')",
        )
        batch_op.create_index("ix_executions_trigger_source", ["trigger_source"])


def downgrade() -> None:
    with op.batch_alter_table("executions") as batch_op:
        batch_op.drop_index("ix_executions_trigger_source")
        batch_op.drop_constraint("ck_executions_trigger_source", type_="check")
        batch_op.drop_column("trigger_source")
