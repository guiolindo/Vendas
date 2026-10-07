"""Editar e excluir: cada exclusão tem regra de segurança, deixa rastro e não quebra os números."""
import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.errors import BusinessError
from app.models import AuditLog, Owner, Payment, Product, Sale, SaleItem, StockMovement
from app.services import customers as csvc
from app.services import dashboard
from app.services import owners as own
from app.services import payments as pay
from app.services import products as pvc
from app.services import sales as sale_svc
from app.services import stock
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY, owned, create_sale_for
from .test_web import app, client, db, post, post_json  # noqa: F401  (fixtures)


def sell(session, product, qty=1, **kw):
    kw.setdefault("paid_cents", product.price_cents * qty)
    kw.setdefault("payment_method", "pix")
    return create_sale_for(session, SaleInput(items=[ItemInput(product.id, qty)], **kw))


def movement_sum(session, product):
    return session.scalar(select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(StockMovement.product_id == product.id))


# ── excluir venda ───────────────────────────────────────────────────────────
def test_only_a_cancelled_sale_can_be_deleted(session, product):
    s = sell(session, product, 2)
    with pytest.raises(BusinessError, match="Cancele primeiro"):
        sale_svc.delete_sale(session, s.id)
    assert session.get(Sale, s.id) is not None and session.get(Product, product.id).stock_qty == 98


def test_deleting_a_cancelled_sale_removes_it_and_keeps_stock_consistent(session, product):
    keep = sell(session, product, 1)
    gone = sell(session, product, 3)
    sale_svc.cancel_sale(session, gone.id, "lançada errado")
    summary = sale_svc.delete_sale(session, gone.id)
    assert f"#{gone.id}" in summary and "itens=1" in summary and "pagamentos=1" in summary and "lançada errado" in summary
    assert session.get(Sale, gone.id) is None
    assert session.scalar(select(func.count()).select_from(SaleItem).where(SaleItem.sale_id == gone.id)) == 0
    assert session.scalar(select(func.count()).select_from(Payment).where(Payment.sale_id == gone.id)) == 0
    assert session.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.sale_id == gone.id)) == 0
    session.refresh(product)
    assert product.stock_qty == 99 == movement_sum(session, product)                 # estoque = soma das movimentações, sempre
    assert session.get(Sale, keep.id) is not None                                    # a outra venda ficou intacta
    d = dashboard.build(session, TODAY)
    assert d.sold_today == product.price_cents and d.received_today == product.price_cents


def test_delete_unknown_sale(session):
    with pytest.raises(BusinessError, match="não encontrada"):
        sale_svc.delete_sale(session, 424242)


# ── excluir pessoa ──────────────────────────────────────────────────────────
def test_owner_can_be_deleted_only_when_unused(session):
    free, busy = own.create_owner(session, "Livre"), own.create_owner(session, "Ocupada")
    p = pvc.create_product(session, pvc.ProductInput(name="X", price_cents=100, initial_stock=5))
    assert own.delete_owner(session, free.id) == "Livre" and session.get(Owner, free.id) is None
    sell(session, p, 1, seller_id=busy.id)
    with pytest.raises(BusinessError, match="1 venda"):                              # tem venda no histórico: só desativar
        own.delete_owner(session, busy.id)


# ── telas ───────────────────────────────────────────────────────────────────
@pytest.fixture
def shop(client, db):
    ana = own.create_owner(db, "Ana")
    p = owned(pvc.create_product(db, pvc.ProductInput(name="Vestido", price_cents=10000, cost_cents=4000, initial_stock=10)), ana)
    gone = owned(pvc.create_product(db, pvc.ProductInput(name="Fora de linha", price_cents=5000, cost_cents=1000, initial_stock=10)), ana)
    c = csvc.create_customer(db, csvc.CustomerInput(name="Marina"))
    return ana, p, gone, c


def new_sale(client, shop, items=None, **extra):
    ana, p, gone, c = shop
    items = items or [{"product_id": p.id, "quantity": 2, "price": "90"}]
    r = post_json(client, "/vendas/nova", {"items": items, "customer_id": c.id, "paid": "50", "payment_method": "pix", "seller_id": ana.id,
                                           "due_date": (TODAY + timedelta(days=9)).isoformat(), "notes": "entregar sexta", **extra})
    return int(r.get_json()["redirect"].rsplit("/", 1)[1])


def test_fix_cancels_and_opens_pos_prefilled(client, db, shop):
    ana, p, gone, c = shop
    sid = new_sale(client, shop, [{"product_id": p.id, "quantity": 2, "price": "90"}, {"product_id": gone.id, "quantity": 1}], discount="5")
    r = post(client, f"/vendas/{sid}/corrigir")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/vendas/nova?refazer={sid}")
    db.rollback()
    assert db.get(Sale, sid).cancelled and db.get(Sale, sid).cancel_reason.startswith("Corrigida")
    assert db.get(Product, p.id).stock_qty == 10                                   # o estoque voltou
    page = client.get(r.headers["Location"], follow_redirects=True).get_data(as_text=True)
    assert "Refazendo a venda" in page and "cancelada para correção" in page and "desfeitos" in page
    data = json.loads(re.search(r"data-prefill='([^']*)'", page).group(1).replace("&#34;", '"').replace("&#39;", "'"))
    by = {i["name"]: i for i in data["items"]}
    assert by["Vestido"]["qty"] == 2 and by["Vestido"]["price"] == 9000           # o preço combinado (90) foi mantido
    assert by["Fora de linha"]["price"] is None                                    # preço de tabela: sem sobrescrever
    assert data["customer"]["name"] == "Marina" and data["discount"] == "5,00" and data["notes"] == "entregar sexta"


def test_redo_skips_items_without_stock_and_says_so(client, db, shop):
    ana, p, gone, c = shop
    sid = new_sale(client, shop, [{"product_id": p.id, "quantity": 1}, {"product_id": gone.id, "quantity": 10}], paid="0")
    pvc.set_active(db, gone.id, False)
    post(client, f"/vendas/{sid}/cancelar", {"reason": "teste"})
    page = client.get(f"/vendas/nova?refazer={sid}").get_data(as_text=True)
    assert "1 item ficou de fora" in page
    assert [i["name"] for i in json.loads(re.search(r"data-prefill='([^']*)'", page).group(1).replace("&#34;", '"'))["items"]] == ["Vestido"]


def test_redo_only_works_for_cancelled_sales_and_junk_ids(client, shop):
    sid = new_sale(client, shop)
    for arg in (str(sid), "lixo", "99999999999999", "-1", ""):
        assert "Refazendo a venda" not in client.get(f"/vendas/nova?refazer={arg}").get_data(as_text=True), arg


def test_delete_sale_through_the_web_is_audited_and_requires_cancel(client, db, shop):
    sid = new_sale(client, shop)
    r = post(client, f"/vendas/{sid}/excluir", follow_redirects=True)
    assert "Só dá para excluir uma venda que já foi cancelada" in r.get_data(as_text=True)
    post(client, f"/vendas/{sid}/cancelar", {"reason": "teste"})
    detail = client.get(f"/vendas/{sid}").get_data(as_text=True)
    assert "Refazer esta venda" in detail and "Excluir definitivamente" in detail
    r = post(client, f"/vendas/{sid}/excluir", follow_redirects=True)
    assert f"Venda #{sid} excluída definitivamente" in r.get_data(as_text=True)
    assert client.get(f"/vendas/{sid}", follow_redirects=True).status_code == 200
    db.rollback()
    assert db.get(Sale, sid) is None
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "venda_excluida"))
    assert audit and f"#{sid}" in audit.detail and "teste" in audit.detail
    assert client.post(f"/vendas/{sid}/excluir", data={}).status_code in (302, 400)   # sem CSRF


def test_active_sale_page_offers_fix_and_cancel_but_not_delete(client, shop):
    detail = client.get(f"/vendas/{new_sale(client, shop)}").get_data(as_text=True)
    assert "Corrigir e refazer" in detail and "Cancelar esta venda" in detail and "Excluir definitivamente" not in detail


def test_purchase_screens_and_void_route(client, db, shop):
    ana, p, gone, c = shop
    r = post(client, f"/produtos/{p.id}/estoque", {"kind": "entrada", "quantity": "5", "unit_cost": "52,00", "update_cost": "on", "note": "lote B"},
             follow_redirects=True)
    assert "Compra de 5 registrada e custo do produto atualizado" in r.get_data(as_text=True)
    page = client.get(f"/produtos/{p.id}").get_data(as_text=True)
    for needle in ("Histórico de custo", "R$ 52,00", "total R$ 260,00", "Já gasto em compras", "Excluir compra", "Compra", "lote B"):
        assert needle in page, needle
    db.rollback()
    entry = db.scalar(select(StockMovement).where(StockMovement.kind == "entrada"))
    r = post(client, f"/produtos/compras/{entry.id}/excluir", {"reason": "engano"}, follow_redirects=True)
    assert "Compra excluída" in r.get_data(as_text=True)
    page = client.get(f"/produtos/{p.id}").get_data(as_text=True)
    assert "Excluída" in page and "engano" in page and "Compra excluída" in page
    db.rollback()
    assert db.get(Product, p.id).stock_qty == 10 and db.get(Product, p.id).cost_cents == 4000
    r = post(client, f"/produtos/compras/{entry.id}/excluir", follow_redirects=True)
    assert "já foi excluída" in r.get_data(as_text=True)
    actions = [a.action for a in db.scalars(select(AuditLog))]
    assert "compra_registrada" in actions and "compra_excluida" in actions


def test_edit_form_warns_that_cost_and_price_changes_are_forward_only(client, shop):
    ana, p, gone, c = shop
    edit = client.get(f"/produtos/{p.id}/editar").get_data(as_text=True)
    assert "vale só para as próximas vendas" in edit.lower() and "continuam com o custo da época" in edit
    assert "continuam com o custo da época" not in client.get("/produtos/novo").get_data(as_text=True)


def test_editing_cost_in_the_form_changes_only_what_comes_next(client, db, shop):
    ana, p, gone, c = shop
    old = new_sale(client, shop, [{"product_id": p.id, "quantity": 1}], paid="100")
    post(client, f"/produtos/{p.id}/editar", {"name": "Vestido", "price": "100", "cost": "70", "owner": str(ana.id), "unit": "un", "active": "on"})
    new = new_sale(client, shop, [{"product_id": p.id, "quantity": 1}], paid="100")
    margin = lambda sid: re.search(r"Margem desta venda.*?<tfoot>.*?<td class=\"right money\">R\$ [\d.,]+</td><td class=\"right money\">R\$ [\d.,]+</td>\s*<td class=\"right money\">(R\$ [\d.,]+)", client.get(f"/vendas/{sid}").get_data(as_text=True), re.S).group(1)
    assert margin(old) == "R$ 60,00" and margin(new) == "R$ 30,00"                  # R$ 100 - 40  versus  R$ 100 - 70
    hist = client.get(f"/produtos/{p.id}").get_data(as_text=True)
    assert "Edição" in hist and "R$ 70,00" in hist


def test_owner_delete_route(client, db):
    post(client, "/pessoas/nova", {"name": "Provisória"})
    db.rollback()
    oid = db.scalar(select(Owner.id))
    assert "Excluir" in client.get("/pessoas").get_data(as_text=True)
    r = post(client, f"/pessoas/{oid}/excluir", follow_redirects=True)
    assert "Pessoa excluída: Provisória" in r.get_data(as_text=True)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Owner)) == 0
