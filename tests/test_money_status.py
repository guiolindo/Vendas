from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.money import MoneyError, format_brl, parse_money, percent_of
from app.domain.status import Status, due_hint, sale_status

T = date(2026, 10, 6)


@pytest.mark.parametrize("text,cents", [
    ("0", 0), ("10", 1000), ("10,5", 1050), ("1.234,56", 123456), ("R$ 300", 30000),
    ("1234.56", 123456), ("1.234", 123400), ("12.5", 1250), ("100,01", 10001), (" 0,01 ", 1),
])
def test_parse_money(text, cents):
    assert parse_money(text) == cents


@pytest.mark.parametrize("text", ["", "abc", "-5", "1,234,5", "10,999", "1e5", "nan", "9" * 15])
def test_parse_money_invalid(text):
    with pytest.raises(MoneyError):
        parse_money(text)


def test_format_brl():
    assert format_brl(123456) == "R$ 1.234,56"
    assert format_brl(5) == "R$ 0,05"
    assert format_brl(-150) == "-R$ 1,50"
    assert format_brl(1000000, symbol=False) == "10.000,00"


def test_percent_rounds_half_up():
    assert percent_of(1005, Decimal("10")) == 101  # 100,5 -> 101
    assert percent_of(10000, Decimal("12.5")) == 1250


@pytest.mark.parametrize("total,paid,due,cancelled,expected", [
    (10000, 10000, T, False, Status.PAGO),
    (10000, 10000, T - timedelta(days=30), False, Status.PAGO),  # quitado nunca fica vencido
    (10000, 0, T + timedelta(days=5), False, Status.PENDENTE),
    (10000, 0, T, False, Status.PENDENTE),                       # vence hoje: ainda não venceu
    (10000, 0, T - timedelta(days=1), False, Status.VENCIDO),
    (10000, 5000, T + timedelta(days=1), False, Status.PARCIAL),
    (10000, 5000, T - timedelta(days=1), False, Status.VENCIDO),  # parcial atrasado = vencido
    (10000, 0, None, False, Status.PENDENTE),
    (10000, 5000, T, True, Status.CANCELADA),
])
def test_sale_status(total, paid, due, cancelled, expected):
    assert sale_status(total, paid, due, T, cancelled) == expected


def test_due_hint():
    assert due_hint(T, T) == "vence hoje"
    assert due_hint(T - timedelta(days=1), T) == "venceu há 1 dia"
    assert due_hint(T - timedelta(days=3), T) == "venceu há 3 dias"
    assert due_hint(T + timedelta(days=1), T) == "vence amanhã"
    assert due_hint(T + timedelta(days=9), T) == "vence em 9 dias"
