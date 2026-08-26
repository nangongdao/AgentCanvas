"""Evaluation gate policy on workflows (Backlog: eval gate on publish).

Adds an optional ``workflows.evaluation_policy`` JSON column holding e.g.
``{"dataset_version_id": "...", "threshold": 0.8}``. When set, publishing a
new workflow version requires the latest completed evaluation run against
that dataset version to meet the pass-rate threshold; otherwise publish is
rejected with 409. The column is nullable and default-empty so existing
workflows are unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0047_evaluation_policy"
down_revision = "0046_org_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflows") as batch:
        batch.add_column(sa.Column("evaluation_policy", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workflows") as batch:
        batch.drop_column("evaluation_policy")
