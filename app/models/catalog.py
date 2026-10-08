from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .. import clock
from ..db import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)


class Owner(Base):
    """Pessoa que vende (o negócio é tocado por duas pessoas). Quem vendeu é escolhido em cada venda."""

    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    active: Mapped[bool] = mapped_column(default=True)


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
    # False = produto sem controle de quantidade: vende sem baixar nem exigir estoque.
    track_stock: Mapped[bool] = mapped_column(default=True, server_default=true())
    min_stock: Mapped[int] = mapped_column(default=0)
    unit: Mapped[str] = mapped_column(String(10), default="un")
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=clock.now)

    category: Mapped[Category | None] = relationship()
    sizes: Mapped[list["ProductSize"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="ProductSize.position")

    @property
    def has_sizes(self) -> bool:
        return bool(self.sizes)

    def size_row(self, size: str | None):
        return next((s for s in self.sizes if s.size == size), None)

    @property
    def low_stock(self) -> bool:
        return self.track_stock and self.stock_qty <= self.min_stock

    @property
    def margin_cents(self) -> int:
        return self.price_cents - self.cost_cents

    @property
    def margin_percent(self) -> float | None:
        """Margem sobre o preço de venda, em %. None quando não dá para calcular."""
        if self.price_cents <= 0 or self.cost_cents <= 0:
            return None
        return round(self.margin_cents * 100 / self.price_cents, 1)


class ProductSize(Base):
    """Tamanho de um produto, com o saldo de estoque DELE. O saldo do produto (`Product.stock_qty`) é a soma deles."""

    __tablename__ = "product_sizes"
    __table_args__ = (
        UniqueConstraint("product_id", "size", name="uq_product_sizes_product_size"),
        CheckConstraint("stock_qty >= 0", name="size_stock_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    size: Mapped[str] = mapped_column(String(10))
    position: Mapped[int] = mapped_column(default=0)   # P, M, G, GG nessa ordem
    stock_qty: Mapped[int] = mapped_column(default=0)

    product: Mapped["Product"] = relationship(back_populates="sizes")


MOVEMENT_KINDS = {
    "inicial": "Estoque inicial",
    "entrada": "Entrada",
    "ajuste": "Ajuste de contagem",
    "venda": "Venda",
    "cancelamento": "Venda cancelada",
    "estorno_compra": "Compra excluída",
}


class StockMovement(Base):
    __tablename__ = "stock_movements"
    __table_args__ = (CheckConstraint("quantity <> 0", name="quantity_not_zero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[int]  # com sinal: negativo = saída
    size: Mapped[str | None] = mapped_column(String(10))   # tamanho movimentado (None = produto sem tamanhos)
    balance_after: Mapped[int]                             # saldo depois: do tamanho, se houver; senão do produto
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id", ondelete="RESTRICT"), index=True)
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=clock.now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Compra de mercadoria: quanto custou cada unidade nesta entrada (None = não informado)
    unit_cost_cents: Mapped[int | None]
    cost_updated: Mapped[bool] = mapped_column(default=False)      # esta compra atualizou o custo do produto?
    cost_before_cents: Mapped[int | None]                          # custo do produto antes desta compra
    # Compra excluída: o registro fica no histórico (como o pagamento estornado), marcado
    voided_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(String(255))
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("stock_movements.id", ondelete="RESTRICT"))

    product: Mapped[Product] = relationship()

    @property
    def voided(self) -> bool:
        return self.voided_at is not None

    @property
    def total_cost_cents(self) -> int | None:
        return None if self.unit_cost_cents is None else self.quantity * self.unit_cost_cents


class CostChange(Base):
    """Histórico do custo de cada produto: o que mudou, quando, por quê e quem mudou.
    As vendas já feitas NÃO mudam: cada item da venda guarda o custo da época."""

    __tablename__ = "cost_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    old_cents: Mapped[int | None]
    new_cents: Mapped[int]
    origin: Mapped[str] = mapped_column(String(30))  # cadastro | edição | compra | compra excluída
    at: Mapped[datetime] = mapped_column(default=clock.now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
