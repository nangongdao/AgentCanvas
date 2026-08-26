"""Chunking strategy configuration for knowledge bases (C4-2).

Adds two columns to ``knowledge_bases``:

* ``split_strategy``: ``window`` | ``recursive`` | ``heading``. Selects the
  chunk splitter used at ingestion time.
* ``parent_chunk``: when true under the ``heading`` strategy, child chunks keep
  a parent reference whose wider context can be expanded for citation display.

The migration is batch-mode so SQLite can add the new columns and check
constraint without an in-place ALTER.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036_rag_chunk_strategy"
down_revision = "0035_rag_hybrid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(
            sa.Column(
                "split_strategy",
                sa.String(length=16),
                nullable=False,
                server_default="window",
            )
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_split_strategy",
            "split_strategy IN ('window', 'recursive', 'heading')",
        )
        batch.add_column(
            sa.Column(
                "parent_chunk",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_column("parent_chunk")
        batch.drop_constraint("ck_knowledge_bases_split_strategy", type_="check")
        batch.drop_column("split_strategy")
