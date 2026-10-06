from flask import Blueprint, Response, abort, render_template, request
from sqlalchemy import select

from .. import clock
from ..models import Customer
from ..schemas.parsing import Form
from ..services import reports as svc
from .helpers import db

bp = Blueprint("reports", __name__, url_prefix="/relatorios")


@bp.get("")
def index():
    return render_template("reports/index.html", catalog=svc.CATALOG)


@bp.get("/<key>")
def show(key: str):
    if key not in svc.BUILDERS:
        abort(404)
    f = Form(request.args)
    today = clock.today()
    d_from, d_to = f.date("de", "De"), f.date("ate", "Até")
    if "de" not in request.args and "ate" not in request.args and key in ("vendas", "produtos", "recebimentos", "movimento", "pessoas"):
        d_from, d_to = today.replace(day=1), today  # padrão: mês atual
    params = svc.ReportParams(d_from, d_to, f.optional_int("cliente"), None, f.optional_int("pessoa"), f.text("situacao"), f.text("ordem"))
    report = svc.build(db(), key, params, today)
    if request.args.get("exportar") == "csv":
        name = f"{key}-{today.isoformat()}.csv"
        return Response(svc.to_csv(report), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    customers = list(db().scalars(select(Customer).order_by(Customer.name)))
    from ..services import owners as owner_svc
    return render_template("reports/show.html", r=report, params=params, customers=customers, catalog=svc.CATALOG,
                           owners=owner_svc.list_owners(db()),
                           de=d_from, ate=d_to)
