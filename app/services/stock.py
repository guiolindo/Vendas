"""Estoque: única porta de entrada para alterar `Product.stock_qty`."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import atomic
from ..domain.text import limited
from ..errors import BusinessError, NotFound
from ..models import Product, StockMovement

MAX_QTY = 1_000_000


def apply_movement(
    session: Session,
    product_id: int,
    delta: int,
    kind: str,
    *,
    sale_id: int | None = None,
    note: str | None = None,
    user_id: int | None = None,
) -> StockMovement:
    """Aplica uma variação ao saldo e registra a movimentação (sem commit).

    A atualização é condicional (`stock_qty + delta >= 0`), então o estoque
    nunca fica negativo, mesmo com duas vendas simultâneas do mesmo produto.
    """
    if delta == 0:
        raise BusinessError("A quantidade não pode ser zero.")
    result = session.execute(
        update(Product)
        .where(Product.id == product_id, Product.stock_qty + delta >= 0)
        .values(stock_qty=Product.stock_qty + delta)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        product = session.get(Product, product_id)
        if product is None:
            raise NotFound("Produto não encontrado.")
        session.refresh(product)
        raise BusinessError(
            f"Estoque insuficiente de “{product.name}”: restam {product.stock_qty} {product.unit}.",
            field="quantity",
        )
    balance = session.scalar(select(Product.stock_qty).where(Product.id == product_id))
    movement = StockMovement(
        product_id=product_id, kind=kind, quantity=delta, balance_after=balance,
        sale_id=sale_id, note=note or None, created_by=user_id,
    )
    session.add(movement)
    session.flush()
    session.expire_all()
    return movement


def register_entry(session: Session, product_id: int, quantity: int, note: str | None = None,
                   user_id: int | None = None) -> StockMovement:
    if quantity <= 0 or quantity > MAX_QTY:
        raise BusinessError("Informe uma quantidade maior que zero.", field="quantity")
    limited(note, 255, "Observação", "note")
    with atomic(session):
        return apply_movement(session, product_id, quantity, "entrada", note=note, user_id=user_id)


def adjust_to_count(session: Session, product_id: int, counted: int, note: str | None = None,
                    user_id: int | None = None) -> StockMovement:
    """Ajusta o estoque para a quantidade contada fisicamente."""
    if counted < 0 or counted > MAX_QTY:
        raise BusinessError("A quantidade contada não pode ser negativa.", field="quantity")
    limited(note, 255, "Observação", "note")
    with atomic(session):
        current = session.scalar(select(Product.stock_qty).where(Product.id == product_id))
        if current is None:
            raise NotFound("Produto não encontrado.")
        if counted == current:
            raise BusinessError("O estoque já está com essa quantidade.", field="quantity")
        return apply_movement(session, product_id, counted - current, "ajuste", note=note, user_id=user_id)
