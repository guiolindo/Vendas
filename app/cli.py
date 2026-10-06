"""Comandos de terminal: `flask --app run create-user`, `flask --app run demo-data`."""
from __future__ import annotations

import click
from sqlalchemy import select

from .models import User


def register_cli(app) -> None:
    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--name", prompt=True)
    @click.password_option()
    def create_user(username, name, password):
        """Cria (ou redefine a senha de) um usuário. A senha segue a mesma política da tela."""
        from .errors import BusinessError
        from .services import security as sec
        try:
            sec.validate_password(password)
        except BusinessError as e:
            raise click.ClickException(e.message)
        session = app.extensions["database"].session()
        user = session.scalar(select(User).where(User.username == username)) or User(username=username, name=name)
        user.name = name
        user.set_password(password)
        session.add(user)
        session.commit()
        click.echo(f"Usuário “{username}” salvo.")

    @app.cli.command("demo-data")
    def demo_data():
        """Carrega produtos, clientes e vendas de exemplo para conhecer o sistema."""
        from .demo import load
        if app.config.get("PRODUCTION"):
            raise click.ClickException("Os dados de exemplo não podem ser carregados em produção.")
        session = app.extensions["database"].session()
        click.echo(load(session))
