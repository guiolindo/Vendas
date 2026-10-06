"""Limites de texto. O PostgreSQL recusa strings maiores que a coluna (o SQLite deixava passar),
então validamos antes e respondemos com uma mensagem clara em vez de erro 500."""
from ..errors import BusinessError

MAX_ID = 2_147_483_647  # maior INTEGER do PostgreSQL


def limited(value: str | None, max_len: int, label: str, field: str | None = None) -> str | None:
    if value is not None and len(value) > max_len:
        raise BusinessError(f"{label}: use no máximo {max_len} caracteres (você digitou {len(value)}).", field=field)
    return value
