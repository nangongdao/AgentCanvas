"""Add per-user language preference (C5-9 i18n).

Revision ID: 0040_user_language
Revises: 0039_online_source_schedule
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0040_user_language"
down_revision = "0039_online_source_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("language", sa.String(length=8), nullable=True))
        batch.create_check_constraint(
            "ck_users_language",
            "language IS NULL OR language IN ('zh', 'en')",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_language", type_="check")
        batch.drop_column("language")
