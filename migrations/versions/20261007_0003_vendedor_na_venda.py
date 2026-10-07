"""vendedor escolhido na venda; produto deixa de ter dono

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sales") as batch:
        batch.add_column(sa.Column("seller_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(op.f("fk_sales_seller_id_owners"), "owners", ["seller_id"], ["id"], ondelete="RESTRICT")
        batch.create_index(op.f("ix_sales_seller_id"), ["seller_id"], unique=False)
    # vendas antigas: quem vendeu = a pessoa do primeiro item
    op.execute("""
        UPDATE sales SET seller_id = (
            SELECT MIN(owner_id) FROM sale_items WHERE sale_items.sale_id = sales.id AND sale_items.owner_id IS NOT NULL)
    """)
    with op.batch_alter_table("products") as batch:
        batch.drop_index(op.f("ix_products_owner_id"))
        batch.drop_constraint(op.f("fk_products_owner_id_owners"), type_="foreignkey")
        batch.drop_column("owner_id")


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(op.f("fk_products_owner_id_owners"), "owners", ["owner_id"], ["id"], ondelete="RESTRICT")
        batch.create_index(op.f("ix_products_owner_id"), ["owner_id"], unique=False)
    with op.batch_alter_table("sales") as batch:
        batch.drop_index(op.f("ix_sales_seller_id"))
        batch.drop_constraint(op.f("fk_sales_seller_id_owners"), type_="foreignkey")
        batch.drop_column("seller_id")
