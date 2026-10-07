from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .. import clock
from ..db import Base


class UserSession(Base):
    """Sessão no servidor: o cookie só guarda um identificador aleatório.
    Sair (ou trocar a senha) apaga a linha, então um cookie roubado deixa de valer na hora."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    sid_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 do identificador
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=clock.now)
    last_seen_at: Mapped[datetime] = mapped_column(default=clock.now)
    expires_at: Mapped[datetime]
    ip_hash: Mapped[str | None] = mapped_column(String(32))
    user_agent: Mapped[str | None] = mapped_column(String(200))


class LoginAttempt(Base):
    """Falhas de autenticação. Ficam no banco (e não na memória) para valer entre os workers."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_key_at", "key", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120))  # "user:<nome>", "ip:<hash>" ou "setup:<hash>"
    at: Mapped[datetime] = mapped_column(default=clock.now)


class AuditLog(Base):
    """Trilha de eventos sensíveis. Só se acrescenta; IP guardado pseudonimizado."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=clock.now, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str | None] = mapped_column(String(255))
    ip_hash: Mapped[str | None] = mapped_column(String(32))


class Setting(Base):
    """Configurações simples do negócio (nome, telefone... usados no cabeçalho do comprovante)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(300))
