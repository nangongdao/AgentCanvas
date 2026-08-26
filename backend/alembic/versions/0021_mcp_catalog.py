"""Add approved, versioned MCP catalog metadata and upgrade history.

Revision ID: 0021_mcp_catalog
Revises: 0020_provider_capabilities
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021_mcp_catalog"
down_revision = "0020_provider_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_catalog_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mcp_catalog_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("entry_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=500), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["mcp_catalog_entries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", "version", name="uq_mcp_catalog_entry_version"),
    )
    op.create_index(
        "ix_mcp_catalog_versions_entry_id", "mcp_catalog_versions", ["entry_id"]
    )
    op.create_index(
        "ix_mcp_catalog_versions_status", "mcp_catalog_versions", ["status"]
    )
    op.create_table(
        "mcp_catalog_upgrade_history",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("entry_id", sa.String(length=64), nullable=False),
        sa.Column("from_version", sa.String(length=64), nullable=True),
        sa.Column("to_version", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_key", sa.String(length=160), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["mcp_catalog_entries.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mcp_catalog_upgrade_history_entry_id",
        "mcp_catalog_upgrade_history",
        ["entry_id"],
    )
    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.add_column(sa.Column("catalog_entry_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("catalog_version_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_mcp_servers_catalog_entry_id",
            "mcp_catalog_entries",
            ["catalog_entry_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_mcp_servers_catalog_version_id",
            "mcp_catalog_versions",
            ["catalog_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_mcp_servers_catalog_entry_id", ["catalog_entry_id"])
        batch_op.create_index("ix_mcp_servers_catalog_version_id", ["catalog_version_id"])


def downgrade() -> None:
    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.drop_index("ix_mcp_servers_catalog_version_id")
        batch_op.drop_index("ix_mcp_servers_catalog_entry_id")
        batch_op.drop_constraint("fk_mcp_servers_catalog_version_id", type_="foreignkey")
        batch_op.drop_constraint("fk_mcp_servers_catalog_entry_id", type_="foreignkey")
        batch_op.drop_column("catalog_version_id")
        batch_op.drop_column("catalog_entry_id")
    op.drop_index(
        "ix_mcp_catalog_upgrade_history_entry_id", table_name="mcp_catalog_upgrade_history"
    )
    op.drop_table("mcp_catalog_upgrade_history")
    op.drop_index("ix_mcp_catalog_versions_status", table_name="mcp_catalog_versions")
    op.drop_index("ix_mcp_catalog_versions_entry_id", table_name="mcp_catalog_versions")
    op.drop_table("mcp_catalog_versions")
    op.drop_table("mcp_catalog_entries")
