"""Relatórios. Todos devolvem a mesma estrutura (colunas + linhas + totais),
usada tanto na tela quanto na exportação CSV."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from ..domain.money import format_brl
from ..domain.payment_methods import label as method_label
from ..domain.status import LABELS, Status
from ..models import Customer, Payment, Product, Sale, SaleItem
from ..repositories import customers as customer_repo
from ..repositories import sales as sale_repo
from ..repositories.sales import ACTIVE, REMAINING, SaleFilters


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
    status: str = ""
    sort: str = ""


CATALOG = {
    "vendas": ("Vendas por período", "Todas as vendas do período, com quanto foi pago e quanto falta."),
    "produtos": ("Vendas por produto", "Quantidade e valor vendido de cada produto. Ordene por quantidade para ver os mais vendidos."),
    "clientes": ("Vendas por cliente", "Quanto cada cliente comprou, pagou e ainda deve."),
    "recebimentos": ("Pagamentos recebidos", "Cada pagamento recebido no período, por forma de pagamento."),
    "a-receber": ("Valores a receber", "Vendas com saldo em aberto: pendentes, parciais e vencidas."),
    "estoque": ("Estoque", "Saldo atual, custo e valor de venda do estoque."),
    "movimento": ("Movimentação financeira", "Por dia: quanto foi vendido e quanto foi recebido."),
}


def _sale_rows(sales: list[Sale], today: date) -> list[dict]:
    return [{
        "number": s.id, "sale_date": s.sale_date, "customer": s.customer.name if s.customer else "Consumidor",
        "due_date": s.due_date, "total": s.total_cents, "paid": s.paid_cents, "remaining": s.remaining_cents,
        "status": s.status,
    } for s in sales]


SALE_COLS = [Column("number", "Venda", "link"), Column("sale_date", "Data", "date"), Column("customer", "Cliente"),
             Column("due_date", "Vencimento", "date"), Column("total", "Total", "money"),
             Column("paid", "Pago", "money"), Column("remaining", "Falta pagar", "money"),
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
    return Report("vendas", *CATALOG["vendas"], SALE_COLS, rows, _sum(live, "total", "paid", "remaining"),
                  ("period", "customer", "status"))


def due_report(session: Session, p: ReportParams, today: date) -> Report:
    f = SaleFilters(customer_id=p.customer_id, date_from=p.date_from, date_to=p.date_to,
                    bucket={"vencido": "vencidos", "parcial": "parciais"}.get(p.status, "abertos"),
                    amount_field="remaining")
    sales = list(session.scalars(sale_repo.sales_query(f, today, order="due")))
    rows = _sale_rows(sales, today)
    return Report("a-receber", *CATALOG["a-receber"], SALE_COLS, rows, _sum(rows, "total", "paid", "remaining"),
                  ("period", "customer", "due_status"))


def products_report(session: Session, p: ReportParams, today: date) -> Report:
    q = (select(SaleItem.product_name, SaleItem.product_code,
                func.sum(SaleItem.quantity).label("qty"), func.sum(SaleItem.total_cents).label("revenue"),
                func.sum(SaleItem.quantity * SaleItem.unit_cost_cents).label("cost"))
         .join(Sale, Sale.id == SaleItem.sale_id).where(ACTIVE)
         .group_by(SaleItem.product_id, SaleItem.product_name, SaleItem.product_code))
    if p.date_from:
        q = q.where(Sale.sale_date >= p.date_from)
    if p.date_to:
        q = q.where(Sale.sale_date <= p.date_to)
    if p.customer_id:
        q = q.where(Sale.customer_id == p.customer_id)
    order = func.sum(SaleItem.total_cents) if p.sort == "valor" else func.sum(SaleItem.quantity)
    rows = [{"name": r.product_name, "code": r.product_code, "qty": r.qty, "revenue": r.revenue,
             "cost": r.cost, "margin": r.revenue - r.cost} for r in session.execute(q.order_by(order.desc()))]
    cols = [Column("name", "Produto"), Column("code", "Código"), Column("qty", "Qtd. vendida", "int"),
            Column("revenue", "Valor vendido", "money"), Column("cost", "Custo", "money"),
            Column("margin", "Margem", "money")]
    return Report("produtos", *CATALOG["produtos"], cols, rows, _sum(rows, "qty", "revenue", "cost", "margin"),
                  ("period", "customer", "sort_products"))


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
    rows = [{"name": x.name, "code": x.code, "stock": x.stock_qty, "min": x.min_stock, "unit": x.unit,
             "cost_value": x.stock_qty * x.cost_cents, "sale_value": x.stock_qty * x.price_cents}
            for x in session.scalars(q)]
    cols = [Column("name", "Produto"), Column("code", "Código"), Column("stock", "Em estoque", "int"),
            Column("min", "Mínimo", "int"), Column("unit", "Un."), Column("cost_value", "Valor a custo", "money"),
            Column("sale_value", "Valor de venda", "money")]
    return Report("estoque", *CATALOG["estoque"], cols, rows, _sum(rows, "cost_value", "sale_value"), ("stock_status",))


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


BUILDERS = {"vendas": sales_report, "produtos": products_report, "clientes": customers_report,
            "recebimentos": payments_report, "a-receber": due_report, "estoque": stock_report,
            "movimento": movement_report}


def build(session: Session, key: str, params: ReportParams, today: date) -> Report:
    return BUILDERS[key](session, params, today)


def format_cell(value, kind: str) -> str:
    if value is None or value == "":
        return ""
    if kind == "money":
        return format_brl(value, symbol=False)
    if kind == "date":
        return value.strftime("%d/%m/%Y")
    if kind == "status":
        return LABELS[Status(value)]
    return str(value)


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
