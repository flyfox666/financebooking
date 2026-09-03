"""ai style setting add style_prompt

Revision ID: d6a9c2f5e8b1
Revises: c4f7b2d9e6a1
Create Date: 2026-09-03 22:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6a9c2f5e8b1'
down_revision: Union[str, Sequence[str], None] = 'c4f7b2d9e6a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """AI 偏好加自定义模板列：用户最终编辑的注入内容，非空时优先于标签自动合成。"""
    with op.batch_alter_table('ai_style_setting', schema=None) as batch_op:
        batch_op.add_column(sa.Column('style_prompt', sa.Text(), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('ai_style_setting', schema=None) as batch_op:
        batch_op.drop_column('style_prompt')
