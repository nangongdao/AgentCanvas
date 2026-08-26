"""Add embed/theme configuration columns to apps (C3-3)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033_apps_embed_config"
down_revision = "0032_chat_session_app"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("apps", schema=None) as batch_op:
        # Optional brand theme color (#rrggbb) applied on the runtime page.
        batch_op.add_column(sa.Column("theme_color", sa.String(length=9), nullable=True))
        # Allow-list of origins permitted to embed the app via iframe/bubble.
        # An empty/NULL list means embedding is disabled (frame-ancestors 'none').
        batch_op.add_column(
            sa.Column("embed_allowed_origins", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("apps", schema=None) as batch_op:
        batch_op.drop_column("embed_allowed_origins")
        batch_op.drop_column("theme_color")
