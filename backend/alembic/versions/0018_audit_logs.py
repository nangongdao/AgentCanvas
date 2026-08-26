"""Add append-only administrative audit history.

Revision ID: 0018_audit_logs
Revises: 0017_workflow_reviews
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_audit_logs"
down_revision = "0017_workflow_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("organization_id", sa.String(length=32), nullable=True),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("actor_user_id", sa.String(length=32), nullable=True),
        sa.Column("actor_key", sa.String(length=512), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("resource_name", sa.String(length=255), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_audit_logs_created_id", ["created_at", "id"]),
        ("ix_audit_logs_organization_created", ["organization_id", "created_at"]),
        ("ix_audit_logs_project_created", ["project_id", "created_at"]),
        ("ix_audit_logs_actor_created", ["actor_key", "created_at"]),
        ("ix_audit_logs_action_created", ["action", "created_at"]),
        ("ix_audit_logs_resource_created", ["resource_type", "created_at"]),
    ):
        op.create_index(name, "audit_logs", columns)


def downgrade() -> None:
    op.drop_table("audit_logs")
