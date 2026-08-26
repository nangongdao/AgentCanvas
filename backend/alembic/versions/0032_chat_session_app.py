"""Bind chat sessions to published apps for C3-2 runtime attribution."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032_chat_session_app"
down_revision = "0031_apps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("app_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_chat_sessions_app_id_apps",
            "apps",
            ["app_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_chat_sessions_app_id", ["app_id"])


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.drop_index("ix_chat_sessions_app_id")
        batch_op.drop_constraint("fk_chat_sessions_app_id_apps", type_="foreignkey")
        batch_op.drop_column("app_id")
