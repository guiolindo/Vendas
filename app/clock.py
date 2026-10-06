"""Fonte única de "hoje". Facilita testar vencimentos."""
from datetime import date, datetime

_override: date | None = None


def today() -> date:
    return _override or date.today()


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def set_today(value: date | None) -> None:
    """Usado apenas nos testes."""
    global _override
    _override = value
