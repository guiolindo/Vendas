from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (CheckConstraint, Date, ForeignKey, Index, String, Text,
                        func, select)
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from .. import clock
from ..db import Base
from ..domain.status import Status, sale_status
from .catalog import Owner, Product
from .customer import Customer


class Sale(Base):
    """Uma venda. O número exibido ("Venda #1048") é o próprio id."""

    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("subtotal_cents > 0", name="subtotal_positive"),
        CheckConstraint("discount_cents >= 0 AND discount_cents <= subtotal_cents", name="discount_range"),
        CheckConstraint("total_cents = subtotal_cents - discount_cents", name="total_consistent"),
        CheckConstraint("total_cents > 0", name="total_positive"),
        Index("ix_sales_due_date", "due_date"),
        Index("ix_sales_sale_date", "sale_date"),
        {"sqlite_autoincrement": True},  # números nunca são reaproveitados
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_token: Mapped[str | None] = mapped_column(String(64), unique=True)  # evita venda duplicada por duplo clique
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    # Quem vendeu (escolhido na hora da venda). Vazio só quando o negócio ainda não cadastrou pessoas.
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"), index=True)
    sale_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    subtotal_cents: Mapped[int]
    discount_cents: Mapped[int] = mapped_column(default=0)
    total_cents: Mapped[int]
    notes: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None]
    cancel_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=clock.now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    customer: Mapped[Customer | None] = relationship()
    seller: Mapped[Owner | None] = relationship(foreign_keys=[seller_id])
    items: Mapped[list[SaleItem]] = relationship(
        back_populates="sale", cascade="all, delete-orphan", order_by="SaleItem.id"
    )
    payments: Mapped[list[Payment]] = relationship(
        back_populates="sale", order_by="Payment.paid_at, Payment.id"
    )

    # paid_cents é definido abaixo (soma dos pagamentos não estornados).

    @property
    def cancelled(self) -> bool:
        return self.cancelled_at is not None

    @property
    def remaining_cents(self) -> int:
        return 0 if self.cancelled else max(self.total_cents - self.paid_cents, 0)

    @property
    def status(self) -> Status:
        return sale_status(self.total_cents, self.paid_cents, self.due_date, clock.today(), self.cancelled)

    @property
    def number(self) -> int:
        return self.id


class SaleItem(Base):
    """Item vendido. Nome, preço e custo são uma cópia do momento da venda."""

    __tablename__ = "sale_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="price_non_negative"),
        CheckConstraint("total_cents = quantity * unit_price_cents", name="total_consistent"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    # quem vendeu: cópia de Sale.seller_id (todos os itens de uma venda são da mesma pessoa)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("owners.id", ondelete="RESTRICT"), index=True)
    product_name: Mapped[str] = mapped_column(String(160))
    product_code: Mapped[str] = mapped_column(String(40))
    unit: Mapped[str] = mapped_column(String(10), default="un")
    quantity: Mapped[int]
    unit_price_cents: Mapped[int]
    unit_cost_cents: Mapped[int] = mapped_column(default=0)
    total_cents: Mapped[int]

    sale: Mapped[Sale] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()
    owner: Mapped[Owner | None] = relationship(foreign_keys=[owner_id])


class Payment(Base):
    """Pagamento recebido. Nunca é apagado nem alterado: corrigir = estornar."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        Index("ix_payments_paid_at", "paid_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="RESTRICT"), index=True)
    amount_cents: Mapped[int]
    method: Mapped[str] = mapped_column(String(20))
    paid_at: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(255))
    client_token: Mapped[str | None] = mapped_column(String(80), unique=True)  # evita pagamento duplicado por duplo clique
    created_at: Mapped[datetime] = mapped_column(default=clock.now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    voided_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(String(255))

    sale: Mapped[Sale] = relationship(back_populates="payments")

    @property
    def voided(self) -> bool:
        return self.voided_at is not None


# Soma dos pagamentos válidos: o "pago" nunca é um campo que alguém precise manter.
Sale.paid_cents = column_property(
    select(func.coalesce(func.sum(Payment.amount_cents), 0))
    .where(Payment.sale_id == Sale.id, Payment.voided_at.is_(None))
    .correlate_except(Payment)
    .scalar_subquery(),
    deferred=False,
)
