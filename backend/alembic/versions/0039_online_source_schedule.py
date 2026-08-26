"""Add durable periodic scheduling for online sources (C4-3).

Revision ID: 0039_online_source_schedule
Revises: 0038_citation_context
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0039_online_source_schedule"
down_revision = "0038_citation_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("online_sources") as batch:
        batch.add_column(sa.Column("sync_interval_minutes", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("sync_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "sync_generation",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.create_check_constraint(
            "ck_online_sources_sync_interval",
            "sync_interval_minutes IS NULL OR "
            "(sync_interval_minutes >= 5 AND sync_interval_minutes <= 10080)",
        )
        batch.create_index(
            "ix_online_sources_schedule_due",
            ["next_sync_at", "status"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("online_sources") as batch:
        batch.drop_index("ix_online_sources_schedule_due")
        batch.drop_constraint("ck_online_sources_sync_interval", type_="check")
        batch.drop_column("sync_generation")
        batch.drop_column("sync_started_at")
        batch.drop_column("next_sync_at")
        batch.drop_column("sync_interval_minutes")
