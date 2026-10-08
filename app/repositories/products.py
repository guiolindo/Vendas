from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import Category, Product, Sale, SaleItem
from .common import like

SORTS = {
    "nome": Product.name, "preco": Product.price_cents, "estoque": Product.stock_qty,
    "codigo": Product.code, "cadastro": Product.created_at,
}


def products_query(q: str = "", category_id: int | None = None, status: str = "", stock: str = "",
                   sort: str = "nome", direction: str = "asc") -> Select:
    query = select(Product).options(selectinload(Product.category), selectinload(Product.sizes))
    if q.strip():
        term = like(q.strip())
        query = query.where(or_(Product.name.ilike(term, escape="\\"), Product.code.ilike(term, escape="\\"),
                                Product.sku.ilike(term, escape="\\")))
    if category_id:
        query = query.where(Product.category_id == category_id)
    if status == "ativos":
        query = query.where(Product.active.is_(True))
    elif status == "inativos":
        query = query.where(Product.active.is_(False))
    if stock == "baixo":
        query = query.where(Product.track_stock.is_(True), Product.stock_qty <= Product.min_stock, Product.stock_qty > 0)
    elif stock == "zerado":
        query = query.where(Product.track_stock.is_(True), Product.stock_qty == 0)
    column = SORTS.get(sort, Product.name)
    return query.order_by(column.desc() if direction == "desc" else column.asc(), Product.id)


def search_for_sale(session: Session, q: str, limit: int = 12) -> list[Product]:
    """Busca do PDV. Código/SKU exato vem primeiro; sem texto, mostra os mais vendidos."""
    q = q.strip()
    base = select(Product).options(selectinload(Product.sizes)).where(Product.active.is_(True))
    if not q:
        sold = (select(SaleItem.product_id, func.sum(SaleItem.quantity).label("qty"))
                .join(Sale, Sale.id == SaleItem.sale_id).where(Sale.cancelled_at.is_(None))
                .group_by(SaleItem.product_id).subquery())
        query = (base.outerjoin(sold, sold.c.product_id == Product.id)
                 .order_by(func.coalesce(sold.c.qty, 0).desc(), Product.name))
        return list(session.scalars(query.limit(limit)))
    term = like(q)
    exact = or_(func.lower(Product.code) == q.lower(), func.lower(Product.sku) == q.lower())
    query = base.where(or_(Product.name.ilike(term, escape="\\"), Product.code.ilike(term, escape="\\"),
                           Product.sku.ilike(term, escape="\\")))
    return list(session.scalars(query.order_by(exact.desc(), Product.name).limit(limit)))


def categories(session: Session) -> list[Category]:
    return list(session.scalars(select(Category).order_by(Category.name)))
