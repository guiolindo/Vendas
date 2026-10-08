"""Endpoints JSON usados pela tela de venda (busca rápida e cadastro de cliente na hora)."""
from flask import Blueprint, jsonify, request
from sqlalchemy import select

from ..errors import BusinessError
from ..models import Customer
from ..repositories import customers as customer_repo
from ..repositories import products as product_repo
from ..services import customers as customer_svc
from .. import clock
from .helpers import db

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/produtos")
def products():
    items = product_repo.search_for_sale(db(), request.args.get("q", ""))
    return jsonify([{
        "id": p.id, "name": p.name, "code": p.code, "sku": p.sku, "unit": p.unit,
        "price_cents": p.price_cents, "cost_cents": p.cost_cents, "stock": p.stock_qty if p.track_stock else None,
        "sizes": [{"size": s.size, "stock": s.stock_qty if p.track_stock else None} for s in p.sizes],
    } for p in items])


@bp.get("/clientes")
def customers():
    q = request.args.get("q", "")
    query = customer_repo.balances_query(clock.today(), q)
    if request.args.get("id", "").isdigit():
        query = query.where(Customer.id == int(request.args["id"]))
    rows = db().execute(query.order_by(Customer.name).limit(8)).all()
    out = []
    for r in map(customer_repo.to_row, rows):
        out.append({"id": r.customer.id, "name": r.customer.name, "phone": r.customer.phone or "",
                    "pending_cents": r.pending, "overdue_cents": r.overdue})
    return jsonify(out)


@bp.post("/clientes")
def create_customer():
    data = request.get_json(silent=True) or {}
    try:
        c = customer_svc.create_customer(db(), customer_svc.CustomerInput(
            name=str(data.get("name", "")), phone=str(data.get("phone", "")) or None))
    except BusinessError as e:
        return jsonify(ok=False, message=e.message), 422
    return jsonify(ok=True, customer={"id": c.id, "name": c.name, "phone": c.phone or "",
                                      "pending_cents": 0, "overdue_cents": 0})
