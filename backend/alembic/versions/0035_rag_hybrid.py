"""Hybrid retrieval configuration for knowledge bases (C4-1).

Adds per-knowledge-base retrieval switches:

* ``retrieval_mode``: ``vector`` (default, existing behavior) or ``hybrid``
  (BM25/tsvector keyword candidates fused with vector ranking via RRF).
* ``rerank_enabled`` / ``rerank_model_id``: optional rerank stage backed by an
  OpenAI-compatible ``/rerank`` endpoint configured as a ``kind="rerank"``
  model row. Disabled by default.

On PostgreSQL a GIN expression index over ``to_tsvector('simple', text)``
accelerates keyword ranking; SQLite ranks in process, so no index is needed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035_rag_hybrid"
down_revision = "0034_chat_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode keeps SQLite viable: it cannot ALTER constraints in place.
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(
            sa.Column(
                "retrieval_mode",
                sa.String(length=16),
                nullable=False,
                server_default="vector",
            )
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_retrieval_mode",
            "retrieval_mode IN ('vector', 'hybrid')",
        )
        batch.add_column(
            sa.Column(
                "rerank_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("rerank_model_id", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_document_chunks_text_tsv "
            "ON document_chunks USING gin (to_tsvector('simple', text))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_document_chunks_text_tsv")
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_column("rerank_model_id")
        batch.drop_column("rerank_enabled")
        batch.drop_constraint("ck_knowledge_bases_retrieval_mode", type_="check")
        batch.drop_column("retrieval_mode")
