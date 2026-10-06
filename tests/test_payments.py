import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import clock
from app.errors import BusinessError
from app.models import Payment, Sale
from app.services import payments as pay
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput

from .conftest import TODAY


@pytest.fixture
def open_sale(session, make_product, customer):
    """Venda de R$ 100,00 sem nada pago, vencendo em 10 dias."""
    p = make_product("Item", price=10000)
    return sale_svc.create_sale(session, SaleInput(
        items=[ItemInput(p.id, 1)], customer_id=customer.id, due_date=TODAY + timedelta(days=10)))


def pay_(session, sale, cents, method="pix", **kw):
    pay.register_payment(session, sale.id, cents, method, **kw)
    session.refresh(sale)
    return sale


# ---- matriz de casos extremos: venda de R$ 100,00 --------------------------------
def test_payment_zero_rejected(session, open_sale):
    with pytest.raises(BusinessError, match="maior que zero"):
        pay_(session, open_sale, 0)
    assert open_sale.paid_cents == 0


def test_payment_50_is_partial(session, open_sale):
    pay_(session, open_sale, 5000)
    assert (open_sale.paid_cents, open_sale.remaining_cents, open_sale.status) == (5000, 5000, "parcial")


def test_payment_100_settles(session, open_sale):
    pay_(session, open_sale, 10000)
    assert (open_sale.remaining_cents, open_sale.status) == (0, "pago")


def test_payment_10001_rejected_and_nothing_recorded(session, open_sale):
    with pytest.raises(BusinessError, match=r"maior que o valor restante \(R\$ 100,00\)"):
        pay_(session, open_sale, 10001)
    assert open_sale.paid_cents == 0 and open_sale.payments == []


def test_two_payments_of_50(session, open_sale):
    pay_(session, open_sale, 5000); pay_(session, open_sale, 5000)
    assert (open_sale.status, len(open_sale.payments)) == ("pago", 2)


def test_three_payments_totalling_100(session, open_sale):
    for part in (3000, 3000, 4000):
        pay_(session, open_sale, part)
    assert (open_sale.paid_cents, open_sale.status) == (10000, "pago")
    assert [p.amount_cents for p in open_sale.payments] == [3000, 3000, 4000]


def test_cannot_exceed_remaining_after_partial(session, open_sale):
    pay_(session, open_sale, 7000)
    with pytest.raises(BusinessError, match=r"restante \(R\$ 30,00\)"):
        pay_(session, open_sale, 3001)
    pay_(session, open_sale, 3000)
    assert open_sale.status == "pago"


def test_paying_a_paid_sale_rejected(session, open_sale):
    pay_(session, open_sale, 10000)
    with pytest.raises(BusinessError, match="já está paga"):
        pay_(session, open_sale, 1)


def test_500_sale_in_three_payments_keeps_full_history(session, make_product, customer):
    p = make_product("Grande", price=50000)
    sale = sale_svc.create_sale(session, SaleInput(items=[ItemInput(p.id, 1)], customer_id=customer.id,
                                                   due_date=TODAY + timedelta(days=5)))
    pay_(session, sale, 20000, "pix")
    assert (sale.total_cents, sale.paid_cents, sale.remaining_cents, sale.status) == (50000, 20000, 30000, "parcial")
    pay_(session, sale, 15000, "dinheiro", note="troco já devolvido")
    pay_(session, sale, 15000, "pix")
    assert sale.status == "pago"
    assert [(p.amount_cents, p.method) for p in sale.payments] == [(20000, "pix"), (15000, "dinheiro"), (15000, "pix")]
    assert sale.payments[1].note == "troco já devolvido"


def test_invalid_method_and_dates(session, open_sale):
    with pytest.raises(BusinessError, match="forma de pagamento"):
        pay_(session, open_sale, 100, method="cheque_em_branco")
    with pytest.raises(BusinessError, match="futuro"):
        pay_(session, open_sale, 100, paid_at=TODAY + timedelta(days=1))
    with pytest.raises(BusinessError, match="anterior à data da venda"):
        pay_(session, open_sale, 100, paid_at=TODAY - timedelta(days=1))


def test_payment_on_cancelled_sale_rejected(session, open_sale):
    sale_svc.cancel_sale(session, open_sale.id, "erro")
    with pytest.raises(BusinessError, match="cancelada"):
        pay_(session, open_sale, 100)


# ---- estorno -------------------------------------------------------------------
def test_void_payment_restores_balance_and_keeps_record(session, open_sale):
    pay_(session, open_sale, 6000)
    payment = open_sale.payments[0]
    pay.void_payment(session, payment.id, "digitei errado")
    session.refresh(open_sale)
    assert (open_sale.paid_cents, open_sale.status) == (0, "pendente")
    assert len(open_sale.payments) == 1 and open_sale.payments[0].voided
    assert open_sale.payments[0].void_reason == "digitei errado"
    with pytest.raises(BusinessError, match="já foi estornado"):
        pay.void_payment(session, payment.id)
    pay_(session, open_sale, 10000)  # saldo voltou a ser o total
    assert open_sale.status == "pago"


# ---- vencimento ----------------------------------------------------------------
def test_status_follows_the_calendar(session, open_sale):
    assert open_sale.status == "pendente"
    clock.set_today(open_sale.due_date)
    assert open_sale.status == "pendente"                 # vence hoje: ainda não venceu
    clock.set_today(open_sale.due_date + timedelta(days=1))
    assert open_sale.status == "vencido"
    pay_(session, open_sale, 4000)
    assert open_sale.status == "vencido"                  # parcial atrasado continua vencido
    pay_(session, open_sale, 6000)
    assert open_sale.status == "pago"                     # quitado nunca é vencido


def test_changing_due_date_changes_status(session, open_sale, customer):
    clock.set_today(open_sale.due_date + timedelta(days=3))
    assert open_sale.status == "vencido"
    sale_svc.update_sale(session, open_sale.id, customer.id, clock.today() + timedelta(days=7), None)
    session.refresh(open_sale)
    assert open_sale.status == "pendente"


# ---- recebimento por cliente ---------------------------------------------------
@pytest.fixture
def three_open_sales(session, make_product, customer):
    p = make_product("X", price=10000, stock=50)
    sales = []
    for days in (30, 5, 15):  # criadas fora de ordem de vencimento
        sales.append(sale_svc.create_sale(session, SaleInput(
            items=[ItemInput(p.id, 1)], customer_id=customer.id, due_date=TODAY + timedelta(days=days))))
    return sales  # vencimentos: +30, +5, +15


def test_customer_payment_goes_to_earliest_due_first(session, three_open_sales, customer):
    far, near, mid = three_open_sales
    payments = pay.register_customer_payment(session, customer.id, 15000, "pix")
    assert [(p.sale_id, p.amount_cents) for p in payments] == [(near.id, 10000), (mid.id, 5000)]
    for s in three_open_sales:
        session.refresh(s)
    assert (near.status, mid.status, far.status) == ("pago", "parcial", "pendente")


def test_customer_payment_more_than_owed_rejected_atomically(session, three_open_sales, customer):
    with pytest.raises(BusinessError, match=r"em aberto do cliente \(R\$ 300,00\)"):
        pay.register_customer_payment(session, customer.id, 30001, "pix")
    assert session.scalar(select(func.count()).select_from(Payment)) == 0


def test_customer_payment_pays_everything(session, three_open_sales, customer):
    pay.register_customer_payment(session, customer.id, 30000, "dinheiro")
    for s in three_open_sales:
        session.refresh(s)
        assert s.status == "pago"
    with pytest.raises(BusinessError, match="nada em aberto"):
        pay.register_customer_payment(session, customer.id, 100, "pix")


# ---- concorrência --------------------------------------------------------------
def test_concurrent_payments_never_exceed_balance(database, session, open_sale):
    """Oito caixas tentam receber R$ 30,00 de uma venda de R$ 100,00 ao mesmo tempo."""
    sale_id, results = open_sale.id, []

    def worker():
        s = database.session()
        try:
            pay.register_payment(s, sale_id, 3000, "pix")
            results.append("ok")
        except BusinessError:
            results.append("recusado")
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert results.count("ok") == 3 and results.count("recusado") == 5
    session.rollback()  # descarta o snapshot de leitura antigo da sessão do teste
    paid = session.scalar(select(func.sum(Payment.amount_cents)).where(Payment.sale_id == sale_id))
    assert paid == 9000


def test_concurrent_sales_never_oversell(database, session, make_product):
    p = make_product("Raro", price=1000, stock=5)
    product_id, outcomes = p.id, []

    def worker():
        s = database.session()
        try:
            sale_svc.create_sale(s, SaleInput(items=[ItemInput(product_id, 1)], paid_cents=1000, payment_method="pix"))
            outcomes.append("ok")
        except BusinessError:
            outcomes.append("sem estoque")
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(9)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert outcomes.count("ok") == 5
    session.rollback()
    session.refresh(p)
    assert p.stock_qty == 0
    assert session.scalar(select(func.count()).select_from(Sale)) == 5


def test_same_token_sent_simultaneously_creates_one_sale_and_one_payment(database, session, make_product, customer):
    """Duplo clique de verdade: duas requisições com o mesmo token chegam juntas."""
    p = make_product("Item", price=10000, stock=20)
    product_id, customer_id = p.id, customer.id
    sale_results, pay_results = [], []

    def sell():
        s = database.session()
        try:
            sale = sale_svc.create_sale(s, SaleInput(items=[ItemInput(product_id, 1)], customer_id=customer_id,
                                                     due_date=TODAY + timedelta(days=5), client_token="dup-sale"))
            sale_results.append(sale.id)
        except Exception as e:  # noqa: BLE001
            sale_results.append(repr(e))
        finally:
            s.close()

    threads = [threading.Thread(target=sell) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(set(sale_results)) == 1 and isinstance(sale_results[0], int), sale_results
    sale_id = sale_results[0]

    def payer():
        s = database.session()
        try:
            pay.register_payment(s, sale_id, 3000, "pix", token="dup-pay")
            pay_results.append("ok")
        except Exception as e:  # noqa: BLE001
            pay_results.append(repr(e))
        finally:
            s.close()

    threads = [threading.Thread(target=payer) for _ in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert pay_results == ["ok"] * 6, pay_results
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Sale)) == 1
    assert session.scalar(select(func.sum(Payment.amount_cents))) == 3000
    session.refresh(p)
    assert p.stock_qty == 19
