from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.orm import Session

from ..models import Customer, Sale
from .common import like
from .sales import ACTIVE, REMAINING


@dataclass
class CustomerRow:
    customer: Customer
    bought: int
    paid: int
    pending: int
    overdue: int
    last_sale: date | None
    open_sales: int


def balances_query(today: date, q: str = "", only: str = "", active: bool | None = True) -> Select:
    """Um cliente por linha, com tudo somado pelas regras de negócio (só vendas não canceladas)."""
    remaining = case((ACTIVE, REMAINING), else_=0)
    query = (
        select(
            Customer,
            func.coalesce(func.sum(case((ACTIVE, Sale.total_cents), else_=0)), 0).label("bought"),
            func.coalesce(func.sum(case((ACTIVE, Sale.paid_cents), else_=0)), 0).label("paid"),
            func.coalesce(func.sum(remaining), 0).label("pending"),
            func.coalesce(func.sum(case((and_(Sale.due_date.is_not(None), Sale.due_date < today), remaining), else_=0)), 0).label("overdue"),
            func.max(case((ACTIVE, Sale.sale_date))).label("last_sale"),
            func.coalesce(func.sum(case((and_(ACTIVE, Sale.paid_cents < Sale.total_cents), 1), else_=0)), 0).label("open_sales"),
        )
        .outerjoin(Sale, Sale.customer_id == Customer.id)
        .group_by(Customer.id)
    )
    if active is not None:
        query = query.where(Customer.active == active)
    if q.strip():
        term = like(q.strip())
        query = query.where(Customer.name.ilike(term, escape="\\") | Customer.phone.ilike(term, escape="\\")
                            | Customer.document.ilike(term, escape="\\"))
    if only == "devendo":
        query = query.having(func.sum(remaining) > 0)
    elif only == "vencidos":
        query = query.having(func.sum(case((and_(Sale.due_date.is_not(None), Sale.due_date < today), remaining), else_=0)) > 0)
    return query


def to_row(r) -> CustomerRow:
    # int(): no Postgres, SUM de bigint volta como Decimal
    return CustomerRow(r[0], int(r.bought), int(r.paid), int(r.pending), int(r.overdue), r.last_sale, int(r.open_sales))


def summary(session: Session, customer_id: int, today: date) -> CustomerRow:
    row = session.execute(balances_query(today, active=None).where(Customer.id == customer_id)).one()
    return to_row(row)
