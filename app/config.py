import os
import secrets
from pathlib import Path


class Config:
    DATABASE_URL: str | None = None  # padrão: instance/vendas.db
    SECRET_KEY: str | None = None  # padrão: gerado e guardado em instance/secret_key
    DEFAULT_DUE_DAYS = 30
    PER_PAGE = 25
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12


def load_secret_key(instance_path: str) -> str:
    env = os.environ.get("VENDAS_SECRET_KEY")
    if env:
        return env
    path = Path(instance_path) / "secret_key"
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key)
    path.chmod(0o600)
    return key
