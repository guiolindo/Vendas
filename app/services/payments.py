"""Pagamentos: sempre acrescentam histórico; nunca sobrescrevem."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..db import atomic
from ..domain.money import format_brl
from ..domain.payment_methods import METHODS
from ..errors import BusinessError, NotFound
from ..models import Customer, Payment, Sale


def _paid_total(session: Session, sale_id: int) -> int:
    return int(session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0))
        .where(Payment.sale_id == sale_id, Payment.voided_at.is_(None))
    ))


def _check_common(amount_cents: int, method: str, paid_at: date | None) -> date:
    if amount_cents <= 0:
        raise BusinessError("O valor do pagamento precisa ser maior que zero.", field="amount")
    if method not in METHODS:
        raise BusinessError("Escolha a forma de pagamento.", field="method")
    today = clock.today()
    paid_at = paid_at or today
    if paid_at > today:
        raise BusinessError("A data do pagamento não pode ser no futuro.", field="paid_at")
    return paid_at


def _add_payment(session: Session, sale: Sale, amount: int, method: str, paid_at: date,
                 note: str | None, user_id: int | None) -> Payment:
    if paid_at < sale.sale_date:
        raise BusinessError("O pagamento não pode ser anterior à data da venda.", field="paid_at")
    payment = Payment(sale_id=sale.id, amount_cents=amount, method=method, paid_at=paid_at,
                      note=(note or "").strip() or None, created_by=user_id)
    session.add(payment)
    return payment


def register_payment(session: Session, sale_id: int, amount_cents: int, method: str,
                     paid_at: date | None = None, note: str | None = None,
                     user_id: int | None = None) -> Payment:
    paid_at = _check_common(amount_cents, method, paid_at)
    with atomic(session):
        sale = session.get(Sale, sale_id, with_for_update=True)
        if sale is None:
            raise NotFound("Venda não encontrada.")
        if sale.cancelled:
            raise BusinessError("Esta venda foi cancelada e não recebe pagamentos.")
        remaining = sale.total_cents - _paid_total(session, sale_id)
        if remaining <= 0:
            raise BusinessError("Esta venda já está paga.")
        if amount_cents > remaining:
            raise BusinessError(
                f"O valor do pagamento não pode ser maior que o valor restante ({format_brl(remaining)}).",
                field="amount",
            )
        payment = _add_payment(session, sale, amount_cents, method, paid_at, note, user_id)
        session.flush()
        session.expire(sale)
    return payment


def register_customer_payment(session: Session, customer_id: int, amount_cents: int, method: str,
                              paid_at: date | None = None, note: str | None = None,
                              user_id: int | None = None) -> list[Payment]:
    """Recebe um valor do cliente e o distribui nas vendas em aberto, da mais antiga
    (menor vencimento) para a mais nova. Gera um pagamento por venda atingida."""
    paid_at = _check_common(amount_cents, method, paid_at)
    with atomic(session):
        customer = session.get(Customer, customer_id)
        if customer is None:
            raise NotFound("Cliente não encontrado.")
        sales = session.scalars(
            select(Sale)
            .where(Sale.customer_id == customer_id, Sale.cancelled_at.is_(None))
            .order_by(Sale.due_date.is_(None), Sale.due_date, Sale.sale_date, Sale.id)
            .with_for_update()
        ).all()
        open_sales = []
        for sale in sales:
            remaining = sale.total_cents - _paid_total(session, sale.id)
            if remaining > 0:
                open_sales.append((sale, remaining))
        owed = sum(r for _, r in open_sales)
        if owed == 0:
            raise BusinessError("Este cliente não tem nada em aberto.")
        if amount_cents > owed:
            raise BusinessError(
                f"O valor não pode ser maior que o total em aberto do cliente ({format_brl(owed)}).",
                field="amount",
            )
        left = amount_cents
        payments = []
        for sale, remaining in open_sales:
            if left <= 0:
                break
            part = min(left, remaining)
            payments.append(_add_payment(session, sale, part, method, paid_at, note, user_id))
            left -= part
        session.flush()
        session.expire_all()
    return payments


def void_payment(session: Session, payment_id: int, reason: str | None = None,
                 user_id: int | None = None) -> Payment:
    """Estorna um pagamento lançado por engano. O registro continua no histórico."""
    with atomic(session):
        payment = session.get(Payment, payment_id, with_for_update=True)
        if payment is None:
            raise NotFound("Pagamento não encontrado.")
        if payment.voided:
            raise BusinessError("Este pagamento já foi estornado.")
        if payment.sale.cancelled:
            raise BusinessError("A venda foi cancelada; seus pagamentos já estão estornados.")
        payment.voided_at = clock.now()
        payment.void_reason = (reason or "").strip()[:255] or "Lançado por engano"
        session.flush()
        session.expire_all()
    return payment
