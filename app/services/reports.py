"""Relatórios. Todos devolvem a mesma estrutura (colunas + linhas + totais),
usada tanto na tela quanto na exportação CSV."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, selectinload

from ..domain.money import format_brl
from ..domain.payment_methods import label as method_label
from ..domain.status import LABELS, Status
from ..models import Customer, Payment, Product, Sale, SaleItem
from ..repositories import customers as customer_repo
from ..repositories import sales as sale_repo
from ..repositories.sales import ACTIVE, REMAINING, SaleFilters
from . import margin as margin_svc
from . import owners as owner_svc


@dataclass
class Column:
    key: str
    label: str
    kind: str = "text"  # text | money | int | date | status | link


@dataclass
class Report:
    key: str
    title: str
    description: str
    columns: list[Column]
    rows: list[dict]
    totals: dict = field(default_factory=dict)
    filters: tuple[str, ...] = ()  # quais filtros a tela deve mostrar


@dataclass
class ReportParams:
    date_from: date | None = None
    date_to: date | None = None
    customer_id: int | None = None
    product_id: int | None = None
    owner_id: int | None = None
    status: str = ""
    sort: str = ""


CATALOG = {
    "vendas": ("Vendas por período", "Todas as vendas do período, com quanto foi pago e quanto falta."),
    "produtos": ("Vendas por produto", "Quantidade e valor vendido de cada produto. Ordene por quantidade para ver os mais vendidos."),
    "clientes": ("Vendas por cliente", "Quanto cada cliente comprou, pagou e ainda deve."),
    "recebimentos": ("Pagamentos recebidos", "Cada pagamento recebido no período, por forma de pagamento."),
    "a-receber": ("Valores a receber", "Vendas com saldo em aberto: pendentes, parciais e vencidas."),
    "margem-produtos": ("Produtos e margem", "Quanto cada produto custou, por quanto é vendido e quanto sobra, com o lucro que ainda está parado no estoque."),
    "estoque": ("Estoque", "Saldo atual, custo e valor de venda do estoque."),
    "pessoas": ("Resultado por pessoa", "Quanto cada pessoa vendeu, custou, rendeu, recebeu e ainda tem a receber (a parte dela em cada venda)."),
    "movimento": ("Movimentação financeira", "Por dia: quanto foi vendido e quanto foi recebido."),
}


def _sale_rows(sales: list[Sale], today: date) -> list[dict]:
    return [{
        "number": s.id, "sale_date": s.sale_date, "customer": s.customer.name if s.customer else "Consumidor",
        "due_date": s.due_date, "total": s.total_cents, "margin": margin_svc.sale_margin(s).amount, "paid": s.paid_cents,
        "remaining": s.remaining_cents, "status": s.status,
    } for s in sales]


SALE_COLS = [Column("number", "Venda", "link"), Column("sale_date", "Data", "date"), Column("customer", "Cliente"),
             Column("due_date", "Vencimento", "date"), Column("total", "Total", "money"),
             Column("margin", "Margem", "money"), Column("paid", "Pago", "money"), Column("remaining", "Falta pagar", "money"),
             Column("status", "Situação", "status")]


def _sum(rows: list[dict], *keys: str) -> dict:
    return {k: sum(r[k] for r in rows) for k in keys}


def sales_report(session: Session, p: ReportParams, today: date) -> Report:
    f = SaleFilters(customer_id=p.customer_id, date_from=p.date_from, date_to=p.date_to, status=p.status)
    query = sale_repo.sales_query(f, today)
    if not p.status:
        query = query.where(ACTIVE)  # canceladas só aparecem quando filtradas
    sales = list(session.scalars(query))
    rows = _sale_rows(sales, today)
    live = [r for r in rows if r["status"] != Status.CANCELADA]
    return Report("vendas", *CATALOG["vendas"], SALE_COLS, rows, _sum(live, "total", "margin", "paid", "remaining"),
                  ("period", "customer", "status"))


def due_report(session: Session, p: ReportParams, today: date) -> Report:
    f = SaleFilters(customer_id=p.customer_id, date_from=p.date_from, date_to=p.date_to,
                    bucket={"vencido": "vencidos", "parcial": "parciais"}.get(p.status, "abertos"),
                    amount_field="remaining")
    sales = list(session.scalars(sale_repo.sales_query(f, today, order="due")))
    rows = _sale_rows(sales, today)
    return Report("a-receber", *CATALOG["a-receber"], SALE_COLS, rows, _sum(rows, "total", "margin", "paid", "remaining"),
                  ("period", "customer", "due_status"))


def products_report(session: Session, p: ReportParams, today: date) -> Report:
    """Por produto, com o valor LÍQUIDO (desconto da venda rateado nos itens) e a margem de verdade."""
    q = (select(Sale).where(ACTIVE).options(selectinload(Sale.items)).order_by(Sale.id))
    if p.date_from:
        q = q.where(Sale.sale_date >= p.date_from)
    if p.date_to:
        q = q.where(Sale.sale_date <= p.date_to)
    if p.customer_id:
        q = q.where(Sale.customer_id == p.customer_id)
    names = {o.id: o.name for o in owner_svc.list_owners(session)}
    acc: dict = {}
    for sale in session.scalars(q).unique():
        nets = margin_svc.item_nets(sale)
        for item in sale.items:
            if p.owner_id and item.owner_id != p.owner_id:
                continue
            r = acc.setdefault(item.product_id, {"name": item.product_name, "code": item.product_code,
                                                 "owner": names.get(item.owner_id, ""), "qty": 0, "revenue": 0, "cost": 0,
                                                 "m_rev": 0, "m_cost": 0})
            m = margin_svc.item_margin(item, nets[item.id])
            r["qty"] += item.quantity; r["revenue"] += nets[item.id]; r["cost"] += item.quantity * item.unit_cost_cents
            r["m_rev"] += m.revenue; r["m_cost"] += m.cost
    key = {"valor": "revenue", "margem": "margin"}.get(p.sort, "qty")
    rows = []
    for r in acc.values():
        amount = r["m_rev"] - r["m_cost"]
        rows.append({"name": r["name"], "code": r["code"], "owner": r["owner"], "qty": r["qty"], "revenue": r["revenue"],
                     "cost": r["cost"], "margin": amount,
                     "margin_pct": round(amount * 100 / r["m_rev"], 1) if r["m_rev"] > 0 else None})
    rows.sort(key=lambda r: (-r[key], r["name"]))
    cols = [Column("name", "Produto"), Column("code", "Código"), Column("owner", "Pessoa"), Column("qty", "Qtd. vendida", "int"),
            Column("revenue", "Valor vendido", "money"), Column("cost", "Custo", "money"), Column("margin", "Margem", "money"),
            Column("margin_pct", "Margem %", "percent")]
    return Report("produtos", *CATALOG["produtos"], cols, rows, _sum(rows, "qty", "revenue", "cost", "margin"),
                  ("period", "customer", "owner", "sort_products"))


def margin_report(session: Session, p: ReportParams, today: date) -> Report:
    """Catálogo com custo, preço, margem e o lucro que ainda está no estoque."""
    names = {o.id: o.name for o in owner_svc.list_owners(session)}
    q = select(Product).where(Product.active.is_(True)).order_by(Product.name)
    if p.owner_id:
        q = q.where(Product.owner_id == p.owner_id)
    rows = []
    for x in session.scalars(q):
        has_cost = x.cost_cents > 0
        rows.append({"name": x.name, "owner": names.get(x.owner_id, ""), "cost": x.cost_cents if has_cost else None,
                     "price": x.price_cents, "margin": x.margin_cents if has_cost else None, "margin_pct": x.margin_percent,
                     "stock": x.stock_qty, "stock_cost": x.stock_qty * x.cost_cents, "stock_value": x.stock_qty * x.price_cents,
                     "potential": x.stock_qty * x.margin_cents if has_cost else None})
    rows.sort(key=lambda r: (r["margin_pct"] is None, -(r["margin_pct"] or 0), r["name"]))
    cols = [Column("name", "Produto"), Column("owner", "Pessoa"), Column("cost", "Custo", "money"), Column("price", "Preço", "money"),
            Column("margin", "Margem", "money"), Column("margin_pct", "Margem %", "percent"), Column("stock", "Em estoque", "int"),
            Column("stock_cost", "Estoque a custo", "money"), Column("stock_value", "Estoque a preço", "money"),
            Column("potential", "Lucro no estoque", "money")]
    return Report("margem-produtos", *CATALOG["margem-produtos"], cols, rows,
                  _sum([{k: (v or 0) for k, v in r.items()} for r in rows], "stock_cost", "stock_value", "potential"), ("owner",))


def customers_report(session: Session, p: ReportParams, today: date) -> Report:
    rows = [{"name": r.customer.name, "phone": r.customer.phone or "", "bought": r.bought, "paid": r.paid,
             "pending": r.pending, "overdue": r.overdue, "last_sale": r.last_sale}
            for r in map(customer_repo.to_row, session.execute(
                customer_repo.balances_query(today, active=None).order_by(func.sum(case((ACTIVE, Sale.total_cents), else_=0)).desc())))
            if r.bought or r.pending]
    cols = [Column("name", "Cliente"), Column("phone", "Telefone"), Column("bought", "Comprou", "money"),
            Column("paid", "Pagou", "money"), Column("pending", "Deve", "money"),
            Column("overdue", "Vencido", "money"), Column("last_sale", "Última compra", "date")]
    return Report("clientes", *CATALOG["clientes"], cols, rows, _sum(rows, "bought", "paid", "pending", "overdue"), ())


def payments_report(session: Session, p: ReportParams, today: date) -> Report:
    q = (select(Payment, Sale).join(Sale, Sale.id == Payment.sale_id)
         .where(Payment.voided_at.is_(None)).order_by(Payment.paid_at.desc(), Payment.id.desc()))
    if p.date_from:
        q = q.where(Payment.paid_at >= p.date_from)
    if p.date_to:
        q = q.where(Payment.paid_at <= p.date_to)
    if p.customer_id:
        q = q.where(Sale.customer_id == p.customer_id)
    rows = []
    for pay, sale in session.execute(q):
        rows.append({"paid_at": pay.paid_at, "customer": sale.customer.name if sale.customer else "Consumidor",
                     "number": sale.id, "method": method_label(pay.method), "amount": pay.amount_cents,
                     "note": pay.note or ""})
    cols = [Column("paid_at", "Data", "date"), Column("customer", "Cliente"), Column("number", "Venda", "link"),
            Column("method", "Forma"), Column("amount", "Valor", "money"), Column("note", "Observação")]
    return Report("recebimentos", *CATALOG["recebimentos"], cols, rows, _sum(rows, "amount"), ("period", "customer"))


def stock_report(session: Session, p: ReportParams, today: date) -> Report:
    q = select(Product).where(Product.active.is_(True)).order_by(Product.name)
    if p.status == "baixo":
        q = q.where(Product.stock_qty <= Product.min_stock)
    if p.owner_id:
        q = q.where(Product.owner_id == p.owner_id)
    rows = [{"name": x.name, "code": x.code, "stock": x.stock_qty, "min": x.min_stock, "unit": x.unit,
             "cost_value": x.stock_qty * x.cost_cents, "sale_value": x.stock_qty * x.price_cents}
            for x in session.scalars(q)]
    cols = [Column("name", "Produto"), Column("code", "Código"), Column("stock", "Em estoque", "int"),
            Column("min", "Mínimo", "int"), Column("unit", "Un."), Column("cost_value", "Valor a custo", "money"),
            Column("sale_value", "Valor de venda", "money")]
    return Report("estoque", *CATALOG["estoque"], cols, rows, _sum(rows, "cost_value", "sale_value"), ("owner", "stock_status"))


def movement_report(session: Session, p: ReportParams, today: date) -> Report:
    sold_q = select(Sale.sale_date.label("d"), func.sum(Sale.total_cents).label("v")).where(ACTIVE).group_by(Sale.sale_date)
    rec_q = select(Payment.paid_at.label("d"), func.sum(Payment.amount_cents).label("v")).where(Payment.voided_at.is_(None)).group_by(Payment.paid_at)
    days: dict[date, dict] = {}
    for key, q, col in (("sold", sold_q, Sale.sale_date), ("received", rec_q, Payment.paid_at)):
        if p.date_from:
            q = q.where(col >= p.date_from)
        if p.date_to:
            q = q.where(col <= p.date_to)
        for r in session.execute(q):
            days.setdefault(r.d, {"day": r.d, "sold": 0, "received": 0})[key] = r.v
    rows = sorted(days.values(), key=lambda r: r["day"], reverse=True)
    for r in rows:
        r["difference"] = r["sold"] - r["received"]
    cols = [Column("day", "Dia", "date"), Column("sold", "Vendido", "money"), Column("received", "Recebido", "money"),
            Column("difference", "Vendido − recebido", "money")]
    return Report("movimento", *CATALOG["movimento"], cols, rows, _sum(rows, "sold", "received", "difference"), ("period",))


def owners_report(session: Session, p: ReportParams, today: date) -> Report:
    """Por pessoa: vendido no período, margem, recebido no período e a receber hoje."""
    start = p.date_from or today.replace(day=1)
    end = p.date_to or today
    in_period_payments = select(Payment.sale_id).where(Payment.voided_at.is_(None), Payment.paid_at >= start, Payment.paid_at <= end)
    sales = session.scalars(
        select(Sale).where(ACTIVE, ((Sale.sale_date >= start) & (Sale.sale_date <= end)) | Sale.id.in_(in_period_payments)
                           | (Sale.paid_cents < Sale.total_cents))
        .options(selectinload(Sale.items), selectinload(Sale.payments))
    ).unique().all()
    names = {o.id: o.name for o in owner_svc.list_owners(session)}
    blank = lambda name: {"name": name, "sold": 0, "cost": 0, "margin": 0, "m_rev": 0, "received": 0, "receivable": 0}
    acc = {oid: blank(name) for oid, name in names.items()}
    for sale in sales:
        shares = owner_svc.sale_shares(sale)
        got = owner_svc.period_received(sale, shares, start, end)
        splits = owner_svc.payment_splits(sale, shares)
        paid_by = {k: sum(part[k] for _, part in splits) for k in shares}
        margins = margin_svc.margins_by_owner(sale)
        for oid, share in shares.items():
            row = acc.setdefault(oid, blank(names.get(oid, "Produtos sem dono")))
            row["received"] += got[oid]
            row["receivable"] += (share - paid_by[oid]) if sale.paid_cents < sale.total_cents else 0
            if start <= sale.sale_date <= end:
                row["sold"] += share
                m = margins[oid]
                row["cost"] += m.cost; row["margin"] += m.amount; row["m_rev"] += m.revenue
    rows = [{**r, "margin_pct": round(r["margin"] * 100 / r["m_rev"], 1) if r["m_rev"] > 0 else None} for r in acc.values()]
    cols = [Column("name", "Pessoa"), Column("sold", "Vendido", "money"), Column("cost", "Custo", "money"),
            Column("margin", "Margem", "money"), Column("margin_pct", "Margem %", "percent"),
            Column("received", "Recebido", "money"), Column("receivable", "A receber hoje", "money")]
    return Report("pessoas", *CATALOG["pessoas"], cols, rows, _sum(rows, "sold", "cost", "margin", "received", "receivable"), ("period",))


BUILDERS = {"vendas": sales_report, "produtos": products_report, "clientes": customers_report,
            "recebimentos": payments_report, "a-receber": due_report, "estoque": stock_report, "pessoas": owners_report,
            "margem-produtos": margin_report,
            "movimento": movement_report}


def build(session: Session, key: str, params: ReportParams, today: date) -> Report:
    return BUILDERS[key](session, params, today)


def format_cell(value, kind: str) -> str:
    if value is None or value == "":
        return ""
    if kind == "money":
        return format_brl(value, symbol=False)
    if kind == "percent":
        return f"{value:.1f}".replace(".", ",") + "%"
    if kind == "date":
        return value.strftime("%d/%m/%Y")
    if kind == "status":
        return LABELS[Status(value)]
    text = str(value)
    # Excel/Sheets executam células que começam com = + - @ (injeção de fórmula): neutraliza com apóstrofo.
    return "'" + text if kind in ("text", "link") and text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(report: Report) -> bytes:
    """CSV para Excel brasileiro: separador `;`, vírgula decimal e BOM UTF-8."""
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow([c.label for c in report.columns])
    for row in report.rows:
        w.writerow([format_cell(row.get(c.key), c.kind) for c in report.columns])
    if report.totals:
        w.writerow(["Total" if i == 0 else format_cell(report.totals.get(c.key), c.kind) if c.key in report.totals else ""
                    for i, c in enumerate(report.columns)])
    return ("﻿" + out.getvalue()).encode("utf-8")
