"""Produtos com tamanhos (P, M, G, GG): estoque por tamanho, venda por tamanho."""
import threading

import pytest
from sqlalchemy import func, select

from app.errors import BusinessError
from app.models import Product, ProductSize, Sale, StockMovement
from app.services import products as pvc
from app.services import sales as sale_svc
from app.services import stock
from app.services.products import ProductInput
from app.services.sales import ItemInput, SaleInput

from .test_web import app, client, db, post, post_json  # noqa: F401  (fixtures da web)


def make(session, name="Camiseta", sizes=("P", "M", "G", "GG"), by=None, **kw):
    by = by or {s: 5 for s in sizes}
    return pvc.create_product(session, ProductInput(name=name, price_cents=5000, cost_cents=2000, sizes=list(sizes),
                                                    initial_by_size=dict(by), **kw))


def sell(session, product, qty=1, size=None, **kw):
    kw.setdefault("paid_cents", product.price_cents * qty)
    kw.setdefault("payment_method", "pix")
    return sale_svc.create_sale(session, SaleInput(items=[ItemInput(product.id, qty, size=size)], **kw))


def stocks(session, product):
    session.expire_all()
    return {s.size: s.stock_qty for s in session.get(Product, product.id).sizes}


def assert_consistent(session, product_id):
    """Soma dos tamanhos = saldo do produto; soma das movimentações de cada tamanho = saldo dele."""
    session.expire_all()
    p = session.get(Product, product_id)
    assert sum(s.stock_qty for s in p.sizes) == p.stock_qty
    for s in p.sizes:
        moved = session.scalar(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.product_id == product_id, StockMovement.size == s.size))
        assert moved == s.stock_qty, s.size


# ── cadastro ────────────────────────────────────────────────────────────────
def test_create_with_sizes_keeps_one_stock_per_size_in_canonical_order(session):
    p = make(session, sizes=("GG", "P", "M"), by={"P": 2, "M": 3, "GG": 4})
    assert [s.size for s in p.sizes] == ["P", "M", "GG"]
    assert stocks(session, p) == {"P": 2, "M": 3, "GG": 4} and session.get(Product, p.id).stock_qty == 9
    assert_consistent(session, p.id)
    first = session.scalars(select(StockMovement).where(StockMovement.product_id == p.id).order_by(StockMovement.id)).first()
    assert first.kind == "inicial" and first.size == "P" and first.balance_after == 2


def test_invalid_size_is_refused(session):
    with pytest.raises(BusinessError, match="Tamanho inválido: XL"):
        make(session, sizes=("P", "XL"))


def test_price_and_cost_are_the_same_for_every_size(session):
    p = make(session)
    assert p.price_cents == 5000 and not hasattr(p.sizes[0], "price_cents")


# ── venda ───────────────────────────────────────────────────────────────────
def test_sale_needs_a_size_and_only_that_size_goes_down(session):
    p = make(session)
    with pytest.raises(BusinessError, match="Escolha o tamanho"):
        sell(session, p)
    with pytest.raises(BusinessError, match="não tem o tamanho XL"):
        sell(session, p, size="XL")
    s = sell(session, p, qty=2, size="M")
    assert s.items[0].size == "M" and s.items[0].label == "Camiseta · M"
    assert stocks(session, p) == {"P": 5, "M": 3, "G": 5, "GG": 5} and session.get(Product, p.id).stock_qty == 18
    assert_consistent(session, p.id)


def test_size_stock_is_checked_per_size_not_in_total(session):
    p = make(session, by={"P": 5, "M": 1, "G": 5, "GG": 5})
    with pytest.raises(BusinessError, match=r"no tamanho M: restam 1"):
        sell(session, p, qty=2, size="M")                       # o produto tem 16 no total, mas M só tem 1
    assert stocks(session, p)["M"] == 1


def test_same_product_and_size_twice_in_one_sale_is_checked_together(session):
    p = make(session, by={"P": 3, "M": 3, "G": 3, "GG": 3})
    data = SaleInput(items=[ItemInput(p.id, 2, size="P"), ItemInput(p.id, 2, size="P")], paid_cents=20000, payment_method="pix")
    with pytest.raises(BusinessError, match="restam 3"):
        sale_svc.create_sale(session, data)
    assert stocks(session, p)["P"] == 3 and session.scalar(select(func.count()).select_from(Sale)) == 0


def test_two_sizes_of_the_same_product_in_one_sale(session):
    p = make(session)
    s = sale_svc.create_sale(session, SaleInput(items=[ItemInput(p.id, 1, size="P"), ItemInput(p.id, 2, size="GG")],
                                                paid_cents=15000, payment_method="pix"))
    assert [i.size for i in s.items] == ["P", "GG"] and stocks(session, p) == {"P": 4, "M": 5, "G": 5, "GG": 3}
    assert_consistent(session, p.id)


def test_product_without_sizes_refuses_a_size(session, make_product):
    p = make_product("Boné", stock=5)
    with pytest.raises(BusinessError, match="não tem tamanhos"):
        sell(session, p, size="M")
    assert sell(session, p).items[0].size is None


def test_cancel_returns_stock_to_the_same_size(session):
    p = make(session)
    s = sale_svc.create_sale(session, SaleInput(items=[ItemInput(p.id, 2, size="G"), ItemInput(p.id, 1, size="P")],
                                                paid_cents=15000, payment_method="pix"))
    sale_svc.cancel_sale(session, s.id, "teste")
    assert stocks(session, p) == {"P": 5, "M": 5, "G": 5, "GG": 5} and session.get(Product, p.id).stock_qty == 20
    assert_consistent(session, p.id)


def test_last_unit_of_a_size_is_sold_only_once_under_concurrency(database, session):
    p = make(session, by={"P": 1, "M": 5, "G": 5, "GG": 5})
    product_id, outcomes = p.id, []

    def worker():
        s = database.session()
        try:
            sell(s, s.get(Product, product_id), size="P")
            outcomes.append("ok")
        except BusinessError:
            outcomes.append("sem estoque")
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert outcomes.count("ok") == 1
    session.rollback()
    assert stocks(session, p)["P"] == 0
    assert_consistent(session, product_id)


# ── estoque: compras, contagem, exclusão ────────────────────────────────────
def test_purchase_and_count_are_per_size(session):
    p = make(session)
    with pytest.raises(BusinessError, match="Escolha o tamanho"):
        stock.register_entry(session, p.id, 4)
    stock.register_entry(session, p.id, 4, size="M")
    assert stocks(session, p)["M"] == 9
    stock.adjust_to_count(session, p.id, 2, size="G")
    assert stocks(session, p)["G"] == 2 and session.get(Product, p.id).stock_qty == 5 + 9 + 2 + 5
    with pytest.raises(BusinessError, match="já está com essa quantidade"):
        stock.adjust_to_count(session, p.id, 2, size="G")
    assert_consistent(session, p.id)


def test_voiding_a_purchase_uses_the_size_stock(session):
    p = make(session, by={"P": 0, "M": 0, "G": 0, "GG": 0})
    entry = stock.register_entry(session, p.id, 3, size="M")
    stock.register_entry(session, p.id, 10, size="G")           # o total (13) cobriria, mas o M não
    sell(session, p, qty=2, size="M")
    with pytest.raises(BusinessError, match="só restam 1 no tamanho M"):
        stock.void_entry(session, entry.id)
    sell(session, p, qty=1, size="M")
    p2 = make(session, name="Outra", by={"P": 0, "M": 0, "G": 0, "GG": 0})
    e2 = stock.register_entry(session, p2.id, 3, size="G")
    stock.void_entry(session, e2.id)
    assert stocks(session, p2)["G"] == 0
    assert_consistent(session, p2.id)


# ── editar tamanhos ─────────────────────────────────────────────────────────
def edit(session, product, sizes, **kw):
    return pvc.update_product(session, product.id, ProductInput(name=product.name, price_cents=product.price_cents,
                                                                cost_cents=product.cost_cents, sizes=list(sizes), **kw))


def test_can_add_a_size_and_can_drop_one_only_when_empty(session):
    p = make(session, sizes=("P", "M"), by={"P": 2, "M": 0})
    edit(session, p, ("P", "M", "G"))
    assert stocks(session, p) == {"P": 2, "M": 0, "G": 0}
    with pytest.raises(BusinessError, match="tamanho P ainda tem 2"):
        edit(session, p, ("M", "G"))
    edit(session, p, ("P", "G"))                                 # M estava zerado: pode sair
    assert set(stocks(session, p)) == {"P", "G"}
    assert_consistent(session, p.id)


def test_starting_to_use_sizes_on_a_product_with_loose_stock_is_refused(session, make_product):
    p = make_product("Antigo", stock=7)
    with pytest.raises(BusinessError, match="Zere o estoque"):
        edit(session, p, ("P", "M"))
    stock.adjust_to_count(session, p.id, 0)
    edit(session, p, ("P", "M"))
    assert set(stocks(session, p)) == {"P", "M"}


def test_sizes_without_stock_control_still_require_a_size_but_move_nothing(session):
    p = make(session, by={}, track_stock=False)
    with pytest.raises(BusinessError, match="Escolha o tamanho"):
        sell(session, p)
    sell(session, p, qty=40, size="G")
    assert stocks(session, p)["G"] == 0 and session.scalar(select(func.count()).select_from(StockMovement)) == 0


def test_deleting_an_unsold_product_removes_its_sizes(session):
    p = make(session)
    pid = p.id
    pvc.delete_product(session, pid)
    assert session.scalar(select(func.count()).select_from(ProductSize).where(ProductSize.product_id == pid)) == 0


# ── telas ───────────────────────────────────────────────────────────────────
def test_form_creates_sizes_and_stock_and_pages_show_them(client, db):
    r = post(client, "/produtos/novo", {"name": "Camiseta", "price": "50,00", "cost": "20,00", "track_stock": "on",
                                        "size": ["P", "M"], "initial_stock_P": "3", "initial_stock_M": "4", "initial_stock": "99"})
    assert r.status_code == 302
    db.rollback()
    p = db.scalar(select(Product).where(Product.name == "Camiseta"))
    assert {s.size: s.stock_qty for s in p.sizes} == {"P": 3, "M": 4} and p.stock_qty == 7     # o estoque "sem tamanho" é ignorado
    page = client.get(f"/produtos/{p.id}").get_data(as_text=True)
    assert "P 3" in page and "M 4" in page and "Qual tamanho?" in page
    assert "P 3" in client.get("/produtos").get_data(as_text=True)
    api = client.get("/api/produtos?q=Camis").get_json()[0]
    assert api["sizes"] == [{"size": "P", "stock": 3}, {"size": "M", "stock": 4}]
    form = client.get("/produtos/novo").get_data(as_text=True)
    assert 'name="size" value="GG"' in form and "Preço e custo são os mesmos em todos os tamanhos" in form


def test_form_rejects_unknown_size(client, db):
    r = post(client, "/produtos/novo", {"name": "X", "price": "10", "size": ["P", "XXL"]})
    assert r.status_code == 422 and "Tamanho inválido" in r.get_data(as_text=True)


def test_sale_over_json_with_size_and_receipt_and_stock_update(client, db):
    post(client, "/produtos/novo", {"name": "Camiseta", "price": "50,00", "track_stock": "on", "size": ["P", "M"],
                                    "initial_stock_P": "3", "initial_stock_M": "4"})
    db.rollback()
    p = db.scalar(select(Product).where(Product.name == "Camiseta"))
    bad = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 1}], "paid": "50", "payment_method": "pix"})
    assert bad.status_code == 422 and "Escolha o tamanho" in bad.get_json()["message"]
    ok = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "size": "M", "quantity": 2}], "paid": "100", "payment_method": "pix"})
    url = ok.get_json()["redirect"]
    assert "Camiseta · M" in client.get(url).get_data(as_text=True)
    assert "Camiseta · M" in client.get(url + "/comprovante").get_data(as_text=True)
    db.rollback()
    assert {s.size: s.stock_qty for s in db.get(Product, p.id).sizes} == {"P": 3, "M": 2}
    assert post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "size": 7, "quantity": 1}], "paid": "50",
                                              "payment_method": "pix"}).status_code == 422
    # estoque por tamanho na página do produto
    post(client, f"/produtos/{p.id}/estoque", {"kind": "entrada", "quantity": "5", "size": "P"})
    db.rollback()
    assert {s.size: s.stock_qty for s in db.get(Product, p.id).sizes} == {"P": 8, "M": 2}


def test_redo_cancelled_sale_keeps_the_size(client, db):
    post(client, "/produtos/novo", {"name": "Camiseta", "price": "50,00", "track_stock": "on", "size": ["P", "M"],
                                    "initial_stock_P": "3", "initial_stock_M": "4"})
    db.rollback()
    p = db.scalar(select(Product).where(Product.name == "Camiseta"))
    url = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "size": "M", "quantity": 1}], "paid": "50",
                                             "payment_method": "pix"}).get_json()["redirect"]
    post(client, url + "/cancelar", {"reason": "teste"})
    page = client.get(f"/vendas/nova?refazer={url.rsplit('/', 1)[1]}").get_data(as_text=True)
    assert '"size": "M"' in page.replace("&#34;", '"')


def test_reports_split_sales_by_size_and_stock_report_lists_sizes(client, db):
    from app.services import reports
    from app.services.reports import ReportParams
    from .conftest import TODAY
    post(client, "/produtos/novo", {"name": "Camiseta", "price": "50,00", "track_stock": "on", "size": ["P", "M"],
                                    "initial_stock_P": "3", "initial_stock_M": "4"})
    db.rollback()
    p = db.scalar(select(Product).where(Product.name == "Camiseta"))
    for size, qty in (("P", 1), ("M", 2), ("M", 1)):
        post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "size": size, "quantity": qty}], "paid": str(50 * qty), "payment_method": "pix"})
    db.rollback()
    rows = {r["name"]: r["qty"] for r in reports.build(db, "produtos", ReportParams(), TODAY).rows}
    assert rows == {"Camiseta · P": 1, "Camiseta · M": 3}
    stock_rows = reports.build(db, "estoque", ReportParams(), TODAY).rows
    assert stock_rows[0]["sizes"] == "P 2 · M 1"
