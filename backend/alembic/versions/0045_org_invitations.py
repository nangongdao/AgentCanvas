"""Organization membership invitations (C7-4).

An invitation is a single-use, expiring token URL: the raw token is shown
once to the inviting admin (and optionally emailed via SMTP); only its
SHA-256 hash is stored. Accepting binds the authenticated user to the
organization with the invited role and burns the invitation.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0045_org_invitations"
down_revision = "0044_platform_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_invitations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(32),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by", sa.String(200), nullable=False, default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'editor', 'admin')",
            name="ck_org_invitations_role",
        ),
    )
    op.create_index(
        "ix_org_invitations_org_pending",
        "organization_invitations",
        ["organization_id", "accepted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_org_invitations_org_pending", table_name="organization_invitations")
    op.drop_table("organization_invitations")
