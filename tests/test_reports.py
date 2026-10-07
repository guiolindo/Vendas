from datetime import timedelta

import pytest

from app import clock
from app.services import dashboard, reports
from app.services import payments as pay
from app.services import sales as sale_svc
from app.services.reports import ReportParams
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY


@pytest.fixture
def scenario(session, make_product, make_customer):
    """Cenário conhecido:
    A: R$100, paga integral hoje.   B: R$200, pagou 50, vence ontem (vencida).
    C: R$300, nada pago, vence hoje.   D: R$400 cancelada."""
    p = make_product("Item", price=10000, cost=4000, stock=100, min_stock=95)
    ana, bia = make_customer("Ana"), make_customer("Bia")

    def mk(qty, customer=None, paid=0, due=None):
        return sale_svc.create_sale(session, SaleInput(
            items=[ItemInput(p.id, qty)], customer_id=customer.id if customer else None, paid_cents=paid,
            payment_method="pix" if paid else None, due_date=due))
    a = mk(1, paid=10000)
    b = mk(2, bia, paid=5000, due=TODAY + timedelta(days=5))
    c = mk(3, ana, due=TODAY)
    d = mk(4, ana, due=TODAY + timedelta(days=2))
    sale_svc.cancel_sale(session, d.id, "erro")
    b.due_date = TODAY - timedelta(days=1)
    session.commit()
    return p


def test_dashboard_numbers(session, scenario):
    d = dashboard.build(session, TODAY)
    assert d.sold_today == 60000 and d.sales_today == 3          # cancelada fora
    assert d.received_today == 15000
    assert d.receivable == 15000 + 30000
    assert d.overdue == 15000 and d.overdue_customers == 1
    assert d.due_today == 30000
    assert [r.customer.name for r in d.debtors] == ["Ana", "Bia"]
    assert d.top_products[0].qty == 6
    assert [p.name for p in d.low_stock] == ["Item"]             # 94 <= mínimo 95
    assert len(d.recent_payments) == 2


def test_reports_totals_match(session, scenario):
    r = reports.build(session, "vendas", ReportParams(), TODAY)
    assert r.totals == {"total": 60000, "margin": 36000, "paid": 15000, "remaining": 45000}   # 6 un. vendidas: 600 - custo 240
    assert len(r.rows) == 3                                       # canceladas ficam de fora...
    r = reports.build(session, "vendas", ReportParams(status="cancelada"), TODAY)
    assert [x["number"] for x in r.rows] == [4] and r.totals["total"] == 0  # ...e só aparecem se filtradas
    r = reports.build(session, "vendas", ReportParams(), TODAY)
    r = reports.build(session, "a-receber", ReportParams(status="vencido"), TODAY)
    assert [x["number"] for x in r.rows] == [2] and r.totals["remaining"] == 15000
    r = reports.build(session, "a-receber", ReportParams(status="parcial"), TODAY)
    assert [x["number"] for x in r.rows] == [2]
    r = reports.build(session, "produtos", ReportParams(), TODAY)
    assert r.totals["qty"] == 6 and r.totals["margin"] == 60000 - 24000
    r = reports.build(session, "recebimentos", ReportParams(), TODAY)
    assert r.totals["amount"] == 15000
    r = reports.build(session, "movimento", ReportParams(), TODAY)
    assert r.totals["sold"] == 60000 and r.totals["received"] == 15000
    r = reports.build(session, "clientes", ReportParams(), TODAY)
    assert {x["name"]: x["pending"] for x in r.rows} == {"Ana": 30000, "Bia": 15000}
    r = reports.build(session, "estoque", ReportParams(), TODAY)
    assert r.rows[0]["stock"] == 94


def test_voided_payment_leaves_cash_reports(session, scenario):
    first = session.get(__import__("app.models", fromlist=["Payment"]).Payment, 1)
    pay.void_payment(session, first.id)
    assert reports.build(session, "recebimentos", ReportParams(), TODAY).totals["amount"] == 5000


def test_csv_export_is_excel_friendly(session, scenario):
    data = reports.to_csv(reports.build(session, "vendas", ReportParams(), TODAY)).decode("utf-8-sig")
    lines = data.strip().splitlines()
    assert lines[0].startswith("Venda;Data;Cliente")
    assert "100,00" in lines[1] or "100,00" in data
    assert lines[-1].startswith("Total;")
    assert "600,00" in lines[-1]
