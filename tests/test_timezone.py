"""O servidor roda em UTC; o negócio, em São Paulo. 'Hoje' tem de ser o de São Paulo."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app import clock, create_app
from app.services import sales as sale_svc
from app.services.sales import ItemInput, SaleInput


@pytest.fixture
def utc_clock(monkeypatch):
    """Sem data fixa de teste: usa o relógio de verdade, mas controlado."""
    clock.set_today(None)
    clock.configure("America/Sao_Paulo")
    def at(y, m, d, hh, mm=0):
        monkeypatch.setattr(clock, "_utc_now", lambda: datetime(y, m, d, hh, mm, tzinfo=timezone.utc))
    yield at
    clock.configure("America/Sao_Paulo")


def test_today_is_the_business_day_not_the_servers_utc_day(utc_clock):
    utc_clock(2026, 10, 7, 0, 30)             # 00:30 UTC do dia 7 = 21:30 do dia 6 em São Paulo
    assert clock.today() == date(2026, 10, 6) and clock.now().hour == 21
    utc_clock(2026, 10, 7, 2, 59)             # 23:59 em São Paulo: ainda é dia 6
    assert clock.today() == date(2026, 10, 6)
    utc_clock(2026, 10, 7, 3, 0)              # meia-noite em São Paulo: vira o dia
    assert clock.today() == date(2026, 10, 7) and clock.now().hour == 0


def test_now_is_stored_without_timezone_in_business_time(utc_clock):
    utc_clock(2026, 6, 15, 15, 45)
    n = clock.now()
    assert n.tzinfo is None and (n.hour, n.minute) == (12, 45)


def test_a_sale_at_night_is_dated_that_business_day(utc_clock, session, product):
    utc_clock(2026, 10, 7, 1, 10)             # 22:10 de 06/10 em São Paulo (01:10 UTC de 07/10)
    sale = sale_svc.create_sale(session, SaleInput(items=[ItemInput(product.id, 1)], paid_cents=product.price_cents, payment_method="pix"))
    assert sale.sale_date == date(2026, 10, 6) and sale.created_at.hour == 22      # NÃO sai datada de amanhã
    due = sale_svc.create_sale(session, SaleInput(items=[ItemInput(product.id, 1)], customer_id=None, paid_cents=product.price_cents, payment_method="pix"))
    assert due.status == "pago"


def test_due_today_does_not_flip_at_9pm(utc_clock, session, product, customer):
    utc_clock(2026, 10, 6, 14, 0)             # 11:00 em São Paulo
    s = sale_svc.create_sale(session, SaleInput(items=[ItemInput(product.id, 1)], customer_id=customer.id, due_date=date(2026, 10, 6)))
    assert s.status == "pendente"
    utc_clock(2026, 10, 7, 1, 0)              # 22:00 em São Paulo: continua vencendo hoje (não venceu)
    session.expire_all()
    assert s.status == "pendente" and s.due_date == clock.today()
    utc_clock(2026, 10, 7, 3, 1)              # 00:01 do dia 7 em São Paulo: agora sim venceu
    session.expire_all()
    assert s.status == "vencido"


def test_timezone_is_configurable_and_a_bad_name_fails_at_startup(tmp_path, utc_clock):
    utc_clock(2026, 10, 7, 12, 0)
    clock.configure("America/Manaus")         # UTC-4
    assert clock.now().hour == 8 and clock.timezone_name() == "America/Manaus"
    with pytest.raises(Exception):
        clock.configure("Marte/Olympus")
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'tz.db'}", "TESTING": True, "SECRET_KEY": "k", "TIMEZONE": "America/Sao_Paulo"})
    assert clock.timezone_name() == "America/Sao_Paulo"
    app.extensions["database"].dispose()


def test_app_default_timezone_is_sao_paulo(tmp_path, monkeypatch):
    monkeypatch.delenv("VENDAS_TZ", raising=False)
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'tz2.db'}", "TESTING": True, "SECRET_KEY": "k"})
    assert clock.timezone_name() == "America/Sao_Paulo"
    app.extensions["database"].dispose()
