from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import atomic
from ..domain.text import limited
from ..errors import BusinessError, NotFound
from ..models import Customer, Sale


@dataclass
class CustomerInput:
    name: str
    phone: str | None = None
    document: str | None = None
    address: str | None = None
    notes: str | None = None
    active: bool = True


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def _fill(customer: Customer, data: CustomerInput) -> None:
    if not (data.name or "").strip():
        raise BusinessError("Informe o nome do cliente.", field="name")
    limited(data.name, 160, "Nome", "name"); limited(data.phone, 30, "Telefone", "phone")
    limited(data.document, 30, "Documento", "document"); limited(data.address, 255, "Endereço", "address")
    limited(data.notes, 2000, "Observações", "notes")
    customer.name = data.name.strip()
    customer.phone = _clean(data.phone)
    customer.document = _clean(data.document)
    customer.address = _clean(data.address)
    customer.notes = _clean(data.notes)
    customer.active = data.active


def create_customer(session: Session, data: CustomerInput) -> Customer:
    with atomic(session):
        customer = Customer(name="")
        _fill(customer, data)
        session.add(customer)
    return customer


def update_customer(session: Session, customer_id: int, data: CustomerInput) -> Customer:
    with atomic(session):
        customer = session.get(Customer, customer_id)
        if customer is None:
            raise NotFound("Cliente não encontrado.")
        _fill(customer, data)
    return customer


def delete_customer(session: Session, customer_id: int) -> None:
    with atomic(session):
        customer = session.get(Customer, customer_id)
        if customer is None:
            raise NotFound("Cliente não encontrado.")
        has_sales = session.scalar(select(func.count()).select_from(Sale).where(Sale.customer_id == customer_id))
        if has_sales:
            raise BusinessError("Este cliente tem vendas registradas e não pode ser excluído. Desative-o se não quiser mais vê-lo na lista.")
        session.delete(customer)
