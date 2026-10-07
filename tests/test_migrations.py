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


def test_seller_migration_backfills_old_sales_and_drops_product_owner(url):
    from alembic import command as cmd
    cfg = _config(url)
    cmd.upgrade(cfg, "0002")
    eng = create_engine(normalize_url(url))
    with eng.begin() as c:
        c.execute(text("INSERT INTO owners (id, name, active) VALUES (1, 'Ana', true), (2, 'Bia', true)"))
        c.execute(text("INSERT INTO products (id, code, name, price_cents, cost_cents, stock_qty, min_stock, unit, active, created_at, owner_id, track_stock) "
                       "VALUES (1, 'P1', 'Vestido', 10000, 4000, 5, 0, 'un', true, '2026-10-01 10:00:00', 1, true)"))
        c.execute(text("INSERT INTO sales (id, sale_date, subtotal_cents, discount_cents, total_cents, created_at) "
                       "VALUES (1, '2026-10-01', 10000, 0, 10000, '2026-10-01 10:00:00'), (2, '2026-10-02', 10000, 0, 10000, '2026-10-02 10:00:00')"))
        c.execute(text("INSERT INTO sale_items (id, sale_id, product_id, owner_id, product_name, product_code, unit, quantity, unit_price_cents, unit_cost_cents, total_cents) "
                       "VALUES (1, 1, 1, 2, 'Vestido', 'P1', 'un', 1, 10000, 4000, 10000), (2, 2, 1, NULL, 'Vestido', 'P1', 'un', 1, 10000, 4000, 10000)"))
    eng.dispose()
    cmd.upgrade(cfg, "head")
    eng = create_engine(normalize_url(url))
    with eng.connect() as c:
        got = dict(c.execute(text("SELECT id, seller_id FROM sales")).all())
        cols = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='products'"))} \
            if eng.dialect.name == "postgresql" else {r[1] for r in c.execute(text("PRAGMA table_info(products)"))}
    eng.dispose()
    assert got == {1: 2, 2: None} and "owner_id" not in cols
