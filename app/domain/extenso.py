"""Valor por extenso em português do Brasil, para o recibo ("mil duzentos e trinta e quatro reais e ...")."""
from __future__ import annotations

UN = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze", "treze",
      "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
TENS = ["", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa"]
HUND = ["", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos", "oitocentos", "novecentos"]


def _below_1000(n: int) -> str:
    if n == 100:
        return "cem"
    parts = []
    if n >= 100:
        parts.append(HUND[n // 100]); n %= 100
    if n:
        parts.append(UN[n] if n < 20 else TENS[n // 10] + (f" e {UN[n % 10]}" if n % 10 else ""))
    return " e ".join(parts)


def _joiner(rest: int) -> str:
    """'e' entre o grupo maior e o resto quando o resto é menor que 100 ou é centena exata."""
    return " e " if rest < 100 or rest % 100 == 0 else " "


def _integer(n: int) -> str:
    if n == 0:
        return "zero"
    millions, rest = divmod(n, 1_000_000)
    thousands, units = divmod(rest, 1000)
    out = ""
    if millions:
        out = "um milhão" if millions == 1 else f"{_below_1000(millions)} milhões"
        after = rest
        if after:
            out += _joiner(after)
    if thousands:
        out += "mil" if thousands == 1 else f"{_below_1000(thousands)} mil"
        if units:
            out += _joiner(units)
    if units:
        out += _below_1000(units)
    return out


def reais(cents: int) -> str:
    """12345 -> 'cento e vinte e três reais e quarenta e cinco centavos'."""
    cents = int(cents)
    if cents < 0:
        raise ValueError("valor negativo")
    r, c = divmod(cents, 100)
    parts = []
    if r or not c:
        word = "real" if r == 1 else ("de reais" if r and r % 1_000_000 == 0 else "reais")
        parts.append(f"{_integer(r)} {word}")
    if c:
        parts.append(f"{_integer(c)} {'centavo' if c == 1 else 'centavos'}")
    return " e ".join(parts)
