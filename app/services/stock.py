"""Estoque: única porta de entrada para alterar `Product.stock_qty`."""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import clock
from ..db import atomic
from ..domain.text import limited
from ..errors import BusinessError, NotFound
from ..domain.money import MAX_CENTS
from ..models import Product, ProductSize, StockMovement
from . import costs

MAX_QTY = 1_000_000


def _require_tracked(session: Session, product_id: int) -> None:
    tracked = session.scalar(select(Product.track_stock).where(Product.id == product_id))
    if tracked is False:
        raise BusinessError("Este produto está sem controle de estoque. Ligue o controle na edição do produto para usar estoque.")


def _size_stock(session: Session, product_id: int, size: str) -> int | None:
    return session.scalar(select(ProductSize.stock_qty).where(ProductSize.product_id == product_id, ProductSize.size == size))


def apply_movement(
    session: Session,
    product_id: int,
    delta: int,
    kind: str,
    *,
    size: str | None = None,
    sale_id: int | None = None,
    note: str | None = None,
    user_id: int | None = None,
) -> StockMovement:
    """Aplica uma variação ao saldo e registra a movimentação (sem commit).

    A atualização é condicional (`stock_qty + delta >= 0`), então o estoque
    nunca fica negativo, mesmo com duas vendas simultâneas do mesmo produto.
    Produto com tamanhos: o saldo mexido é o do tamanho, e o do produto (a soma) acompanha.
    """
    if delta == 0:
        raise BusinessError("A quantidade não pode ser zero.")
    has_sizes = bool(session.scalar(select(func.count()).select_from(ProductSize).where(ProductSize.product_id == product_id)))
    size = (size or "").strip() or None
    if has_sizes and size is None:
        name = session.scalar(select(Product.name).where(Product.id == product_id))
        raise BusinessError(f"Escolha o tamanho de “{name}”.", field="size")
    if size is not None and not has_sizes:
        raise BusinessError("Este produto não tem tamanhos.", field="size")
    if kind not in ("venda", "cancelamento"):
        _require_tracked(session, product_id)
    if size is None:
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
    else:
        result = session.execute(
            update(ProductSize)
            .where(ProductSize.product_id == product_id, ProductSize.size == size, ProductSize.stock_qty + delta >= 0)
            .values(stock_qty=ProductSize.stock_qty + delta)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            product = session.get(Product, product_id)
            if product is None:
                raise NotFound("Produto não encontrado.")
            left = _size_stock(session, product_id, size)
            if left is None:
                raise BusinessError(f"“{product.name}” não tem o tamanho {size}.", field="size")
            raise BusinessError(f"Estoque insuficiente de “{product.name}” no tamanho {size}: restam {left} {product.unit}.",
                                field="quantity")
        # o saldo do produto é a soma dos tamanhos: acompanha sempre, na mesma transação
        session.execute(update(Product).where(Product.id == product_id).values(stock_qty=Product.stock_qty + delta)
                        .execution_options(synchronize_session=False))
        balance = _size_stock(session, product_id, size)
    movement = StockMovement(
        product_id=product_id, kind=kind, quantity=delta, size=size, balance_after=balance,
        sale_id=sale_id, note=note or None, created_by=user_id,
    )
    session.add(movement)
    session.flush()
    session.expire_all()
    return movement


def register_entry(session: Session, product_id: int, quantity: int, note: str | None = None,
                   user_id: int | None = None, unit_cost_cents: int | None = None,
                   update_cost: bool = False, size: str | None = None) -> StockMovement:
    """Chegou mercadoria. Se informar quanto custou cada unidade, a compra guarda esse custo e, se pedido,
    ele vira o custo atual do produto (vale só para as vendas daqui pra frente)."""
    if quantity <= 0 or quantity > MAX_QTY:
        raise BusinessError("Informe uma quantidade maior que zero.", field="quantity")
    limited(note, 255, "Observação", "note")
    if unit_cost_cents is not None and not 0 < unit_cost_cents <= MAX_CENTS:
        raise BusinessError("O custo de cada unidade precisa ser maior que zero.", field="unit_cost")
    with atomic(session):
        movement = apply_movement(session, product_id, quantity, "entrada", size=size, note=note, user_id=user_id)
        movement.unit_cost_cents = unit_cost_cents
        if update_cost and unit_cost_cents:
            product = session.get(Product, product_id)
            movement.cost_before_cents = product.cost_cents
            movement.cost_updated = costs.set_cost(session, product, unit_cost_cents, "compra", user_id)
        return movement


def void_entry(session: Session, movement_id: int, reason: str | None = None, user_id: int | None = None) -> StockMovement:
    """Exclui uma compra de mercadoria lançada errada. O registro continua no histórico, marcado como excluído.
    Só dá para excluir se ainda houver em estoque as unidades dessa compra (senão elas já foram vendidas)."""
    reason = (reason or "").strip()
    limited(reason, 255, "Motivo", "reason")
    with atomic(session):
        entry = session.get(StockMovement, movement_id, with_for_update=True)
        if entry is None:
            raise NotFound("Compra não encontrada.")
        if entry.kind != "entrada":
            raise BusinessError("Só compras de mercadoria podem ser excluídas.")
        if entry.voided:
            raise BusinessError("Esta compra já foi excluída.")
        product = session.get(Product, entry.product_id)
        left = _size_stock(session, entry.product_id, entry.size) if entry.size else product.stock_qty
        if left is None or left < entry.quantity:
            where = f" no tamanho {entry.size}" if entry.size else ""
            raise BusinessError(
                f"Não dá para excluir: esta compra trouxe {entry.quantity} {product.unit}, mas só restam {left or 0}{where} no estoque "
                f"(o resto já foi vendido). Se a quantidade estiver errada, use “Contei e o número é outro”.")
        reversal = apply_movement(session, entry.product_id, -entry.quantity, "estorno_compra", size=entry.size,
                                  note=f"Compra de {entry.created_at:%d/%m/%Y} excluída", user_id=user_id)
        reversal.reverses_id = entry.id
        entry.voided_at = clock.now()
        entry.void_reason = reason or "Lançada por engano"
        # se esta compra tinha mudado o custo e ninguém mexeu nele depois, volta ao custo anterior
        product = session.get(Product, entry.product_id)
        if entry.cost_updated and entry.cost_before_cents is not None and product.cost_cents == entry.unit_cost_cents:
            costs.set_cost(session, product, entry.cost_before_cents, "compra excluída", user_id)
    return entry


def adjust_to_count(session: Session, product_id: int, counted: int, note: str | None = None,
                    user_id: int | None = None, size: str | None = None) -> StockMovement:
    """Ajusta o estoque para a quantidade contada fisicamente."""
    if counted < 0 or counted > MAX_QTY:
        raise BusinessError("A quantidade contada não pode ser negativa.", field="quantity")
    limited(note, 255, "Observação", "note")
    with atomic(session):
        current = session.scalar(select(Product.stock_qty).where(Product.id == product_id))
        if current is None:
            raise NotFound("Produto não encontrado.")
        if size:
            current = _size_stock(session, product_id, size)
            if current is None:
                raise BusinessError(f"Este produto não tem o tamanho {size}.", field="size")
        if counted == current:
            raise BusinessError("O estoque já está com essa quantidade.", field="quantity")
        return apply_movement(session, product_id, counted - current, "ajuste", size=size, note=note, user_id=user_id)
