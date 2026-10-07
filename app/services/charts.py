"""Dados dos gráficos. Tudo vem das mesmas regras dos painéis (divisão por pessoa, margem líquida),
então os gráficos nunca divergem dos números."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..domain.payment_methods import label as method_label
from ..models import Owner, Payment, Sale
from . import margin as margin_svc
from . import owners as owner_svc

PERIODS = {"30d": "Últimos 30 dias", "mes": "Este mês", "90d": "Últimos 90 dias", "6m": "Últimos 6 meses"}
MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
AGING = ["A vencer", "Venceu há até 7 dias", "Venceu há 8 a 30 dias", "Venceu há mais de 30 dias"]


@dataclass
class Bucket:
    start: date
    end: date
    label: str   # curto, para o eixo
    long: str    # completo, para a dica e a tabela


@dataclass
class ProductMargin:
    name: str
    owner_id: int | None
    revenue: int = 0
    cost: int = 0

    @property
    def amount(self) -> int:
        return self.revenue - self.cost

    @property
    def percent(self) -> float | None:
        return round(self.amount * 100 / self.revenue, 1) if self.revenue > 0 else None


@dataclass
class ChartData:
    period: str
    start: date
    end: date
    buckets: list[Bucket]
    owners: list[Owner]
    chosen: Owner | None
    sold_by_owner: dict = field(default_factory=dict)     # chave de pessoa -> lista por bucket
    sold: list[int] = field(default_factory=list)          # já filtrado pela pessoa escolhida (ou geral)
    received: list[int] = field(default_factory=list)
    margin_products: list[ProductMargin] = field(default_factory=list)
    uncosted_products: int = 0
    aging: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    methods: list[tuple[str, int]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(self.sold) and not any(self.received)


def _fmt(d: date) -> str:
    return d.strftime("%d/%m")


def make_buckets(period: str, today: date) -> tuple[date, date, list[Bucket]]:
    if period == "mes":
        start = today.replace(day=1)
        days = [start + timedelta(days=i) for i in range((today - start).days + 1)]
        return start, today, [Bucket(d, d, _fmt(d), d.strftime("%d/%m/%Y")) for d in days]
    if period == "90d":
        start = today - timedelta(days=89)
        out = []
        for i in range(13):
            a = start + timedelta(days=7 * i)
            b = min(a + timedelta(days=6), today)
            out.append(Bucket(a, b, _fmt(a), f"{_fmt(a)} a {_fmt(b)}"))
        return start, today, out
    if period == "6m":
        months = []
        y, m = today.year, today.month
        for _ in range(6):
            months.append((y, m)); m -= 1
            if m == 0:
                y, m = y - 1, 12
        months.reverse()
        out = []
        for y, m in months:
            a = date(y, m, 1)
            nxt = date(y + (m == 12), (m % 12) + 1, 1)
            b = min(nxt - timedelta(days=1), today)
            out.append(Bucket(a, b, f"{MONTHS[m - 1]}/{str(y)[2:]}", f"{MONTHS[m - 1]}/{y}"))
        return out[0].start, today, out
    start = today - timedelta(days=29)  # 30d
    return start, today, [Bucket(start + timedelta(days=i), start + timedelta(days=i),
                                 _fmt(start + timedelta(days=i)), (start + timedelta(days=i)).strftime("%d/%m/%Y")) for i in range(30)]


def aging_index(due: date | None, today: date) -> int:
    if due is None or due >= today:
        return 0
    late = (today - due).days
    return 1 if late <= 7 else 2 if late <= 30 else 3


def build(session: Session, today: date, owner_id: int | None = None, period: str = "30d") -> ChartData:
    if period not in PERIODS:
        period = "30d"
    start, end, buckets = make_buckets(period, today)
    owners = owner_svc.list_owners(session)
    chosen = next((o for o in owners if o.id == owner_id), None)
    data = ChartData(period, start, end, buckets, owners, chosen)

    def idx(day: date) -> int | None:
        for i, b in enumerate(buckets):
            if b.start <= day <= b.end:
                return i
        return None

    keep = (lambda oid: oid == chosen.id) if chosen else (lambda oid: True)
    paid_in_range = select(Payment.sale_id).where(Payment.voided_at.is_(None), Payment.paid_at >= start, Payment.paid_at <= end)
    sales = session.scalars(
        select(Sale).where(Sale.cancelled_at.is_(None),
                           ((Sale.sale_date >= start) & (Sale.sale_date <= end)) | Sale.id.in_(paid_in_range)
                           | (Sale.paid_cents < Sale.total_cents))
        .options(selectinload(Sale.items), selectinload(Sale.payments))
    ).unique().all()

    n = len(buckets)
    sold_by_owner: dict = defaultdict(lambda: [0] * n)
    sold, received = [0] * n, [0] * n
    methods: dict[str, int] = defaultdict(int)
    products: dict[int, ProductMargin] = {}
    uncosted: set[int] = set()
    for sale in sales:
        shares = owner_svc.sale_shares(sale)
        splits = owner_svc.payment_splits(sale, shares)
        i = idx(sale.sale_date) if start <= sale.sale_date <= end else None
        if i is not None:
            for oid, share in shares.items():
                sold_by_owner[oid][i] += share
                if keep(oid):
                    sold[i] += share
            nets = margin_svc.item_nets(sale)
            for item in sale.items:
                if not keep(item.owner_id):
                    continue
                m = margin_svc.item_margin(item, nets[item.id])
                if m.uncosted:
                    uncosted.add(item.product_id)
                    continue
                pm = products.setdefault(item.product_id, ProductMargin(item.product_name, None))
                pm.revenue += m.revenue; pm.cost += m.cost
        for payment, part in splits:
            j = idx(payment.paid_at) if start <= payment.paid_at <= end else None
            if j is None:
                continue
            for oid, v in part.items():
                if keep(oid):
                    received[j] += v
                    methods[payment.method] += v
        if sale.paid_cents < sale.total_cents:
            paid_by = {k: sum(p[k] for _, p in splits) for k in shares}
            k = aging_index(sale.due_date, today)
            for oid, share in shares.items():
                if keep(oid):
                    data.aging[k] += share - paid_by[oid]
    data.sold_by_owner = dict(sold_by_owner)
    data.sold, data.received = sold, received
    data.margin_products = sorted((p for p in products.values() if p.amount != 0 or p.revenue),
                                  key=lambda p: -p.amount)[:8]
    data.uncosted_products = len(uncosted - set(products))
    data.methods = sorted(((method_label(m), v) for m, v in methods.items() if v), key=lambda kv: -kv[1])
    return data
