from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .. import clock
from ..db import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint("cost_cents >= 0", name="cost_non_negative"),
        CheckConstraint("stock_qty >= 0", name="stock_non_negative"),
        CheckConstraint("min_stock >= 0", name="min_stock_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    sku: Mapped[str | None] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    price_cents: Mapped[int]
    cost_cents: Mapped[int] = mapped_column(default=0)
    # Saldo em cache. Só muda por `services.stock.apply_movement`, que também grava
    # a movimentação correspondente (soma das movimentações == stock_qty).
    stock_qty: Mapped[int] = mapped_column(default=0)
    min_stock: Mapped[int] = mapped_column(default=0)
    unit: Mapped[str] = mapped_column(String(10), default="un")
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=clock.now)

    category: Mapped[Category | None] = relationship()

    @property
    def low_stock(self) -> bool:
        return self.stock_qty <= self.min_stock

    @property
    def margin_cents(self) -> int:
        return self.price_cents - self.cost_cents


MOVEMENT_KINDS = {
    "inicial": "Estoque inicial",
    "entrada": "Entrada",
    "ajuste": "Ajuste de contagem",
    "venda": "Venda",
    "cancelamento": "Venda cancelada",
}


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (CheckConstraint("quantity <> 0", name="quantity_not_zero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int]  # com sinal: negativo = saída
    balance_after: Mapped[int]
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id", ondelete="RESTRICT"), index=True)
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=clock.now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    product: Mapped[Product] = relationship()
