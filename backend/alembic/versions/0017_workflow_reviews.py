"""Add durable workflow comments and version reviews.

Revision ID: 0017_workflow_reviews
Revises: 0016_identity_hardening
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_workflow_reviews"
down_revision = "0016_identity_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_versions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_workflow_versions_id_workflow",
            ["id", "workflow_id"],
        )

    op.create_table(
        "workflow_comments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("parent_comment_id", sa.String(length=32), nullable=True),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("author_user_id", sa.String(length=32), nullable=True),
        sa.Column("author_key", sa.String(length=512), nullable=False),
        sa.Column("author_subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_key", sa.String(length=512), nullable=True),
        sa.Column("resolved_by_subject", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["version_id", "workflow_id"],
            ["workflow_versions.id", "workflow_versions.workflow_id"],
            name="fk_workflow_comments_version_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_comment_id", "workflow_id", "version_id"],
            [
                "workflow_comments.id",
                "workflow_comments.workflow_id",
                "workflow_comments.version_id",
            ],
            name="fk_workflow_comments_parent_aggregate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "workflow_id",
            "version_id",
            name="uq_workflow_comments_aggregate",
        ),
    )
    op.create_index(
        "ix_workflow_comments_workflow_version_created",
        "workflow_comments",
        ["workflow_id", "version_id", "created_at"],
    )

    op.create_table(
        "workflow_reviews",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("version_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("requested_by_key", sa.String(length=512), nullable=False),
        sa.Column("requested_by_subject", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_summary", sa.Text(), nullable=False),
        sa.Column("decided_by_user_id", sa.String(length=32), nullable=True),
        sa.Column("decided_by_key", sa.String(length=512), nullable=True),
        sa.Column("decided_by_subject", sa.String(length=255), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["version_id", "workflow_id"],
            ["workflow_versions.id", "workflow_versions.workflow_id"],
            name="fk_workflow_reviews_version_workflow",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", name="uq_workflow_reviews_version"),
    )
    op.create_index(
        "ix_workflow_reviews_workflow_created",
        "workflow_reviews",
        ["workflow_id", "created_at"],
    )
    op.create_index("ix_workflow_reviews_status", "workflow_reviews", ["status"])


def downgrade() -> None:
    op.drop_index("ix_workflow_reviews_status", table_name="workflow_reviews")
    op.drop_index("ix_workflow_reviews_workflow_created", table_name="workflow_reviews")
    op.drop_table("workflow_reviews")
    op.drop_index(
        "ix_workflow_comments_workflow_version_created",
        table_name="workflow_comments",
    )
    op.drop_table("workflow_comments")
    with op.batch_alter_table("workflow_versions") as batch_op:
        batch_op.drop_constraint("uq_workflow_versions_id_workflow", type_="unique")
