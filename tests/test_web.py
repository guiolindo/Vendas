import json
import os
from datetime import timedelta

import pytest

from app import create_app
from app.models import Payment, Product, Sale, User
from app.services import customers as csvc
from app.services import products as pvc

from .conftest import TODAY


@pytest.fixture
def app(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'web.db'}"
    from app.db import Base, Database
    if not url.startswith("sqlite"):
        d = Database(url); Base.metadata.drop_all(d.engine); d.dispose()
    app = create_app({"DATABASE_URL": url, "TESTING": True, "SECRET_KEY": "t", "SESSION_COOKIE_SECURE": False})
    yield app
    app.extensions["database"].dispose()


@pytest.fixture
def db(app):
    s = app.extensions["database"].session()
    yield s
    s.close()


@pytest.fixture
def anon(app):
    return app.test_client()


@pytest.fixture
def client(app, db):
    user = User(username="ana", name="Ana Lima"); user.set_password("senha-forte-1")
    db.add(user); db.commit()
    from app.services import security as sec
    sid = sec.create_session(db, user, None, "pytest")
    c = app.test_client()
    with c.session_transaction() as s:
        s["sid"] = sid
        s["csrf"] = "tok"
    c.sid = sid
    return c


def post(client, url, data=None, **kw):
    return client.post(url, data={**(data or {}), "csrf_token": "tok"}, **kw)


def post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json", headers={"X-CSRF-Token": "tok"})


@pytest.fixture
def catalog(db):
    p = pvc.create_product(db, pvc.ProductInput(name="Camiseta", price_cents=5000, cost_cents=2000, initial_stock=50, min_stock=5))
    c = csvc.create_customer(db, csvc.CustomerInput(name="João da Silva", phone="11 99999-0000"))
    return p, c


def test_requires_login_and_first_run_setup(anon, app):
    assert anon.get("/").headers["Location"].endswith("/configurar")
    anon.get("/configurar")  # o navegador abre a página primeiro (é aí que nasce o token)
    with anon.session_transaction() as s:
        s["csrf"] = "tok"
    r = post(anon, "/configurar", {"name": "Ana", "username": "ana", "password": "curta", "password2": "curta"})
    assert r.status_code == 200 and "pelo menos 8" in r.get_data(as_text=True)
    # sem token CSRF a requisição é recusada
    assert anon.post("/configurar", data={"name": "x"}).status_code == 302  # sem token: recusado
    r = post(anon, "/configurar", {"name": "Ana Lima", "username": "ana", "password": "senha-forte-1", "password2": "senha-forte-1"})
    assert r.status_code == 302
    assert anon.get("/").status_code == 200
    with anon.session_transaction() as s:
        token = s["csrf"]  # o login gerou um token novo
    anon.post("/sair", data={"csrf_token": token})
    assert "/entrar" in anon.get("/").headers["Location"]


def test_login_wrong_and_right_password(app, db):
    u = User(username="bia", name="Bia"); u.set_password("senha-forte-1"); db.add(u); db.commit()
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf"] = "tok"
    bad = post(c, "/entrar", {"username": "bia", "password": "errada"})
    assert "incorretos" in bad.get_data(as_text=True)
    ok = post(c, "/entrar?next=//evil.com", {"username": "BIA", "password": "senha-forte-1"})
    assert ok.status_code == 302 and "evil.com" not in ok.headers["Location"]  # sem open redirect


def test_post_without_csrf_rejected(client, catalog):
    p, c = catalog
    r = client.post(f"/produtos/{p.id}/excluir")
    assert r.status_code == 302
    r = client.post("/vendas/nova", data="{}", content_type="application/json")
    assert r.status_code == 400


def test_every_page_renders_empty_and_filled(client, db, catalog):
    p, c = catalog
    pages = ["/", "/vendas", "/vendas/nova", "/cobrancas", "/cobrancas?aba=abertos", "/clientes", "/produtos",
             "/produtos/novo", "/clientes/novo", "/relatorios", f"/produtos/{p.id}", f"/produtos/{p.id}/editar",
             f"/clientes/{c.id}", f"/clientes/{c.id}/editar", "/api/produtos?q=cam", f"/api/clientes?id={c.id}"]
    for key in ("vendas", "produtos", "clientes", "recebimentos", "a-receber", "estoque", "movimento"):
        pages.append(f"/relatorios/{key}")
    for url in pages:
        assert client.get(url).status_code == 200, url
    # cria uma venda parcial vencida e repete tudo com dados
    r = post_json(client, "/vendas/nova", {
        "items": [{"product_id": p.id, "quantity": 3}], "customer_id": c.id, "paid": "50,00", "payment_method": "pix",
        "due_date": (TODAY + timedelta(days=5)).isoformat(), "client_token": "t1"})
    assert r.status_code == 200, r.get_json()
    sale_id = int(r.get_json()["redirect"].rsplit("/", 1)[1])
    pages += [f"/vendas/{sale_id}", f"/vendas/{sale_id}/editar", "/vendas?status=parcial", "/clientes?mostrar=devendo",
              "/cobrancas?aba=parciais", "/cobrancas?aba=pagos", "/cobrancas?aba=hoje", "/cobrancas?aba=proximos",
              "/produtos?estoque=baixo", "/relatorios/a-receber?situacao=vencido"]
    for url in pages:
        assert client.get(url).status_code == 200, url
    assert "Venda #" in client.get(f"/vendas/{sale_id}").get_data(as_text=True)


def test_full_sale_and_payment_flow(client, db, catalog):
    p, c = catalog
    r = post_json(client, "/vendas/nova", {
        "items": [{"product_id": p.id, "quantity": 10}], "customer_id": c.id, "paid": "200", "payment_method": "pix",
        "due_date": (TODAY + timedelta(days=10)).isoformat()})
    sale_id = int(r.get_json()["redirect"].rsplit("/", 1)[1])
    page = client.get(f"/vendas/{sale_id}").get_data(as_text=True)
    assert "R$ 500,00" in page and "R$ 200,00" in page and "R$ 300,00" in page and "Parcial" in page

    r = post(client, f"/vendas/{sale_id}/pagamentos", {"amount": "400", "method": "pix"}, follow_redirects=True)
    assert "maior que o valor restante (R$ 300,00)" in r.get_data(as_text=True)
    r = post(client, f"/vendas/{sale_id}/pagamentos", {"amount": "150", "method": "dinheiro"}, follow_redirects=True)
    assert "Pagamento de R$ 150,00 registrado." in r.get_data(as_text=True)
    r = post(client, f"/vendas/{sale_id}/pagamentos", {"amount": "150", "method": "pix", "note": "final"}, follow_redirects=True)
    assert "Pago" in r.get_data(as_text=True)
    db.rollback()
    sale = db.get(Sale, sale_id)
    assert sale.status == "pago" and [x.amount_cents for x in sale.payments] == [20000, 15000, 15000]

    r = post(client, f"/pagamentos/{sale.payments[1].id}/estornar", {"reason": "engano"}, follow_redirects=True)
    assert "desfeito" in r.get_data(as_text=True)
    db.rollback()
    assert db.get(Sale, sale_id).status == "parcial"

    r = post(client, f"/vendas/{sale_id}/cancelar", {"reason": "teste"}, follow_redirects=True)
    assert "cancelada" in r.get_data(as_text=True).lower()
    db.rollback()
    assert db.get(Product, p.id).stock_qty == 50


def test_sale_errors_are_friendly_json(client, catalog):
    p, c = catalog
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 2}], "paid": "10", "payment_method": "pix"})
    assert r.status_code == 422 and "Escolha o cliente" in r.get_json()["message"]
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 999}], "customer_id": c.id,
                                           "due_date": TODAY.isoformat()})
    assert "Estoque insuficiente" in r.get_json()["message"]
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 1}], "paid": "abc", "payment_method": "pix"})
    assert r.status_code == 422 and "Valor pago" in r.get_json()["message"]
    r = post_json(client, "/vendas/nova", {"items": []})
    assert "pelo menos um produto" in r.get_json()["message"]


def test_percent_discount_via_api(client, db, catalog):
    p, _ = catalog
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 2}], "discount": "10", "discount_type": "percent",
                                           "paid": "90,00", "payment_method": "pix"})
    assert r.status_code == 200
    db.rollback()
    assert db.query(Sale).one().total_cents == 9000


def test_customer_payment_route_and_quick_create(client, db, catalog):
    p, c = catalog
    for _ in range(2):
        post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 2}], "customer_id": c.id,
                                           "due_date": (TODAY + timedelta(days=3)).isoformat()})
    r = post(client, f"/clientes/{c.id}/receber", {"amount": "150", "method": "pix"}, follow_redirects=True)
    assert "distribuído em 2 vendas" in r.get_data(as_text=True)
    r = post(client, f"/clientes/{c.id}/receber", {"amount": "999", "method": "pix"}, follow_redirects=True)
    assert "total em aberto do cliente (R$ 50,00)" in r.get_data(as_text=True)
    r = post_json(client, "/api/clientes", {"name": "Novo Cliente"})
    assert r.get_json()["ok"] and r.get_json()["customer"]["name"] == "Novo Cliente"
    assert post_json(client, "/api/clientes", {"name": " "}).status_code == 422


def test_forms_validation_messages(client, catalog):
    r = post(client, "/produtos/novo", {"name": "Boné", "price": "abc"})
    assert r.status_code == 422 and "Preço de venda" in r.get_data(as_text=True)
    r = post(client, "/produtos/novo", {"name": "Boné", "price": "39,90", "initial_stock": "5"}, follow_redirects=True)
    assert "Produto “Boné” cadastrado." in r.get_data(as_text=True)
    r = post(client, "/clientes/novo", {"name": ""})
    assert r.status_code == 422 and "Informe o nome" in r.get_data(as_text=True)


def test_csv_export_route(client, catalog):
    r = client.get("/relatorios/estoque?exportar=csv")
    assert r.status_code == 200 and r.mimetype == "text/csv" and "attachment" in r.headers["Content-Disposition"]
    assert r.data.startswith("﻿".encode("utf-8"))


def test_filters_are_remembered_until_cleared(client, catalog):
    client.get("/produtos?q=cam")
    assert "última visita" in client.get("/produtos").get_data(as_text=True)
    assert "última visita" not in client.get("/produtos?limpar=1").get_data(as_text=True)
    assert "última visita" not in client.get("/produtos").get_data(as_text=True)


def test_cobrancas_tabs_count_correctly(client, db, catalog):
    p, c = catalog
    post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 1}], "customer_id": c.id, "due_date": TODAY.isoformat()})
    page = client.get("/cobrancas?aba=hoje").get_data(as_text=True)
    assert "vence hoje" in page and "/vendas/1" in page
    assert "/vendas/1" not in client.get("/cobrancas?aba=vencidos").get_data(as_text=True)


def test_invalid_url_dates_never_500(client):
    r = client.get("/relatorios/vendas?de=32/13/2026")
    assert r.status_code == 400 and "data inválida" in r.get_data(as_text=True)
    r = post_json(client, "/vendas/nova", {"items": ["lixo"]})
    assert r.status_code == 422
