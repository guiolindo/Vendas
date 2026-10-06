"""Consultas de vendas e do que há para receber.

Os critérios de status aqui em SQL espelham `domain.status.sale_status`.
Há um teste que garante que os dois sempre concordam.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..domain.status import Status, upcoming_limit
from ..models import Customer, Payment, Sale
from .common import like

ACTIVE = Sale.cancelled_at.is_(None)
REMAINING = Sale.total_cents - Sale.paid_cents


def status_clause(status: str, today: date):
    s = Status(status)
    if s is Status.CANCELADA:
        return Sale.cancelled_at.is_not(None)
    unpaid = and_(ACTIVE, Sale.paid_cents < Sale.total_cents)
    overdue = and_(Sale.due_date.is_not(None), Sale.due_date < today)
    if s is Status.PAGO:
        return and_(ACTIVE, Sale.paid_cents >= Sale.total_cents)
    if s is Status.VENCIDO:
        return and_(unpaid, overdue)
    not_overdue = or_(Sale.due_date.is_(None), Sale.due_date >= today)
    if s is Status.PARCIAL:
        return and_(unpaid, not_overdue, Sale.paid_cents > 0)
    return and_(unpaid, not_overdue, Sale.paid_cents == 0)  # pendente


def open_clause():
    return and_(ACTIVE, Sale.paid_cents < Sale.total_cents)


def bucket_clause(bucket: str, today: date):
    """Abas da tela de Cobranças."""
    if bucket == "vencidos":
        return status_clause("vencido", today)
    if bucket == "hoje":
        return and_(open_clause(), Sale.due_date == today)
    if bucket == "proximos":
        return and_(open_clause(), Sale.due_date > today, Sale.due_date <= upcoming_limit(today))
    if bucket == "parciais":  # abertas com alguma parte já paga (inclui as vencidas)
        return and_(open_clause(), Sale.paid_cents > 0)
    if bucket == "pagos":
        return status_clause("pago", today)
    return open_clause()  # "abertos"


@dataclass
class SaleFilters:
    q: str = ""
    status: str = ""
    bucket: str = ""
    customer_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    min_cents: int | None = None
    max_cents: int | None = None
    amount_field: str = "total"  # "total" (lista de vendas) ou "remaining" (cobranças)


def sales_query(f: SaleFilters, today: date, order: str = "recent") -> Select:
    query = select(Sale).options(selectinload(Sale.customer))
    if f.q:
        q = f.q.strip().lstrip("#")
        conds = [Sale.customer.has(Customer.name.ilike(like(f.q.strip()), escape="\\"))]
        if q.isdigit():
            conds.append(Sale.id == int(q))
        query = query.where(or_(*conds))
    if f.status:
        query = query.where(status_clause(f.status, today))
    if f.bucket:
        query = query.where(bucket_clause(f.bucket, today))
    if f.customer_id:
        query = query.where(Sale.customer_id == f.customer_id)
    if f.date_from:
        query = query.where(Sale.sale_date >= f.date_from)
    if f.date_to:
        query = query.where(Sale.sale_date <= f.date_to)
    if f.due_from:
        query = query.where(Sale.due_date >= f.due_from)
    if f.due_to:
        query = query.where(Sale.due_date <= f.due_to)
    amount = REMAINING if f.amount_field == "remaining" else Sale.total_cents
    if f.min_cents is not None:
        query = query.where(amount >= f.min_cents)
    if f.max_cents is not None:
        query = query.where(amount <= f.max_cents)
    if order == "due":
        query = query.order_by(Sale.due_date.is_(None), Sale.due_date, Sale.id)
    else:
        query = query.order_by(Sale.sale_date.desc(), Sale.id.desc())
    return query


def bucket_summary(session: Session, today: date) -> dict[str, dict[str, int]]:
    """Quantidade e valor a receber de cada aba de Cobranças."""
    out = {}
    for bucket in ("abertos", "vencidos", "hoje", "proximos", "parciais", "pagos"):
        value = Sale.total_cents if bucket == "pagos" else REMAINING
        count, cents = session.execute(
            select(func.count(), func.coalesce(func.sum(value), 0)).where(bucket_clause(bucket, today))
        ).one()
        out[bucket] = {"count": int(count), "cents": int(cents)}
    return out


def recent_payments(session: Session, limit: int = 8) -> list[Payment]:
    return list(session.scalars(
        select(Payment).where(Payment.voided_at.is_(None))
        .options(selectinload(Payment.sale).selectinload(Sale.customer))
        .order_by(Payment.paid_at.desc(), Payment.id.desc()).limit(limit)
    ))
