import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Payment, Product, Sale, SaleItem
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput


def test_foreign_keys_are_enforced(session, product):
    sale = sale_svc.create_sale(session, SaleInput(
        items=[ItemInput(product.id, 1)], paid_cents=5000, payment_method="pix"))
    with pytest.raises(IntegrityError):
        session.execute(text("DELETE FROM products WHERE id = :i"), {"i": product.id})
    session.rollback()
    with pytest.raises(IntegrityError):  # pagamento não some junto com a venda
        session.execute(text("DELETE FROM sales WHERE id = :i"), {"i": sale.id})
    session.rollback()


def test_database_rejects_inconsistent_rows(session, product):
    with pytest.raises(IntegrityError):  # total != subtotal - desconto
        session.add(Sale(sale_date=__import__("datetime").date.today(), subtotal_cents=100,
                         discount_cents=0, total_cents=90))
        session.flush()
    session.rollback()
    with pytest.raises(IntegrityError):  # estoque negativo
        session.execute(text("UPDATE products SET stock_qty = -1 WHERE id = :i"), {"i": product.id})
    session.rollback()


def test_payment_amount_must_be_positive_in_db(session, product):
    sale = sale_svc.create_sale(session, SaleInput(
        items=[ItemInput(product.id, 1)], paid_cents=5000, payment_method="pix"))
    with pytest.raises(IntegrityError):
        session.add(Payment(sale_id=sale.id, amount_cents=0, method="pix",
                            paid_at=sale.sale_date))
        session.flush()
    session.rollback()


def test_duplicate_code_and_sku_rejected(session, make_product):
    from app.errors import BusinessError
    make_product("A", code="X1", sku="S1")
    with pytest.raises(BusinessError, match="código"):
        make_product("B", code="X1")
    with pytest.raises(BusinessError, match="SKU"):
        make_product("C", sku="S1")


def test_auto_code_generated(session, make_product):
    a, b = make_product("A"), make_product("B")
    assert a.code != b.code and a.code.startswith("P")


def test_customer_with_sales_cannot_be_deleted(session, product, customer):
    from datetime import date
    from app.errors import BusinessError
    from app.services import customers as csvc
    sale_svc.create_sale(session, SaleInput(items=[ItemInput(product.id, 1)], customer_id=customer.id,
                                            due_date=date(2026, 12, 1)))
    with pytest.raises(BusinessError, match="vendas registradas"):
        csvc.delete_customer(session, customer.id)


def test_sql_status_filters_agree_with_python_status(session, make_product, make_customer):
    """Os filtros em SQL e o status calculado em Python nunca podem divergir."""
    from datetime import timedelta
    from app import clock
    from app.domain.status import Status
    from app.repositories.sales import status_clause
    from app.services import payments as pay
    from sqlalchemy import select
    from .conftest import TODAY

    p = make_product("X", price=10000, stock=999)
    c = make_customer()
    for due in (-5, 0, 5):
        for paid in (0, 4000, 10000):
            s = sale_svc.create_sale(session, SaleInput(
                items=[ItemInput(p.id, 1)], customer_id=c.id, paid_cents=paid,
                payment_method="pix", due_date=TODAY + timedelta(days=5)))
            # move o vencimento sem passar pela regra "não antes da venda"
            s.due_date = TODAY + timedelta(days=due)
            session.commit()
    last = sale_svc.create_sale(session, SaleInput(items=[ItemInput(p.id, 1)], paid_cents=10000, payment_method="pix"))
    sale_svc.cancel_sale(session, last.id, "teste")

    for status in Status:
        sql_ids = set(session.scalars(select(Sale.id).where(status_clause(status, clock.today()))))
        py_ids = {s.id for s in session.scalars(select(Sale)) if s.status == status}
        assert sql_ids == py_ids, status
