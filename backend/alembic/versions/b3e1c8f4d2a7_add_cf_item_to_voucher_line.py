"""add cf_item to voucher_line

Revision ID: b3e1c8f4d2a7
Revises: 9c81d2e4a510
Create Date: 2026-09-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3e1c8f4d2a7"
down_revision: Union[str, Sequence[str], None] = "9c81d2e4a510"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("voucher_line", sa.Column("cf_item", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("voucher_line", "cf_item")
