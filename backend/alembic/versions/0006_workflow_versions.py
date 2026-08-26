"""Add immutable workflow versions and bind executions to snapshots.

Revision ID: 0006_workflow_versions
Revises: 0005_ingest_jobs
Create Date: 2026-08-05
"""

from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0006_workflow_versions"
down_revision = "0005_ingest_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("dsl_json", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "number", name="uq_workflow_version_number"),
    )
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])
    op.create_index("ix_workflow_versions_status", "workflow_versions", ["status"])

    connection = op.get_bind()
    workflows = connection.execute(
        sa.text("SELECT id, name, description, dsl_json, version, created_at FROM workflows")
    ).mappings()
    version_ids: dict[str, str] = {}
    for workflow in workflows:
        version_id = uuid4().hex
        workflow_id = str(workflow["id"])
        version_ids[workflow_id] = version_id
        connection.execute(
            sa.text(
                "INSERT INTO workflow_versions "
                "(id, workflow_id, number, status, name, description, dsl_json, "
                "change_summary, created_at, published_at, archived_at) "
                "VALUES (:id, :workflow_id, :number, 'draft', :name, :description, "
                ":dsl_json, :change_summary, :created_at, NULL, NULL)"
            ),
            {
                "id": version_id,
                "workflow_id": workflow_id,
                "number": workflow["version"],
                "name": workflow["name"],
                "description": workflow["description"] or "",
                "dsl_json": workflow["dsl_json"],
                "change_summary": "Backfilled during workflow version migration",
                "created_at": workflow["created_at"],
            },
        )

    with op.batch_alter_table("executions") as batch_op:
        batch_op.add_column(sa.Column("workflow_version_id", sa.String(length=32), nullable=True))

    for workflow_id, version_id in version_ids.items():
        connection.execute(
            sa.text(
                "UPDATE executions SET workflow_version_id = :version_id "
                "WHERE workflow_id = :workflow_id"
            ),
            {"version_id": version_id, "workflow_id": workflow_id},
        )

    with op.batch_alter_table("executions") as batch_op:
        batch_op.alter_column(
            "workflow_version_id", existing_type=sa.String(length=32), nullable=False
        )
        batch_op.create_foreign_key(
            "fk_executions_workflow_version_id",
            "workflow_versions",
            ["workflow_version_id"],
            ["id"],
        )
        batch_op.create_index("ix_executions_workflow_version_id", ["workflow_version_id"])


def downgrade() -> None:
    with op.batch_alter_table("executions") as batch_op:
        batch_op.drop_index("ix_executions_workflow_version_id")
        batch_op.drop_constraint("fk_executions_workflow_version_id", type_="foreignkey")
        batch_op.drop_column("workflow_version_id")
    op.drop_index("ix_workflow_versions_status", table_name="workflow_versions")
    op.drop_index("ix_workflow_versions_workflow_id", table_name="workflow_versions")
    op.drop_table("workflow_versions")
