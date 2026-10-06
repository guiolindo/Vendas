"""Cobranças: o que vence, o que venceu e o que já foi pago."""
from flask import Blueprint, render_template

from .. import clock
from ..repositories import customers as customer_repo
from ..repositories import sales as repo
from ..repositories.common import paginate
from ..schemas.parsing import Form
from ..models import Customer
from sqlalchemy import select
from .helpers import db, page_number, remembered_args

bp = Blueprint("collections", __name__, url_prefix="/cobrancas")
FILTERS = ("aba", "cliente", "q", "venc_de", "venc_ate", "min", "max")
TABS = [("abertos", "Em aberto"), ("vencidos", "Vencidos"), ("hoje", "Vencem hoje"),
        ("proximos", "Próximos 7 dias"), ("parciais", "Pagos em parte"), ("pagos", "Pagos")]


@bp.get("")
def index():
    args, remembered = remembered_args("cobrancas", FILTERS)
    today = clock.today()
    tab = args.get("aba") if args.get("aba") in dict(TABS) else "vencidos"
    errors = []

    def money(name, label):
        try:
            return Form({name: args.get(name, "")}).money(name, label, default=None)
        except Exception as e:  # valor digitado errado: ignora e avisa
            errors.append(getattr(e, "message", str(e)))
            return None

    f = repo.SaleFilters(
        q=args.get("q", ""), bucket=tab, customer_id=int(args["cliente"]) if args.get("cliente", "").isdigit() else None,
        due_from=_d(args.get("venc_de")), due_to=_d(args.get("venc_ate")),
        min_cents=money("min", "Valor mínimo"), max_cents=money("max", "Valor máximo"),
        amount_field="total" if tab == "pagos" else "remaining")
    page = paginate(db(), repo.sales_query(f, today, order="due"), page_number(), 25)
    customers = list(db().scalars(select(Customer).order_by(Customer.name)))
    return render_template("collections/index.html", page=page, tab=tab, tabs=TABS, args=args, remembered=remembered,
                           summary=repo.bucket_summary(db(), today), customers=customers, errors=errors)


def _d(raw):
    return Form({"d": raw or ""}).date("d", "Data") if raw else None
