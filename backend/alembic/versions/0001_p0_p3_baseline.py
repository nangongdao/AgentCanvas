"""Create the P0-P3 AgentCanvas schema.

Revision ID: 0001_p0_p3
Revises: None
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_p0_p3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("dsl_json", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "model_configs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("command", sa.String(length=500), nullable=True),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("env_json", sa.JSON(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("headers_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("tools_cache_json", sa.JSON(), nullable=False),
        sa.Column("tools_cached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "executions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_executions_status", "executions", ["status"])
    op.create_index("ix_executions_workflow_id", "executions", ["workflow_id"])
    op.create_table(
        "execution_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("execution_id", sa.String(length=32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "seq", name="uq_exec_seq"),
    )
    op.create_index(
        "ix_execution_events_execution_id", "execution_events", ["execution_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_execution_events_execution_id", table_name="execution_events")
    op.drop_table("execution_events")
    op.drop_index("ix_executions_workflow_id", table_name="executions")
    op.drop_index("ix_executions_status", table_name="executions")
    op.drop_table("executions")
    op.drop_table("mcp_servers")
    op.drop_table("model_configs")
    op.drop_table("workflows")
