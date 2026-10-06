"""Filtros e variáveis globais dos templates."""
from __future__ import annotations

from datetime import date
from urllib.parse import urlencode

from flask import g, request, session
from markupsafe import Markup

from .. import clock
from ..domain.money import format_brl
from ..domain.payment_methods import METHODS, label as method_label
from ..domain.status import LABELS, Status, due_hint
from ..models.catalog import MOVEMENT_KINDS
from .helpers import csrf_token


def init_app(app) -> None:
    env = app.jinja_env
    env.filters["brl"] = format_brl
    env.filters["brl_plain"] = lambda c: format_brl(c, symbol=False)
    env.filters["data"] = lambda d: d.strftime("%d/%m/%Y") if d else "—"
    env.filters["data_curta"] = lambda d: d.strftime("%d/%m") if d else "—"
    env.filters["iso"] = lambda d: d.isoformat() if d else ""
    env.filters["hora"] = lambda d: d.strftime("%d/%m/%Y %H:%M") if d else "—"
    env.filters["status_label"] = lambda s: LABELS[Status(s)]
    env.filters["method_label"] = method_label
    env.filters["movement_label"] = lambda k: MOVEMENT_KINDS.get(k, k)
    env.filters["due_hint"] = lambda d: due_hint(d, clock.today())
    env.filters["qtd"] = lambda n: f"{n:,}".replace(",", ".")
    env.filters["is_today"] = lambda d: d == clock.today()
    # globais: visíveis também dentro de macros importados
    env.globals["csrf_token"] = csrf_token
    env.globals["qs"] = qs

    @app.context_processor
    def inject():
        return {
            "today": clock.today(),
            "methods": METHODS,
            "current_user": g.get("user"),
        }


def qs(**changes) -> str:
    """Monta a query string atual com alterações (usado em ordenação, abas e paginação)."""
    args = {k: v for k, v in request.args.items()}
    for key, value in changes.items():
        if value in (None, ""):
            args.pop(key, None)
        else:
            args[key] = value
    return "?" + urlencode(args) if args else "?"
