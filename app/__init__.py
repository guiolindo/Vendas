"""Sistema de vendas e controle financeiro."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, render_template

from . import clock
from .config import Config, load_secret_key
from .db import Database
from .errors import BusinessError


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.config.update(overrides or {})
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    production = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("VENDAS_ENV") == "prod")
    app.config["PRODUCTION"] = production = app.config.get("PRODUCTION", production)
    app.config["SETUP_TOKEN"] = app.config.get("SETUP_TOKEN") or os.environ.get("VENDAS_SETUP_TOKEN") or None
    if production and not (app.config.get("SECRET_KEY") or os.environ.get("VENDAS_SECRET_KEY")):
        # Falha fechada: sem chave fixa as sessões ficariam num disco efêmero.
        raise RuntimeError("Defina VENDAS_SECRET_KEY antes de rodar em produção.")
    if production and not (app.config.get("DATABASE_URL") or os.environ.get("DATABASE_URL")):
        raise RuntimeError("Defina DATABASE_URL (PostgreSQL) antes de rodar em produção.")
    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or load_secret_key(app.instance_path)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # nenhum formulário daqui precisa de mais que isso
    url = (app.config.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
           or f"sqlite:///{Path(app.instance_path) / 'vendas.db'}")
    database = Database(url)
    database.create_all()
    app.extensions["database"] = database
    if production or os.environ.get("FORCE_HTTPS"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_NAME"] = "__Host-session"  # preso a este domínio, só HTTPS
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # TLS termina no proxy do Railway

    _setup_logging(app)

    from .web import auth, collections, customers, dashboard, owners, products, reports, sales, templating, api
    templating.init_app(app)
    auth.init_app(app)
    for module in (auth, dashboard, owners, products, customers, sales, collections, reports, api):
        app.register_blueprint(module.bp)

    @app.teardown_appcontext
    def _close_session(_exc):
        from flask import g
        session = g.pop("db", None)
        if session is not None:
            session.close()

    @app.get("/robots.txt")
    def robots():
        return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.get("/manifest.webmanifest")
    def manifest():
        from flask import jsonify, url_for
        return jsonify(
            name="Vendas", short_name="Vendas", start_url="/", display="standalone", lang="pt-BR",
            background_color="#f3f0e8", theme_color="#1c2622",
            icons=[{"src": url_for("static", filename="icon.svg"), "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
        ), 200, {"Content-Type": "application/manifest+json"}

    @app.get("/favicon.ico")
    def favicon():
        from flask import redirect, url_for
        return redirect(url_for("static", filename="favicon.svg"), code=301)

    @app.get("/saude")
    def health():
        from sqlalchemy import text
        from .web.helpers import db
        try:
            db().execute(text("SELECT 1"))
        except Exception:  # sem detalhes para quem pergunta
            return {"ok": False}, 503
        return {"ok": True}

    @app.after_request
    def security_headers(response):
        from flask import request
        h = response.headers
        h["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                        "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
                                        "form-action 'self'; frame-ancestors 'none'")
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["X-Robots-Tag"] = "noindex, nofollow"  # sistema interno: nada de buscadores
        if app.config.get("SESSION_COOKIE_SECURE"):
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if not request.path.startswith("/static/"):
            h["Cache-Control"] = "no-store"  # telas com dados financeiros não ficam em cache (nem no botão Voltar)
        return response

    @app.errorhandler(BusinessError)
    def business_error(e):
        # Erro de regra que escapou de uma rota (ex.: data inválida na URL): mensagem humana, nunca 500.
        return render_template("error.html", title="Não foi possível continuar", message=e.message), 400

    from sqlalchemy.exc import DataError

    from sqlalchemy.exc import IntegrityError

    @app.errorhandler(IntegrityError)
    def conflict(_e):
        from .web.helpers import db
        db().rollback()
        return render_template("error.html", title="Conflito de dados",
                               message="Outra pessoa alterou isto ao mesmo tempo. Volte, atualize a página e tente de novo."), 409

    @app.errorhandler(DataError)
    def bad_data(_e):
        # número fora do alcance do banco (ex.: id gigante na URL): trata como não encontrado
        from .web.helpers import db
        db().rollback()
        return render_template("error.html", title="Página não encontrada",
                               message="O endereço não existe ou o registro foi removido."), 404

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("error.html", title="Envio grande demais", message="O pedido passou do tamanho permitido."), 413

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", title="Página não encontrada",
                               message="O endereço não existe ou o registro foi removido."), 404

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("error.html", title="Algo deu errado",
                               message="Não foi possível concluir. Nada foi salvo pela metade. Tente de novo."), 500

    from .cli import register_cli
    register_cli(app)
    return app


def _setup_logging(app: Flask) -> None:
    app.logger.setLevel(logging.INFO)
    if app.testing or os.environ.get("RAILWAY_ENVIRONMENT"):
        return  # no Railway os logs vão para a saída padrão
    handler = RotatingFileHandler(Path(app.instance_path) / "vendas.log", maxBytes=1_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    app.logger.addHandler(handler)
