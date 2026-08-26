"""Harden identity bootstrap and cap legacy access sessions.

Revision ID: 0016_identity_hardening
Revises: 0015_refresh_oidc
Create Date: 2026-08-07
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from alembic import op

revision = "0016_identity_hardening"
down_revision = "0015_refresh_oidc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_bootstrap",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claimed_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    connection = op.get_bind()
    users = sa.table(
        "users",
        sa.column("id", sa.String(length=32)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    bootstrap = sa.table(
        "identity_bootstrap",
        sa.column("id", sa.Integer()),
        sa.column("claimed_by_user_id", sa.String(length=32)),
        sa.column("claimed_at", sa.DateTime(timezone=True)),
    )
    first_user = connection.execute(
        sa.select(users.c.id, users.c.created_at)
        .order_by(users.c.created_at.asc(), users.c.id.asc())
        .limit(1)
    ).first()
    if first_user is not None:
        connection.execute(
            sa.insert(bootstrap).values(
                id=1,
                claimed_by_user_id=first_user.id,
                claimed_at=first_user.created_at,
            )
        )

    sessions = sa.table(
        "sessions",
        sa.column("expires_at", sa.DateTime(timezone=True)),
    )
    access_cutoff = datetime.now(UTC) + timedelta(minutes=15)
    connection.execute(
        sa.update(sessions)
        .where(sessions.c.expires_at > access_cutoff)
        .values(expires_at=access_cutoff)
    )


def downgrade() -> None:
    op.drop_table("identity_bootstrap")
