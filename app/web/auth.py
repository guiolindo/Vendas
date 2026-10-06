"""Login, sessão, primeiro acesso e troca de senha."""
from __future__ import annotations

import hmac

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)
from sqlalchemy import func, select

from ..models import User
from ..services import security as sec
from .helpers import audit_event, client_ip_hash, csrf_token, db, safe_next

bp = Blueprint("auth", __name__)
PUBLIC = {"auth.login", "auth.setup", "static", "health", "manifest", "favicon", "robots"}


def init_app(app) -> None:
    @app.before_request
    def _guard():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
            expected = session.get("csrf") or ""
            if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
                if request.is_json or request.path.startswith("/api/") or request.path.startswith("/vendas/nova"):
                    abort(400, "Sessão expirada. Recarregue a página.")
                flash("Sua sessão expirou. Tente novamente.", "error")
                return redirect(request.referrer or url_for("auth.login"))
        endpoint = request.endpoint
        if endpoint is None or endpoint in PUBLIC:
            return None
        has_users = db().scalar(select(func.count()).select_from(User)) > 0
        if not has_users:
            db().rollback()
            return redirect(url_for("auth.setup"))
        sid = session.get("sid")
        user = sec.load_session(db(), sid)
        if user is None:
            session.pop("sid", None)
            if request.path.startswith("/api/") or request.is_json:
                abort(401)
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        g.user, g.sid = user, sid
        return None


def _start_session(user: User) -> None:
    keep_csrf = None  # token novo a cada login (evita fixação de sessão)
    session.clear()
    session["sid"] = sec.create_session(db(), user, client_ip_hash(), request.headers.get("User-Agent"))
    session.permanent = True
    session["csrf"] = keep_csrf or csrf_token()


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        ip = client_ip_hash()
        if sec.is_locked(db(), username, ip):
            audit_event("login_bloqueado", username[:60])
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "error")
            return render_template("auth/login.html", csrf_token=csrf_token()), 429
        user = sec.verify_login(db(), username, request.form.get("password", ""))
        if user:
            sec.clear_failures(db(), username)
            _start_session(user)
            sec.audit(db(), "login", user.id, None, ip)
            return redirect(safe_next(request.args.get("next"), url_for("dashboard.index")))
        sec.record_failure(db(), username, ip)
        sec.audit(db(), "login_falhou", None, username[:60], ip)
        flash("Usuário ou senha incorretos.", "error")
    return render_template("auth/login.html", csrf_token=csrf_token())


@bp.route("/configurar", methods=["GET", "POST"])
def setup():
    """Primeiro acesso: cria o administrador. Em produção exige o código de instalação,
    senão qualquer visitante que chegasse primeiro viraria dono do sistema."""
    if db().scalar(select(func.count()).select_from(User)) > 0:
        return redirect(url_for("auth.login"))
    required = current_app.config.get("SETUP_TOKEN")
    if current_app.config.get("PRODUCTION") and not required:
        return render_template("auth/setup_locked.html"), 503
    if request.method == "POST":
        ip = client_ip_hash()
        if sec.is_locked(db(), "", ip, kind="setup"):
            flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "error")
            return render_template("auth/setup.html", csrf_token=csrf_token(), need_token=bool(required)), 429
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            if required and not hmac.compare_digest(request.form.get("setup_token", "").encode(), required.encode()):
                sec.record_failure(db(), "", ip, kind="setup")
                raise_error = "O código de instalação não confere."
                flash(raise_error, "error")
                return render_template("auth/setup.html", csrf_token=csrf_token(), need_token=True), 403
            if not name or not username or len(name) > 120 or len(username) > 60:
                flash("Informe seu nome e um nome de usuário (até 60 letras).", "error")
            else:
                sec.validate_password(password)
                if password != request.form.get("password2", ""):
                    flash("As duas senhas não são iguais.", "error")
                else:
                    user = User(name=name, username=username)
                    user.set_password(password)
                    db().add(user)
                    db().commit()
                    for person in (request.form.get("person1", "").strip(), request.form.get("person2", "").strip()):
                        if person and len(person) <= 80:
                            from ..services import owners as owner_svc
                            try:
                                owner_svc.create_owner(db(), person)
                            except Exception:
                                db().rollback()
                    _start_session(user)
                    sec.audit(db(), "setup", user.id, None, ip)
                    flash(f"Tudo pronto, {name.split()[0]}. Comece cadastrando seus produtos.", "success")
                    return redirect(url_for("dashboard.index"))
        except Exception as e:  # BusinessError da política de senha
            if hasattr(e, "message"):
                flash(e.message, "error")
            else:
                raise
    return render_template("auth/setup.html", csrf_token=csrf_token(), need_token=bool(required))


@bp.post("/sair")
def logout():
    sec.destroy_session(db(), session.get("sid"))
    audit_event("logout")
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/conta", methods=["GET", "POST"])
def account():
    if request.method == "POST":
        if request.form.get("new") != request.form.get("new2"):
            flash("As duas senhas novas não são iguais.", "error")
        else:
            try:
                sec.change_password(db(), g.user, request.form.get("current", ""), request.form.get("new", ""), g.sid)
                audit_event("senha_trocada")
                flash("Senha alterada. Os outros aparelhos foram desconectados.", "success")
                return redirect(url_for("auth.account"))
            except Exception as e:
                if not hasattr(e, "message"):
                    raise
                flash(e.message, "error")
    return render_template("auth/account.html")
