import os
from datetime import date

import pytest

from app import clock
from app.db import Base, Database
from app.services import customers as customer_svc
from app.services import products as product_svc

TODAY = date(2026, 10, 6)


@pytest.fixture(autouse=True)
def fixed_today():
    clock.set_today(TODAY)
    yield
    clock.set_today(None)


@pytest.fixture
def database(tmp_path):
    """SQLite por padrão; com TEST_DATABASE_URL (ex.: Postgres) roda a suíte no banco real."""
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    db = Database(url)
    Base.metadata.drop_all(db.engine) if db.engine.dialect.name != "sqlite" else None
    db.create_all()
    yield db
    db.dispose()


@pytest.fixture
def session(database):
    s = database.session()
    yield s
    s.close()


@pytest.fixture
def make_product(session):
    def _make(name="Camiseta", price=5000, cost=2000, stock=100, **kw):
        return product_svc.create_product(
            session,
            product_svc.ProductInput(name=name, price_cents=price, cost_cents=cost, initial_stock=stock, **kw),
        )
    return _make


@pytest.fixture
def make_customer(session):
    def _make(name="João da Silva"):
        return customer_svc.create_customer(session, customer_svc.CustomerInput(name=name))
    return _make


@pytest.fixture
def product(make_product):
    return make_product()


@pytest.fixture
def customer(make_customer):
    return make_customer()
