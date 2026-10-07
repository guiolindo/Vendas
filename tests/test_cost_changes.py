"""Mudar o custo vale DAQUI PRA FRENTE. Compras de mercadoria têm custo e podem ser excluídas com segurança."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.errors import BusinessError
from app.models import CostChange, Product, StockMovement
from app.services import charts, dashboard, margin as mg, reports
from app.services import owners as own
from app.services import payments as pay
from app.services import products as pvc
from app.services import sales as sale_svc
from app.services import stock
from app.services.sales import ItemInput, SaleInput

from .conftest import OWNER_OF, TODAY, create_sale_for, owned


def sell(session, product, qty=1, price=None, **kw):
    kw.setdefault("paid_cents", (price or product.price_cents) * qty)
    kw.setdefault("payment_method", "pix")
    return create_sale_for(session, SaleInput(items=[ItemInput(product.id, qty, price)], **kw))


def edit_cost(session, product, cost, price=None):
    pvc.update_product(session, product.id, pvc.ProductInput(name=product.name, price_cents=price or product.price_cents,
                                                              cost_cents=cost), user_id=None)
    session.refresh(product)


@pytest.fixture
def item(session):
    ana = own.create_owner(session, "Ana")
    return owned(pvc.create_product(session, pvc.ProductInput(name="Vestido", price_cents=10000, cost_cents=4000, initial_stock=50)), ana)


# ── 1. mudar o custo NÃO mexe no passado ────────────────────────────────────
def test_changing_cost_leaves_every_past_statistic_untouched(session, item):
    old = sell(session, item, 2)                                              # vendida com custo R$ 40
    snapshot = lambda: (
        mg.sale_margin(session.get(type(old), old.id)).amount,
        dashboard.build(session, TODAY).margin_month.amount,
        dashboard.build_for_owner(session, TODAY, OWNER_OF[item.id]).margin_month.amount,
        [(p.name, p.amount) for p in charts.build(session, TODAY, None, "mes").margin_products],
        [(r["name"], r["margin"]) for r in reports.build(session, "produtos", reports.ReportParams(), TODAY).rows],
        reports.build(session, "vendas", reports.ReportParams(), TODAY).totals["margin"],
        reports.build(session, "pessoas", reports.ReportParams(), TODAY).totals["margin"],
    )
    before = snapshot()
    assert before[0] == 2 * 10000 - 2 * 4000
    edit_cost(session, item, 7500)                                            # custo sobe para R$ 75
    assert snapshot() == before                                               # NADA do passado mudou
    assert session.get(type(old), old.id).items[0].unit_cost_cents == 4000   # o item guardou o custo da época


def test_new_sales_use_the_new_cost_and_old_ones_keep_theirs(session, item):
    first = sell(session, item, 1)
    edit_cost(session, item, 7500)
    second = sell(session, item, 1)
    edit_cost(session, item, 2000)
    third = sell(session, item, 1)
    assert [mg.sale_margin(s).amount for s in (first, second, third)] == [6000, 2500, 8000]
    assert dashboard.build(session, TODAY).margin_month.amount == 6000 + 2500 + 8000     # o mês soma cada uma pelo custo da época


def test_changing_price_is_also_forward_only(session, item):
    old = sell(session, item, 1)
    edit_cost(session, item, 4000, price=15000)
    assert old.items[0].unit_price_cents == 10000 and mg.sale_margin(old).amount == 6000
    new = sell(session, item, 1)
    assert new.items[0].unit_price_cents == 15000 and mg.sale_margin(new).amount == 11000


def test_cost_set_to_zero_makes_future_sales_uncosted_not_free_money(session, item):
    edit_cost(session, item, 0)
    s = sell(session, item, 1)
    assert mg.sale_margin(s).uncosted == 1 and mg.sale_margin(s).amount == 0


def test_cost_history_records_each_change_once(session, item):
    edit_cost(session, item, 4000)                         # igual: não registra
    edit_cost(session, item, 5000); edit_cost(session, item, 5000); edit_cost(session, item, 4500)
    rows = session.scalars(select(CostChange).where(CostChange.product_id == item.id).order_by(CostChange.id)).all()
    assert [(r.old_cents, r.new_cents, r.origin) for r in rows] == [(None, 4000, "cadastro"), (4000, 5000, "edição"), (5000, 4500, "edição")]


# ── 2. compras de mercadoria com custo ──────────────────────────────────────
def test_purchase_records_cost_and_can_update_the_product_cost(session, item):
    m = stock.register_entry(session, item.id, 10, "fornecedor X", unit_cost_cents=5200, update_cost=True)
    session.refresh(item)
    assert (item.stock_qty, item.cost_cents) == (60, 5200)
    assert (m.unit_cost_cents, m.total_cost_cents, m.cost_updated, m.cost_before_cents) == (5200, 52000, True, 4000)
    history = session.scalars(select(CostChange).where(CostChange.product_id == item.id).order_by(CostChange.id)).all()
    assert history[-1].origin == "compra" and (history[-1].old_cents, history[-1].new_cents) == (4000, 5200)


def test_purchase_without_update_keeps_current_cost_and_without_cost_is_allowed(session, item):
    stock.register_entry(session, item.id, 5, unit_cost_cents=9900, update_cost=False)
    stock.register_entry(session, item.id, 5)                                  # sem custo informado
    session.refresh(item)
    assert item.cost_cents == 4000 and item.stock_qty == 60
    with pytest.raises(BusinessError, match="maior que zero"):
        stock.register_entry(session, item.id, 5, unit_cost_cents=0)


def test_purchases_never_touch_past_sales(session, item):
    old = sell(session, item, 3)
    stock.register_entry(session, item.id, 10, unit_cost_cents=9000, update_cost=True)
    assert mg.sale_margin(old).amount == 3 * 10000 - 3 * 4000


# ── 3. excluir compra ───────────────────────────────────────────────────────
def test_voiding_a_purchase_restores_stock_keeps_the_record_and_reverts_cost(session, item):
    m = stock.register_entry(session, item.id, 10, unit_cost_cents=5200, update_cost=True)
    stock.void_entry(session, m.id, "digitei errado")
    session.refresh(item); session.refresh(m)
    assert item.stock_qty == 50 and item.cost_cents == 4000                      # estoque e custo voltaram
    assert m.voided and m.void_reason == "digitei errado"                       # o registro fica no histórico
    kinds = [x.kind for x in session.scalars(select(StockMovement).where(StockMovement.product_id == item.id).order_by(StockMovement.id))]
    assert kinds == ["inicial", "entrada", "estorno_compra"]
    reversal = session.scalars(select(StockMovement).where(StockMovement.kind == "estorno_compra")).one()
    assert reversal.reverses_id == m.id and reversal.quantity == -10
    assert session.scalar(select(func.sum(StockMovement.quantity)).where(StockMovement.product_id == item.id)) == item.stock_qty


def test_void_keeps_a_cost_somebody_edited_afterwards(session, item):
    m = stock.register_entry(session, item.id, 10, unit_cost_cents=5200, update_cost=True)
    edit_cost(session, item, 6100)                                              # alguém ajustou o custo depois da compra
    stock.void_entry(session, m.id)
    session.refresh(item)
    assert item.cost_cents == 6100                                              # não desfaz a decisão posterior


def test_cannot_void_a_purchase_whose_units_were_already_sold(session, make_product):
    p = make_product("Raro", price=5000, cost=1000, stock=0)
    m = stock.register_entry(session, p.id, 10, unit_cost_cents=1000)
    sell(session, p, 7)                                                         # sobram 3
    with pytest.raises(BusinessError, match="só restam 3"):
        stock.void_entry(session, m.id)
    session.refresh(p)
    assert p.stock_qty == 3 and not session.get(StockMovement, m.id).voided     # nada mudou
    sale = session.scalars(select(sale_svc.Sale)).one()
    sale_svc.cancel_sale(session, sale.id, "teste")                             # as unidades voltam: agora pode
    stock.void_entry(session, m.id)
    session.refresh(p)
    assert p.stock_qty == 0


def test_void_rules(session, item):
    m = stock.register_entry(session, item.id, 5)
    stock.void_entry(session, m.id)
    with pytest.raises(BusinessError, match="já foi excluída"):
        stock.void_entry(session, m.id)
    initial = session.scalars(select(StockMovement).where(StockMovement.kind == "inicial")).one()
    with pytest.raises(BusinessError, match="Só compras"):
        stock.void_entry(session, initial.id)
    with pytest.raises(BusinessError, match="não encontrada"):
        stock.void_entry(session, 99999)


def test_product_with_voided_purchases_can_still_be_deleted_if_never_sold(session, make_product):
    p = make_product("Teste", stock=0)
    m = stock.register_entry(session, p.id, 4, unit_cost_cents=100)
    stock.void_entry(session, m.id)
    pvc.delete_product(session, p.id)                                          # estorno aponta para a compra: apaga na ordem certa
    assert session.get(Product, p.id) is None
    assert session.scalar(select(func.count()).select_from(StockMovement)) == 0
    assert session.scalar(select(func.count()).select_from(CostChange).where(CostChange.product_id == p.id)) == 0


# ── 4. relatório de compras ─────────────────────────────────────────────────
def test_purchases_report_lists_costs_totals_and_skips_voided(session, item):
    stock.register_entry(session, item.id, 10, "lote A", unit_cost_cents=5000)
    bad = stock.register_entry(session, item.id, 3, unit_cost_cents=7000)
    stock.register_entry(session, item.id, 4)                                    # sem custo informado
    stock.void_entry(session, bad.id)
    r = reports.build(session, "compras", reports.ReportParams(), TODAY)
    kinds = [(x["kind"], x["qty"], x["unit_cost"], x["total"]) for x in r.rows]
    assert ("Compra", 10, 5000, 50000) in kinds and ("Compra", 4, None, None) in kinds and ("Estoque inicial", 50, 4000, 200000) in kinds
    assert all(x[1] != 3 for x in kinds)                                         # a compra excluída não aparece
    assert r.totals["qty"] == 64 and r.totals["total"] == 250000
    assert "Compras de mercadoria" in reports.CATALOG["compras"][0]


def test_stock_report_is_honest_that_valuation_uses_the_current_cost(session, item):
    r = reports.build(session, "estoque", reports.ReportParams(), TODAY)
    assert "Valor ao custo atual" in [c.label for c in r.columns]
    r = reports.build(session, "margem-produtos", reports.ReportParams(), TODAY)
    assert "Estoque ao custo atual" in [c.label for c in r.columns]
