from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import clock
from ..errors import BusinessError
from ..models import Customer, Payment, Sale
from ..repositories import customers as repo
from ..repositories.common import paginate
from ..schemas.inputs import customer_from_form
from ..schemas.parsing import Form
from ..services import customers as svc
from ..services import payments
from ..domain.money import format_brl
from .helpers import (current_user_id, db, handle_business_errors, page_number,
                      remembered_args, safe_next)

bp = Blueprint("customers", __name__, url_prefix="/clientes")
FILTERS = ("q", "mostrar", "status")


@bp.get("")
def index():
    args, remembered = remembered_args("clientes", FILTERS)
    active = {"inativos": False, "todos": None}.get(args.get("status", ""), True)
    query = repo.balances_query(clock.today(), args.get("q", ""), args.get("mostrar", ""), active)
    query = query.order_by(Customer.name)
    page = paginate(db(), query, page_number(), 25, scalars=False)
    page.items = [repo.to_row(r) for r in page.items]
    return render_template("customers/list.html", page=page, args=args, remembered=remembered)


@bp.route("/novo", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        try:
            customer = svc.create_customer(db(), customer_from_form(request.form, creating=True))
        except BusinessError as e:
            flash(e.message, "error")
            return render_template("customers/form.html", customer=None, values=request.form, error_field=e.field), 422
        flash(f"Cliente “{customer.name}” cadastrado.", "success")
        return redirect(safe_next(request.args.get("next"), url_for("customers.detail", customer_id=customer.id)))
    return render_template("customers/form.html", customer=None, values={}, error_field=None)


@bp.get("/<int:customer_id>")
def detail(customer_id: int):
    customer = db().get(Customer, customer_id)
    if customer is None:
        flash("Cliente não encontrado.", "error")
        return redirect(url_for("customers.index"))
    summary = repo.summary(db(), customer_id, clock.today())
    sales = list(db().scalars(select(Sale).where(Sale.customer_id == customer_id)
                              .order_by(Sale.sale_date.desc(), Sale.id.desc())))
    open_sales = sorted((s for s in sales if s.remaining_cents > 0),
                        key=lambda s: (s.due_date is None, s.due_date, s.id))
    pays = list(db().scalars(select(Payment).join(Sale, Sale.id == Payment.sale_id)
                             .where(Sale.customer_id == customer_id, Payment.voided_at.is_(None))
                             .order_by(Payment.paid_at.desc(), Payment.id.desc()).limit(20)))
    return render_template("customers/detail.html", customer=customer, s=summary, sales=sales,
                           open_sales=open_sales, payments=pays)


@bp.route("/<int:customer_id>/editar", methods=["GET", "POST"])
def edit(customer_id: int):
    customer = db().get(Customer, customer_id)
    if customer is None:
        flash("Cliente não encontrado.", "error")
        return redirect(url_for("customers.index"))
    if request.method == "POST":
        try:
            svc.update_customer(db(), customer_id, customer_from_form(request.form))
        except BusinessError as e:
            flash(e.message, "error")
            return render_template("customers/form.html", customer=customer, values=request.form, error_field=e.field), 422
        flash("Alterações salvas.", "success")
        return redirect(url_for("customers.detail", customer_id=customer_id))
    return render_template("customers/form.html", customer=customer, values={}, error_field=None)


@bp.post("/<int:customer_id>/receber")
@handle_business_errors
def receive(customer_id: int):
    f = Form(request.form)
    made = payments.register_customer_payment(
        db(), customer_id, f.money("amount", "Valor", required=True), f.text("method"),
        f.date("paid_at", "Data"), f.text("note") or None, current_user_id())
    total = sum(p.amount_cents for p in made)
    n = len(made)
    flash(f"Pagamento de {format_brl(total)} registrado" + (f" e distribuído em {n} vendas." if n > 1 else "."), "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.post("/<int:customer_id>/ativo")
@handle_business_errors
def toggle(customer_id: int):
    customer = db().get(Customer, customer_id)
    active = request.form.get("active") == "1"
    data = customer_from_form({"name": customer.name, "phone": customer.phone or "", "document": customer.document or "",
                               "address": customer.address or "", "notes": customer.notes or "",
                               "active": "on" if active else ""})
    svc.update_customer(db(), customer_id, data)
    flash("Cliente reativado." if active else "Cliente desativado.", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.post("/<int:customer_id>/excluir")
@handle_business_errors
def delete(customer_id: int):
    svc.delete_customer(db(), customer_id)
    flash("Cliente excluído.", "success")
    return redirect(url_for("customers.index"))
