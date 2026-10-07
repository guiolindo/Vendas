"""Configurações do negócio. Poucas, simples, com limites de tamanho (o banco recusa texto maior)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import atomic
from ..domain.text import limited
from ..models import Setting

# chave -> (rótulo, tamanho máximo, padrão)
FIELDS = {
    "business_name": ("Nome do negócio", 80, "Meu Negócio"),
    "document": ("CNPJ ou CPF", 30, ""),
    "phone": ("Telefone ou WhatsApp", 40, ""),
    "address": ("Endereço", 160, ""),
    "social": ("Instagram ou e-mail", 80, ""),
    "pix_key": ("Chave Pix para receber", 77, ""),
    "pix_name": ("Nome de quem recebe o Pix", 40, ""),
    "thanks": ("Mensagem de agradecimento", 160, "Obrigado pela preferência!"),
}
DEFAULT_NAME = FIELDS["business_name"][2]


def load(session: Session) -> dict[str, str]:
    stored = {s.key: (s.value or "") for s in session.scalars(select(Setting))}
    out = {k: stored.get(k, default) if k in stored else default for k, (_, _, default) in FIELDS.items()}
    out["show_seller"] = stored.get("show_seller", "1") == "1"
    out["track_stock"] = stored.get("track_stock", "1") == "1"   # padrão dos produtos novos
    return out


def save(session: Session, values: dict[str, str], show_seller: bool, track_stock: bool | None = None) -> None:
    clean = {}
    for key, (label, maxlen, default) in FIELDS.items():
        text = (values.get(key) or "").strip()
        limited(text, maxlen, label, key)
        clean[key] = text
    if not clean["business_name"]:
        clean["business_name"] = DEFAULT_NAME
    clean["show_seller"] = "1" if show_seller else "0"
    if track_stock is not None:
        clean["track_stock"] = "1" if track_stock else "0"
    with atomic(session):
        existing = {s.key: s for s in session.scalars(select(Setting))}
        for key, value in clean.items():
            if key in existing:
                existing[key].value = value
            else:
                session.add(Setting(key=key, value=value))
