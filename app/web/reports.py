from flask import Blueprint, Response, abort, render_template, request
from sqlalchemy import select

from .. import clock
from ..models import Customer
from ..schemas.parsing import Form
from ..services import reports as svc
from ..services import xlsx as xlsx_svc
from .helpers import db

bp = Blueprint("reports", __name__, url_prefix="/relatorios")


@bp.get("")
def index():
    return render_template("reports/index.html", catalog=svc.CATALOG)


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PACKAGE = ("pessoas", "vendas", "recebimentos", "a-receber", "produtos", "margem-produtos", "estoque", "clientes")


@bp.get("/pacote")
def package():
    """Uma única planilha do Excel com todos os relatórios, uma aba para cada (bom para o fechamento do mês)."""
    f = Form(request.args)
    today = clock.today()
    d_from, d_to = f.date("de", "De") or today.replace(day=1), f.date("ate", "Até") or today
    params = svc.ReportParams(d_from, d_to, None, None, f.optional_int("pessoa"))
    reports = [svc.build(db(), key, params, today) for key in PACKAGE]
    data = xlsx_svc.to_xlsx(reports, xlsx_svc.period_text(d_from, d_to))
    return Response(data, mimetype=XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="vendas-{d_from.isoformat()}-a-{d_to.isoformat()}.xlsx"'})


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
    if request.args.get("exportar") == "xlsx":
        data = xlsx_svc.to_xlsx([report], xlsx_svc.period_text(d_from, d_to) if "period" in report.filters else "")
        return Response(data, mimetype=XLSX_MIME,
                        headers={"Content-Disposition": f'attachment; filename="{key}-{today.isoformat()}.xlsx"'})
    if request.args.get("exportar") == "csv":
        name = f"{key}-{today.isoformat()}.csv"
        return Response(svc.to_csv(report), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    customers = list(db().scalars(select(Customer).order_by(Customer.name)))
    from ..services import owners as owner_svc
    return render_template("reports/show.html", r=report, params=params, customers=customers, catalog=svc.CATALOG,
                           owners=owner_svc.list_owners(db()),
                           de=d_from, ate=d_to)
