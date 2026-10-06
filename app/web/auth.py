from __future__ import annotations

import time

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from sqlalchemy import func, select

from ..models import User
from .helpers import csrf_token, db, safe_next

bp = Blueprint("auth", __name__)

MAX_ATTEMPTS, WINDOW = 5, 300  # 5 erros em 5 minutos bloqueiam a tentativa seguinte
_failures: dict[str, list[float]] = {}


def _throttle_key() -> str:
    return f"{request.remote_addr}|{request.form.get('username', '').strip().lower()}"


def _blocked(key: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(key, []) if now - t < WINDOW]
    _failures[key] = recent
    return len(recent) >= MAX_ATTEMPTS
PUBLIC = {"auth.login", "auth.setup", "static", "health"}


def init_app(app) -> None:
    @app.before_request
    def _guard():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not sent or sent != session.get("csrf"):
                if request.is_json or request.path.startswith("/api/"):
                    abort(400, "Sessão expirada. Recarregue a página.")
                flash("Sua sessão expirou. Tente novamente.", "error")
                return redirect(request.referrer or url_for("auth.login"))
        endpoint = request.endpoint
        if endpoint is None or endpoint in PUBLIC:
            return None
        has_users = db().scalar(select(func.count()).select_from(User)) > 0
        db().rollback()
        if not has_users:
            return redirect(url_for("auth.setup"))
        user_id = session.get("user_id")
        user = db().get(User, user_id) if user_id else None
        if user is None or not user.active:
            session.pop("user_id", None)
            if request.path.startswith("/api/"):
                abort(401)
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        g.user = user
        return None


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        key = _throttle_key()
        if _blocked(key):
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "error")
            return render_template("auth/login.html", csrf_token=csrf_token()), 429
        user = db().scalar(select(User).where(func.lower(User.username) == request.form.get("username", "").strip().lower()))
        if user and user.active and user.check_password(request.form.get("password", "")):
            _failures.pop(key, None)
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            return redirect(safe_next(request.args.get("next"), url_for("dashboard.index")))
        _failures.setdefault(key, []).append(time.monotonic())
        flash("Usuário ou senha incorretos.", "error")
    return render_template("auth/login.html", csrf_token=csrf_token())


@bp.route("/configurar", methods=["GET", "POST"])
def setup():
    """Primeiro acesso: cria o usuário administrador."""
    if db().scalar(select(func.count()).select_from(User)) > 0:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not name or not username:
            flash("Informe seu nome e um nome de usuário.", "error")
        elif len(password) < 8:
            flash("A senha precisa ter pelo menos 8 caracteres.", "error")
        elif password != request.form.get("password2", ""):
            flash("As duas senhas não são iguais.", "error")
        else:
            user = User(name=name, username=username)
            user.set_password(password)
            db().add(user)
            db().commit()
            session.clear()
            session["user_id"] = user.id
            flash(f"Tudo pronto, {name.split()[0]}. Comece cadastrando seus produtos.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/setup.html", csrf_token=csrf_token())


@bp.route("/sair", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
