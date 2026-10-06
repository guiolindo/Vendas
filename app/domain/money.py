"""Dinheiro: sempre inteiro em centavos dentro do sistema."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_CENTS = 2_000_000_000  # R$ 20.000.000,00: cabe em INTEGER (int4) no Postgres


class MoneyError(ValueError):
    pass


def parse_money(text: object) -> int:
    """Converte "1.234,56", "1234,5", "R$ 10", "10.50" em centavos.

    A vírgula é sempre o separador decimal. Um ponto sozinho só é milhar quando
    segue o padrão 1.234 / 12.345.678; caso contrário é tratado como decimal.
    """
    if text is None:
        raise MoneyError("Informe um valor.")
    raw = str(text).strip().replace("R$", "").replace(" ", "")
    if not raw:
        raise MoneyError("Informe um valor.")
    if raw.startswith("-"):
        raise MoneyError("O valor não pode ser negativo.")
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        raw = raw.replace(".", "")
    if not re.fullmatch(r"\d+(\.\d+)?", raw):
        raise MoneyError("Valor inválido. Use o formato 1.234,56.")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise MoneyError("Valor inválido. Use o formato 1.234,56.") from None
    if not value.is_finite():
        raise MoneyError("Valor inválido. Use o formato 1.234,56.")
    if value.as_tuple().exponent < -2:
        raise MoneyError("Use no máximo duas casas decimais (centavos).")
    cents = int((value * 100).to_integral_value(ROUND_HALF_UP))
    if cents > MAX_CENTS:
        raise MoneyError("Valor muito alto.")
    return cents


def percent_of(cents: int, percent: Decimal) -> int:
    """Percentual de um valor, arredondado ao centavo (meio para cima)."""
    return int((Decimal(cents) * percent / 100).to_integral_value(ROUND_HALF_UP))


def format_brl(cents: int | None, symbol: bool = True) -> str:
    cents = int(cents or 0)  # o Postgres devolve Decimal em somas de bigint
    sign = "-" if cents < 0 else ""
    reais, cent = divmod(abs(cents), 100)
    body = f"{reais:,}".replace(",", ".") + f",{cent:02d}"
    return f"{sign}R$ {body}" if symbol else f"{sign}{body}"
