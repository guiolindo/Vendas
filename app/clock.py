"""Fonte única de "hoje". Facilita testar vencimentos."""
from datetime import date, datetime

_override: date | None = None


def today() -> date:
    return _override or date.today()


def now() -> datetime:
    real = datetime.now().replace(microsecond=0)
    # nos testes, a data fixada vale também para os horários (a hora do dia continua a real)
    return datetime.combine(_override, real.time()) if _override else real


def set_today(value: date | None) -> None:
    """Usado apenas nos testes."""
    global _override
    _override = value
