"""Duas (ou mais) pessoas dividem o negócio; cada produto é de uma delas.

Como o dinheiro é dividido:
  * Uma venda pode misturar produtos de pessoas diferentes. O TOTAL da venda (já com desconto)
    é repartido entre as pessoas na proporção do valor dos itens de cada uma.
  * O que foi PAGO de uma venda é repartido na mesma proporção.
Tudo em centavos inteiros, com arredondamento para baixo: nunca dá valor negativo, e quando a
venda está quitada cada pessoa recebe exatamente a sua parte. No meio do caminho (venda só
parcialmente paga) a soma das pessoas pode ficar até alguns centavos abaixo do total real.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import atomic
from ..domain.text import limited
from ..domain.status import Status, sale_status, upcoming_limit
from ..errors import BusinessError, NotFound
from ..models import Owner, Payment, Product, Sale, SaleItem


# ── cadastro das pessoas ─────────────────────────────────────────────────────
def create_owner(session: Session, name: str) -> Owner:
    name = (name or "").strip()
    if not name:
        raise BusinessError("Informe o nome da pessoa.", field="name")
    limited(name, 80, "Nome", "name")
    with atomic(session):
        if session.scalar(select(Owner.id).where(func.lower(Owner.name) == name.lower())):
            raise BusinessError(f"Já existe uma pessoa chamada “{name}”.", field="name")
        owner = Owner(name=name)
        session.add(owner)
    return owner


def rename_owner(session: Session, owner_id: int, name: str) -> Owner:
    name = (name or "").strip()
    if not name:
        raise BusinessError("Informe o nome da pessoa.", field="name")
    limited(name, 80, "Nome", "name")
    with atomic(session):
        owner = session.get(Owner, owner_id)
        if owner is None:
            raise NotFound("Pessoa não encontrada.")
        clash = session.scalar(select(Owner.id).where(func.lower(Owner.name) == name.lower(), Owner.id != owner_id))
        if clash:
            raise BusinessError(f"Já existe uma pessoa chamada “{name}”.", field="name")
        owner.name = name
    return owner


def set_owner_active(session: Session, owner_id: int, active: bool) -> Owner:
    with atomic(session):
        owner = session.get(Owner, owner_id)
        if owner is None:
            raise NotFound("Pessoa não encontrada.")
        owner.active = active
    return owner


def list_owners(session: Session, only_active: bool = False) -> list[Owner]:
    query = select(Owner).order_by(Owner.name)
    if only_active:
        query = query.where(Owner.active.is_(True))
    return list(session.scalars(query))


# ── divisão proporcional ─────────────────────────────────────────────────────
def allocate(total: int, weights: dict) -> dict:
    """Reparte `total` centavos conforme os pesos, sem perder nem criar centavos.
    O resto da divisão vai para quem tem o maior peso (empate: menor chave)."""
    wsum = sum(weights.values())
    if wsum <= 0 or total <= 0:
        return {k: 0 for k in weights}
    out = {k: total * w // wsum for k, w in weights.items()}
    leftover = total - sum(out.values())
    if leftover:
        top = sorted(weights, key=lambda k: (-weights[k], -1 if k is None else k))[0]
        out[top] += leftover
    return out


def sale_shares(sale: Sale) -> dict[int | None, int]:
    """Parte de cada pessoa no TOTAL da venda (soma exatamente total_cents)."""
    weights: dict[int | None, int] = defaultdict(int)
    for item in sale.items:
        weights[item.owner_id] += item.total_cents
    return allocate(sale.total_cents, dict(weights))


def owner_paid(shares: dict, total: int, paid: int) -> dict:
    """Quanto do que foi pago já 'pertence' a cada pessoa. Monótono: pagar mais nunca diminui."""
    if total <= 0:
        return {k: 0 for k in shares}
    paid = min(paid, total)
    return {k: paid * v // total for k, v in shares.items()}


# ── painéis ──────────────────────────────────────────────────────────────────
@dataclass
class SaleView:
    """Venda vista pela parte de uma pessoa (mesmos campos que as telas usam)."""
    id: int
    customer: object
    customer_id: int | None
    due_date: date | None
    remaining_cents: int
    status: Status
    cancelled: bool = False


@dataclass
class Acc:
    sold_today: int = 0
    sales_today: int = 0
    sold_month: int = 0
    sales_month: int = 0
    received_today: int = 0
    received_month: int = 0
    receivable: int = 0
    overdue: int = 0
    due_today: int = 0
    due_soon: int = 0
    overdue_customers: set = field(default_factory=set)
    debtors: dict = field(default_factory=dict)  # customer_id -> [customer, pendente, vencido, qtd em aberto]
    overdue_sales: list = field(default_factory=list)


def _paid_until(sale: Sale, day: date) -> int:
    return sum(p.amount_cents for p in sale.payments if p.voided_at is None and p.paid_at <= day)


def compute(session: Session, today: date) -> dict[int | None, Acc]:
    """Uma passada pelas vendas relevantes, calculando o painel de todas as pessoas de uma vez."""
    month_start = today.replace(day=1)
    before_month = month_start - timedelta(days=1)
    yesterday = today - timedelta(days=1)
    paid_in_month = select(Payment.sale_id).where(Payment.voided_at.is_(None), Payment.paid_at >= month_start)
    sales = session.scalars(
        select(Sale).where(
            Sale.cancelled_at.is_(None),
            (Sale.sale_date >= month_start) | (Sale.id.in_(paid_in_month)) | (Sale.paid_cents < Sale.total_cents),
        ).options(selectinload(Sale.items), selectinload(Sale.payments), selectinload(Sale.customer))
    ).unique().all()

    acc: dict[int | None, Acc] = defaultdict(Acc)
    for sale in sales:
        shares = sale_shares(sale)
        paid_now = owner_paid(shares, sale.total_cents, sale.paid_cents)
        cum_today = owner_paid(shares, sale.total_cents, _paid_until(sale, today))
        cum_yesterday = owner_paid(shares, sale.total_cents, _paid_until(sale, yesterday))
        cum_before_month = owner_paid(shares, sale.total_cents, _paid_until(sale, before_month))
        open_sale = sale.paid_cents < sale.total_cents
        status = sale_status(sale.total_cents, sale.paid_cents, sale.due_date, today)
        for oid, share in shares.items():
            a = acc[oid]
            if sale.sale_date == today:
                a.sold_today += share; a.sales_today += 1
            if month_start <= sale.sale_date <= today:
                a.sold_month += share; a.sales_month += 1
            a.received_today += cum_today[oid] - cum_yesterday[oid]
            a.received_month += cum_today[oid] - cum_before_month[oid]
            remaining = share - paid_now[oid]
            if open_sale and remaining > 0:
                a.receivable += remaining
                late = sale.due_date is not None and sale.due_date < today
                if late:
                    a.overdue += remaining
                    a.overdue_customers.add(sale.customer_id)
                    a.overdue_sales.append(SaleView(sale.id, sale.customer, sale.customer_id, sale.due_date,
                                                    remaining, status))
                if sale.due_date == today:
                    a.due_today += remaining
                if sale.due_date and today < sale.due_date <= upcoming_limit(today):
                    a.due_soon += remaining
                if sale.customer_id is not None:
                    d = a.debtors.setdefault(sale.customer_id, [sale.customer, 0, 0, 0])
                    d[1] += remaining
                    d[2] += remaining if late else 0
                    d[3] += 1
    return acc


def period_received(sale: Sale, shares: dict, start: date, end: date) -> dict:
    """Recebido por pessoa entre duas datas (diferença de acumulados, nunca negativa)."""
    after = owner_paid(shares, sale.total_cents, _paid_until(sale, end))
    before = owner_paid(shares, sale.total_cents, _paid_until(sale, start - timedelta(days=1)))
    return {k: after[k] - before[k] for k in shares}
