"""merge marketplace and evaluation_policy heads

Revision ID: 2a2231226aa0
Revises: 0047_evaluation_policy, 20260913_marketplace
Create Date: 2026-09-13 15:22:06.534529
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2a2231226aa0'
down_revision: Union[str, None] = ('0047_evaluation_policy', '20260913_marketplace')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
