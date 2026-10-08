from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import atomic
from ..domain.text import limited
from ..errors import BusinessError, NotFound
from ..domain.sizes import SIZES, clean_sizes
from ..models import Category, Product, ProductSize, SaleItem, StockMovement
from ..domain.money import MAX_CENTS, format_brl
from . import costs
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
    track_stock: bool = True     # False: vende sem controlar quantidade
    sizes: list[str] = field(default_factory=list)            # tamanhos do produto (vazio = sem tamanhos)
    initial_by_size: dict[str, int] = field(default_factory=dict)   # só na criação: estoque inicial de cada tamanho


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
        raise BusinessError(f"O preço ou o custo passa do limite de {format_brl(MAX_CENTS)}.", field="price")
    if not 0 <= data.initial_stock <= MAX_QTY:
        raise BusinessError("O estoque inicial precisa ser zero ou mais.", field="initial_stock")
    try:
        clean_sizes(data.sizes)
    except ValueError as e:
        raise BusinessError(f"Tamanho inválido: {e}. Use {', '.join(SIZES)}.", field="size") from None
    for qty in data.initial_by_size.values():
        if not isinstance(qty, int) or not 0 <= qty <= MAX_QTY:
            raise BusinessError("O estoque inicial de cada tamanho precisa ser zero ou mais.", field="initial_stock")


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


def _fill(session: Session, product: Product, data: ProductInput, user_id: int | None = None) -> None:
    code = _clean(data.code) or product.code or _next_code(session)
    sku = _clean(data.sku)
    _check_unique(session, code, sku, product.id)
    product.code = code
    product.sku = sku
    product.name = data.name.strip()
    product.price_cents = data.price_cents
    new_product = product.id is None
    if new_product:
        product.cost_cents = 0                    # o cadastro entra no histórico de custo (abaixo), depois do flush
    elif data.cost_cents != product.cost_cents:
        costs.set_cost(session, product, data.cost_cents, "edição", user_id)   # só vale para as próximas vendas
    product.track_stock = data.track_stock
    product.min_stock = data.min_stock if data.track_stock else product.min_stock or 0
    product.unit = _clean(data.unit) or "un"
    product.description = _clean(data.description)
    product.active = data.active
    product.category = _category(session, data.category_name)


def create_product(session: Session, data: ProductInput, user_id: int | None = None) -> Product:
    _validate(data)
    with atomic(session):
        product = Product(code="", price_cents=0, name="")
        _fill(session, product, data, user_id)
        session.add(product)
        session.flush()
        if data.cost_cents > 0:
            costs.set_cost(session, product, data.cost_cents, "cadastro", user_id)
        wanted = clean_sizes(data.sizes)
        if wanted:
            for size in wanted:
                product.sizes.append(ProductSize(size=size, position=SIZES.index(size)))
            session.flush()
            if data.track_stock:
                for size in wanted:
                    qty = data.initial_by_size.get(size, 0)
                    if qty > 0:
                        movement = apply_movement(session, product.id, qty, "inicial", size=size, user_id=user_id)
                        movement.unit_cost_cents = data.cost_cents or None
        elif data.track_stock and data.initial_stock > 0:
            movement = apply_movement(session, product.id, data.initial_stock, "inicial", user_id=user_id)
            movement.unit_cost_cents = data.cost_cents or None
    return product


def update_product(session: Session, product_id: int, data: ProductInput, user_id: int | None = None) -> Product:
    _validate(data)
    with atomic(session):
        product = session.get(Product, product_id)
        if product is None:
            raise NotFound("Produto não encontrado.")
        _fill(session, product, data, user_id)
        _sync_sizes(session, product, clean_sizes(data.sizes))
    return product


def _sync_sizes(session: Session, product: Product, wanted: list[str]) -> None:
    """Ajusta os tamanhos do produto. Tirar um tamanho só se o estoque dele estiver zerado;
    começar a usar tamanhos num produto que já tem estoque sem tamanho exige zerar antes."""
    current = {s.size: s for s in product.sizes}
    add = [s for s in wanted if s not in current]
    drop = [s for s in current if s not in wanted]
    if add and not current and product.track_stock and product.stock_qty > 0:
        raise BusinessError(
            f"Este produto já tem {product.stock_qty} {product.unit} em estoque sem tamanho. Zere o estoque em "
            "“Contei e o número é outro” e depois escolha os tamanhos: assim cada tamanho começa com a sua quantidade certa.",
            field="size")
    for size in drop:
        row = current[size]
        if product.track_stock and row.stock_qty > 0:
            raise BusinessError(f"O tamanho {size} ainda tem {row.stock_qty} {product.unit} em estoque. "
                                "Zere em “Contei e o número é outro” antes de tirar o tamanho.", field="size")
        product.stock_qty = max(product.stock_qty - row.stock_qty, 0)   # o saldo do produto é a soma dos tamanhos
        product.sizes.remove(row)
    for size in add:
        product.sizes.append(ProductSize(size=size, position=SIZES.index(size)))
    session.flush()


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
        for movement in session.scalars(select(StockMovement).where(StockMovement.product_id == product_id)
                                        .order_by(StockMovement.id.desc())):
            session.delete(movement)       # do mais novo ao mais antigo: o estorno aponta para a compra original
            session.flush()
        session.delete(product)
