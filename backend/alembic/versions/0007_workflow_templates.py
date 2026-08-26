"""Add the searchable workflow template library.

Revision ID: 0007_workflow_templates
Revises: 0006_workflow_versions
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_workflow_templates"
down_revision = "0006_workflow_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=False),
        sa.Column("dsl_json", sa.JSON(), nullable=False),
        sa.Column("is_official", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_templates_name", "workflow_templates", ["name"])
    op.create_index("ix_workflow_templates_category", "workflow_templates", ["category"])
    op.create_index("ix_workflow_templates_is_official", "workflow_templates", ["is_official"])


def downgrade() -> None:
    op.drop_index("ix_workflow_templates_is_official", table_name="workflow_templates")
    op.drop_index("ix_workflow_templates_category", table_name="workflow_templates")
    op.drop_index("ix_workflow_templates_name", table_name="workflow_templates")
    op.drop_table("workflow_templates")
