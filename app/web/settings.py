from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..errors import BusinessError
from ..services import settings as svc
from .helpers import audit_event, db

bp = Blueprint("settings", __name__, url_prefix="/configuracoes")


@bp.route("", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            svc.save(db(), request.form, request.form.get("show_seller") == "on")
        except BusinessError as e:
            flash(e.message, "error")
            return render_template("settings/index.html", values={**svc.load(db()), **request.form.to_dict()},
                                   fields=svc.FIELDS, error_field=e.field), 422
        audit_event("configuracoes_salvas")
        flash("Configurações salvas. Elas já aparecem no comprovante.", "success")
        return redirect(url_for("settings.index"))
    return render_template("settings/index.html", values=svc.load(db()), fields=svc.FIELDS, error_field=None)
