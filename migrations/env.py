"""Ambiente do Alembic. Usa os mesmos modelos e a mesma regra de URL do sistema."""
from __future__ import annotations

import os
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registra todas as tabelas em Base.metadata)
from app.db import Base, normalize_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = (config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL")
           or f"sqlite:///{Path(__file__).resolve().parent.parent / 'instance' / 'vendas.db'}")
    return normalize_url(url)


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=connection.dialect.name == "sqlite")   # SQLite altera tabelas copiando
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
