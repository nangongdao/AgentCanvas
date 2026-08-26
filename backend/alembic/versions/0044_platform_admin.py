"""Platform admin console primitives (C7-3).

- ``organizations.status``: ``active`` (default) or ``disabled``. A disabled
  organization is invisible to its members' authorization checks — org- and
  project-scoped routes return 403 until a platform admin re-enables it,
  while global admins keep full access. Public app runtimes and tenant
  deletion remain governed by their own flows (C3/C7-5).
- ``platform_announcements``: durable banner messages a platform admin
  publishes to every authenticated user. One row per announcement;
  ``is_active`` toggles visibility without deleting history.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0044_platform_admin"
down_revision = "0043_org_plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organizations") as batch:
        batch.add_column(
            sa.Column("status", sa.String(16), nullable=False, server_default="active")
        )
        batch.create_check_constraint(
            "ck_organizations_status",
            "status IN ('active', 'disabled')",
        )

    op.create_table(
        "platform_announcements",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "level",
            sa.String(16),
            nullable=False,
            server_default="info",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "level IN ('info', 'warning', 'critical')",
            name="ck_platform_announcements_level",
        ),
    )
    op.create_index(
        "ix_platform_announcements_active_created",
        "platform_announcements",
        ["is_active", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_announcements_active_created",
        table_name="platform_announcements",
    )
    op.drop_table("platform_announcements")
    with op.batch_alter_table("organizations") as batch:
        batch.drop_constraint("ck_organizations_status", type_=None)
        batch.drop_column("status")
