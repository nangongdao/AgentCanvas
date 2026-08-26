"""Add chat message feedback and session variables (C3-4).

Introduces:

* ``chat_message_feedback``: one feedback row per assistant message
  (positive/negative with an optional comment). A negative vote is the
  canonical signal that a conversation turn should be considered as an
  evaluation-dataset candidate.
* ``chat_session_variables``: a per-session key/value store so multi-turn
  memory can be read/written from node config (``{{vars.<name>}}``) instead
  of only from the static ``inputs`` snapshot.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034_chat_feedback"
down_revision = "0033_apps_embed_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_message_feedback",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(length=32),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("session_id", sa.String(length=32), nullable=False, index=True),
        # positive | negative
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text, nullable=False, server_default=""),
        sa.Column("promoted_dataset_version_id", sa.String(length=32), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "chat_session_variables",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=32),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "name", name="uq_chat_session_variable"),
    )


def downgrade() -> None:
    op.drop_table("chat_session_variables")
    op.drop_table("chat_message_feedback")
