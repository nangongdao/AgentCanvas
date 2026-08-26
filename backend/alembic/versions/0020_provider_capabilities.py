"""Add model-level Provider capability overrides.

Revision ID: 0020_provider_capabilities
Revises: 0019_project_quotas
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020_provider_capabilities"
down_revision = "0019_project_quotas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_configs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "capabilities_json",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("model_configs") as batch_op:
        batch_op.drop_column("capabilities_json")
