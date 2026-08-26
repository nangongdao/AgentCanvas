"""Add multi-instance-safe document chunk vector table.

Revision ID: 0024_document_chunks
Revises: 0023_event_relay
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from pgvector.sqlalchemy import VECTOR

from alembic import op

revision = "0024_document_chunks"
down_revision = "0023_event_relay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    postgresql = op.get_bind().dialect.name == "postgresql"
    if postgresql:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("kb_id", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR() if postgresql else sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_index",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index_nonnegative",
        ),
        sa.CheckConstraint(
            "dimensions > 0 AND dimensions <= 16000",
            name="ck_document_chunks_dimensions",
        ),
    )
    op.create_index("ix_document_chunks_kb_id", "document_chunks", ["kb_id"])
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index(
        "ix_document_chunks_kb_document",
        "document_chunks",
        ["kb_id", "document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_kb_document", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_kb_id", table_name="document_chunks")
    op.drop_table("document_chunks")
