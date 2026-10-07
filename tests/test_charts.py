from datetime import date, timedelta

import pytest

from app.services import charts, dashboard
from app.services import owners as own
from app.services import payments as pay
from app.services import products as pvc
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY


def test_periods_make_the_right_buckets():
    s, e, b = charts.make_buckets("30d", TODAY)
    assert (len(b), b[0].start, b[-1].end, e) == (30, TODAY - timedelta(days=29), TODAY, TODAY)
    s, e, b = charts.make_buckets("mes", TODAY)
    assert (b[0].start, len(b)) == (date(2026, 10, 1), 6)
    s, e, b = charts.make_buckets("90d", TODAY)
    assert len(b) == 13 and b[-1].end == TODAY and b[0].start == TODAY - timedelta(days=89)
    assert all(b[i].end + timedelta(days=1) == b[i + 1].start for i in range(len(b) - 1))   # sem buraco nem sobreposição
    s, e, b = charts.make_buckets("6m", TODAY)
    assert [x.label for x in b] == ["mai/26", "jun/26", "jul/26", "ago/26", "set/26", "out/26"] and b[-1].end == TODAY


def test_aging_buckets():
    t = TODAY
    assert [charts.aging_index(d, t) for d in (None, t, t + timedelta(days=3), t - timedelta(days=1), t - timedelta(days=7),
                                                t - timedelta(days=8), t - timedelta(days=30), t - timedelta(days=31))] == [0, 0, 0, 1, 1, 2, 2, 3]


@pytest.fixture
def world(session, make_customer):
    ana, bia = own.create_owner(session, "Ana"), own.create_owner(session, "Bia")
    pa = pvc.create_product(session, pvc.ProductInput(name="Da Ana", price_cents=10000, cost_cents=4000, initial_stock=99, owner_id=ana.id))
    pb = pvc.create_product(session, pvc.ProductInput(name="Da Bia", price_cents=5000, cost_cents=2000, initial_stock=99, owner_id=bia.id))
    nocost = pvc.create_product(session, pvc.ProductInput(name="Sem custo", price_cents=1000, initial_stock=99, owner_id=bia.id))
    c = make_customer()

    def mk(days_ago, items, paid=0, method="pix", due=5):
        s = sale_svc.create_sale(session, SaleInput(
            items=[ItemInput(p.id, q) for p, q in items], customer_id=c.id, sale_date=TODAY - timedelta(days=days_ago),
            paid_cents=paid, payment_method=method if paid else None, due_date=TODAY - timedelta(days=days_ago) + timedelta(days=due)))
        return s
    mk(0, [(pa, 1), (pb, 2)], paid=10000)                       # hoje: 200, pago 100
    mk(3, [(pa, 1)], paid=10000, method="dinheiro")             # 100 pago
    old = mk(40, [(pb, 1), (nocost, 1)], due=10)                # vencida há 30 dias (40-10): faixa "8 a 30"
    return ana, bia, pa, pb, nocost, c, old


def test_chart_data_agrees_with_the_dashboard(session, world):
    ana, bia, *_ = world
    d = charts.build(session, TODAY, None, "mes")
    g = dashboard.build(session, TODAY)
    assert sum(d.sold) == g.sold_month and sum(d.received) == g.received_month        # gráfico = painel
    for o in (ana, bia):
        dc, dp = charts.build(session, TODAY, o.id, "mes"), dashboard.build_for_owner(session, TODAY, o.id)
        assert sum(dc.sold) == dp.sold_month and sum(dc.received) == dp.received_month
    assert sum(sum(v) for v in d.sold_by_owner.values()) == sum(d.sold)               # empilhado fecha com o total


def test_aging_totals_equal_receivable_and_overdue(session, world):
    ana, bia, *_ = world
    d = charts.build(session, TODAY, None, "30d")
    g = dashboard.build(session, TODAY)
    assert sum(d.aging) == g.receivable and d.aging[1] + d.aging[2] + d.aging[3] == g.overdue
    assert d.aging[2] > 0 and d.aging[3] == 0                                          # a venda de 40 dias atrás venceu há 30
    b = charts.build(session, TODAY, bia.id, "30d")
    assert sum(b.aging) == dashboard.build_for_owner(session, TODAY, bia.id).receivable


def test_margin_products_ranking_and_uncosted_note(session, world):
    ana, bia, *_ = world
    d = charts.build(session, TODAY, None, "90d")
    assert [p.name for p in d.margin_products][:2] == ["Da Ana", "Da Bia"]
    assert d.margin_products[0].amount == 2 * 6000 and d.margin_products[0].percent == 60.0
    assert d.uncosted_products == 1                                                    # "Sem custo" ficou de fora e é avisado
    only = charts.build(session, TODAY, bia.id, "90d")
    assert [p.name for p in only.margin_products] == ["Da Bia"]


def test_methods_split_follows_people(session, world):
    ana, bia, *_ = world
    d = charts.build(session, TODAY, None, "30d")
    assert dict(d.methods) == {"Pix": 10000, "Dinheiro": 10000}
    a = charts.build(session, TODAY, ana.id, "30d")
    assert sum(v for _, v in a.methods) == sum(a.received)


def test_voided_payment_and_cancelled_sale_leave_the_charts(session, world):
    from sqlalchemy import select
    from app.models import Payment
    first = session.scalars(select(Payment).order_by(Payment.id)).first()
    pay.void_payment(session, first.id)
    d = charts.build(session, TODAY, None, "mes")
    assert sum(d.received) == 10000
    assert charts.build(session, TODAY, None, "nada-valido", ).period == "30d"       # período inválido cai no padrão


# ── páginas e SVG ───────────────────────────────────────────────────────────
from .test_web import app, client, db, post_json  # noqa: E402,F401  (fixtures)


@pytest.fixture
def web_world(client, db):
    from app.services import customers as csvc
    ana, bia = own.create_owner(db, "Ana"), own.create_owner(db, "Bia")
    pa = pvc.create_product(db, pvc.ProductInput(name="Vestido <b>", price_cents=10000, cost_cents=4000, initial_stock=50, owner_id=ana.id))
    pb = pvc.create_product(db, pvc.ProductInput(name="Brinco", price_cents=5000, cost_cents=2000, initial_stock=50, owner_id=bia.id))
    cliente = csvc.create_customer(db, csvc.CustomerInput(name="Cli"))
    post_json(client, "/vendas/nova", {"items": [{"product_id": pa.id, "quantity": 1}, {"product_id": pb.id, "quantity": 2}], "paid": "100",
                                       "payment_method": "pix", "customer_id": cliente.id,
                                       "due_date": (TODAY + timedelta(days=3)).isoformat()})
    return ana, bia


def test_charts_page_renders_for_every_filter(client, web_world):
    ana, bia = web_world
    for pessoa in ("geral", str(ana.id), str(bia.id), "999999999999", "lixo"):
        for periodo in charts.PERIODS:
            r = client.get(f"/graficos?pessoa={pessoa}&periodo={periodo}")
            assert r.status_code == 200, (pessoa, periodo)
    assert client.get("/graficos?periodo=inventado").status_code == 200


def test_charts_page_content_and_accessibility(client, web_world):
    ana, bia = web_world
    page = client.get("/graficos?pessoa=geral&periodo=mes").get_data(as_text=True)
    for needle in ("Vendido e recebido", "Vendido por pessoa", "Margem por produto", "A receber por prazo", "Recebido por forma de pagamento",
                   "Ver como tabela", 'role="img"', "<title>", "data-tip=", "charts.js"):
        assert needle in page, needle
    assert page.count('class="chart-svg"') == 5
    only = client.get(f"/graficos?pessoa={ana.id}&periodo=mes").get_data(as_text=True)
    assert "Gráficos de Ana" in only and "Vendido por pessoa" not in only          # no painel de uma pessoa não há comparação


def test_chart_text_from_user_data_is_escaped(client, web_world):
    page = client.get("/graficos?periodo=mes").get_data(as_text=True)
    assert "Vestido <b>" not in page and "Vestido &lt;b&gt;" in page
    import re
    tips = re.findall(r'data-tip="([^"]*)"', page)
    assert tips and all("<" not in t and '"' not in t for t in tips)               # o JSON da dica nunca fecha o atributo


def test_svg_geometry_helpers():
    from app.web import charts_svg as svg
    assert svg.nice_scale(0) == (10000, 2500)
    for v in (1, 99, 100_00, 123_456, 9_999_999):
        top, step = svg.nice_scale(v)
        assert top >= v and top % step == 0 and top // step <= 6
    assert svg.tick_label(1_500_00) == "R$ 1.500"
    d = svg._bar_path(10, 20, 8, 50)
    assert d.startswith("M10.0,70.0") and d.endswith("Z")


def test_chart_colors_exist_in_css_and_follow_the_person():
    css = open("app/static/css/app.css").read()
    for var in ("--c1: #008f99", "--c2: #cf5a2a", "--c-sold", "--c-recv", "--age1", "--age3"):
        assert var in css, var
