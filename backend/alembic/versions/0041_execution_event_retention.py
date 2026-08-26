"""Add execution event retention index (C6-1).

Revision ID: 0041_execution_event_retention
Revises: 0040_user_language
"""

from __future__ import annotations

from alembic import op


revision = "0041_execution_event_retention"
down_revision = "0040_user_language"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite index on ``ts`` so the retention scheduler can scan for aged-out
    # events by time without a full table scan. batch_alter_table keeps the
    # statement portable across SQLite and PostgreSQL.
    with op.batch_alter_table("execution_events") as batch:
        batch.create_index("ix_execution_events_ts", ["ts"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("execution_events") as batch:
        batch.drop_index("ix_execution_events_ts")
