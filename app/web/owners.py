from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import func, select

from ..errors import BusinessError
from ..models import Product
from ..services import owners as svc
from .helpers import audit_event, db, handle_business_errors

bp = Blueprint("owners", __name__, url_prefix="/pessoas")


@bp.get("")
def index():
    counts = dict(db().execute(select(Product.owner_id, func.count()).group_by(Product.owner_id)).all())
    return render_template("owners/index.html", owners=svc.list_owners(db()), counts=counts)


@bp.post("/nova")
@handle_business_errors
def create():
    owner = svc.create_owner(db(), request.form.get("name", ""))
    audit_event("pessoa_criada", owner.name)
    flash(f"Pessoa cadastrada: {owner.name}.", "success")
    return redirect(url_for("owners.index"))


@bp.post("/<int:owner_id>/nome")
@handle_business_errors
def rename(owner_id: int):
    owner = svc.rename_owner(db(), owner_id, request.form.get("name", ""))
    flash("Nome alterado.", "success")
    return redirect(url_for("owners.index"))


@bp.post("/<int:owner_id>/excluir")
@handle_business_errors
def delete(owner_id: int):
    name = svc.delete_owner(db(), owner_id)
    audit_event("pessoa_excluida", name)
    flash(f"Pessoa excluída: {name}.", "success")
    return redirect(url_for("owners.index"))


@bp.post("/<int:owner_id>/ativo")
@handle_business_errors
def toggle(owner_id: int):
    active = request.form.get("active") == "1"
    svc.set_owner_active(db(), owner_id, active)
    flash("Pessoa reativada." if active else "Pessoa desativada. Ela não recebe produtos novos, mas o histórico continua.", "success")
    return redirect(url_for("owners.index"))
