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

    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or load_secret_key(app.instance_path)
    url = (app.config.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
           or f"sqlite:///{Path(app.instance_path) / 'vendas.db'}")
    database = Database(url)
    database.create_all()
    app.extensions["database"] = database
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("FORCE_HTTPS"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.config["SESSION_COOKIE_SECURE"] = True
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # TLS termina no proxy do Railway

    _setup_logging(app)

    from .web import auth, collections, customers, dashboard, products, reports, sales, templating, api
    templating.init_app(app)
    auth.init_app(app)
    for module in (auth, dashboard, products, customers, sales, collections, reports, api):
        app.register_blueprint(module.bp)

    @app.teardown_appcontext
    def _close_session(_exc):
        from flask import g
        session = g.pop("db", None)
        if session is not None:
            session.close()

    @app.get("/favicon.ico")
    def favicon():
        from flask import redirect, url_for
        return redirect(url_for("static", filename="favicon.svg"), code=301)

    @app.get("/saude")
    def health():
        return {"ok": True}

    @app.errorhandler(BusinessError)
    def business_error(e):
        # Erro de regra que escapou de uma rota (ex.: data inválida na URL): mensagem humana, nunca 500.
        return render_template("error.html", title="Não foi possível continuar", message=e.message), 400

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
