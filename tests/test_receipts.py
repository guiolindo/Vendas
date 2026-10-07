import re
from datetime import timedelta

import pytest

from app.services import customers as csvc
from app.services import owners as own
from app.services import payments as pay
from app.services import products as pvc
from app.services import settings as settings_svc
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY
from .test_web import app, client, db, post, post_json  # noqa: F401  (fixtures)


@pytest.fixture
def shop(client, db):
    ana, bia = own.create_owner(db, "Ana"), own.create_owner(db, "Bia")
    # custos com valores "assinatura" fáceis de procurar no papel
    pa = pvc.create_product(db, pvc.ProductInput(name="Vestido floral", price_cents=18900, cost_cents=7531, initial_stock=20, owner_id=ana.id))
    pb = pvc.create_product(db, pvc.ProductInput(name="Brinco <b>prata</b>", price_cents=8900, cost_cents=3177, initial_stock=20, owner_id=bia.id))
    c = csvc.create_customer(db, csvc.CustomerInput(name="Marina Souza", phone="(11) 91234-5678", document="123.456.789-00"))
    return ana, bia, pa, pb, c


def make_sale(client, shop, paid="100", due=True, **extra):
    ana, bia, pa, pb, c = shop
    payload = {"items": [{"product_id": pa.id, "quantity": 1}, {"product_id": pb.id, "quantity": 2}], "customer_id": c.id,
               "paid": paid, "payment_method": "pix", **extra}
    if due:
        payload["due_date"] = (TODAY + timedelta(days=10)).isoformat()
    r = post_json(client, "/vendas/nova", payload)
    return int(r.get_json()["redirect"].rsplit("/", 1)[1])


def test_receipt_shows_everything_the_customer_needs(client, shop):
    sid = make_sale(client, shop)
    page = client.get(f"/vendas/{sid}/comprovante").get_data(as_text=True)
    for needle in ("Comprovante de venda", "Nº 000001", "Marina Souza", "(11) 91234-5678", "Vestido floral", "Vendido por", "Ana e Bia",
                   "R$ 367,00", "R$ 100,00", "R$ 267,00", "Pagamentos recebidos", "Pix", "Falta pagar", "Parcial", "Documento sem valor fiscal",
                   "Reconheço dever", "Assinatura" if False else "Marina Souza", "Obrigado pela preferência!"):
        assert needle in page, needle
    assert "Meu Negócio" in page and "Dica: coloque o nome" in page                       # antes de configurar: avisa
    assert 'class="sheet a4"' in page and "receipt.css" in page and "base.html" not in page


def test_receipt_never_shows_cost_or_margin(client, shop):
    sid = make_sale(client, shop, paid="367", due=False)
    page = client.get(f"/vendas/{sid}/comprovante?formato=termico").get_data(as_text=True)
    for secret in ("75,31", "31,77", "7531", "3177", "Margem", "margem", "Custo", "custo", "Custou", "Lucro", "lucro"):
        assert secret not in page, secret
    page = client.get(f"/vendas/{sid}/comprovante").get_data(as_text=True)
    assert all(s not in page for s in ("75,31", "31,77", "Margem", "Custou"))
    detail = client.get(f"/vendas/{sid}").get_data(as_text=True)
    assert "Margem desta venda" in detail                                                  # o painel privado continua na tela interna


def test_paid_sale_has_no_debt_acknowledgement_and_unpaid_does(client, shop):
    paid = client.get(f"/vendas/{make_sale(client, shop, paid='367', due=False)}/comprovante").get_data(as_text=True)
    assert "Reconheço dever" not in paid and "Falta pagar" not in paid and ">Pago<" in paid
    owed = client.get(f"/vendas/{make_sale(client, shop, paid='')}/comprovante").get_data(as_text=True)
    assert "Reconheço dever" in owed and "R$ 367,00" in owed and "Pendente" in owed


def test_discount_lines_only_when_there_is_a_discount(client, shop):
    sid = make_sale(client, shop, paid="", discount="10", discount_type="percent")
    page = client.get(f"/vendas/{sid}/comprovante").get_data(as_text=True)
    assert "Subtotal" in page and "R$ 36,70" in page and "R$ 330,30" in page
    assert "Subtotal" not in client.get(f"/vendas/{make_sale(client, shop, paid='')}/comprovante").get_data(as_text=True)


def test_cancelled_sale_receipt_says_so_and_hides_payments(client, shop):
    sid = make_sale(client, shop)
    post(client, f"/vendas/{sid}/cancelar", {"reason": "Cliente desistiu"})
    page = client.get(f"/vendas/{sid}/comprovante").get_data(as_text=True)
    assert "Cancelada" in page and "Venda cancelada" in page and "Cliente desistiu" in page
    assert "Reconheço dever" not in page and "Pagamentos recebidos" not in page


def test_user_text_is_escaped_on_paper(client, shop):
    page = client.get(f"/vendas/{make_sale(client, shop)}/comprovante").get_data(as_text=True)
    assert "<b>prata</b>" not in page and "&lt;b&gt;prata&lt;/b&gt;" in page


def test_settings_feed_the_receipt_header(client, shop, db):
    r = post(client, "/configuracoes", {"business_name": "Ateliê Flor & Cia", "document": "12.345.678/0001-90", "phone": "(11) 4002-8922",
                                        "address": "Rua das Flores, 100", "social": "@atelieflor", "thanks": "Volte sempre!", "show_seller": "on"},
             follow_redirects=True)
    assert "Configurações salvas" in r.get_data(as_text=True)
    page = client.get(f"/vendas/{make_sale(client, shop)}/comprovante").get_data(as_text=True)
    for needle in ("Ateliê Flor &amp; Cia", "12.345.678/0001-90", "(11) 4002-8922", "Rua das Flores, 100", "@atelieflor", "Volte sempre!", 'aria-hidden="true">A<'):
        assert needle in page, needle
    assert "Dica: coloque o nome" not in page
    post(client, "/configuracoes", {"business_name": "Ateliê Flor & Cia"})                  # desmarcar "mostrar quem vendeu"
    assert "Vendido por" not in client.get(f"/vendas/{make_sale(client, shop)}/comprovante").get_data(as_text=True)


def test_settings_validation_and_defaults(client, db):
    assert settings_svc.load(db)["business_name"] == "Meu Negócio"
    r = post(client, "/configuracoes", {"business_name": "x" * 81})
    assert r.status_code == 422 and "no máximo 80" in r.get_data(as_text=True)
    post(client, "/configuracoes", {"business_name": "  "})
    db.rollback()
    assert settings_svc.load(db)["business_name"] == "Meu Negócio"                           # vazio volta ao padrão
    assert client.post("/configuracoes", data={"business_name": "Hack"}).status_code in (302, 400)   # sem CSRF


def test_payment_receipt_has_amount_in_words_and_running_balance(client, shop, db):
    ana, bia, pa, pb, c = shop
    sid = make_sale(client, shop, paid="100")
    pay.register_payment(db, sid, 12345, "dinheiro", note="troco já devolvido")
    db.rollback()
    first, second = sorted([p for p in sale_svc.Sale.__mro__ and db.get(sale_svc.Sale, sid).payments], key=lambda p: p.id)
    page = client.get(f"/pagamentos/{second.id}/recibo").get_data(as_text=True)
    for needle in ("Recibo de pagamento", "R$ 123,45", "cento e vinte e três reais e quarenta e cinco centavos", "Marina Souza", "Dinheiro",
                   "troco já devolvido", "R$ 143,55", "venda nº 000001"):
        assert needle in page, needle                                                          # saldo: 367 - 100 - 123,45 = 143,55
    early = client.get(f"/pagamentos/{first.id}/recibo").get_data(as_text=True)
    assert "R$ 267,00" in early                                                                 # o saldo é o da época daquele pagamento


def test_voided_payment_has_no_receipt(client, shop, db):
    sid = make_sale(client, shop)
    db.rollback()
    p = db.get(sale_svc.Sale, sid).payments[0]
    pay.void_payment(db, p.id)
    r = client.get(f"/pagamentos/{p.id}/recibo", follow_redirects=True)
    assert "foi estornado" in r.get_data(as_text=True)


def test_receipt_links_and_unknown_ids(client, shop):
    sid = make_sale(client, shop)
    detail = client.get(f"/vendas/{sid}").get_data(as_text=True)
    assert f"/vendas/{sid}/comprovante" in detail and "/recibo" in detail
    assert client.get("/vendas/99999999999/comprovante").status_code in (200, 302, 404)
    assert client.get("/pagamentos/99999999999/recibo").status_code in (200, 302, 404)
    assert "Configurações" in client.get("/").get_data(as_text=True)


def test_paper_formats_declare_their_own_page_size(client, shop):
    sid = make_sale(client, shop)
    a4 = client.get(f"/vendas/{sid}/comprovante").get_data(as_text=True)
    assert "receipt-a4.css" in a4 and "size: 80mm" not in a4
    thermal = client.get(f"/vendas/{sid}/comprovante?formato=termico").get_data(as_text=True)
    m = re.search(r"@page \{ size: 80mm (\d+)mm", thermal)
    assert m and 200 < int(m.group(1)) < 400 and 'class="sheet termico"' in thermal
    assert 'aria-current="true">Bobina 80 mm' in thermal                                   # o botão do formato escolhido fica marcado
    assert client.get(f"/vendas/{sid}/comprovante?formato=inventado").get_data(as_text=True).count('class="sheet a4"') == 1


def test_thermal_height_grows_with_content_and_never_below_a_sane_minimum():
    from app.web.receipts import thermal_height_mm as h
    assert h(1, 0, False) < h(5, 0, False) < h(5, 3, False) < h(5, 3, True)
    assert h(1, 0, False) >= 150 + 19                                                       # cabeçalho + totais + rodapé + 1 item


def test_receipt_page_is_standalone_and_script_free_of_inline_code(client, shop):
    page = client.get(f"/vendas/{make_sale(client, shop)}/comprovante").get_data(as_text=True)
    assert "<nav class=\"nav\"" not in page and "tabbar" not in page                        # sem menu do sistema: é uma folha de papel
    assert not re.search(r"<script(?![^>]*\bsrc=)", page) and not re.search(r"\son(click|load)=", page)
    assert 'name="robots" content="noindex' in page
