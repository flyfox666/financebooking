"""user book membership

Revision ID: e8f3a1c7d5b9
Revises: d6a9c2f5e8b1
Create Date: 2026-09-05 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8f3a1c7d5b9'
down_revision: Union[str, Sequence[str], None] = 'd6a9c2f5e8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """用户↔账套成员表（多租户权限地基）+ 存量回填（全局 admin 挂到所有既有账套）。"""
    op.create_table(
        'user_book',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.ForeignKeyConstraint(['book_id'], ['book.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'book_id', name='uq_user_book'),
    )
    with op.batch_alter_table('user_book', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_book_user_id'), ['user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_book_book_id'), ['book_id'], unique=False)

    # 存量回填：全局 admin 用户挂到所有既有账套（幂等——已存在的跳过）
    conn = op.get_bind()
    admins = conn.execute(sa.text("SELECT id FROM \"user\" WHERE role = 'admin'")).fetchall()
    if admins:
        conn.execute(
            sa.text(
                "INSERT INTO user_book (user_id, book_id, role) "
                "SELECT :uid, b.id, 'admin' FROM book b "
                "WHERE NOT EXISTS (SELECT 1 FROM user_book ub WHERE ub.user_id = :uid AND ub.book_id = b.id)"
            ),
            [{"uid": row[0]} for row in admins],
        )


def downgrade() -> None:
    with op.batch_alter_table('user_book', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_book_book_id'))
        batch_op.drop_index(batch_op.f('ix_user_book_user_id'))
    op.drop_table('user_book')
