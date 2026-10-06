"""Status de uma venda. É SEMPRE calculado, nunca digitado.

Regras (em ordem):
  cancelada  -> venda cancelada
  pago       -> pago >= total
  vencido    -> falta pagar e o vencimento já passou (mesmo com pagamento parcial)
  parcial    -> pagou uma parte e ainda não venceu
  pendente   -> nada pago e ainda não venceu

Vencer "hoje" ainda NÃO é vencido: o cliente tem até o fim do dia.
"""
from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum


class Status(StrEnum):
    PAGO = "pago"
    PARCIAL = "parcial"
    PENDENTE = "pendente"
    VENCIDO = "vencido"
    CANCELADA = "cancelada"


LABELS = {
    Status.PAGO: "Pago",
    Status.PARCIAL: "Parcial",
    Status.PENDENTE: "Pendente",
    Status.VENCIDO: "Vencido",
    Status.CANCELADA: "Cancelada",
}


def sale_status(
    total_cents: int,
    paid_cents: int,
    due_date: date | None,
    today: date,
    cancelled: bool = False,
) -> Status:
    if cancelled:
        return Status.CANCELADA
    if paid_cents >= total_cents:
        return Status.PAGO
    if due_date is not None and due_date < today:
        return Status.VENCIDO
    if paid_cents > 0:
        return Status.PARCIAL
    return Status.PENDENTE


def due_hint(due_date: date | None, today: date) -> str:
    """Texto curto: 'venceu há 3 dias', 'vence hoje', 'vence em 5 dias'."""
    if due_date is None:
        return ""
    delta = (due_date - today).days
    if delta < 0:
        n = -delta
        return f"venceu há {n} dia" + ("s" if n != 1 else "")
    if delta == 0:
        return "vence hoje"
    if delta == 1:
        return "vence amanhã"
    return f"vence em {delta} dias"


UPCOMING_DAYS = 7


def upcoming_limit(today: date) -> date:
    return today + timedelta(days=UPCOMING_DAYS)
