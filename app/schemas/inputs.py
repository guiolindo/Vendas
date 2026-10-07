"""Do formulário/JSON da web para os objetos dos serviços."""
from __future__ import annotations

from typing import Any, Mapping

from ..domain.money import MoneyError, parse_money
from ..domain.text import MAX_ID
from ..errors import BusinessError
from ..services.customers import CustomerInput
from ..services.products import ProductInput
from ..services.sales import ItemInput, SaleInput
from .parsing import Form, parse_percent


def product_from_form(data: Mapping[str, str], creating: bool = False) -> ProductInput:
    f = Form(data)
    return ProductInput(
        name=f.text("name"),
        price_cents=f.money("price", "Preço de venda", required=True),
        cost_cents=f.money("cost", "Custo"),
        code=f.text("code") or None, sku=f.text("sku") or None,
        category_name=f.text("category") or None,
        unit=f.text("unit", "un"),
        min_stock=f.integer("min_stock", "Estoque mínimo"),
        description=f.text("description") or None,
        active=True if creating else f.flag("active"),
        initial_stock=f.integer("initial_stock", "Estoque inicial") if creating else 0,
        owner_id=f.optional_int("owner"),
    )


def customer_from_form(data: Mapping[str, str], creating: bool = False) -> CustomerInput:
    f = Form(data)
    return CustomerInput(
        name=f.text("name"), phone=f.text("phone") or None, document=f.text("document") or None,
        address=f.text("address") or None, notes=f.text("notes") or None,
        active=True if creating else f.flag("active"),
    )


def _money(value: Any, label: str, field: str) -> int:
    if value in (None, ""):
        return 0
    try:
        return parse_money(value)
    except MoneyError as e:
        raise BusinessError(f"{label}: {e}", field=field) from None


def sale_from_json(payload: dict) -> SaleInput:
    items = []
    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list) or len(raw_items) > 200:
        raise BusinessError("A venda pode ter no máximo 200 itens.", field="items")
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BusinessError("Um dos itens da venda está com dados inválidos. Remova-o e adicione de novo.", field="items")
        try:
            quantity = int(raw.get("quantity"))
            product_id = int(raw.get("product_id"))
            if isinstance(raw.get("quantity"), bool) or not 0 < product_id <= MAX_ID or abs(quantity) > 10**9:
                raise ValueError
        except (TypeError, ValueError):
            raise BusinessError("Confira a quantidade dos itens: use números inteiros, de 1 para cima.", field="items") from None
        price = raw.get("price")
        items.append(ItemInput(product_id, quantity, None if price in (None, "") else _money(price, "Preço", "items")))

    f = Form({k: ("" if v is None else str(v)) for k, v in payload.items() if not isinstance(v, (list, dict))})
    discount_raw = f.text("discount")
    discount_cents, percent = 0, None
    if discount_raw:
        if payload.get("discount_type") == "percent":
            percent = parse_percent(discount_raw)
        else:
            discount_cents = _money(discount_raw, "Desconto", "discount")
    return SaleInput(
        items=items, customer_id=f.optional_int("customer_id"),
        discount_cents=discount_cents, discount_percent=percent,
        paid_cents=_money(payload.get("paid"), "Valor pago", "paid"),
        payment_method=f.text("payment_method") or None,
        due_date=f.date("due_date", "Vencimento"), sale_date=f.date("sale_date", "Data da venda"),
        notes=f.text("notes") or None, client_token=f.text("client_token") or None,
    )
