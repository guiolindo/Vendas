from flask import Blueprint, render_template, request, session

from .. import clock
from ..services import dashboard
from ..services import owners as owner_svc
from .helpers import db

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    """Painel geral ou o painel simples de uma pessoa (?pessoa=ID). A escolha fica lembrada."""
    people = owner_svc.list_owners(db())
    choice = request.args.get("pessoa", session.get("painel", "geral"))
    chosen = next((o for o in people if str(o.id) == choice), None)
    session["painel"] = str(chosen.id) if chosen else "geral"
    today = clock.today()
    d = dashboard.build_for_owner(db(), today, chosen.id) if chosen else dashboard.build(db(), today)
    return render_template("dashboard.html", d=d, people=people, chosen=chosen)
