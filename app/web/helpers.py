"""Utilidades das rotas: sessão de banco, CSRF, filtros lembrados, paginação."""
from __future__ import annotations

import secrets
from functools import wraps
from typing import Callable
from urllib.parse import urlparse

from flask import current_app, flash, g, redirect, request, session, url_for
from sqlalchemy.orm import Session

from ..errors import BusinessError, NotFound


def db() -> Session:
    if "db" not in g:
        g.db = current_app.extensions["database"].session()
    return g.db


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def safe_next(target: str | None, fallback: str) -> str:
    """Só aceita redirecionamento para caminhos do próprio sistema."""
    if target and target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc:
        return target
    return fallback


def current_user_id() -> int | None:
    return session.get("user_id")


def handle_business_errors(view: Callable) -> Callable:
    """Erros de regra de negócio viram aviso amigável, sem estourar para o usuário."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except NotFound as e:
            flash(e.message, "error")
            return redirect(url_for("dashboard.index"))
        except BusinessError as e:
            flash(e.message, "error")
            return redirect(safe_next(request.form.get("next") or request.referrer and urlparse(request.referrer).path, url_for("dashboard.index")))
    return wrapper


REMEMBER_KEY = "filtros"


def remembered_args(page: str, keep: tuple[str, ...]) -> tuple[dict, bool]:
    """Filtros persistentes: se a pessoa abrir a lista sem filtros, volta ao último filtro usado.
    Devolve (argumentos efetivos, vieram_da_memoria)."""
    memory = session.setdefault(REMEMBER_KEY, {})
    if request.args.get("limpar"):
        memory.pop(page, None)
        session.modified = True
        return {}, False
    args = {k: request.args[k] for k in keep if request.args.get(k)}
    has_any = any(k in request.args for k in request.args if k != "pagina")
    if has_any:
        if args:
            memory[page] = args
        else:
            memory.pop(page, None)
        session.modified = True
        return args, False
    saved = memory.get(page)
    if saved:
        return dict(saved), True
    return {}, False


def page_number() -> int:
    raw = request.args.get("pagina", "1")
    return int(raw) if raw.isdigit() else 1
