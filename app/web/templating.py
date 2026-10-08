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


MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def init_app(app) -> None:
    import hashlib
    from pathlib import Path


    app.config.setdefault("SEND_FILE_MAX_AGE_DEFAULT", 31536000 if app.config.get("PRODUCTION") else 0)
    _versions: dict[str, str] = {}

    @app.url_defaults
    def _bust_static_cache(endpoint, values):
        # ?v=<hash do arquivo>: cache longo no navegador sem prender versão antiga após um deploy.
        if endpoint != "static" or "filename" not in values:
            return
        name = values["filename"]
        if name not in _versions or not app.config.get("PRODUCTION"):
            path = Path(app.static_folder) / name
            _versions[name] = hashlib.sha256(path.read_bytes()).hexdigest()[:10] if path.is_file() else ""
        if _versions[name]:
            values["v"] = _versions[name]

    env = app.jinja_env
    from ..domain.sizes import SIZES
    env.globals["SIZES"] = SIZES
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
    env.filters["data_extenso"] = lambda d: f"{d.day} de {MESES[d.month - 1]} de {d.year}" if d else "—"
    env.filters["numero"] = lambda n: f"{int(n):06d}"
    env.filters["is_today"] = lambda d: d == clock.today()
    # globais: visíveis também dentro de macros importados
    env.globals["csrf_token"] = csrf_token
    env.globals["qs"] = qs
    env.globals["new_token"] = lambda: __import__("uuid").uuid4().hex

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
