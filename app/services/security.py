"""Autenticação: sessões no servidor, bloqueio por tentativas, política de senha e auditoria."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from .. import clock
from ..errors import BusinessError
from ..models import AuditLog, LoginAttempt, User, UserSession

IDLE_TIMEOUT = timedelta(hours=12)       # some sozinha após 12h sem uso
ABSOLUTE_TIMEOUT = timedelta(days=7)     # e nunca dura mais que 7 dias
LOCK_WINDOW = timedelta(minutes=10)
MAX_USER_FAILURES = 5                    # por nome de usuário
MAX_IP_FAILURES = 30                     # por origem (contra "password spraying")
MAX_PASSWORD_LEN = 128                   # evita senha gigante usada para pesar o servidor
TOUCH_EVERY = timedelta(minutes=5)

# Hash falso: usuário inexistente gasta o mesmo tempo que um existente (anti-enumeração por tempo).
_DUMMY_HASH = generate_password_hash("senha-que-nunca-existe-1")


def hash_sid(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def pseudonymize(secret_key: str, value: str | None) -> str | None:
    """HMAC do IP: permite correlacionar eventos sem guardar o endereço (LGPD)."""
    if not value:
        return None
    return hmac.new(secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


# ── senha ────────────────────────────────────────────────────────────────────
def validate_password(password: str) -> None:
    if len(password) < 8:
        raise BusinessError("A senha precisa ter pelo menos 8 caracteres.", field="password")
    if len(password) > MAX_PASSWORD_LEN:
        raise BusinessError(f"A senha pode ter no máximo {MAX_PASSWORD_LEN} caracteres.", field="password")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise BusinessError("A senha precisa ter pelo menos uma letra e um número.", field="password")


def verify_login(session: Session, username: str, password: str) -> User | None:
    """Confere usuário e senha gastando o mesmo tempo nos dois casos de erro."""
    user = session.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    ok = check_password_hash(user.password_hash if user else _DUMMY_HASH, password[:MAX_PASSWORD_LEN + 1])
    return user if (user and user.active and ok and len(password) <= MAX_PASSWORD_LEN) else None


def change_password(session: Session, user: User, current: str, new: str, keep_sid: str | None) -> None:
    if not check_password_hash(user.password_hash, current):
        raise BusinessError("A senha atual não confere.", field="current")
    validate_password(new)
    if check_password_hash(user.password_hash, new):
        raise BusinessError("A nova senha precisa ser diferente da atual.", field="password")
    user.set_password(new)
    user.password_changed_at = clock.now()
    destroy_user_sessions(session, user.id, except_sid=keep_sid)  # derruba os outros aparelhos
    session.commit()


# ── tentativas de login ──────────────────────────────────────────────────────
def _keys(username: str, ip_hash: str | None, kind: str = "user") -> list[tuple[str, int]]:
    keys = [(f"{kind}:{username.strip().lower()[:80]}", MAX_USER_FAILURES)] if username else []
    if ip_hash:
        keys.append((f"ip:{ip_hash}" if kind == "user" else f"{kind}:{ip_hash}", MAX_IP_FAILURES))
    return keys


def is_locked(session: Session, username: str, ip_hash: str | None, kind: str = "user") -> bool:
    since = clock.now() - LOCK_WINDOW
    for key, limit in _keys(username, ip_hash, kind):
        count = session.scalar(select(func.count()).select_from(LoginAttempt)
                               .where(LoginAttempt.key == key, LoginAttempt.at >= since))
        if count >= limit:
            return True
    return False


def record_failure(session: Session, username: str, ip_hash: str | None, kind: str = "user") -> None:
    for key, _ in _keys(username, ip_hash, kind):
        session.add(LoginAttempt(key=key))
    session.execute(delete(LoginAttempt).where(LoginAttempt.at < clock.now() - timedelta(days=1)))
    session.commit()


def clear_failures(session: Session, username: str) -> None:
    session.execute(delete(LoginAttempt).where(LoginAttempt.key == f"user:{username.strip().lower()[:80]}"))
    session.commit()


# ── sessões ──────────────────────────────────────────────────────────────────
def create_session(session: Session, user: User, ip_hash: str | None, user_agent: str | None) -> str:
    sid = secrets.token_urlsafe(32)
    now = clock.now()
    session.add(UserSession(sid_hash=hash_sid(sid), user_id=user.id, expires_at=now + ABSOLUTE_TIMEOUT,
                            ip_hash=ip_hash, user_agent=(user_agent or "")[:200] or None))
    session.execute(delete(UserSession).where(UserSession.expires_at < now))
    session.commit()
    return sid


def load_session(session: Session, sid: str | None) -> User | None:
    """Devolve o usuário dono da sessão, ou None se ela não existe, expirou ou o usuário foi desativado."""
    if not sid:
        return None
    row = session.scalar(select(UserSession).where(UserSession.sid_hash == hash_sid(sid)))
    if row is None:
        return None
    now = clock.now()
    if row.expires_at < now or now - row.last_seen_at > IDLE_TIMEOUT:
        session.delete(row)
        session.commit()
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.active:
        return None
    if now - row.last_seen_at > TOUCH_EVERY:
        row.last_seen_at = now
        session.commit()
    return user


def destroy_session(session: Session, sid: str | None) -> None:
    if sid:
        session.execute(delete(UserSession).where(UserSession.sid_hash == hash_sid(sid)))
        session.commit()


def destroy_user_sessions(session: Session, user_id: int, except_sid: str | None = None) -> None:
    query = delete(UserSession).where(UserSession.user_id == user_id)
    if except_sid:
        query = query.where(UserSession.sid_hash != hash_sid(except_sid))
    session.execute(query)


# ── auditoria ────────────────────────────────────────────────────────────────
def audit(session: Session, action: str, user_id: int | None = None, detail: str | None = None,
          ip_hash: str | None = None) -> None:
    session.add(AuditLog(action=action[:40], user_id=user_id, detail=(detail or "")[:255] or None, ip_hash=ip_hash))
    session.commit()
