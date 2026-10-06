from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


@dataclass
class Page(Generic[T]):
    items: list[T]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(ceil(self.total / self.per_page), 1)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def paginate(session: Session, query: Select, page: int, per_page: int, scalars: bool = True) -> Page:
    page = max(page, 1)
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    page = min(page, max((total + per_page - 1) // per_page, 1))
    rows = session.execute(query.limit(per_page).offset((page - 1) * per_page))
    items: list[Any] = list(rows.scalars().unique() if scalars else rows.all())
    return Page(items, page, per_page, total)


def like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
