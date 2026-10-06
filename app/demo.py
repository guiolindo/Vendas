"""Dados de exemplo (`flask --app run demo-data`)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from . import clock
from .models import Product
from .services import customers as csvc
from .services import payments as psvc
from .services import products as pvc
from .services import sales as ssvc
from .services.sales import ItemInput, SaleInput


def load(session) -> str:
    if session.scalar(select(func.count()).select_from(Product)):
        return "O banco já tem produtos; nada foi carregado."
    today = clock.today()
    catalog = [("Camiseta básica", "Roupas", 4990, 2200, 40, 10), ("Calça jeans", "Roupas", 12990, 6000, 25, 8),
               ("Boné", "Acessórios", 3990, 1500, 4, 6), ("Cinto de couro", "Acessórios", 6990, 3000, 15, 5),
               ("Tênis esportivo", "Calçados", 24990, 12000, 12, 4), ("Meia (par)", "Acessórios", 1990, 700, 80, 20)]
    products = [pvc.create_product(session, pvc.ProductInput(name=n, category_name=c, price_cents=p, cost_cents=k,
                                                             initial_stock=s, min_stock=m)) for n, c, p, k, s, m in catalog]
    people = [csvc.create_customer(session, csvc.CustomerInput(name=n, phone=t)) for n, t in
              [("João da Silva", "(11) 91234-5678"), ("Maria Oliveira", "(21) 99876-5432"), ("Carlos Souza", None)]]
    d = timedelta
    plan = [(people[0], [(1, 2), (4, 1)], 20, 5000, -5), (people[0], [(0, 3)], 6, 0, -2),
            (people[1], [(1, 1), (3, 1)], 8, 15000, 5), (people[2], [(2, 2)], 1, 0, 0),
            (None, [(5, 3)], 0, 5970, None)]
    for customer, items, ago, paid, due_in in plan:
        sale = ssvc.create_sale(session, SaleInput(
            items=[ItemInput(products[i].id, q) for i, q in items], customer_id=customer.id if customer else None,
            sale_date=today - d(days=ago), paid_cents=paid, payment_method="pix" if paid else None,
            due_date=today + d(days=due_in) if due_in is not None else None))
    return "Dados de exemplo carregados."
