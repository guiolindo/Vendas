"""As migrações precisam produzir exatamente o esquema dos modelos."""
import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app import models  # noqa: F401
from app.db import Base, Database, normalize_url


def _config(url):
    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture
def url(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'mig.db'}"
    if not url.startswith("sqlite"):
        eng = create_engine(normalize_url(url))
        Base.metadata.drop_all(eng)
        with eng.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS alembic_version"))
        eng.dispose()
    return url


def test_upgrade_creates_exactly_the_model_schema(url):
    command.upgrade(_config(url), "head")
    eng = create_engine(normalize_url(url))
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    eng.dispose()
    assert diff == []


def test_unmigrated_database_is_refused_then_accepted(url):
    db = Database(url)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        db.ensure_migrated()
    db.dispose()
    command.upgrade(_config(url), "head")
    db = Database(url)
    db.ensure_migrated()
    db.dispose()
