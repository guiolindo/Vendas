from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import atomic
from ..domain.text import limited
from ..errors import BusinessError, NotFound
from ..models import Category, Product, SaleItem, StockMovement
from ..domain.money import MAX_CENTS
from .stock import MAX_QTY, apply_movement


@dataclass
class ProductInput:
    name: str
    price_cents: int
    cost_cents: int = 0
    code: str | None = None
    sku: str | None = None
    category_name: str | None = None
    unit: str = "un"
    min_stock: int = 0
    description: str | None = None
    active: bool = True
    initial_stock: int = 0  # só na criação


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _validate(data: ProductInput) -> None:
    if not (data.name or "").strip():
        raise BusinessError("Informe o nome do produto.", field="name")
    if data.price_cents < 0:
        raise BusinessError("O preço de venda não pode ser negativo.", field="price")
    if data.cost_cents < 0:
        raise BusinessError("O custo não pode ser negativo.", field="cost")
    if not 0 <= data.min_stock <= MAX_QTY:
        raise BusinessError("O estoque mínimo precisa ser zero ou mais.", field="min_stock")
    limited(data.name, 160, "Nome", "name"); limited(data.code, 40, "Código", "code"); limited(data.sku, 60, "SKU", "sku")
    limited(data.category_name, 80, "Categoria", "category"); limited(data.unit, 10, "Unidade", "unit")
    limited(data.description, 2000, "Descrição", "description")
    if data.price_cents > MAX_CENTS or data.cost_cents > MAX_CENTS:
        raise BusinessError("Valor muito alto.", field="price")
    if not 0 <= data.initial_stock <= MAX_QTY:
        raise BusinessError("O estoque inicial precisa ser zero ou mais.", field="initial_stock")


def _category(session: Session, name: str | None) -> Category | None:
    name = _clean(name)
    if not name:
        return None
    existing = session.scalar(select(Category).where(func.lower(Category.name) == name.lower()))
    if existing:
        return existing
    category = Category(name=name)
    session.add(category)
    session.flush()
    return category


def _next_code(session: Session) -> str:
    last = session.scalar(select(func.max(Product.id))) or 0
    number = last + 1
    while session.scalar(select(Product.id).where(Product.code == f"P{number:04d}")):
        number += 1
    return f"P{number:04d}"


def _check_unique(session: Session, code: str, sku: str | None, own_id: int | None) -> None:
    clash = session.scalar(select(Product.id).where(Product.code == code, Product.id != (own_id or 0)))
    if clash:
        raise BusinessError(f"Já existe um produto com o código “{code}”.", field="code")
    if sku:
        clash = session.scalar(select(Product.id).where(Product.sku == sku, Product.id != (own_id or 0)))
        if clash:
            raise BusinessError(f"Já existe um produto com o SKU “{sku}”.", field="sku")


def _fill(session: Session, product: Product, data: ProductInput) -> None:
    code = _clean(data.code) or product.code or _next_code(session)
    sku = _clean(data.sku)
    _check_unique(session, code, sku, product.id)
    product.code = code
    product.sku = sku
    product.name = data.name.strip()
    product.price_cents = data.price_cents
    product.cost_cents = data.cost_cents
    product.min_stock = data.min_stock
    product.unit = _clean(data.unit) or "un"
    product.description = _clean(data.description)
    product.active = data.active
    product.category = _category(session, data.category_name)


def create_product(session: Session, data: ProductInput, user_id: int | None = None) -> Product:
    _validate(data)
    with atomic(session):
        product = Product(code="", price_cents=0, name="")
        _fill(session, product, data)
        session.add(product)
        session.flush()
        if data.initial_stock > 0:
            apply_movement(session, product.id, data.initial_stock, "inicial", user_id=user_id)
    return product


def update_product(session: Session, product_id: int, data: ProductInput) -> Product:
    _validate(data)
    with atomic(session):
        product = session.get(Product, product_id)
        if product is None:
            raise NotFound("Produto não encontrado.")
        _fill(session, product, data)
    return product


def set_active(session: Session, product_id: int, active: bool) -> Product:
    with atomic(session):
        product = session.get(Product, product_id)
        if product is None:
            raise NotFound("Produto não encontrado.")
        product.active = active
    return product


def delete_product(session: Session, product_id: int) -> None:
    """Só exclui produto que nunca foi vendido. Senão, o histórico perderia sentido."""
    with atomic(session):
        product = session.get(Product, product_id)
        if product is None:
            raise NotFound("Produto não encontrado.")
        sold = session.scalar(select(func.count()).select_from(SaleItem).where(SaleItem.product_id == product_id))
        if sold:
            raise BusinessError(
                "Este produto já foi vendido e não pode ser excluído. Desative-o para tirá-lo da lista de vendas."
            )
        for movement in session.scalars(select(StockMovement).where(StockMovement.product_id == product_id)):
            session.delete(movement)
        session.delete(product)
