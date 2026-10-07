"""Custo do produto: mudar vale DAQUI PRA FRENTE. As vendas já feitas guardam o custo da época
(SaleItem.unit_cost_cents), então nenhuma estatística do passado muda. Cada mudança fica no histórico."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import CostChange, Product


def set_cost(session: Session, product: Product, new_cents: int, origin: str, user_id: int | None = None) -> bool:
    """Muda o custo atual do produto e registra no histórico. Devolve True se de fato mudou."""
    old = product.cost_cents
    if new_cents == old:
        return False
    product.cost_cents = new_cents
    session.add(CostChange(product_id=product.id, old_cents=old or None, new_cents=new_cents, origin=origin, created_by=user_id))
    return True
