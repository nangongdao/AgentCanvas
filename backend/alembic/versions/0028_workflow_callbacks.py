"""Add outbound workflow callback configuration and durable deliveries."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_workflow_callbacks"
down_revision = "0027_workflow_api_publications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_callbacks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("event_types_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", name="uq_workflow_callbacks_workflow"),
    )
    op.create_index("ix_workflow_callbacks_workflow_id", "workflow_callbacks", ["workflow_id"])
    op.create_index("ix_workflow_callbacks_project_id", "workflow_callbacks", ["project_id"])
    op.create_index("ix_workflow_callbacks_status", "workflow_callbacks", ["status"])
    op.create_table(
        "workflow_callback_deliveries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("callback_id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["callback_id"], ["workflow_callbacks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "callback_id", "source_type", "source_id", name="uq_callback_delivery_source"
        ),
    )
    op.create_index("ix_workflow_callback_deliveries_callback_id", "workflow_callback_deliveries", ["callback_id"])
    op.create_index("ix_workflow_callback_deliveries_workflow_id", "workflow_callback_deliveries", ["workflow_id"])
    op.create_index("ix_workflow_callback_deliveries_project_id", "workflow_callback_deliveries", ["project_id"])
    op.create_index("ix_workflow_callback_deliveries_status", "workflow_callback_deliveries", ["status"])
    op.create_index("ix_workflow_callback_deliveries_next_attempt_at", "workflow_callback_deliveries", ["next_attempt_at"])
    op.create_index("ix_callback_deliveries_due", "workflow_callback_deliveries", ["status", "next_attempt_at"])
    op.create_table(
        "workflow_callback_cursors",
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("last_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_source_id", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source_type"),
    )


def downgrade() -> None:
    op.drop_table("workflow_callback_cursors")
    op.drop_index("ix_callback_deliveries_due", table_name="workflow_callback_deliveries")
    op.drop_index("ix_workflow_callback_deliveries_next_attempt_at", table_name="workflow_callback_deliveries")
    op.drop_index("ix_workflow_callback_deliveries_status", table_name="workflow_callback_deliveries")
    op.drop_index("ix_workflow_callback_deliveries_project_id", table_name="workflow_callback_deliveries")
    op.drop_index("ix_workflow_callback_deliveries_workflow_id", table_name="workflow_callback_deliveries")
    op.drop_index("ix_workflow_callback_deliveries_callback_id", table_name="workflow_callback_deliveries")
    op.drop_table("workflow_callback_deliveries")
    op.drop_index("ix_workflow_callbacks_status", table_name="workflow_callbacks")
    op.drop_index("ix_workflow_callbacks_project_id", table_name="workflow_callbacks")
    op.drop_index("ix_workflow_callbacks_workflow_id", table_name="workflow_callbacks")
    op.drop_table("workflow_callbacks")
