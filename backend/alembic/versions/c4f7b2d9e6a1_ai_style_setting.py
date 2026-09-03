"""ai style setting

Revision ID: c4f7b2d9e6a1
Revises: b3e1c8f4d2a7
Create Date: 2026-09-03 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f7b2d9e6a1'
down_revision: Union[str, Sequence[str], None] = 'b3e1c8f4d2a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """账套级 AI 记账偏好（行业标签多选 + 业务描述）。"""
    op.create_table('ai_style_setting',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('book_id', sa.Integer(), nullable=False),
    sa.Column('tags_json', sa.Text(), nullable=False),
    sa.Column('business_desc', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['book_id'], ['book.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('ai_style_setting', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_style_setting_book_id'), ['book_id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('ai_style_setting', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_style_setting_book_id'))

    op.drop_table('ai_style_setting')
