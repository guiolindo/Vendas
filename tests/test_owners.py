import random
from datetime import timedelta

import pytest

from app.errors import BusinessError
from app.models import Payment, SaleItem
from app.services import dashboard
from app.services import owners as own
from app.services import payments as pay
from app.services import products as pvc
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput
from sqlalchemy import select

from .conftest import TODAY


@pytest.fixture
def duo(session, make_customer):
    ana, bia = own.create_owner(session, "Ana"), own.create_owner(session, "Bia")
    pa = pvc.create_product(session, pvc.ProductInput(name="Produto da Ana", price_cents=10000, cost_cents=4000, initial_stock=50, owner_id=ana.id))
    pb = pvc.create_product(session, pvc.ProductInput(name="Produto da Bia", price_cents=5000, cost_cents=2000, initial_stock=50, owner_id=bia.id))
    return ana, bia, pa, pb, make_customer("Cliente")


def sell(session, items, **kw):
    return sale_svc.create_sale(session, SaleInput(items=[ItemInput(p.id, q) for p, q in items], **kw))


# ── divisão ─────────────────────────────────────────────────────────────────
def test_allocate_never_loses_or_creates_cents():
    rng = random.Random(7)
    for _ in range(500):
        total = rng.randint(1, 10_000_000)
        weights = {i: rng.randint(1, 1_000_000) for i in range(rng.randint(1, 4))}
        got = own.allocate(total, weights)
        assert sum(got.values()) == total and all(v >= 0 for v in got.values())


def test_payment_splits_are_exact_nonnegative_and_settle_each_person(session, duo):
    ana, bia, pa, pb, c = duo
    rng = random.Random(11)
    for _ in range(15):  # até 45 unidades de cada produto, dentro do estoque de 50
        sale = sell(session, [(pa, rng.randint(1, 3)), (pb, rng.randint(1, 3))],
                    discount_cents=rng.randint(0, 999), customer_id=c.id, due_date=TODAY + timedelta(days=9))
        shares = own.sale_shares(sale)
        left = sale.total_cents
        while left > 0:
            part = rng.randint(1, left) if rng.random() < .7 else left
            pay.register_payment(session, sale.id, part, "pix"); left -= part
        session.refresh(sale)
        splits = own.payment_splits(sale, shares)
        got = {k: sum(p[k] for _, p in splits) for k in shares}
        assert got == shares                                              # quitada: cada pessoa recebeu exatamente a sua parte
        assert all(sum(p.values()) == pay_.amount_cents and min(p.values()) >= 0 for pay_, p in splits)  # cada pagamento fecha


def test_partial_state_sums_to_the_real_totals_to_the_cent(session, duo):
    """O caso que dava R$ 609,41 contra R$ 609,40: a soma das pessoas tem de bater com o geral."""
    ana, bia, pa, pb, c = duo
    odd = pvc.create_product(session, pvc.ProductInput(name="Quebrado", price_cents=33333, initial_stock=9, owner_id=bia.id))
    sale = sell(session, [(pa, 1), (odd, 1)], customer_id=c.id, paid_cents=5001, payment_method="pix", due_date=TODAY + timedelta(days=3))
    shares = own.sale_shares(sale)
    paid = {k: sum(p[k] for _, p in own.payment_splits(sale, shares)) for k in shares}
    assert sum(paid.values()) == 5001
    assert sum(shares[k] - paid[k] for k in shares) == sale.total_cents - 5001


def test_mixed_sale_with_discount_is_split_proportionally(session, duo):
    ana, bia, pa, pb, c = duo
    sale = sell(session, [(pa, 1), (pb, 2)], discount_cents=2000, customer_id=c.id, due_date=TODAY)   # 200,00 - 20,00
    assert own.sale_shares(sale) == {ana.id: 9000, bia.id: 9000}
    assert sum(own.sale_shares(sale).values()) == sale.total_cents == 18000


def test_shares_always_sum_to_total_with_awkward_numbers(session, duo):
    ana, bia, pa, pb, c = duo
    sale = sell(session, [(pa, 1), (pb, 1)], discount_percent=__import__("decimal").Decimal("33.33"), customer_id=c.id, due_date=TODAY)
    assert sum(own.sale_shares(sale).values()) == sale.total_cents


# ── painéis ─────────────────────────────────────────────────────────────────
@pytest.fixture
def scenario(session, duo):
    ana, bia, pa, pb, c = duo
    s1 = sell(session, [(pa, 1), (pb, 2)], customer_id=c.id, paid_cents=10000, payment_method="pix", due_date=TODAY + timedelta(days=5))
    s2 = sell(session, [(pa, 1)], discount_percent=__import__("decimal").Decimal("10"), paid_cents=9000, payment_method="dinheiro")
    s3 = sell(session, [(pb, 1)], customer_id=c.id, due_date=TODAY + timedelta(days=1))
    s3.due_date = TODAY - timedelta(days=1)   # venceu ontem
    session.commit()
    return ana, bia, pa, pb, c, (s1, s2, s3)


def test_each_person_panel_shows_only_their_part(session, scenario):
    ana, bia, *_ = scenario
    a = dashboard.build_for_owner(session, TODAY, ana.id)
    b = dashboard.build_for_owner(session, TODAY, bia.id)
    assert (a.sold_today, a.sales_today) == (100_00 + 90_00, 2)
    assert a.received_today == 50_00 + 90_00 and a.receivable == 50_00 and a.overdue == 0
    assert (b.sold_today, b.sales_today) == (100_00 + 50_00, 2)
    assert b.received_today == 50_00 and b.receivable == 50_00 + 50_00 and b.overdue == 50_00
    assert [s.remaining_cents for s in b.overdue_sales] == [50_00] and b.overdue_customers == 1
    assert [p.product_name for p in a.top_products] == ["Produto da Ana"] and a.top_products[0].qty == 2
    assert [r.pending for r in b.debtors] == [100_00] and [r.overdue for r in b.debtors] == [50_00]


def test_general_panel_equals_sum_of_people_when_amounts_divide_evenly(session, scenario):
    ana, bia, *_ = scenario
    g = dashboard.build(session, TODAY)
    a, b = (dashboard.build_for_owner(session, TODAY, o.id) for o in (ana, bia))
    for attr in ("sold_today", "sold_month", "received_today", "received_month", "receivable", "overdue"):
        assert getattr(g, attr) == getattr(a, attr) + getattr(b, attr), attr
    assert [r.owner.name for r in g.by_owner] == ["Ana", "Bia"]


def test_cancelled_sale_and_voided_payment_leave_the_panels(session, scenario):
    ana, bia, pa, pb, c, (s1, s2, s3) = scenario
    sale_svc.cancel_sale(session, s2.id, "erro")
    first = session.scalars(select(Payment).where(Payment.sale_id == s1.id)).one()
    pay.void_payment(session, first.id)
    a = dashboard.build_for_owner(session, TODAY, ana.id)
    assert a.sold_today == 100_00 and a.received_today == 0 and a.receivable == 100_00


def test_later_payments_are_attributed_by_date(session, scenario):
    ana, bia, pa, pb, c, (s1, *_) = scenario
    pay.register_payment(session, s1.id, 100_00, "pix")
    a = dashboard.build_for_owner(session, TODAY, ana.id)
    assert a.receivable == 0 and a.received_today == 100_00 + 90_00    # parte da Ana na venda 1 quitada


def test_changing_product_owner_does_not_rewrite_history(session, scenario):
    ana, bia, pa, pb, c, (s1, *_) = scenario
    pvc.update_product(session, pa.id, pvc.ProductInput(name="Produto da Ana", price_cents=10000, owner_id=bia.id))
    assert {i.owner_id for i in session.scalars(select(SaleItem).where(SaleItem.product_id == pa.id))} == {ana.id}
    a = dashboard.build_for_owner(session, TODAY, ana.id)
    assert a.sold_today == 190_00                                       # vendas antigas continuam da Ana
    s = sell(session, [(pa, 1)], paid_cents=10000, payment_method="pix")  # a nova já é da Bia
    assert own.sale_shares(s) == {bia.id: 10000}


# ── cadastro ────────────────────────────────────────────────────────────────
def test_product_needs_an_owner_once_people_exist(session, duo):
    with pytest.raises(BusinessError, match="de quem é o produto"):
        pvc.create_product(session, pvc.ProductInput(name="Sem dono", price_cents=100))
    with pytest.raises(BusinessError, match="pessoa válida"):
        pvc.create_product(session, pvc.ProductInput(name="Dono falso", price_cents=100, owner_id=9999))


def test_product_without_people_registered_is_allowed(session, make_product):
    assert make_product("Livre").owner_id is None


def test_inactive_person_cannot_receive_new_products_but_keeps_old(session, duo):
    ana, bia, pa, pb, c = duo
    own.set_owner_active(session, ana.id, False)
    with pytest.raises(BusinessError, match="pessoa válida"):
        pvc.create_product(session, pvc.ProductInput(name="Novo", price_cents=100, owner_id=ana.id))
    pvc.update_product(session, pa.id, pvc.ProductInput(name="Produto da Ana", price_cents=12000, owner_id=ana.id))  # edição ok


def test_owner_names_are_unique_and_validated(session):
    own.create_owner(session, "Ana")
    with pytest.raises(BusinessError, match="Já existe"):
        own.create_owner(session, " ana ")
    with pytest.raises(BusinessError, match="Informe o nome"):
        own.create_owner(session, "  ")
    with pytest.raises(BusinessError, match="no máximo 80"):
        own.create_owner(session, "x" * 81)
    bia = own.create_owner(session, "Bia")
    with pytest.raises(BusinessError, match="Já existe"):
        own.rename_owner(session, bia.id, "ANA")
    assert own.rename_owner(session, bia.id, "Beatriz").name == "Beatriz"


def test_products_without_owner_still_show_up_in_general_panel(session, make_product, duo):
    ana, bia, pa, pb, c = duo
    loose = make_product("Antigo sem dono", price=3000, owner_id=ana.id)
    loose.owner_id = None  # produto de antes do cadastro das pessoas
    session.commit()
    sell(session, [(loose, 1)], paid_cents=3000, payment_method="pix")
    g = dashboard.build(session, TODAY)
    assert g.sold_today == 3000
    assert g.by_owner[-1].owner is None and g.by_owner[-1].acc.sold_today == 3000


# ── relatórios por pessoa ───────────────────────────────────────────────────
def test_report_by_person_matches_panels(session, scenario):
    from app.services import reports
    r = reports.build(session, "pessoas", reports.ReportParams(), TODAY)
    by = {row["name"]: row for row in r.rows}
    assert by["Ana"]["sold"] == 190_00 and by["Ana"]["cost"] == 2 * 40_00 and by["Ana"]["margin"] == 190_00 - 80_00
    assert by["Ana"]["received"] == 140_00 and by["Ana"]["receivable"] == 50_00
    assert by["Bia"]["sold"] == 150_00 and by["Bia"]["cost"] == 3 * 20_00 and by["Bia"]["received"] == 50_00
    assert by["Bia"]["receivable"] == 100_00
    assert r.totals["sold"] == 340_00 and r.totals["received"] == 190_00 and r.totals["receivable"] == 150_00


def test_product_and_stock_reports_filter_by_person(session, scenario):
    from app.services import reports
    ana, bia, pa, pb, *_ = scenario
    r = reports.build(session, "produtos", reports.ReportParams(owner_id=bia.id), TODAY)
    assert [row["name"] for row in r.rows] == ["Produto da Bia"] and r.totals["qty"] == 3
    r = reports.build(session, "estoque", reports.ReportParams(owner_id=ana.id), TODAY)
    assert [row["name"] for row in r.rows] == ["Produto da Ana"]


def test_general_panel_equals_sum_of_people_to_the_cent_with_awkward_amounts(session, duo):
    """Valores que não dividem certo + pagamentos parciais em dias diferentes: o geral nunca difere das pessoas."""
    from decimal import Decimal
    ana, bia, pa, pb, c = duo
    odd = pvc.create_product(session, pvc.ProductInput(name="Quebrado", price_cents=33333, initial_stock=99, owner_id=bia.id))
    rng = random.Random(3)
    for _ in range(12):
        s = sell(session, [(pa, rng.randint(1, 2)), (odd, rng.randint(1, 3)), (pb, 1)],
                 discount_percent=Decimal(str(rng.choice([0, 3.7, 12.5]))), customer_id=c.id,
                 due_date=TODAY + timedelta(days=rng.randint(1, 9)))
        s.due_date = TODAY - timedelta(days=rng.randint(0, 4))  # parte vencida, parte vence hoje
        session.commit()
        for _ in range(rng.randint(0, 2)):
            left = s.total_cents - sum(p.amount_cents for p in s.payments if not p.voided)
            if left > 1:
                pay.register_payment(session, s.id, rng.randint(1, left - 1), "pix")
            session.refresh(s)
    g = dashboard.build(session, TODAY)
    a, b = (dashboard.build_for_owner(session, TODAY, o.id) for o in (ana, bia))
    for attr in ("sold_today", "sold_month", "received_today", "received_month", "receivable", "overdue", "due_today"):
        assert getattr(g, attr) == getattr(a, attr) + getattr(b, attr), attr
