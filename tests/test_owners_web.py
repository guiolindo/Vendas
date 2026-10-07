from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import Owner, Product, Sale
from app.services import customers as csvc
from app.services import owners as own
from app.services import products as pvc

from .conftest import TODAY
from .test_web import app, client, db, post, post_json  # noqa: F401  (fixtures)


@pytest.fixture
def two(db):
    ana, bia = own.create_owner(db, "Ana"), own.create_owner(db, "Bia")
    pa = pvc.create_product(db, pvc.ProductInput(name="Vestido", price_cents=10000, cost_cents=4000, initial_stock=10, owner_id=ana.id))
    pb = pvc.create_product(db, pvc.ProductInput(name="Brinco", price_cents=5000, cost_cents=2000, initial_stock=10, owner_id=bia.id))
    c = csvc.create_customer(db, csvc.CustomerInput(name="Cliente"))
    return ana, bia, pa, pb, c


def test_people_page_and_management(client, db):
    assert client.get("/pessoas").status_code == 200
    r = post(client, "/pessoas/nova", {"name": "Ana"}, follow_redirects=True)
    assert "Pessoa cadastrada: Ana." in r.get_data(as_text=True)
    r = post(client, "/pessoas/nova", {"name": "ana"}, follow_redirects=True)
    assert "Já existe uma pessoa" in r.get_data(as_text=True)
    db.rollback()
    oid = db.scalar(select(Owner.id))
    assert "Nome alterado" in post(client, f"/pessoas/{oid}/nome", {"name": "Ana Paula"}, follow_redirects=True).get_data(as_text=True)
    assert "desativada" in post(client, f"/pessoas/{oid}/ativo", {"active": "0"}, follow_redirects=True).get_data(as_text=True)
    assert client.post(f"/pessoas/{oid}/ativo", data={"active": "1"}).status_code in (302, 400)   # sem CSRF: recusado


def test_dashboard_general_and_each_person(client, db, two):
    ana, bia, pa, pb, c = two
    post_json(client, "/vendas/nova", {"items": [{"product_id": pa.id, "quantity": 1}, {"product_id": pb.id, "quantity": 2}],
                                       "customer_id": c.id, "paid": "100", "payment_method": "pix",
                                       "due_date": (TODAY + timedelta(days=5)).isoformat()})
    geral = client.get("/?pessoa=geral").get_data(as_text=True)
    assert "Por pessoa" in geral and ">Ana<" in geral and ">Bia<" in geral and "R$ 200,00" in geral
    pana = client.get(f"/?pessoa={ana.id}").get_data(as_text=True)
    assert "Painel de Ana" in pana and "só o que pertence a Ana" in pana and "Por pessoa" not in pana and "R$ 100,00" in pana
    assert "Painel de Bia" in client.get(f"/?pessoa={bia.id}").get_data(as_text=True)
    # a escolha fica lembrada ao voltar ao início sem parâmetro
    assert "Painel de Bia" in client.get("/").get_data(as_text=True)
    assert "Painel de" not in client.get("/?pessoa=geral").get_data(as_text=True).split("<main")[1].split("Por pessoa")[0].replace("Painel de quem?", "")
    # valor estranho cai no geral, nunca em erro
    assert client.get("/?pessoa=999999999999").status_code == 200
    assert client.get("/?pessoa=abc").status_code == 200


def test_product_form_requires_owner_and_saves_it(client, db, two):
    ana, bia, *_ = two
    r = post(client, "/produtos/novo", {"name": "Colar", "price": "20"})
    assert r.status_code == 422 and "de quem é o produto" in r.get_data(as_text=True)
    post(client, "/produtos/novo", {"name": "Colar", "price": "20", "owner": str(bia.id)})
    db.rollback()
    assert db.scalar(select(Product).where(Product.name == "Colar")).owner_id == bia.id
    page = client.get("/produtos").get_data(as_text=True)
    assert "owner-tag" in page and ">Bia<" in page
    only = client.get(f"/produtos?dono={ana.id}&limpar=").get_data(as_text=True)
    assert "Vestido" in only and "Colar" not in only


def test_sale_search_can_filter_by_person(client, two):
    ana, bia, *_ = two
    names = lambda r: [p["name"] for p in r.get_json()]
    assert set(names(client.get("/api/produtos"))) == {"Vestido", "Brinco"}
    assert names(client.get(f"/api/produtos?owner={ana.id}")) == ["Vestido"]
    assert client.get(f"/api/produtos?owner={bia.id}").get_json()[0]["owner"] == "Bia"
    assert client.get("/api/produtos?owner=99999999999999").status_code == 200
    assert "data-owner" in client.get("/vendas/nova").get_data(as_text=True)


def test_sale_page_shows_owner_of_the_time_of_sale(client, db, two):
    ana, bia, pa, *_ = two
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": pa.id, "quantity": 1}], "paid": "100", "payment_method": "pix"})
    url = r.get_json()["redirect"]
    pvc.update_product(db, pa.id, pvc.ProductInput(name="Vestido", price_cents=10000, owner_id=bia.id))
    page = client.get(url).get_data(as_text=True)
    assert 'owner-tag">Ana<' in page and 'owner-tag">Bia<' not in page


def test_person_reports_render(client, two):
    ana, *_ = two
    for url in ("/relatorios/pessoas", f"/relatorios/produtos?pessoa={ana.id}", f"/relatorios/estoque?pessoa={ana.id}",
                "/relatorios/pessoas?exportar=csv"):
        assert client.get(url).status_code == 200, url


def test_setup_creates_the_two_people(tmp_path):
    from app import create_app
    a = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'s.db'}", "TESTING": True, "SECRET_KEY": "k"})
    c = a.test_client(); c.get("/configurar")
    with c.session_transaction() as s:
        tok = s["csrf"]
    r = c.post("/configurar", data={"name": "Dona", "username": "dona", "password": "Senha12345", "password2": "Senha12345",
                                    "person1": "Ana", "person2": "Bia", "csrf_token": tok})
    assert r.status_code == 302
    s = a.extensions["database"].session()
    assert [o.name for o in own.list_owners(s)] == ["Ana", "Bia"]
    s.close(); a.extensions["database"].dispose()


def test_margin_shows_on_sale_product_and_form_pages(client, db, two):
    ana, bia, pa, pb, c = two
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": pa.id, "quantity": 2}, {"product_id": pb.id, "quantity": 1, "price": "80"}],
                                           "paid": "280", "payment_method": "pix"})
    page = client.get(r.get_json()["redirect"]).get_data(as_text=True)
    assert "Margem desta venda" in page and "Só você vê" in page
    assert "R$ 120,00" in page and "R$ 60,00" in page          # vestido: 200-80 ; brinco vendido a 80: 80-20
    prod = client.get(f"/produtos/{pa.id}").get_data(as_text=True)
    assert "Lucro até hoje" in prod and "R$ 120,00" in prod and "60,0%" in prod
    lst = client.get("/produtos").get_data(as_text=True)
    assert "Margem" in lst and "R$ 60,00" in lst
    form = client.get("/produtos/novo").get_data(as_text=True)
    assert "Quanto custou pra você" in form and "product-form.js" in form
    api = client.get("/api/produtos").get_json()
    assert {p["name"]: p["cost_cents"] for p in api} == {"Vestido": 4000, "Brinco": 2000}


def test_margin_is_private_and_absent_from_cancelled_sale(client, db, two):
    ana, bia, pa, pb, c = two
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": pa.id, "quantity": 1}], "paid": "100", "payment_method": "pix"})
    url = r.get_json()["redirect"]
    post(client, url + "/cancelar", {"reason": "teste"})
    assert "Margem desta venda" not in client.get(url).get_data(as_text=True)
