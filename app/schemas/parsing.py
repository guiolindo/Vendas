"""Converte texto de formulário em tipos Python, com mensagens humanas."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping

from ..domain.money import MoneyError, parse_money
from ..errors import BusinessError


class Form:
    def __init__(self, data: Mapping[str, str]):
        self.data = data

    def text(self, name: str, default: str = "") -> str:
        return (self.data.get(name) or default).strip()

    def money(self, name: str, label: str, required: bool = False, default: int = 0) -> int:
        raw = self.text(name)
        if not raw:
            if required:
                raise BusinessError(f"{label}: informe um valor.", field=name)
            return default
        try:
            return parse_money(raw)
        except MoneyError as e:
            raise BusinessError(f"{label}: {e}", field=name) from None

    def integer(self, name: str, label: str, default: int = 0, minimum: int = 0) -> int:
        raw = self.text(name)
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise BusinessError(f"{label}: use um número inteiro.", field=name) from None
        if value < minimum:
            raise BusinessError(f"{label}: o mínimo é {minimum}.", field=name)
        return value

    def optional_int(self, name: str) -> int | None:
        raw = self.text(name)
        return int(raw) if raw.isdigit() else None

    def date(self, name: str, label: str) -> date | None:
        raw = self.text(name)
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise BusinessError(f"{label}: data inválida.", field=name) from None

    def flag(self, name: str) -> bool:
        return self.data.get(name) in ("on", "1", "true", "sim")


def parse_percent(raw: str) -> Decimal:
    try:
        return Decimal(raw.strip().replace("%", "").replace(",", "."))
    except InvalidOperation:
        raise BusinessError("Desconto: use um percentual válido, como 10 ou 7,5.", field="discount") from None
