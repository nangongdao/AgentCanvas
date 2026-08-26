"""Add published workflow webhook triggers.

Revision ID: 0025_webhook_triggers
Revises: 0024_document_chunks
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025_webhook_triggers"
down_revision = "0024_document_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_triggers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("published_version_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ip_allowlist", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["published_version_id"], ["workflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", name="uq_webhook_triggers_workflow"),
        sa.UniqueConstraint("token_hash", name="uq_webhook_triggers_token_hash"),
    )
    op.create_index("ix_webhook_triggers_workflow_id", "webhook_triggers", ["workflow_id"])
    op.create_index(
        "ix_webhook_triggers_published_version_id",
        "webhook_triggers",
        ["published_version_id"],
    )
    op.create_index("ix_webhook_triggers_status", "webhook_triggers", ["status"])


def downgrade() -> None:
    op.drop_index("ix_webhook_triggers_status", table_name="webhook_triggers")
    op.drop_index("ix_webhook_triggers_published_version_id", table_name="webhook_triggers")
    op.drop_index("ix_webhook_triggers_workflow_id", table_name="webhook_triggers")
    op.drop_table("webhook_triggers")
