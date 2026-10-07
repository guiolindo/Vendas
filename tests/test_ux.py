"""Texto e usabilidade para qualquer pessoa: sem jargão, sem frase genérica, sem assumir gênero,
com contraste suficiente e com orientação no primeiro uso."""
import glob
import re

import pytest

from app import create_app
from app.services import customers as csvc
from app.services import owners as own
from app.services import products as pvc

from .test_web import app, client, db, post, post_json  # noqa: F401  (fixtures)


# ── 1. varredura do texto que o usuário lê ──────────────────────────────────
def visible_strings() -> list[tuple[str, str]]:
    """Frases dos templates e das mensagens (BusinessError, flash) do código."""
    found = []
    for path in glob.glob("app/templates/**/*.html", recursive=True):
        raw = open(path).read()
        raw = re.sub(r"\{#.*?#\}", " ", raw, flags=re.S)
        raw = re.sub(r"\{%.*?%\}", "\n", raw, flags=re.S)
        raw = re.sub(r"\{\{.*?\}\}", " ", raw, flags=re.S)
        raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S)
        for attr in re.findall(r'(?:placeholder|aria-label|title|data-confirm)="([^"]+)"', raw):
            found.append((path, attr))
        for line in re.sub(r"<[^>]+>", "\n", raw).split("\n"):
            if line.strip():
                found.append((path, line.strip()))
    for path in glob.glob("app/**/*.py", recursive=True):
        for m in re.finditer(r'(?:BusinessError|NotFound|flash|MoneyError)\(\s*f?(["\'])(.+?)\1', open(path).read(), flags=re.S):
            found.append((path, re.sub(r"\s+", " ", m.group(2))))
    return found


BANNED = {
    r"\bestorn": "jargão contábil: use “desfazer/desfeito”",
    r"auditoria": "termo técnico: diga “registro interno”",
    r"\b(dela|dele|delas|deles)\b": "assume gênero: use o nome ou uma frase neutra",
    r"bem-vind|seja bem|insights?|inteligente|poderos|intuitiv|otimiz|solução|experiência do usuário|revolucion": "frase de marketing/genérica",
    r"ocorreu um erro|erro inesperado|operação (realizada|concluída) com sucesso|persistên|transação|exceção|payload|endpoint|CSRF|backend": "texto técnico ou genérico demais",
    r"\bsessão expirou\b": "diga o que aconteceu em palavras simples",
}


def test_interface_text_has_no_jargon_marketing_or_assumed_gender():
    problems = []
    for path, text in visible_strings():
        for pattern, why in BANNED.items():
            if re.search(pattern, text, re.I):
                problems.append(f"{path}: “{text[:90]}” → {why}")
    assert not problems, "\n".join(problems)


def test_names_of_people_are_never_followed_by_a_gendered_participle():
    """“João cadastrada” / “Ana excluído”: o nome de uma pessoa não diz o gênero, então a frase não pode concordar com ele."""
    pattern = re.compile(r"(\{\{[^}]*name[^}]*\}\}|\{[\w.]*name\})\s*(cadastrad[oa]|exclu[ií]d[oa]|desativad[oa]|reativad[oa]|inativ[oa])\b", re.I)
    offenders = []
    for path in glob.glob("app/**/*.py", recursive=True) + glob.glob("app/templates/**/*.html", recursive=True):
        for m in pattern.finditer(open(path).read()):
            offenders.append(f"{path}: {m.group(0)}")
    assert not offenders, offenders


def test_no_emojis_or_decorative_pictographs_in_the_interface():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅❌]")
    offenders = [f"{p}: {t[:60]}" for p, t in visible_strings() if emoji.search(t) and "◆" not in t]
    assert not offenders, offenders


def test_error_messages_say_what_to_do_not_just_what_failed():
    from app.errors import BusinessError
    from app.domain.money import MoneyError, parse_money
    for bad in ("abc", "1,234,5", "-5", "99999999999999"):
        with pytest.raises(MoneyError) as e:
            parse_money(bad)
        assert len(str(e.value)) > 10 and str(e.value)[0].isupper() or str(e.value).startswith(("o ", "Use", "Valor", "Digite", "Informe", "O "))


# ── 2. contraste das cores de texto (WCAG AA = 4,5:1) ───────────────────────
def _lum(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def tokens():
    css = open("app/static/css/app.css").read()
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", css.split("}")[0]))


@pytest.mark.parametrize("fg,bg", [
    ("ink", "paper"), ("ink", "surface"), ("ink-2", "paper"), ("ink-2", "surface"),
    ("ink-3", "paper"), ("ink-3", "surface"), ("ink-3", "sunken"),             # texto secundário: datas, dicas, legendas
    ("primary", "paper"), ("primary", "surface"),                               # links
    ("ok", "ok-soft"), ("warn", "warn-soft"), ("bad", "bad-soft"),              # selos de situação
    ("ok", "surface"), ("bad", "surface"), ("warn", "surface"),                 # valores coloridos
])
def test_text_colors_meet_wcag_aa(fg, bg):
    t = tokens()
    assert contrast(t[fg], t[bg]) >= 4.5, f"{fg} sobre {bg}: {contrast(t[fg], t[bg]):.2f}"


def test_white_text_on_primary_buttons_is_readable():
    t = tokens()
    assert contrast("#ffffff", t["primary"]) >= 4.5 and contrast("#ffffff", "#237a80") >= 4.5
    css = open("app/static/css/app.css").read()
    assert "#237a80" in css                                                    # botão “Nova venda” do menu


# ── 3. primeiro uso: a tela diz o que fazer ─────────────────────────────────
def test_getting_started_guides_a_new_user_and_disappears_after_the_first_sale(client, db):
    page = client.get("/?pessoa=geral").get_data(as_text=True)
    assert "Para começar" in page and "Cadastre quem vende" in page and "Faça a primeira venda" in page
    assert page.count('class="btn primary small"') == 1 and "Cadastrar pessoas" in page         # um único próximo passo em destaque
    ana = own.create_owner(db, "Ana")
    p = pvc.create_product(db, pvc.ProductInput(name="Vestido", price_cents=10000, cost_cents=4000, initial_stock=5))
    page = client.get("/?pessoa=geral").get_data(as_text=True)
    assert "Cadastrar produto" not in page and "Nova venda</a>" in page and "step-mark" in page
    post_json(client, "/vendas/nova", {"items": [{"product_id": p.id, "quantity": 1}], "seller_id": ana.id, "paid": "100", "payment_method": "pix"})
    assert "Para começar" not in client.get("/?pessoa=geral").get_data(as_text=True)


def test_getting_started_is_not_shown_in_a_persons_own_panel(client, db):
    ana = own.create_owner(db, "Ana")
    assert "Para começar" not in client.get(f"/?pessoa={ana.id}").get_data(as_text=True)


def test_setup_asks_the_business_name_and_the_receipt_uses_it(tmp_path):
    a = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'u.db'}", "TESTING": True, "SECRET_KEY": "k"})
    c = a.test_client(); c.get("/configurar")
    with c.session_transaction() as s:
        tok = s["csrf"]
    r = c.post("/configurar", data={"name": "Joana Lima", "username": "joana", "password": "Senha12345", "password2": "Senha12345",
                                    "business_name": "Ateliê da Joana", "person1": "Joana", "person2": "Beto", "csrf_token": tok})
    assert r.status_code == 302
    page = c.get("/").get_data(as_text=True)
    assert "Tudo certo, Joana" in page and "Siga o passo a passo" in page
    assert "Coloque o nome do seu negócio" in page and "Feito" in page                      # o passo do nome já aparece como feito
    assert "Ateliê da Joana" in c.get("/configuracoes").get_data(as_text=True)
    a.extensions["database"].dispose()


# ── 4. teclado e leitores de tela ───────────────────────────────────────────
def test_pages_have_skip_link_language_title_and_labelled_fields(client, db):
    for url in ("/", "/vendas", "/produtos/novo", "/clientes/novo", "/vendas/nova", "/configuracoes", "/conta"):
        page = client.get(url).get_data(as_text=True)
        assert 'lang="pt-BR"' in page and "<title>" in page and 'class="skip"' in page and 'id="conteudo"' in page, url
        for field in re.findall(r'<input(?![^>]*type="(?:hidden|checkbox|radio)")[^>]*\sid="([^"]+)"', page):
            assert f'for="{field}"' in page or f'aria-label' in page, f"{url}: campo #{field} sem rótulo"
    assert 'aria-label="Pular' not in client.get("/").get_data(as_text=True)


def test_icon_only_buttons_have_names(client):
    page = client.get("/vendas/nova").get_data(as_text=True)
    js = open("app/static/js/sale.js").read()
    assert re.findall(r'aria-label="Remover', js) and re.findall(r'aria-label="Diminuir', js) and re.findall(r'aria-label="Aumentar', js)
    assert 'aria-label="Buscar produto"' in page and 'aria-label="Tipo de desconto"' in page


def test_password_rules_are_visible_before_typing(tmp_path):
    from app import create_app
    app = create_app({"DATABASE_URL": f"sqlite:///{tmp_path/'p.db'}", "TESTING": True, "SECRET_KEY": "k"})
    page = app.test_client().get("/configurar").get_data(as_text=True)
    for text in ("A senha é obrigatória e precisa ter", "8 caracteres ou mais", "pelo menos uma letra", "pelo menos um número"):
        assert text in page
    assert 'minlength="8"' in page and "password-rules.js" in page
    app.extensions["database"].dispose()
