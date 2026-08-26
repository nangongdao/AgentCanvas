"""Persist parent chunk context for citation expansion (C4-5).

Revision ID: 0038_citation_context
Revises: 0037_online_sources
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0038_citation_context"
down_revision = "0037_online_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document_chunks") as batch:
        batch.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("parent_text", sa.Text(), nullable=True))

    # Parent group membership did not exist in either legacy SQL rows or
    # Chroma metadata, so it cannot be reconstructed losslessly in-place.
    # Make affected documents visibly require an explicit rebuild; retrieval
    # already excludes pending documents, and the next ingest replaces stale
    # SQL/Chroma vectors with metadata that includes the new parent fields.
    bind = op.get_bind()
    parent_kbs = "SELECT id FROM knowledge_bases WHERE parent_chunk = :enabled"
    bind.execute(
        sa.text(
            f"DELETE FROM document_chunks WHERE kb_id IN ({parent_kbs})"
        ),
        {"enabled": True},
    )
    bind.execute(
        sa.text(
            "UPDATE documents SET status = 'pending', chunk_count = 0, error = NULL "
            f"WHERE kb_id IN ({parent_kbs})"
        ),
        {"enabled": True},
    )
    # An unchanged online-source hash would otherwise skip the required
    # re-ingest and leave its linked document pending forever.
    bind.execute(
        sa.text(
            "UPDATE online_sources SET status = 'pending', content_sha256 = NULL, error = NULL "
            f"WHERE kb_id IN ({parent_kbs})"
        ),
        {"enabled": True},
    )


def downgrade() -> None:
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_column("parent_text")
        batch.drop_column("parent_id")
