"""Registro, edição e cancelamento de vendas."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import clock
from ..db import atomic
from ..domain.money import MAX_CENTS, format_brl, percent_of
from ..domain.payment_methods import METHODS
from ..domain.text import limited
from ..errors import BusinessError, NotFound
from ..models import Customer, Payment, Product, Sale, SaleItem, StockMovement
from .stock import MAX_QTY, apply_movement


@dataclass
class ItemInput:
    product_id: int
    quantity: int
    unit_price_cents: int | None = None  # None = preço de tabela


@dataclass
class SaleInput:
    items: list[ItemInput]
    customer_id: int | None = None
    discount_cents: int = 0
    discount_percent: Decimal | None = None  # se informado, tem prioridade sobre discount_cents
    paid_cents: int = 0
    payment_method: str | None = None
    due_date: date | None = None
    sale_date: date | None = None
    notes: str | None = None
    client_token: str | None = None


def compute_totals(subtotal: int, discount_cents: int = 0, discount_percent: Decimal | None = None) -> tuple[int, int]:
    """Retorna (desconto, total) em centavos."""
    if discount_percent is not None:
        if discount_percent < 0 or discount_percent > 100:
            raise BusinessError("O desconto em % precisa estar entre 0 e 100.", field="discount")
        discount = percent_of(subtotal, discount_percent)
    else:
        discount = discount_cents
    if discount < 0:
        raise BusinessError("O desconto não pode ser negativo.", field="discount")
    if discount > subtotal:
        raise BusinessError(
            f"O desconto não pode ser maior que o subtotal ({format_brl(subtotal)}).", field="discount"
        )
    total = subtotal - discount
    if total <= 0:
        raise BusinessError("O total da venda precisa ser maior que zero.", field="discount")
    return discount, total


def create_sale(session: Session, data: SaleInput, user_id: int | None = None) -> Sale:
    """Registra venda + itens + baixa de estoque + pagamento inicial, tudo ou nada.

    Se duas requisições com o mesmo token chegam juntas (duplo clique), uma grava e a outra
    recebe de volta a venda já gravada, em vez de um erro."""
    try:
        return _create_sale(session, data, user_id)
    except IntegrityError:
        session.rollback()
        if data.client_token:
            existing = session.scalar(select(Sale).where(Sale.client_token == data.client_token))
            if existing:
                return existing
        raise


def _create_sale(session: Session, data: SaleInput, user_id: int | None = None) -> Sale:
    today = clock.today()
    with atomic(session):
        if data.client_token:
            existing = session.scalar(select(Sale).where(Sale.client_token == data.client_token))
            if existing:  # duplo clique / reenvio: devolve a mesma venda
                return existing

        if not data.items:
            raise BusinessError("Adicione pelo menos um produto à venda.", field="items")

        limited(data.notes, 500, "Observação", "notes")
        sale_date = data.sale_date or today
        if sale_date < today - timedelta(days=3650):
            raise BusinessError("A data da venda está muito no passado.", field="sale_date")
        if data.due_date and data.due_date > today + timedelta(days=3650):
            raise BusinessError("O vencimento está muito distante.", field="due_date")
        if sale_date > today:
            raise BusinessError("A data da venda não pode ser no futuro.", field="sale_date")

        ids = {i.product_id for i in data.items}
        products = {p.id: p for p in session.scalars(select(Product).where(Product.id.in_(ids)))}
        lines: list[tuple[Product, int, int]] = []
        for item in data.items:
            product = products.get(item.product_id)
            if product is None:
                raise BusinessError("Um dos produtos da venda não existe mais.", field="items")
            if not product.active:
                raise BusinessError(f"“{product.name}” está inativo e não pode ser vendido.", field="items")
            if not isinstance(item.quantity, int) or item.quantity < 1 or item.quantity > MAX_QTY:
                raise BusinessError(f"Quantidade inválida para “{product.name}”.", field="items")
            price = product.price_cents if item.unit_price_cents is None else item.unit_price_cents
            if price < 0:
                raise BusinessError(f"Preço inválido para “{product.name}”.", field="items")
            lines.append((product, item.quantity, price))

        subtotal = sum(qty * price for _, qty, price in lines)
        if subtotal <= 0:
            raise BusinessError("O total da venda precisa ser maior que zero.", field="items")
        if subtotal > MAX_CENTS:
            raise BusinessError(f"O valor da venda passa do limite de {format_brl(MAX_CENTS)}.", field="items")
        discount, total = compute_totals(subtotal, data.discount_cents, data.discount_percent)

        paid = data.paid_cents
        if paid < 0:
            raise BusinessError("O valor pago não pode ser negativo.", field="paid")
        if paid > total:
            raise BusinessError(
                f"O valor pago não pode ser maior que o total da venda ({format_brl(total)}).", field="paid"
            )
        if paid > 0 and data.payment_method not in METHODS:
            raise BusinessError("Escolha a forma de pagamento.", field="payment_method")

        remaining = total - paid
        customer = None
        if data.customer_id is not None:
            customer = session.get(Customer, data.customer_id)
            if customer is None:
                raise BusinessError("Cliente não encontrado.", field="customer")
            if not customer.active:
                raise BusinessError(f"O cliente “{customer.name}” está inativo.", field="customer")
        if remaining > 0:
            if customer is None:
                raise BusinessError("Escolha o cliente: a venda ficará com saldo a receber.", field="customer")
            if data.due_date is None:
                raise BusinessError("Informe o vencimento do valor restante.", field="due_date")
        if data.due_date is not None and data.due_date < sale_date:
            raise BusinessError("O vencimento não pode ser antes da data da venda.", field="due_date")

        sale = Sale(
            client_token=data.client_token or None,
            customer_id=customer.id if customer else None,
            sale_date=sale_date,
            due_date=data.due_date,
            subtotal_cents=subtotal,
            discount_cents=discount,
            total_cents=total,
            notes=(data.notes or "").strip() or None,
            created_by=user_id,
        )
        for product, qty, price in lines:
            sale.items.append(SaleItem(
                product_id=product.id, owner_id=product.owner_id, product_name=product.name, product_code=product.code,
                unit=product.unit, quantity=qty, unit_price_cents=price,
                unit_cost_cents=product.cost_cents, total_cents=qty * price,
            ))
        session.add(sale)
        session.flush()

        for product, qty, _ in sorted(lines, key=lambda l: l[0].id):
            apply_movement(session, product.id, -qty, "venda", sale_id=sale.id, user_id=user_id)

        if paid > 0:
            session.add(Payment(
                sale_id=sale.id, amount_cents=paid, method=data.payment_method,
                paid_at=sale_date, created_by=user_id,
            ))
    return sale


def update_sale(session: Session, sale_id: int, customer_id: int | None, due_date: date | None,
                notes: str | None) -> Sale:
    """Corrige cliente, vencimento e observações. Itens e valores não mudam:
    para isso, cancele e registre a venda de novo (o histórico fica íntegro)."""
    limited(notes, 500, "Observação", "notes")
    with atomic(session):
        sale = session.get(Sale, sale_id, with_for_update=True)
        if sale is None:
            raise NotFound("Venda não encontrada.")
        if sale.cancelled:
            raise BusinessError("Esta venda foi cancelada e não pode ser editada.")
        remaining = sale.total_cents - sale.paid_cents
        customer = None
        if customer_id is not None:
            customer = session.get(Customer, customer_id)
            if customer is None:
                raise BusinessError("Cliente não encontrado.", field="customer")
        if remaining > 0:
            if customer is None:
                raise BusinessError("Esta venda tem saldo a receber, então precisa de um cliente.", field="customer")
            if due_date is None:
                raise BusinessError("Informe o vencimento: ainda há valor a receber.", field="due_date")
        if due_date is not None and due_date < sale.sale_date:
            raise BusinessError("O vencimento não pode ser antes da data da venda.", field="due_date")
        sale.customer_id = customer.id if customer else None
        sale.due_date = due_date
        sale.notes = (notes or "").strip() or None
    return sale


def delete_sale(session: Session, sale_id: int) -> str:
    """Exclui DEFINITIVAMENTE uma venda que já foi cancelada (limpeza de lançamento errado ou de teste).

    Exige o cancelamento antes: é ele que devolve o estoque e estorna os pagamentos de forma registrada.
    Como a venda cancelada já não conta em nenhum painel ou relatório, excluí-la só tira a linha da lista.
    Devolve um resumo, que quem chamou grava na trilha de auditoria."""
    with atomic(session):
        sale = session.get(Sale, sale_id, with_for_update=True)
        if sale is None:
            raise NotFound("Venda não encontrada.")
        if not sale.cancelled:
            raise BusinessError("Só dá para excluir uma venda que já foi cancelada. Cancele primeiro: assim o estoque volta e "
                                "os pagamentos são estornados de forma registrada.")
        summary = (f"#{sale.id} total={sale.total_cents} itens={len(sale.items)} pagamentos={len(sale.payments)} "
                   f"cliente={sale.customer.name if sale.customer else 'consumidor'} motivo={sale.cancel_reason}")[:255]
        for movement in session.scalars(select(StockMovement).where(StockMovement.sale_id == sale_id)):
            session.delete(movement)      # a saída da venda e a devolução do cancelamento se anulam: o estoque não muda
        for payment in list(sale.payments):
            session.delete(payment)
        session.flush()
        session.delete(sale)               # os itens vão junto
    return summary


def cancel_sale(session: Session, sale_id: int, reason: str, user_id: int | None = None) -> Sale:
    """Cancela: devolve o estoque e estorna (sem apagar) os pagamentos recebidos."""
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("Informe o motivo do cancelamento.", field="reason")
    with atomic(session):
        sale = session.get(Sale, sale_id, with_for_update=True)
        if sale is None:
            raise NotFound("Venda não encontrada.")
        if sale.cancelled:
            raise BusinessError("Esta venda já foi cancelada.")
        now = clock.now()
        sale.cancelled_at = now
        sale.cancel_reason = reason[:255]
        for item in sorted(sale.items, key=lambda i: i.product_id):
            apply_movement(session, item.product_id, item.quantity, "cancelamento",
                           sale_id=sale.id, note=f"Venda #{sale.id} cancelada", user_id=user_id)
        for payment in sale.payments:
            if not payment.voided:
                payment.voided_at = now
                payment.void_reason = "Venda cancelada"
        session.flush()
        session.expire(sale)
    return sale
