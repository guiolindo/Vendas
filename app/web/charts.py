from flask import Blueprint, render_template, request, session

from .. import clock
from ..services import charts as svc
from ..services import owners as owner_svc
from . import charts_svg as svg
from .helpers import db

bp = Blueprint("charts", __name__, url_prefix="/graficos")


@bp.get("")
def index():
    people = owner_svc.list_owners(db())
    pessoa = request.args.get("pessoa", session.get("painel", "geral"))
    chosen = next((o for o in people if str(o.id) == pessoa), None)
    period = request.args.get("periodo", "30d")
    data = svc.build(db(), clock.today(), chosen.id if chosen else None, period)
    return render_template("charts/index.html", d=data, people=people, chosen=chosen, periods=svc.PERIODS, svg=svg,
                           aging_labels=svc.AGING)
