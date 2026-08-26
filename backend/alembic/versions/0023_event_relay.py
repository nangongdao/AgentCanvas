"""Add durable execution event relay cursor.

Revision ID: 0023_event_relay
Revises: 0022_execution_queue
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_event_relay"
down_revision = "0022_execution_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_event_relay_cursors",
        sa.Column("stream_key", sa.String(length=64), nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("last_event_id >= 0", name="ck_event_relay_last_event_id"),
        sa.PrimaryKeyConstraint("stream_key"),
    )


def downgrade() -> None:
    op.drop_table("execution_event_relay_cursors")
