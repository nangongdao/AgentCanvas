"""Tenant data-compliance primitives (C7-5).

- ``organizations.deletion_status`` drives the two-phase deletion state
  machine: ``none`` (default) → ``requested`` (frozen; members lose access,
  data retained for the grace window) → ``purging`` (sweep in progress) →
  ``purged`` (terminal marker on the audit trail; the org row itself is
  removed by the purge). Cancelling returns the org to ``none``.
- ``deletion_requested_at/by`` and ``purge_due_at`` record who froze the
  organization and when the hard delete becomes due, so the purge scheduler
  can pick up due organizations with a single indexed scan.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0046_org_deletion"
down_revision = "0045_org_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(
            sa.Column(
                "deletion_status",
                sa.String(16),
                nullable=False,
                server_default="none",
            )
        )
        batch.add_column(sa.Column("deletion_requested_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("deletion_requested_by", sa.String(200)))
        batch.add_column(sa.Column("purge_due_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_organizations_deletion_status",
            "deletion_status IN ('none', 'requested', 'purging', 'purged')",
        )

    op.create_index(
        "ix_organizations_deletion_due",
        "organizations",
        ["deletion_status", "purge_due_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_organizations_deletion_due", table_name="organizations")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_constraint("ck_organizations_deletion_status", type_=None)
        batch.drop_column("purge_due_at")
        batch.drop_column("deletion_requested_by")
        batch.drop_column("deletion_requested_at")
        batch.drop_column("deletion_status")
