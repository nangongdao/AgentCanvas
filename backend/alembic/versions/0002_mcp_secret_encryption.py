"""Add encrypted storage for MCP environment variables and headers.

Revision ID: 0002_mcp_secrets
Revises: 0001_p0_p3
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_mcp_secrets"
down_revision = "0001_p0_p3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch:
        batch.add_column(sa.Column("env_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("headers_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch:
        batch.drop_column("headers_encrypted")
        batch.drop_column("env_encrypted")
