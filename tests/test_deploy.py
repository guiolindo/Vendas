"""Configuração de deploy (Railway): erros aqui só apareceriam depois de publicar."""
import json
import re
import subprocess
import sys

import pytest

from app import create_app


def test_railway_start_command_expands_port_inside_a_shell():
    cfg = json.load(open("railway.json"))["deploy"]
    cmd = cfg["startCommand"]
    assert cmd.startswith(("sh -c ", "/bin/sh -c ")) and "${PORT" in cmd   # sem shell, $PORT não é expandido
    assert "alembic upgrade head" in cfg["preDeployCommand"]     # o esquema sobe antes do novo código
    assert "?" not in cmd and "%(q)s" not in cmd and "%(r)s" not in cmd   # log de acesso sem query string
    assert cfg["healthcheckPath"] == "/saude"
    assert re.search(r"--bind 0\.0\.0\.0:", cmd)                  # escuta em todas as interfaces, não só 127.0.0.1


def test_procfile_matches_railway_json():
    assert "gunicorn run:app" in open("Procfile").read()


def test_requirements_are_pinned():
    for line in open("requirements.txt"):
        line = line.strip()
        if line and not line.startswith("#"):
            assert "==" in line, line


def test_cli_create_user_enforces_password_policy(tmp_path):
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'c.db'}", "TESTING": True, "SECRET_KEY": "k"})
    runner = app.test_cli_runner()
    weak = runner.invoke(args=["create-user", "bob", "--name", "Bob", "--password", "curta"])
    assert weak.exit_code != 0 and "pelo menos 8" in weak.output
    ok = runner.invoke(args=["create-user", "bob", "--name", "Bob", "--password", "Senha12345"])
    assert ok.exit_code == 0 and "salvo" in ok.output
    app.extensions["database"].dispose()


def test_demo_data_refuses_to_run_in_production(tmp_path):
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'d.db'}", "TESTING": True, "SECRET_KEY": "k", "PRODUCTION": True})
    out = app.test_cli_runner().invoke(args=["demo-data"])
    assert out.exit_code != 0 and "produção" in out.output
    app.extensions["database"].dispose()
