"""Números do painel inicial. Cada bloco responde a uma pergunta do dono do negócio."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from ..domain.status import upcoming_limit
from ..models import Customer, Payment, Product, Sale, SaleItem
from ..repositories import customers as customer_repo
from ..repositories import sales as sale_repo
from ..repositories.sales import ACTIVE, REMAINING, open_clause


@dataclass
class Dashboard:
    sold_today: int
    sales_today: int
    sold_month: int
    sales_month: int
    received_today: int
    received_month: int
    receivable: int
    overdue: int
    overdue_customers: int
    due_today: int
    due_soon: int
    top_products: list
    low_stock: list[Product]
    debtors: list
    overdue_sales: list[Sale]
    recent_payments: list[Payment]


def _sold(session: Session, start: date, end: date) -> tuple[int, int]:
    cents, count = session.execute(
        select(func.coalesce(func.sum(Sale.total_cents), 0), func.count())
        .where(ACTIVE, Sale.sale_date >= start, Sale.sale_date <= end)
    ).one()
    return int(cents), int(count)


def _received(session: Session, start: date, end: date) -> int:
    return session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0))
        .where(Payment.voided_at.is_(None), Payment.paid_at >= start, Payment.paid_at <= end)
    ) or 0


def _remaining_where(session: Session, *conds) -> int:
    return int(session.scalar(select(func.coalesce(func.sum(REMAINING), 0)).where(open_clause(), *conds)))


def top_products(session: Session, start: date, end: date, limit: int = 5) -> list:
    return session.execute(
        select(SaleItem.product_id, SaleItem.product_name,
               func.sum(SaleItem.quantity).label("qty"), func.sum(SaleItem.total_cents).label("revenue"))
        .join(Sale, Sale.id == SaleItem.sale_id)
        .where(ACTIVE, Sale.sale_date >= start, Sale.sale_date <= end)
        .group_by(SaleItem.product_id, SaleItem.product_name)
        .order_by(func.sum(SaleItem.quantity).desc(), SaleItem.product_name).limit(limit)
    ).all()


def build(session: Session, today: date) -> Dashboard:
    month_start = today.replace(day=1)
    sold_today, n_today = _sold(session, today, today)
    sold_month, n_month = _sold(session, month_start, today)
    overdue = _remaining_where(session, Sale.due_date < today)
    overdue_customers = int(session.scalar(
        select(func.count(func.distinct(Sale.customer_id))).where(open_clause(), Sale.due_date < today)
    ))
    debtors = [customer_repo.to_row(r) for r in session.execute(
        customer_repo.balances_query(today, only="devendo")
        .order_by(func.sum(REMAINING).desc()).limit(6)
    )]
    return Dashboard(
        sold_today=sold_today, sales_today=n_today, sold_month=sold_month, sales_month=n_month,
        received_today=int(_received(session, today, today)), received_month=int(_received(session, month_start, today)),
        receivable=_remaining_where(session), overdue=overdue, overdue_customers=overdue_customers,
        due_today=_remaining_where(session, Sale.due_date == today),
        due_soon=_remaining_where(session, Sale.due_date > today, Sale.due_date <= upcoming_limit(today)),
        top_products=top_products(session, month_start, today),
        low_stock=list(session.scalars(
            select(Product).where(Product.active.is_(True), Product.stock_qty <= Product.min_stock)
            .order_by(Product.stock_qty, Product.name).limit(6))),
        debtors=debtors,
        overdue_sales=list(session.scalars(
            select(Sale).options(selectinload(Sale.customer))
            .where(sale_repo.status_clause("vencido", today)).order_by(Sale.due_date, Sale.id).limit(5))),
        recent_payments=sale_repo.recent_payments(session, 8),
    )
