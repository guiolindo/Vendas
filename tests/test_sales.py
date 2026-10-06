from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.errors import BusinessError
from app.models import Payment, Product, Sale, SaleItem, StockMovement
from app.services import products as product_svc
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY


def sell(session, items, **kw):
    return sale_svc.create_sale(session, SaleInput(items=items, **kw))


def test_total_and_stock(session, product):
    sale = sell(session, [ItemInput(product.id, 3)], paid_cents=15000, payment_method="pix")
    assert sale.subtotal_cents == 15000 and sale.total_cents == 15000
    assert sale.paid_cents == 15000 and sale.remaining_cents == 0
    assert sale.status == "pago"
    session.refresh(product)
    assert product.stock_qty == 97


def test_multiple_items_and_price_override(session, make_product):
    a, b = make_product("A", 1050), make_product("B", 2000)
    sale = sell(session, [ItemInput(a.id, 2), ItemInput(b.id, 1, unit_price_cents=1500)],
                paid_cents=3600, payment_method="dinheiro")
    assert sale.subtotal_cents == 2100 + 1500
    assert sale.status == "pago"


def test_discount_value_and_percent(session, product):
    s1 = sell(session, [ItemInput(product.id, 2)], discount_cents=1000, paid_cents=9000, payment_method="pix")
    assert (s1.discount_cents, s1.total_cents) == (1000, 9000)
    s2 = sell(session, [ItemInput(product.id, 2)], discount_percent=Decimal("10"), paid_cents=9000, payment_method="pix")
    assert (s2.discount_cents, s2.total_cents) == (1000, 9000)


@pytest.mark.parametrize("kw", [
    {"discount_cents": 10001}, {"discount_cents": -1},
    {"discount_percent": Decimal("101")}, {"discount_percent": Decimal("100")},  # total zerado
])
def test_invalid_discount(session, product, kw):
    with pytest.raises(BusinessError):
        sell(session, [ItemInput(product.id, 2)], customer_id=None, **kw)


@pytest.mark.parametrize("qty", [0, -1, 1_000_001])
def test_invalid_quantity(session, product, qty):
    with pytest.raises(BusinessError):
        sell(session, [ItemInput(product.id, qty)], paid_cents=0)


def test_empty_sale_rejected(session):
    with pytest.raises(BusinessError, match="pelo menos um produto"):
        sell(session, [])


def test_insufficient_stock_is_atomic(session, make_product, customer):
    a, b = make_product("A", stock=10), make_product("B", stock=1)
    with pytest.raises(BusinessError, match="Estoque insuficiente de “B”"):
        sell(session, [ItemInput(a.id, 5), ItemInput(b.id, 2)], customer_id=customer.id,
             due_date=TODAY + timedelta(days=5))
    session.refresh(a); session.refresh(b)
    assert (a.stock_qty, b.stock_qty) == (10, 1)           # nada foi baixado
    assert session.scalar(select(func.count()).select_from(Sale)) == 0
    assert session.scalar(select(func.count()).select_from(SaleItem)) == 0
    assert session.scalar(select(func.count()).select_from(Payment)) == 0


def test_same_product_twice_checks_aggregate_stock(session, make_product):
    p = make_product(stock=5)
    with pytest.raises(BusinessError, match="Estoque insuficiente"):
        sell(session, [ItemInput(p.id, 3), ItemInput(p.id, 3)], paid_cents=30000, payment_method="pix")
    session.refresh(p)
    assert p.stock_qty == 5


def test_inactive_product_cannot_be_sold(session, product):
    product_svc.set_active(session, product.id, False)
    with pytest.raises(BusinessError, match="inativo"):
        sell(session, [ItemInput(product.id, 1)])


def test_pending_requires_customer_and_due_date(session, product, customer):
    with pytest.raises(BusinessError, match="Escolha o cliente"):
        sell(session, [ItemInput(product.id, 1)], paid_cents=1000, payment_method="pix", due_date=TODAY)
    with pytest.raises(BusinessError, match="vencimento"):
        sell(session, [ItemInput(product.id, 1)], customer_id=customer.id)
    with pytest.raises(BusinessError, match="antes da data da venda"):
        sell(session, [ItemInput(product.id, 1)], customer_id=customer.id, due_date=TODAY - timedelta(days=1))


def test_paid_in_full_needs_no_customer(session, product):
    sale = sell(session, [ItemInput(product.id, 1)], paid_cents=5000, payment_method="dinheiro")
    assert sale.customer_id is None and sale.status == "pago"


def test_overpaying_at_checkout_rejected(session, product):
    with pytest.raises(BusinessError, match="maior que o total"):
        sell(session, [ItemInput(product.id, 1)], paid_cents=5001, payment_method="pix")


def test_partial_at_checkout(session, product, customer):
    sale = sell(session, [ItemInput(product.id, 10)], customer_id=customer.id, paid_cents=20000,
                payment_method="pix", due_date=TODAY + timedelta(days=10))
    assert (sale.total_cents, sale.paid_cents, sale.remaining_cents) == (50000, 20000, 30000)
    assert sale.status == "parcial"


def test_pending_sale_has_no_payment(session, product, customer):
    sale = sell(session, [ItemInput(product.id, 1)], customer_id=customer.id,
                due_date=TODAY + timedelta(days=3))
    assert sale.paid_cents == 0 and sale.status == "pendente" and sale.payments == []


def test_price_snapshot_survives_product_changes(session, product):
    sale = sell(session, [ItemInput(product.id, 2)], paid_cents=10000, payment_method="pix")
    product_svc.update_product(
        session, product.id, product_svc.ProductInput(name="Outro nome", price_cents=9999, cost_cents=1))
    session.refresh(sale)
    item = sale.items[0]
    assert (item.product_name, item.unit_price_cents, item.unit_cost_cents) == ("Camiseta", 5000, 2000)
    assert sale.total_cents == 10000


def test_idempotent_token_prevents_duplicate_sale(session, product):
    data = SaleInput(items=[ItemInput(product.id, 1)], paid_cents=5000, payment_method="pix", client_token="abc")
    first = sale_svc.create_sale(session, data)
    second = sale_svc.create_sale(session, data)
    assert first.id == second.id
    session.refresh(product)
    assert product.stock_qty == 99


def test_sale_date_cannot_be_future(session, product):
    with pytest.raises(BusinessError, match="futuro"):
        sell(session, [ItemInput(product.id, 1)], sale_date=TODAY + timedelta(days=1), paid_cents=5000,
             payment_method="pix")


def test_cancel_restores_stock_and_voids_payments(session, product, customer):
    sale = sell(session, [ItemInput(product.id, 4)], customer_id=customer.id, paid_cents=5000,
                payment_method="pix", due_date=TODAY + timedelta(days=5))
    sale_svc.cancel_sale(session, sale.id, "Cliente desistiu")
    session.refresh(sale); session.refresh(product)
    assert sale.status == "cancelada" and sale.remaining_cents == 0
    assert product.stock_qty == 100
    assert sale.paid_cents == 0
    assert len(sale.payments) == 1 and sale.payments[0].voided  # histórico preservado
    with pytest.raises(BusinessError, match="já foi cancelada"):
        sale_svc.cancel_sale(session, sale.id, "de novo")


def test_cancel_requires_reason(session, product):
    sale = sell(session, [ItemInput(product.id, 1)], paid_cents=5000, payment_method="pix")
    with pytest.raises(BusinessError, match="motivo"):
        sale_svc.cancel_sale(session, sale.id, "  ")


def test_edit_sale(session, product, customer, make_customer):
    sale = sell(session, [ItemInput(product.id, 1)], customer_id=customer.id, due_date=TODAY + timedelta(days=5))
    other = make_customer("Maria")
    sale_svc.update_sale(session, sale.id, other.id, TODAY + timedelta(days=20), "  combinado por telefone ")
    session.refresh(sale)
    assert sale.customer_id == other.id and sale.due_date == TODAY + timedelta(days=20)
    assert sale.notes == "combinado por telefone"
    with pytest.raises(BusinessError, match="precisa de um cliente"):
        sale_svc.update_sale(session, sale.id, None, TODAY, None)
    with pytest.raises(BusinessError, match="Informe o vencimento"):
        sale_svc.update_sale(session, sale.id, other.id, None, None)


def test_edit_cancelled_sale_rejected(session, product, customer):
    sale = sell(session, [ItemInput(product.id, 1)], customer_id=customer.id, due_date=TODAY)
    sale_svc.cancel_sale(session, sale.id, "erro")
    with pytest.raises(BusinessError, match="cancelada"):
        sale_svc.update_sale(session, sale.id, customer.id, TODAY, None)


def test_stock_movements_always_match_balance(session, make_product, customer):
    from app.services import stock
    p = make_product(stock=50)
    s = sell(session, [ItemInput(p.id, 7)], customer_id=customer.id, due_date=TODAY)
    stock.register_entry(session, p.id, 20)
    stock.adjust_to_count(session, p.id, 30, "contagem")
    sale_svc.cancel_sale(session, s.id, "teste")
    sell(session, [ItemInput(p.id, 3)], paid_cents=15000, payment_method="pix")
    session.refresh(p)
    total = session.scalar(select(func.sum(StockMovement.quantity)).where(StockMovement.product_id == p.id))
    assert total == p.stock_qty == 34  # 50-7+20-33+7-3


def test_product_delete_rules(session, make_product):
    sold, fresh = make_product("Vendido"), make_product("Novo")
    sell(session, [ItemInput(sold.id, 1)], paid_cents=5000, payment_method="pix")
    with pytest.raises(BusinessError, match="já foi vendido"):
        product_svc.delete_product(session, sold.id)
    product_svc.delete_product(session, fresh.id)
    assert session.get(Product, fresh.id) is None
