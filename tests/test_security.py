"""Testes de ataque e de abuso. Cada um descreve o que um invasor (ou um erro) tentaria."""
import json
import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app import clock, create_app
from app.db import Base, Database
from app.models import AuditLog, LoginAttempt, Payment, Sale, User, UserSession
from app.services import customers as csvc
from app.services import products as pvc
from app.services import reports as rsvc
from app.services import security as sec

from .conftest import TODAY
from .test_web import app, client, db, post, post_json  # noqa: F401  (fixtures)


def make_user(db, username="ana", password="senha-forte-1"):
    u = User(username=username, name=username.title()); u.set_password(password)
    db.add(u); db.commit()
    return u


def fresh_client(app):
    c = app.test_client()
    c.get("/entrar")  # abre a página: é aí que nasce o token CSRF
    with c.session_transaction() as s:
        token = s["csrf"]
    return c, token


def login(c, token, username="ana", password="senha-forte-1"):
    return c.post("/entrar", data={"username": username, "password": password, "csrf_token": token})


# ── sessão ──────────────────────────────────────────────────────────────────
def test_stolen_cookie_dies_on_logout(app, db):
    make_user(db)
    victim, tok = fresh_client(app)
    assert login(victim, tok).status_code == 302
    cookie = victim.get_cookie("session")
    assert victim.get("/").status_code == 200
    with victim.session_transaction() as s:
        t = s["csrf"]
    victim.post("/sair", data={"csrf_token": t})
    # o atacante reaproveita o cookie copiado antes do logout
    thief = app.test_client()
    thief.set_cookie("session", cookie.value)
    assert "/entrar" in thief.get("/").headers["Location"]


def test_session_id_is_random_hashed_and_rotated_on_login(app, db):
    make_user(db)
    c, tok = fresh_client(app)
    login(c, tok)
    db.rollback()
    row = db.scalar(select(UserSession))
    assert len(row.sid_hash) == 64  # só o hash fica no banco
    with c.session_transaction() as s:
        assert s["sid"] != row.sid_hash and s["csrf"] != tok  # token CSRF novo após o login


def test_session_expires_when_idle_and_absolute(app, db):
    u = make_user(db)
    sid = sec.create_session(db, u, None, None)
    assert sec.load_session(db, sid) is not None
    db.execute(update(UserSession).values(last_seen_at=clock.now() - timedelta(hours=13)))
    db.commit()
    assert sec.load_session(db, sid) is None       # parada há 13h
    sid = sec.create_session(db, u, None, None)
    db.execute(update(UserSession).values(expires_at=clock.now() - timedelta(seconds=1)))
    db.commit()
    assert sec.load_session(db, sid) is None       # passou de 7 dias


def test_deactivated_user_loses_access_immediately(app, db, client):
    assert client.get("/").status_code == 200
    db.execute(update(User).values(active=False)); db.commit()
    assert "/entrar" in client.get("/").headers["Location"]


def test_forged_or_garbage_session_is_rejected(app, db):
    make_user(db)
    c = app.test_client()
    with c.session_transaction() as s:
        s["sid"] = "adivinhei-um-valor"
    assert "/entrar" in c.get("/vendas").headers["Location"]
    assert c.get("/api/produtos").status_code == 401


# ── força bruta ─────────────────────────────────────────────────────────────
def test_lockout_after_failures_is_stored_in_database_not_memory(app, db):
    make_user(db)
    c, tok = fresh_client(app)
    for _ in range(5):
        assert login(c, tok, password="errada1x").status_code == 200
    assert login(c, tok).status_code == 429          # a senha certa também é recusada durante o bloqueio
    # outro "worker" (nova instância da app, mesmo banco) enxerga o mesmo bloqueio
    other = create_app({"DATABASE_URL": app.config["DATABASE_URL"], "TESTING": True, "SECRET_KEY": "t"})
    c2, tok2 = fresh_client(other)
    assert login(c2, tok2).status_code == 429
    other.extensions["database"].dispose()


def test_lockout_expires_and_success_clears_counter(app, db):
    make_user(db)
    c, tok = fresh_client(app)
    for _ in range(5):
        login(c, tok, password="errada1x")
    db.execute(update(LoginAttempt).values(at=clock.now() - timedelta(minutes=11))); db.commit()
    assert login(c, tok).status_code == 302
    db.rollback()
    assert db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key.like("user:%"))) == 0


def test_password_spraying_from_one_origin_is_blocked(app, db):
    make_user(db)
    c, tok = fresh_client(app)
    for i in range(30):                              # 30 usuários diferentes, 1 senha
        login(c, tok, username=f"alvo{i}", password="Primavera2026")
    assert login(c, tok, username="alvo-novo", password="x").status_code == 429


def test_unknown_user_costs_same_hash_work_as_wrong_password(app, db, monkeypatch):
    make_user(db)
    calls = []
    real = sec.check_password_hash
    monkeypatch.setattr(sec, "check_password_hash", lambda h, p: calls.append(1) or real(h, p))
    sec.verify_login(db, "ninguem", "qualquer1")
    sec.verify_login(db, "ana", "errada123")
    assert len(calls) == 2                           # nos dois casos o hash é calculado (sem diferença de tempo)


def test_giant_password_is_refused_without_hashing_it(app, db):
    make_user(db)
    assert sec.verify_login(db, "ana", "a1" * 500) is None


def test_login_error_message_does_not_reveal_if_user_exists(app, db):
    make_user(db)
    c, tok = fresh_client(app)
    a = login(c, tok, username="ana", password="errada123").get_data(as_text=True)
    b = login(c, tok, username="fantasma", password="errada123").get_data(as_text=True)
    assert "Usuário ou senha incorretos." in a and "Usuário ou senha incorretos." in b


# ── senha ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pw", ["curta1", "somenteletras", "123456789", "a1" * 70])
def test_password_policy_rejects_weak(pw):
    from app.errors import BusinessError
    with pytest.raises(BusinessError):
        sec.validate_password(pw)


def test_change_password_flow_kills_other_sessions(app, db, client):
    user = db.scalar(select(User))
    other = sec.create_session(db, user, None, "outro aparelho")
    r = post(client, "/conta", {"current": "errada", "new": "NovaSenha77", "new2": "NovaSenha77"}, follow_redirects=True)
    assert "senha atual não confere" in r.get_data(as_text=True)
    r = post(client, "/conta", {"current": "senha-forte-1", "new": "senha-forte-1", "new2": "senha-forte-1"}, follow_redirects=True)
    assert "diferente da atual" in r.get_data(as_text=True)
    r = post(client, "/conta", {"current": "senha-forte-1", "new": "fraca", "new2": "fraca"}, follow_redirects=True)
    assert "pelo menos 8" in r.get_data(as_text=True)
    r = post(client, "/conta", {"current": "senha-forte-1", "new": "NovaSenha77", "new2": "NovaSenha77"}, follow_redirects=True)
    assert "Os outros aparelhos foram desconectados" in r.get_data(as_text=True)
    db.rollback()
    assert sec.load_session(db, other) is None                 # o outro aparelho caiu
    assert sec.load_session(db, client.sid) is not None        # este continua
    assert sec.verify_login(db, "ana", "NovaSenha77") is not None
    assert sec.verify_login(db, "ana", "senha-forte-1") is None


# ── primeiro acesso ─────────────────────────────────────────────────────────
def test_setup_is_locked_in_production_without_token(tmp_path):
    a = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'p.db'}", "TESTING": True, "SECRET_KEY": "k", "PRODUCTION": True})
    r = a.test_client().get("/configurar")
    assert r.status_code == 503 and "VENDAS_SETUP_TOKEN" in r.get_data(as_text=True)
    a.extensions["database"].dispose()


def test_setup_in_production_requires_the_installation_code(tmp_path):
    a = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'p2.db'}", "TESTING": True, "SECRET_KEY": "k",
                    "PRODUCTION": True, "SETUP_TOKEN": "codigo-secreto-do-dono"})
    c, tok = fresh_client(a)
    c.get("/configurar")
    with c.session_transaction() as s:
        tok = s["csrf"]
    form = {"name": "Intruso", "username": "x", "password": "Senha12345", "password2": "Senha12345", "csrf_token": tok}
    assert c.post("/configurar", data={**form, "setup_token": "chute"}).status_code == 403
    assert c.post("/configurar", data={**form, "setup_token": "codigo-secreto-do-dono"}).status_code == 302
    # depois do primeiro usuário a tela some
    assert "/entrar" in c.get("/configurar").headers["Location"]
    a.extensions["database"].dispose()


def test_setup_token_guessing_is_rate_limited(tmp_path):
    a = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'p3.db'}", "TESTING": True, "SECRET_KEY": "k",
                    "PRODUCTION": True, "SETUP_TOKEN": "certo"})
    c = a.test_client(); c.get("/configurar")
    with c.session_transaction() as s:
        tok = s["csrf"]
    codes = [c.post("/configurar", data={"setup_token": f"x{i}", "csrf_token": tok}).status_code for i in range(32)]
    assert 429 in codes
    a.extensions["database"].dispose()


def test_production_refuses_to_start_without_secret_or_database(monkeypatch, tmp_path):
    monkeypatch.delenv("VENDAS_SECRET_KEY", raising=False); monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="VENDAS_SECRET_KEY"):
        create_app({"PRODUCTION": True})
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        create_app({"PRODUCTION": True, "SECRET_KEY": "k"})


# ── cabeçalhos, cache, tamanho ──────────────────────────────────────────────
def test_security_headers_on_every_response(client):
    for url in ("/entrar", "/", "/saude", "/static/css/app.css"):
        h = client.get(url).headers
        csp = h["Content-Security-Policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert "unsafe-inline" not in csp.split("style-src")[0]       # nenhum script inline permitido
        assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
        assert "camera=()" in h["Permissions-Policy"]


def test_pages_with_financial_data_are_never_cached(client):
    assert client.get("/").headers["Cache-Control"] == "no-store"
    assert "no-store" not in client.get("/static/css/app.css").headers.get("Cache-Control", "")


def test_no_inline_scripts_or_handlers_anywhere(client, db):
    html = "".join(client.get(u).get_data(as_text=True) for u in ("/", "/vendas/nova", "/produtos", "/entrar"))
    import re
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"\son(click|load|error|submit)=", html)


def test_oversized_body_is_rejected(client):
    r = client.post("/vendas/nova", data=b"x" * (300 * 1024), content_type="application/json", headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 413


def test_health_checks_database_without_leaking_details(app, client, monkeypatch):
    assert client.get("/saude").get_json() == {"ok": True}


# ── injeções ────────────────────────────────────────────────────────────────
def test_html_in_names_is_escaped_everywhere(client, db):
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    p = pvc.create_product(db, pvc.ProductInput(name=evil, price_cents=1000, initial_stock=5))
    c = csvc.create_customer(db, csvc.CustomerInput(name=evil))
    for url in (f"/produtos/{p.id}", "/produtos", f"/clientes/{c.id}", "/clientes"):
        body = client.get(url).get_data(as_text=True)
        assert "<script>alert(1)" not in body and "<img src=x" not in body, url
    # a API devolve o texto cru (é JSON); o que o protege é o tipo de conteúdo + nosniff, e o JS escapa na tela
    api = client.get("/api/produtos?q=script")
    assert api.mimetype == "application/json" and api.headers["X-Content-Type-Options"] == "nosniff"
    assert "function esc(" in open("app/static/js/sale.js").read()


def test_csv_formula_injection_is_neutralized(db):
    p = pvc.create_product(db, pvc.ProductInput(name='=HYPERLINK("http://evil","clique")', price_cents=1000, initial_stock=3))
    csvc.create_customer(db, csvc.CustomerInput(name="+cmd|' /C calc'!A0"))
    out = rsvc.to_csv(rsvc.build(db, "estoque", rsvc.ReportParams(), TODAY)).decode("utf-8-sig")
    assert "'=HYPERLINK" in out and ";=HYPERLINK" not in out and not out.splitlines()[1].startswith("=")
    from app.services.reports import format_cell
    assert format_cell("@SUM(A1)", "text").startswith("'") and format_cell(-500, "money") == "-5,00"


def test_sql_injection_strings_are_just_text(client, db):
    for q in ("' OR 1=1 --", "%", "_", "\\", "'; DROP TABLE sales;--"):
        assert client.get("/produtos", query_string={"q": q}).status_code == 200
        assert client.get("/vendas", query_string={"q": q}).status_code == 200
        assert client.get("/clientes", query_string={"q": q}).status_code == 200
        assert client.get("/api/produtos", query_string={"q": q}).status_code == 200
    assert db.scalar(select(func.count()).select_from(Sale)) == 0


@pytest.mark.parametrize("target", ["//evil.com", "/\\evil.com", "https://evil.com", "javascript:alert(1)", "/ok\r\nX: y"])
def test_open_redirect_variants_blocked(app, db, target):
    make_user(db)
    c, tok = fresh_client(app)
    r = c.post("/entrar", query_string={"next": target}, data={"username": "ana", "password": "senha-forte-1", "csrf_token": tok})
    assert r.status_code == 302 and r.headers["Location"] == "/"


def test_safe_next_accepts_local_paths(app):
    from app.web.helpers import safe_next
    assert safe_next("/vendas?status=pago", "/") == "/vendas?status=pago"


# ── CSRF ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", ["/produtos/novo", "/clientes/novo", "/sair", "/conta", "/vendas/1/cancelar", "/vendas/1/pagamentos"])
def test_every_state_change_requires_csrf(client, url):
    r = client.post(url, data={"name": "x"})
    assert r.status_code in (302, 400) and "token" not in r.get_data(as_text=True).lower()
    r = client.post(url, data={"name": "x", "csrf_token": "adivinhado"})
    assert r.status_code in (302, 400)


def test_get_requests_never_change_state(client, db):
    p = pvc.create_product(db, pvc.ProductInput(name="X", price_cents=100, initial_stock=1))
    for url in (f"/produtos/{p.id}/excluir", f"/produtos/{p.id}/ativo", "/sair", "/vendas/1/cancelar"):
        assert client.get(url).status_code == 405


# ── entradas absurdas: nunca 500 ────────────────────────────────────────────
def test_absurd_inputs_never_cause_server_errors(client, db):
    big = "x" * 5000
    r = post(client, "/produtos/novo", {"name": big, "price": "10"})
    assert r.status_code == 422 and "no máximo 160" in r.get_data(as_text=True)
    r = post(client, "/clientes/novo", {"name": "ok", "phone": big})
    assert r.status_code == 422 and "no máximo 30" in r.get_data(as_text=True)
    for url in ("/produtos/99999999999999", "/clientes/99999999999999", "/vendas/99999999999999",
                "/vendas/99999999999999/editar", "/api/clientes?id=99999999999999"):
        assert client.get(url).status_code in (200, 302, 404), url
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": 10**30, "quantity": 1}]})
    assert r.status_code == 422
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": 1, "quantity": 10**30}]})
    assert r.status_code == 422
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": 1, "quantity": True}]})
    assert r.status_code == 422
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": 1, "quantity": 1}] * 201})
    assert r.status_code == 422 and "200 itens" in r.get_json()["message"]
    assert post(client, "/produtos/novo", {"name": "ok", "price": "99999999999"}).status_code == 422
    assert client.get("/vendas?pagina=99999999999999999999").status_code == 200
    assert client.get("/relatorios/vendas?de=0000-01-01").status_code in (200, 400)
    assert client.get("/relatorios/nao-existe").status_code == 404


def test_long_notes_are_validated_not_crashing(client, db):
    p = pvc.create_product(db, pvc.ProductInput(name="Item", price_cents=1000, initial_stock=5))
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 1}], "paid": "10", "payment_method": "pix",
                                           "notes": "n" * 600})
    assert r.status_code == 422 and "no máximo 500" in r.get_json()["message"]


# ── idempotência e auditoria ────────────────────────────────────────────────
def test_double_submitted_payment_is_recorded_once(client, db):
    p = pvc.create_product(db, pvc.ProductInput(name="Item", price_cents=10000, initial_stock=5))
    c = csvc.create_customer(db, csvc.CustomerInput(name="Cli"))
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 1}], "customer_id": c.id,
                                           "due_date": (TODAY + timedelta(days=5)).isoformat()})
    sale_id = int(r.get_json()["redirect"].rsplit("/", 1)[1])
    for _ in range(3):  # duplo clique / botão Voltar + reenviar
        post(client, f"/vendas/{sale_id}/pagamentos", {"amount": "30", "method": "pix", "request_token": "abc123"})
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    for _ in range(2):
        post(client, f"/clientes/{c.id}/receber", {"amount": "20", "method": "pix", "request_token": "tok-cli"})
    db.rollback()
    assert db.scalar(select(func.sum(Payment.amount_cents))) == 3000 + 2000


def test_sensitive_actions_are_audited_without_secrets(app, db, client):
    p = pvc.create_product(db, pvc.ProductInput(name="Item", price_cents=10000, initial_stock=5))
    r = post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 1}], "paid": "100", "payment_method": "pix"})
    sale_id = int(r.get_json()["redirect"].rsplit("/", 1)[1])
    post(client, f"/vendas/{sale_id}/cancelar", {"reason": "teste"})
    db.rollback()  # descarta o snapshot antigo da sessão do teste antes de escrever
    c2, tok = fresh_client(app)
    make_user(db, "zeca", "Senha12345")
    login(c2, tok, "zeca", "errada123")
    db.rollback()
    actions = [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert {"venda_criada", "venda_cancelada", "login_falhou"} <= set(actions)
    dump = " ".join(f"{a.detail} {a.ip_hash}" for a in db.scalars(select(AuditLog)))
    assert "errada123" not in dump and "127.0.0.1" not in dump        # sem senha e sem IP em claro
