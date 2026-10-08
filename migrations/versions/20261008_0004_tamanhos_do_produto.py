"""tamanhos do produto

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 12:57:57.534817
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('product_sizes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.Integer(), nullable=False),
    sa.Column('size', sa.String(length=10), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('stock_qty', sa.Integer(), nullable=False),
    sa.CheckConstraint('stock_qty >= 0', name=op.f('ck_product_sizes_size_stock_non_negative')),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], name=op.f('fk_product_sizes_product_id_products'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_product_sizes')),
    sa.UniqueConstraint('product_id', 'size', name='uq_product_sizes_product_size')
    )
    with op.batch_alter_table('product_sizes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_product_sizes_product_id'), ['product_id'], unique=False)

    with op.batch_alter_table('sale_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('size', sa.String(length=10), nullable=True))

    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('size', sa.String(length=10), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.drop_column('size')

    with op.batch_alter_table('sale_items', schema=None) as batch_op:
        batch_op.drop_column('size')

    with op.batch_alter_table('product_sizes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_product_sizes_product_id'))

    op.drop_table('product_sizes')
