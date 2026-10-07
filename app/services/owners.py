"""Duas (ou mais) pessoas dividem o negócio; cada produto é de uma delas.

Como o dinheiro é dividido:
  * Uma venda pode misturar produtos de pessoas diferentes. O TOTAL da venda (já com desconto)
    é repartido entre as pessoas na proporção do valor dos itens de cada uma.
  * O que foi PAGO de uma venda é repartido na mesma proporção.
  * Cada PAGAMENTO é repartido pelo que cada pessoa ainda tem a receber daquela venda.
Tudo em centavos inteiros: a soma das pessoas é SEMPRE exatamente o total real (nenhum centavo
some ou aparece), nunca dá valor negativo e, quitada a venda, cada pessoa recebeu a sua parte.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db import atomic
from ..domain.allocation import allocate
from ..domain.text import limited
from ..domain.status import Status, sale_status, upcoming_limit
from ..errors import BusinessError, NotFound
from ..models import Owner, Payment, Product, Sale, SaleItem
from .margin import Margin, margins_by_owner


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


def delete_owner(session: Session, owner_id: int) -> str:
    """Só exclui quem nunca teve produto nem venda. Senão, desative: o histórico precisa continuar de pé."""
    with atomic(session):
        owner = session.get(Owner, owner_id)
        if owner is None:
            raise NotFound("Pessoa não encontrada.")
        products = session.scalar(select(func.count()).select_from(Product).where(Product.owner_id == owner_id))
        sold = session.scalar(select(func.count()).select_from(SaleItem).where(SaleItem.owner_id == owner_id))
        if products or sold:
            raise BusinessError(f"{owner.name} tem {products} produto(s) e {sold} item(ns) vendido(s) no histórico, então não pode ser "
                                "excluída. Desative para que ela deixe de receber produtos novos.")
        name = owner.name
        session.delete(owner)
    return name


def list_owners(session: Session, only_active: bool = False) -> list[Owner]:
    query = select(Owner).order_by(Owner.name)
    if only_active:
        query = query.where(Owner.active.is_(True))
    return list(session.scalars(query))


# ── divisão proporcional ─────────────────────────────────────────────────────
def sale_shares(sale: Sale) -> dict[int | None, int]:
    """Parte de cada pessoa no TOTAL da venda (soma exatamente total_cents).
    Calculada item a item (o desconto é rateado nos itens), para somar igual à margem."""
    from .margin import item_nets
    nets = item_nets(sale)
    shares: dict[int | None, int] = defaultdict(int)
    for item in sale.items:
        shares[item.owner_id] += nets[item.id]
    return dict(shares)


def payment_splits(sale: Sale, shares: dict) -> list[tuple[Payment, dict]]:
    """Reparte cada pagamento válido (em ordem de data) pelo que falta a cada pessoa.
    Exato, sem negativos, e o último centavo da venda fecha certo para todos."""
    received = {k: 0 for k in shares}
    out = []
    for payment in sorted((p for p in sale.payments if p.voided_at is None), key=lambda p: (p.paid_at, p.id)):
        owed = {k: shares[k] - received[k] for k in shares}
        part = allocate(min(payment.amount_cents, sum(owed.values())), owed)
        for k, v in part.items():
            received[k] += v
        out.append((payment, part))
    return out


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
    margin_month: Margin = field(default_factory=Margin)
    overdue_customers: set = field(default_factory=set)
    debtors: dict = field(default_factory=dict)  # customer_id -> [customer, pendente, vencido, qtd em aberto]
    overdue_sales: list = field(default_factory=list)


def compute(session: Session, today: date) -> dict[int | None, Acc]:
    """Uma passada pelas vendas relevantes, calculando o painel de todas as pessoas de uma vez."""
    month_start = today.replace(day=1)
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
        splits = payment_splits(sale, shares)
        sale_margins = margins_by_owner(sale) if month_start <= sale.sale_date <= today else {}
        paid_now = {k: sum(part[k] for _, part in splits) for k in shares}
        open_sale = sale.paid_cents < sale.total_cents
        status = sale_status(sale.total_cents, sale.paid_cents, sale.due_date, today)
        for oid, share in shares.items():
            a = acc[oid]
            if sale.sale_date == today:
                a.sold_today += share; a.sales_today += 1
            if month_start <= sale.sale_date <= today:
                a.sold_month += share; a.sales_month += 1
                a.margin_month.add(sale_margins[oid])
            for payment, part in splits:
                if payment.paid_at == today:
                    a.received_today += part[oid]
                if month_start <= payment.paid_at <= today:
                    a.received_month += part[oid]
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
    """Recebido por pessoa entre duas datas (soma das partes dos pagamentos desse período)."""
    got = {k: 0 for k in shares}
    for payment, part in payment_splits(sale, shares):
        if start <= payment.paid_at <= end:
            for k, v in part.items():
                got[k] += v
    return got
