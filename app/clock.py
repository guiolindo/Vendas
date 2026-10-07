"""Fonte única de "hoje" e "agora", sempre no fuso do negócio (padrão: America/Sao_Paulo).

O servidor do Railway roda em UTC. Sem isso, o "hoje" viraria às 21h no Brasil: uma venda feita às 21h30
sairia datada de amanhã e "vence hoje" mudaria de dia no meio do expediente."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "America/Sao_Paulo"
_tz = ZoneInfo(os.environ.get("VENDAS_TZ", DEFAULT_TZ))
_override: date | None = None


def configure(name: str | None) -> None:
    """Escolhe o fuso do negócio (`VENDAS_TZ`). Nome inválido falha cedo, na subida do sistema."""
    global _tz
    _tz = ZoneInfo(name or DEFAULT_TZ)


def timezone_name() -> str:
    return str(_tz)


def _utc_now() -> datetime:
    """Relógio de verdade (separado para os testes poderem trocá-lo)."""
    return datetime.now(timezone.utc)


def now() -> datetime:
    """Data e hora de agora no fuso do negócio, sem informação de fuso (é assim que ficam gravadas)."""
    real = _utc_now().astimezone(_tz).replace(tzinfo=None, microsecond=0)
    # nos testes, a data fixada vale também para os horários (a hora do dia continua a real)
    return datetime.combine(_override, real.time()) if _override else real


def today() -> date:
    return _override or now().date()


def set_today(value: date | None) -> None:
    """Usado apenas nos testes."""
    global _override
    _override = value
