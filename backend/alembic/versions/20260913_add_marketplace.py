"""add marketplace tables

Revision ID: 20260913_marketplace
Revises:
Create Date: 2026-09-13 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260913_marketplace'
down_revision = '0047_evaluation_policy'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create marketplace_workflows and marketplace_reviews tables."""

    # marketplace_workflows table
    op.create_table(
        "marketplace_workflows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("author_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("icon_url", sa.String(512), nullable=True),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("downloads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rating", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("rating_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("moderator_notes", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_marketplace_workflow_id",
            ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_marketplace_author_id",
            ondelete="CASCADE"
        ),
    )

    # Create indexes for marketplace_workflows
    op.create_index(
        "ix_marketplace_workflows_category",
        "marketplace_workflows",
        ["category"]
    )
    op.create_index(
        "ix_marketplace_workflows_status",
        "marketplace_workflows",
        ["status"]
    )
    op.create_index(
        "ix_marketplace_workflows_downloads",
        "marketplace_workflows",
        ["downloads"]
    )
    op.create_index(
        "ix_marketplace_workflows_rating",
        "marketplace_workflows",
        ["rating"]
    )
    op.create_index(
        "ix_marketplace_workflows_published_at",
        "marketplace_workflows",
        ["published_at"]
    )

    # marketplace_reviews table
    op.create_table(
        "marketplace_reviews",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("marketplace_workflow_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["marketplace_workflow_id"],
            ["marketplace_workflows.id"],
            name="fk_review_marketplace_workflow_id",
            ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_review_user_id",
            ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "marketplace_workflow_id",
            "user_id",
            name="uq_one_review_per_user"
        ),
        sa.CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="ck_rating_range"
        ),
    )

    # Create index for marketplace_reviews
    op.create_index(
        "ix_marketplace_reviews_workflow_id",
        "marketplace_reviews",
        ["marketplace_workflow_id"]
    )
    op.create_index(
        "ix_marketplace_reviews_created_at",
        "marketplace_reviews",
        ["created_at"]
    )


def downgrade() -> None:
    """Drop marketplace tables."""
    op.drop_index("ix_marketplace_reviews_created_at", table_name="marketplace_reviews")
    op.drop_index(
        "ix_marketplace_reviews_workflow_id",
        table_name="marketplace_reviews"
    )
    op.drop_table("marketplace_reviews")

    op.drop_index(
        "ix_marketplace_workflows_published_at",
        table_name="marketplace_workflows"
    )
    op.drop_index("ix_marketplace_workflows_rating", table_name="marketplace_workflows")
    op.drop_index(
        "ix_marketplace_workflows_downloads",
        table_name="marketplace_workflows"
    )
    op.drop_index("ix_marketplace_workflows_status", table_name="marketplace_workflows")
    op.drop_index("ix_marketplace_workflows_category", table_name="marketplace_workflows")
    op.drop_table("marketplace_workflows")
