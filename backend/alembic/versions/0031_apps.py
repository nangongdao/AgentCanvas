"""Add application entities binding published workflow versions (C3-1)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031_apps"
down_revision = "0030_callback_boundary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=True),
        sa.Column("published_version_id", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("icon", sa.String(length=500), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("welcome_message", sa.Text(), nullable=True),
        sa.Column("suggested_questions", sa.JSON(), nullable=True),
        sa.Column("input_form", sa.JSON(), nullable=True),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("public_token_hash", sa.String(length=64), nullable=True),
        sa.Column("token_prefix", sa.String(length=12), nullable=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["published_version_id"], ["workflow_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "slug", name="uq_apps_project_slug"),
        sa.UniqueConstraint("public_token_hash", name="uq_apps_public_token_hash"),
        sa.CheckConstraint("type IN ('chatbot', 'completion', 'api')", name="ck_apps_type"),
        sa.CheckConstraint(
            "visibility IN ('project', 'link', 'public')", name="ck_apps_visibility"
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_apps_status"),
        sa.CheckConstraint(
            "(visibility IN ('link', 'public') AND public_token_hash IS NOT NULL) "
            "OR (visibility = 'project')",
            name="ck_apps_visibility_token",
        ),
    )
    op.create_index("ix_apps_project_id", "apps", ["project_id"])
    op.create_index("ix_apps_workflow_id", "apps", ["workflow_id"])
    op.create_index("ix_apps_published_version_id", "apps", ["published_version_id"])
    op.create_index("ix_apps_type", "apps", ["type"])
    op.create_index("ix_apps_visibility", "apps", ["visibility"])
    op.create_index("ix_apps_status", "apps", ["status"])


def downgrade() -> None:
    op.drop_index("ix_apps_status", table_name="apps")
    op.drop_index("ix_apps_visibility", table_name="apps")
    op.drop_index("ix_apps_type", table_name="apps")
    op.drop_index("ix_apps_published_version_id", table_name="apps")
    op.drop_index("ix_apps_workflow_id", table_name="apps")
    op.drop_index("ix_apps_project_id", table_name="apps")
    op.drop_table("apps")
