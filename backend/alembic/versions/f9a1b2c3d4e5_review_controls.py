"""Voucher edit attribution and single reversal constraint."""
from alembic import op
import sqlalchemy as sa

revision = 'f9a1b2c3d4e5'
down_revision = 'e8f3a1c7d5b9'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    duplicate = conn.execute(sa.text('SELECT reverses_voucher_id FROM voucher WHERE reverses_voucher_id IS NOT NULL GROUP BY reverses_voucher_id HAVING COUNT(*) > 1')).first()
    if duplicate:
        raise RuntimeError('存在重复红冲记录，请先核对；迁移不会自动删除账务数据')
    op.add_column('ai_doc', sa.Column('confirmation_json', sa.Text(), nullable=False, server_default='{}'))
    with op.batch_alter_table('llm_provider') as batch:
        batch.alter_column('api_key', existing_type=sa.String(400), type_=sa.Text())
    with op.batch_alter_table('voucher') as batch:
        batch.add_column(sa.Column('edited_by', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('editor_ids_json', sa.Text(), nullable=False, server_default='[]'))
        batch.create_foreign_key('fk_voucher_edited_by', 'user', ['edited_by'], ['id'])
        batch.create_unique_constraint('uq_reversal_original', ['reverses_voucher_id'])


def downgrade():
    op.drop_column('ai_doc', 'confirmation_json')
    with op.batch_alter_table('voucher') as batch:
        batch.drop_constraint('uq_reversal_original', type_='unique')
        batch.drop_constraint('fk_voucher_edited_by', type_='foreignkey')
        batch.drop_column('edited_by')
        batch.drop_column('editor_ids_json')
