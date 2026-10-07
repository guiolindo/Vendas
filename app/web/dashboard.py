from flask import Blueprint, render_template, request, session

from .. import clock
from ..services import dashboard
from ..services import owners as owner_svc
from ..services import settings as settings_svc
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
    return render_template("dashboard.html", d=d, people=people, chosen=chosen,
                           start=None if chosen else _getting_started(people))


def _getting_started(people) -> list[dict] | None:
    """Passo a passo para quem acabou de começar. Some sozinho depois da primeira venda."""
    from sqlalchemy import func, select
    from ..models import Customer, Product, Sale
    s = db()
    if s.scalar(select(func.count()).select_from(Sale)):
        return None
    has_products = bool(s.scalar(select(func.count()).select_from(Product)))
    steps = [
        {"title": "Cadastre quem vende", "hint": "Cada produto pertence a uma pessoa, e cada uma tem o seu painel.",
         "url": "owners.index", "done": bool(people), "cta": "Cadastrar pessoas"},
        {"title": "Cadastre os produtos", "hint": "Diga quanto cada um custou para você: é assim que o sistema calcula a sua margem.",
         "url": "products.new", "done": has_products, "cta": "Cadastrar produto"},
        {"title": "Cadastre clientes que compram a prazo", "hint": "Também dá para cadastrar na hora da venda.",
         "url": "customers.new", "done": bool(s.scalar(select(func.count()).select_from(Customer))), "cta": "Cadastrar cliente", "optional": True},
        {"title": "Faça a primeira venda", "hint": "Escolha os produtos, veja o total e registre o pagamento.",
         "url": "sales.new", "done": False, "cta": "Nova venda"},
        {"title": "Coloque o nome do seu negócio", "hint": "Aparece no comprovante que você imprime.",
         "url": "settings.index", "done": settings_svc.load(s)["business_name"] != settings_svc.DEFAULT_NAME, "cta": "Abrir configurações", "optional": True},
    ]
    nxt = next((st for st in steps if not st["done"] and not st.get("optional")), None)
    for st in steps:
        st["next"] = st is nxt
    return steps
