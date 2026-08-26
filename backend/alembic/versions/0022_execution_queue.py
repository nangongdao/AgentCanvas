"""Add durable execution queue and fenced worker leases.

Revision ID: 0022_execution_queue
Revises: 0021_mcp_catalog
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_execution_queue"
down_revision = "0021_mcp_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_queue_items",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("execution_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=120), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'retry_wait', 'dead_letter', 'done')",
            name="ck_execution_queue_status",
        ),
        sa.CheckConstraint(
            "kind IN ('start', 'resume', 'rerun')",
            name="ck_execution_queue_kind",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_execution_queue_attempt"),
        sa.CheckConstraint(
            "lease_generation >= 0", name="ck_execution_queue_lease_generation"
        ),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", name="uq_execution_queue_execution"),
    )
    op.create_index(
        "ix_execution_queue_items_execution_id",
        "execution_queue_items",
        ["execution_id"],
    )
    op.create_index(
        "ix_execution_queue_items_status",
        "execution_queue_items",
        ["status"],
    )
    op.create_index(
        "ix_execution_queue_items_owner_id",
        "execution_queue_items",
        ["owner_id"],
    )
    op.create_index(
        "ix_execution_queue_items_lease_expires_at",
        "execution_queue_items",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_execution_queue_items_available_at",
        "execution_queue_items",
        ["available_at"],
    )
    op.create_index(
        "ix_execution_queue_claim",
        "execution_queue_items",
        ["status", "available_at", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_queue_claim", table_name="execution_queue_items")
    op.drop_index("ix_execution_queue_items_available_at", table_name="execution_queue_items")
    op.drop_index(
        "ix_execution_queue_items_lease_expires_at", table_name="execution_queue_items"
    )
    op.drop_index("ix_execution_queue_items_owner_id", table_name="execution_queue_items")
    op.drop_index("ix_execution_queue_items_status", table_name="execution_queue_items")
    op.drop_index("ix_execution_queue_items_execution_id", table_name="execution_queue_items")
    op.drop_table("execution_queue_items")
