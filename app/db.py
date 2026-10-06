"""Banco de dados: engine SQLite, sessões e o limite de transação (`atomic`)."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


_local = threading.local()


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        # Nós mesmos controlamos o BEGIN (veja abaixo).
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        # Operações de escrita pegam o lock de escrita já no início: duas pessoas
        # registrando pagamento na mesma venda são atendidas uma por vez, e a
        # segunda enxerga o saldo já atualizado pela primeira.
        immediate = getattr(_local, "write", False)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def normalize_url(url: str) -> str:
    """O Railway entrega `postgres://...`; o SQLAlchemy quer o driver explícito."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Database:
    def __init__(self, url: str):
        url = normalize_url(url)
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":
            _configure_sqlite(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=True)

    def session(self) -> Session:
        return self.factory()

    def create_all(self) -> None:
        from . import models  # noqa: F401  (registra as tabelas)

        if self.engine.dialect.name == "postgresql":
            # Vários workers sobem juntos: o lock evita que dois criem as tabelas ao mesmo tempo.
            with self.engine.begin() as conn:
                conn.exec_driver_sql("SELECT pg_advisory_xact_lock(727274)")
                Base.metadata.create_all(conn)
        else:
            Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


@contextmanager
def atomic(session: Session) -> Iterator[Session]:
    """Executa uma operação de escrita inteira ou nada.

    Qualquer exceção desfaz tudo (venda, itens, estoque e pagamento juntos).
    """
    if session.in_transaction():
        # Encerra leituras anteriores para que o lock de escrita seja pego já no início.
        session.commit()
    _local.write = True
    try:
        session.connection()  # abre a transação (BEGIN IMMEDIATE)
    finally:
        _local.write = False
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
