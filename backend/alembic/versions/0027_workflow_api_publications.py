"""Add published workflow API bindings."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027_workflow_api_publications"
down_revision = "0026_workflow_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.add_column(
            sa.Column("scope", sa.String(length=32), nullable=False, server_default="management")
        )
    with op.batch_alter_table("service_accounts") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_service_accounts_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_service_accounts_project_id", "service_accounts", ["project_id"])
    op.create_table(
        "workflow_api_publications",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("published_version_id", sa.String(length=32), nullable=False),
        sa.Column("service_account_id", sa.String(length=32), nullable=False),
        sa.Column("api_token_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["published_version_id"], ["workflow_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"], ["service_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["api_token_id"], ["api_tokens.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", name="uq_workflow_api_publications_workflow"),
        sa.UniqueConstraint("api_token_id", name="uq_workflow_api_publications_token"),
    )
    op.create_index(
        "ix_workflow_api_publications_workflow_id",
        "workflow_api_publications",
        ["workflow_id"],
    )
    op.create_index(
        "ix_workflow_api_publications_published_version_id",
        "workflow_api_publications",
        ["published_version_id"],
    )
    op.create_index(
        "ix_workflow_api_publications_service_account_id",
        "workflow_api_publications",
        ["service_account_id"],
    )
    op.create_index(
        "ix_workflow_api_publications_api_token_id",
        "workflow_api_publications",
        ["api_token_id"],
    )
    op.create_index(
        "ix_workflow_api_publications_status", "workflow_api_publications", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_api_publications_status", table_name="workflow_api_publications")
    op.drop_index("ix_workflow_api_publications_api_token_id", table_name="workflow_api_publications")
    op.drop_index("ix_workflow_api_publications_service_account_id", table_name="workflow_api_publications")
    op.drop_index("ix_workflow_api_publications_published_version_id", table_name="workflow_api_publications")
    op.drop_index("ix_workflow_api_publications_workflow_id", table_name="workflow_api_publications")
    op.drop_table("workflow_api_publications")
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.drop_column("scope")
    op.drop_index("ix_service_accounts_project_id", table_name="service_accounts")
    with op.batch_alter_table("service_accounts") as batch_op:
        batch_op.drop_constraint("fk_service_accounts_project_id_projects", type_="foreignkey")
        batch_op.drop_column("project_id")
