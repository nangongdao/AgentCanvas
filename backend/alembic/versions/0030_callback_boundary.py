"""Track callback activation boundaries for historical event filtering."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0030_callback_boundary"
down_revision = "0029_execution_trigger_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_callbacks") as batch_op:
        batch_op.add_column(sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        sa.text(
            "UPDATE workflow_callbacks SET activated_at = created_at WHERE activated_at IS NULL"
        )
    )
    with op.batch_alter_table("workflow_callbacks") as batch_op:
        batch_op.alter_column(
            "activated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_callbacks") as batch_op:
        batch_op.drop_column("activated_at")
