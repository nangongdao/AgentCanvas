"""Add project ownership and durable project quota accounting.

Revision ID: 0019_project_quotas
Revises: 0018_audit_logs
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019_project_quotas"
down_revision = "0018_audit_logs"
branch_labels = None
depends_on = None


def _nonnegative(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(f"{name} IS NULL OR {name} >= 0", name=f"ck_quota_{name}")


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_knowledge_bases_project_id",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_knowledge_bases_project_id", ["project_id"])

    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_mcp_servers_project_id",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_mcp_servers_project_id", ["project_id"])

    op.create_table(
        "project_quotas",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("concurrent_execution_limit", sa.BigInteger(), nullable=True),
        sa.Column("storage_bytes_limit", sa.BigInteger(), nullable=True),
        sa.Column("monthly_embedding_input_bytes_limit", sa.BigInteger(), nullable=True),
        sa.Column("monthly_model_cost_units_limit", sa.BigInteger(), nullable=True),
        sa.Column("stdio_mcp_process_limit", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _nonnegative("concurrent_execution_limit"),
        _nonnegative("storage_bytes_limit"),
        _nonnegative("monthly_embedding_input_bytes_limit"),
        _nonnegative("monthly_model_cost_units_limit"),
        _nonnegative("stdio_mcp_process_limit"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "project_quota_counters",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("concurrent_executions", sa.BigInteger(), nullable=False),
        sa.Column("storage_bytes", sa.BigInteger(), nullable=False),
        sa.Column("stdio_mcp_processes", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "concurrent_executions >= 0", name="ck_quota_counter_concurrent_executions"
        ),
        sa.CheckConstraint("storage_bytes >= 0", name="ck_quota_counter_storage_bytes"),
        sa.CheckConstraint("stdio_mcp_processes >= 0", name="ck_quota_counter_stdio_mcp_processes"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "project_quota_period_usage",
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("embedding_input_bytes", sa.BigInteger(), nullable=False),
        sa.Column("model_cost_units", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "embedding_input_bytes >= 0", name="ck_quota_usage_embedding_input_bytes"
        ),
        sa.CheckConstraint("model_cost_units >= 0", name="ck_quota_usage_model_cost_units"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "period_start"),
    )
    op.create_table(
        "project_quota_reservations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_quota_reservation_amount"),
        sa.CheckConstraint(
            "kind IN ('execution', 'document_storage', 'stdio_mcp_process')",
            name="ck_quota_reservation_kind",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "kind", "resource_id", name="uq_quota_reservation_resource"
        ),
    )
    op.create_index(
        "ix_quota_reservations_project_kind",
        "project_quota_reservations",
        ["project_id", "kind"],
    )

    now = sa.func.current_timestamp()
    project = sa.table("projects", sa.column("id", sa.String(length=32)))
    quota = sa.table(
        "project_quotas",
        sa.column("project_id", sa.String(length=32)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    counter = sa.table(
        "project_quota_counters",
        sa.column("project_id", sa.String(length=32)),
        sa.column("concurrent_executions", sa.BigInteger()),
        sa.column("storage_bytes", sa.BigInteger()),
        sa.column("stdio_mcp_processes", sa.BigInteger()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        quota.insert().from_select(
            ["project_id", "created_at", "updated_at"],
            sa.select(project.c.id, now, now),
        )
    )
    op.execute(
        counter.insert().from_select(
            [
                "project_id",
                "concurrent_executions",
                "storage_bytes",
                "stdio_mcp_processes",
                "updated_at",
            ],
            sa.select(project.c.id, sa.literal(0), sa.literal(0), sa.literal(0), now),
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quota_reservations_project_kind",
        table_name="project_quota_reservations",
    )
    op.drop_table("project_quota_reservations")
    op.drop_table("project_quota_period_usage")
    op.drop_table("project_quota_counters")
    op.drop_table("project_quotas")

    with op.batch_alter_table("mcp_servers") as batch_op:
        batch_op.drop_index("ix_mcp_servers_project_id")
        batch_op.drop_constraint("fk_mcp_servers_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")

    with op.batch_alter_table("knowledge_bases") as batch_op:
        batch_op.drop_index("ix_knowledge_bases_project_id")
        batch_op.drop_constraint("fk_knowledge_bases_project_id", type_="foreignkey")
        batch_op.drop_column("project_id")
