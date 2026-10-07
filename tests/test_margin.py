import random
from datetime import timedelta
from decimal import Decimal

import pytest

from app.services import dashboard
from app.services import margin as mg
from app.services import owners as own
from app.services import products as pvc
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY


def sell(session, items, **kw):
    return sale_svc.create_sale(session, SaleInput(items=[ItemInput(p.id, q, price) for p, q, price in items], **kw))


@pytest.fixture
def goods(session, make_product):
    return (make_product("Vestido", price=10000, cost=4000, stock=50),
            make_product("Brinco", price=5000, cost=2000, stock=50),
            make_product("Sem custo", price=3000, cost=0, stock=50))


def test_margin_is_sold_price_minus_cost(session, goods):
    vestido, brinco, _ = goods
    s = sell(session, [(vestido, 2, None), (brinco, 1, None)], paid_cents=25000, payment_method="pix")
    m = mg.sale_margin(s)
    assert (m.revenue, m.cost, m.amount, m.percent) == (25000, 2 * 4000 + 2000, 15000, 60.0)


def test_margin_uses_the_price_it_was_actually_sold_for(session, goods):
    vestido, *_ = goods
    s = sell(session, [(vestido, 1, 7000)], paid_cents=7000, payment_method="pix")    # vendeu por 70, custou 40
    assert mg.sale_margin(s).amount == 3000
    s2 = sell(session, [(vestido, 1, 3500)], paid_cents=3500, payment_method="pix")   # vendeu abaixo do custo: prejuízo
    assert mg.sale_margin(s2).amount == -500 and mg.sale_margin(s2).percent < 0


def test_discount_is_spread_over_items_and_reduces_margin(session, goods):
    vestido, brinco, _ = goods
    s = sell(session, [(vestido, 1, None), (brinco, 1, None)], discount_cents=1500, paid_cents=13500, payment_method="pix")
    nets = mg.item_nets(s)
    assert sum(nets.values()) == s.total_cents == 13500
    assert mg.sale_margin(s).amount == 13500 - (4000 + 2000)


def test_items_without_cost_are_left_out_not_counted_as_pure_profit(session, goods):
    vestido, _, sem = goods
    s = sell(session, [(vestido, 1, None), (sem, 1, None)], paid_cents=13000, payment_method="pix")
    m = mg.sale_margin(s)
    assert m.uncosted == 1 and m.revenue == 10000 and m.amount == 6000    # os R$ 30 sem custo não inflam o lucro


def test_net_rounding_never_loses_a_cent(session, goods, make_customer):
    vestido, brinco, sem = goods
    c = make_customer()
    rng = random.Random(5)
    for _ in range(25):
        s = sell(session, [(vestido, rng.randint(1, 2), None), (brinco, rng.randint(1, 3), None), (sem, 1, None)],
                 discount_percent=Decimal(str(rng.choice([0, 3.3, 12.5, 33.33]))), customer_id=c.id,
                 due_date=TODAY + timedelta(days=3))
        assert sum(mg.item_nets(s).values()) == s.total_cents


def test_dashboard_margin_adds_up_across_people_and_general(session, goods, make_customer):
    ana, bia = own.create_owner(session, "Ana"), own.create_owner(session, "Bia")
    pa = pvc.create_product(session, pvc.ProductInput(name="Da Ana", price_cents=10000, cost_cents=4000, initial_stock=20, owner_id=ana.id))
    pb = pvc.create_product(session, pvc.ProductInput(name="Da Bia", price_cents=5000, cost_cents=2000, initial_stock=20, owner_id=bia.id))
    c = make_customer()
    sell(session, [(pa, 1, None), (pb, 2, None)], discount_cents=777, customer_id=c.id, due_date=TODAY + timedelta(days=3))
    sell(session, [(pa, 2, 9000)], customer_id=c.id, due_date=TODAY + timedelta(days=3))
    g = dashboard.build(session, TODAY)
    a, b = (dashboard.build_for_owner(session, TODAY, o.id) for o in (ana, bia))
    assert g.margin_month.amount == a.margin_month.amount + b.margin_month.amount
    assert g.margin_month.cost == a.margin_month.cost + b.margin_month.cost == 4000 + 4000 + 8000
    assert a.margin_month.revenue + b.margin_month.revenue == g.margin_month.revenue
    assert [r.acc.margin_month.amount for r in g.by_owner] == [a.margin_month.amount, b.margin_month.amount]


def test_shares_and_margin_use_the_same_item_level_split(session, make_customer):
    ana, bia = own.create_owner(session, "Ana"), own.create_owner(session, "Bia")
    pa = pvc.create_product(session, pvc.ProductInput(name="A", price_cents=3333, cost_cents=1000, initial_stock=9, owner_id=ana.id))
    pb = pvc.create_product(session, pvc.ProductInput(name="B", price_cents=6667, cost_cents=1000, initial_stock=9, owner_id=bia.id))
    s = sell(session, [(pa, 1, None), (pb, 1, None)], discount_cents=1001, customer_id=make_customer().id, due_date=TODAY)
    shares, by = own.sale_shares(s), mg.margins_by_owner(s)
    assert sum(shares.values()) == s.total_cents
    for oid in shares:
        assert by[oid].revenue == shares[oid]                     # a parte da pessoa e a receita da margem dela são a mesma conta


def test_product_report_uses_net_value_and_margin_percent(session, goods, make_customer):
    from app.services import reports
    vestido, brinco, sem = goods
    sell(session, [(vestido, 2, None), (sem, 1, None)], discount_cents=2300, customer_id=make_customer().id, due_date=TODAY)   # 230 - 23 = 207
    r = reports.build(session, "produtos", reports.ReportParams(sort="margem"), TODAY)
    by = {row["name"]: row for row in r.rows}
    assert by["Vestido"]["qty"] == 2 and by["Vestido"]["cost"] == 8000
    assert by["Vestido"]["revenue"] + by["Sem custo"]["revenue"] == 20700                 # desconto rateado: o líquido fecha com a venda
    assert by["Vestido"]["margin"] == by["Vestido"]["revenue"] - 8000
    assert by["Sem custo"]["margin_pct"] is None and by["Sem custo"]["margin"] == 0       # sem custo: não inventa lucro
    assert r.rows[0]["name"] == "Vestido"                                                  # ordenado por margem


def test_margin_catalog_report_shows_potential_profit_in_stock(session, goods):
    from app.services import reports
    r = reports.build(session, "margem-produtos", reports.ReportParams(), TODAY)
    by = {row["name"]: row for row in r.rows}
    assert by["Vestido"]["margin"] == 6000 and by["Vestido"]["margin_pct"] == 60.0
    assert by["Vestido"]["potential"] == 50 * 6000 and by["Vestido"]["stock_cost"] == 50 * 4000
    assert by["Sem custo"]["margin"] is None and by["Sem custo"]["potential"] is None
    assert r.rows[-1]["name"] == "Sem custo"                                               # sem custo vai para o fim, destacado
    assert reports.to_csv(r).decode("utf-8-sig").splitlines()[1].count(";") == 9


def test_product_model_margin_percent(session, goods):
    vestido, _, sem = goods
    assert (vestido.margin_cents, vestido.margin_percent) == (6000, 60.0)
    assert sem.margin_percent is None
