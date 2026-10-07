"""Produto sem controle de estoque: vende sem baixar nem exigir quantidade."""
import pytest

from app.errors import BusinessError
from app.models import Product, StockMovement
from app.services import dashboard, reports
from app.services import sales as sale_svc
from app.services import stock
from app.services.reports import ReportParams
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY
from .test_web import anon, app, client, db, post  # noqa: F401  (fixtures da web)


def _sell(session, product, qty=3):
    return sale_svc.create_sale(session, SaleInput(items=[ItemInput(product.id, qty)], paid_cents=product.price_cents * qty,
                                                   payment_method="dinheiro"))


def _movements(session, product):
    return session.query(StockMovement).filter_by(product_id=product.id).count()


def test_sells_without_stock_and_nothing_moves(session, make_product):
    p = make_product(name="Serviço", stock=0, track_stock=False)
    sale = _sell(session, p, qty=50)                      # 50 com estoque 0: não trava
    session.refresh(p)
    assert sale.total_cents == 50 * p.price_cents
    assert p.stock_qty == 0 and _movements(session, p) == 0


def test_tracked_product_still_blocks_when_out_of_stock(session, make_product):
    p = make_product(name="Camisa", stock=2)
    with pytest.raises(BusinessError, match="Estoque insuficiente"):
        _sell(session, p, qty=3)


def test_cancel_sale_of_untracked_product_does_not_create_stock(session, make_product):
    p = make_product(name="Serviço", stock=0, track_stock=False)
    sale = _sell(session, p)
    sale_svc.cancel_sale(session, sale.id, "teste")
    session.refresh(p)
    assert p.stock_qty == 0 and _movements(session, p) == 0


def test_turning_control_on_later_does_not_inflate_stock_when_old_sale_is_cancelled(session, make_product):
    p = make_product(name="Item", stock=0, track_stock=False)
    sale = _sell(session, p, qty=4)
    from app.services import products as psvc
    from app.services.products import ProductInput
    psvc.update_product(session, p.id, ProductInput(name="Item", price_cents=p.price_cents, cost_cents=p.cost_cents, track_stock=True))
    sale_svc.cancel_sale(session, sale.id, "teste")
    session.refresh(p)
    assert p.track_stock and p.stock_qty == 0              # a venda antiga nunca baixou estoque, então nada volta


def test_stock_entry_and_count_are_refused_without_control(session, make_product):
    p = make_product(name="Serviço", stock=0, track_stock=False)
    with pytest.raises(BusinessError, match="sem controle de estoque"):
        stock.register_entry(session, p.id, 5)
    with pytest.raises(BusinessError, match="sem controle de estoque"):
        stock.adjust_to_count(session, p.id, 5)


def test_untracked_products_stay_out_of_low_stock_and_stock_reports(session, make_product):
    make_product(name="Sem controle", stock=0, track_stock=False)
    make_product(name="Controlado", stock=0)
    d = dashboard.build(session, TODAY)
    assert [x.name for x in d.low_stock] == ["Controlado"]
    rep = reports.stock_report(session, ReportParams(), TODAY)
    assert [r["name"] for r in rep.rows] == ["Controlado"]
    assert not any(p.low_stock for p in session.query(Product).filter_by(name="Sem controle"))


def test_margin_still_counts_untracked_products(session, make_product):
    p = make_product(name="Serviço", price=5000, cost=2000, stock=0, track_stock=False)
    _sell(session, p, qty=2)
    assert dashboard.build(session, TODAY).margin_month.amount == 6000


def test_product_form_checkbox_decides_and_setting_sets_the_default(client, db):
    r = post(client, "/produtos/novo", {"name": "Consultoria", "price": "100,00"})          # sem marcar: sem controle
    assert r.status_code == 302
    db.rollback()                                                                           # enxerga o que a web gravou
    assert db.query(Product).filter_by(name="Consultoria").one().track_stock is False
    post(client, "/produtos/novo", {"name": "Caneca", "price": "20,00", "track_stock": "on", "initial_stock": "7"})
    db.rollback()
    caneca = db.query(Product).filter_by(name="Caneca").one()
    assert caneca.track_stock and caneca.stock_qty == 7
    post(client, "/configuracoes", {"business_name": "Loja"})                              # desmarcado: padrão = não controlar
    assert 'id="f-track" checked' not in client.get("/produtos/novo").get_data(as_text=True)
    post(client, "/configuracoes", {"business_name": "Loja", "track_stock": "on"})
    assert 'id="f-track" checked' in client.get("/produtos/novo").get_data(as_text=True)


def test_pages_and_api_for_untracked_product(client, db):
    post(client, "/produtos/novo", {"name": "Consultoria", "price": "100,00"})
    db.rollback()
    p = db.query(Product).filter_by(name="Consultoria").one()
    assert client.get(f"/produtos/{p.id}").status_code == 200
    assert "Sem controle" in client.get(f"/produtos/{p.id}").get_data(as_text=True)
    assert "Sem controle" in client.get("/produtos").get_data(as_text=True)
    assert client.get(f"/produtos/{p.id}/editar").status_code == 200
    assert client.get("/api/produtos?q=Consul").get_json()[0]["stock"] is None


def test_setup_asks_about_stock(anon, db):
    page = anon.get("/configurar").get_data(as_text=True)
    assert "controlar o estoque" in page
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    r = anon.post("/configurar", data={"csrf_token": token, "name": "Ana", "username": "ana", "password": "Senha12345",
                                       "password2": "Senha12345", "stock_mode": "nao"})
    assert r.status_code == 302
    from app.services import settings as s
    db.rollback()
    assert s.load(db)["track_stock"] is False
