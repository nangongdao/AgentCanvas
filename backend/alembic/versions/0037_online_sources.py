"""Online knowledge sources bound to a knowledge base (C4-3).

``online_sources`` records URL-based sources whose fetched content is ingested
into the knowledge base like an uploaded document. Re-sync compares the
fetched body's sha256 to the stored hash and only re-embeds when it changed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0037_online_sources"
down_revision = "0036_rag_chunk_strategy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "online_sources",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "kb_id",
            sa.String(length=32),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column(
            "document_id",
            sa.String(length=32),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("max_pages", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kb_id", "url", name="uq_online_sources_kb_url"),
        sa.CheckConstraint(
            "status IN ('pending', 'syncing', 'ready', 'failed')",
            name="ck_online_sources_status",
        ),
        sa.CheckConstraint(
            "max_pages > 0 AND max_pages <= 50", name="ck_online_sources_max_pages"
        ),
        sa.CheckConstraint(
            "depth >= 0 AND depth <= 3", name="ck_online_sources_depth"
        ),
    )
    op.create_index(
        "ix_online_sources_status", "online_sources", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_online_sources_status", table_name="online_sources")
    op.drop_table("online_sources")
