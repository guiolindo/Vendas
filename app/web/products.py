from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..errors import BusinessError
from ..models import Product, Sale, SaleItem, StockMovement
from ..repositories import products as repo
from ..repositories.common import paginate
from ..schemas.inputs import product_from_form
from ..schemas.parsing import Form
from ..services import owners as owner_svc
from ..services import products as svc
from ..services import stock
from .helpers import (audit_event, current_user_id, db, handle_business_errors, page_number,
                      remembered_args)

bp = Blueprint("products", __name__, url_prefix="/produtos")
FILTERS = ("q", "categoria", "status", "estoque", "ordem", "dir", "dono")


@bp.get("")
def index():
    args, remembered = remembered_args("produtos", FILTERS)
    page = paginate(db(), repo.products_query(
        args.get("q", ""), int(args["categoria"]) if args.get("categoria", "").isdigit() else None,
        args.get("status", ""), args.get("estoque", ""), args.get("ordem", "nome"), args.get("dir", "asc"),
        int(args["dono"]) if args.get("dono", "").isdigit() and int(args["dono"]) < 2**31 else None),
        page_number(), 25)
    return render_template("products/list.html", page=page, args=args, remembered=remembered,
                           categories=repo.categories(db()), owners=owner_svc.list_owners(db()))


@bp.route("/novo", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        try:
            product = svc.create_product(db(), product_from_form(request.form, creating=True), current_user_id())
        except BusinessError as e:
            flash(e.message, "error")
            return render_template("products/form.html", product=None, values=request.form,
                                   error_field=e.field, categories=repo.categories(db()), owners=owner_svc.list_owners(db(), only_active=True)), 422
        flash(f"Produto “{product.name}” cadastrado.", "success")
        if request.form.get("again"):
            return redirect(url_for("products.new"))
        return redirect(url_for("products.detail", product_id=product.id))
    return render_template("products/form.html", product=None, values={}, error_field=None,
                           categories=repo.categories(db()), owners=owner_svc.list_owners(db(), only_active=True))


@bp.get("/<int:product_id>")
def detail(product_id: int):
    product = db().get(Product, product_id)
    if product is None:
        flash("Produto não encontrado.", "error")
        return redirect(url_for("products.index"))
    movements = list(db().scalars(select(StockMovement).where(StockMovement.product_id == product_id)
                                  .order_by(StockMovement.id.desc()).limit(30)))
    sales = db().execute(
        select(SaleItem, Sale).join(Sale, Sale.id == SaleItem.sale_id).options(selectinload(Sale.customer))
        .where(SaleItem.product_id == product_id).order_by(Sale.sale_date.desc(), Sale.id.desc()).limit(30)
    ).all()
    totals = db().execute(
        select(func.coalesce(func.sum(SaleItem.quantity), 0), func.coalesce(func.sum(SaleItem.total_cents), 0))
        .join(Sale, Sale.id == SaleItem.sale_id).where(SaleItem.product_id == product_id, Sale.cancelled_at.is_(None))
    ).one()
    return render_template("products/detail.html", product=product, movements=movements, sales=sales,
                           sold_qty=totals[0], sold_cents=totals[1])


@bp.route("/<int:product_id>/editar", methods=["GET", "POST"])
def edit(product_id: int):
    product = db().get(Product, product_id)
    if product is None:
        flash("Produto não encontrado.", "error")
        return redirect(url_for("products.index"))
    if request.method == "POST":
        try:
            svc.update_product(db(), product_id, product_from_form(request.form))
        except BusinessError as e:
            flash(e.message, "error")
            return render_template("products/form.html", product=product, values=request.form,
                                   error_field=e.field, categories=repo.categories(db()), owners=owner_svc.list_owners(db(), only_active=True)), 422
        flash("Alterações salvas.", "success")
        return redirect(url_for("products.detail", product_id=product_id))
    return render_template("products/form.html", product=product, values={}, error_field=None,
                           categories=repo.categories(db()), owners=owner_svc.list_owners(db(), only_active=True))


@bp.post("/<int:product_id>/estoque")
@handle_business_errors
def stock_action(product_id: int):
    f = Form(request.form)
    note = f.text("note") or None
    if request.form.get("kind") == "ajuste":
        stock.adjust_to_count(db(), product_id, f.integer("quantity", "Quantidade contada", default=-1, minimum=-1),
                              note, current_user_id())
        flash("Estoque ajustado.", "success")
    else:
        qty = f.integer("quantity", "Quantidade", default=0)
        stock.register_entry(db(), product_id, qty, note, current_user_id())
        flash(f"Entrada de {qty} registrada.", "success")
    return redirect(url_for("products.detail", product_id=product_id))


@bp.post("/<int:product_id>/ativo")
@handle_business_errors
def toggle(product_id: int):
    active = request.form.get("active") == "1"
    svc.set_active(db(), product_id, active)
    flash("Produto reativado." if active else "Produto desativado. Ele some da tela de vendas, mas o histórico continua.", "success")
    return redirect(url_for("products.detail", product_id=product_id))


@bp.post("/<int:product_id>/excluir")
@handle_business_errors
def delete(product_id: int):
    svc.delete_product(db(), product_id)
    audit_event("produto_excluido", f"#{product_id}")
    flash("Produto excluído.", "success")
    return redirect(url_for("products.index"))
