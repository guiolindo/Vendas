"""Comprovantes para imprimir: venda (A4 ou bobina de 80 mm) e recibo de pagamento.
Nunca mostram custo nem margem. Páginas independentes (sem menu), pensadas para o papel."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import clock
from ..domain.extenso import reais
from ..models import Payment, Sale
from ..services import settings as settings_svc
from .helpers import audit_event, db

bp = Blueprint("receipts", __name__)


def _format() -> str:
    return "termico" if request.args.get("formato") == "termico" else "a4"


def thermal_height_mm(items: int, payments: int, owes: bool, extra_lines: int = 0) -> int:
    """Altura da bobina (80 mm de largura) estimada pelo conteúdo. O Chromium não aceita `size: 80mm auto`,
    então a altura é declarada: calibrada pela renderização real e um pouco folgada, para nunca quebrar em
    duas páginas. 150 = cabeçalho, dados, totais e rodapé; cada item ocupa ~19 mm."""
    return int(150 + 19 * items + ((14 + 9 * payments) if payments else 0) + (45 if owes else 0) + 6 * extra_lines + 10)


def _sellers(sale: Sale) -> list[str]:
    """Quem vendeu, para o comprovante (uma pessoa por venda)."""
    return [sale.seller.name] if sale.seller else []


@bp.get("/vendas/<int:sale_id>/comprovante")
def sale(sale_id: int):
    s = db().get(Sale, sale_id)
    if s is None:
        flash("Venda não encontrada.", "error")
        return redirect(url_for("sales.index"))
    cfg = settings_svc.load(db())
    audit_event("comprovante_venda", f"#{sale_id}")
    payments = [p for p in s.payments if not p.voided]
    owes = s.remaining_cents > 0 and s.customer is not None and not s.cancelled
    page_h = thermal_height_mm(len(s.items), len(payments) if not s.cancelled else 0, owes,
                               (2 if s.notes else 0) + (2 if s.cancelled else 0) + (1 if s.discount_cents else 0)
                               + (4 if cfg["pix_key"] and s.remaining_cents > 0 and not s.cancelled else 0))
    return render_template("receipt/sale.html", sale=s, cfg=cfg, fmt=_format(), sellers=_sellers(s),
                           payments=payments, now=clock.now(), page_h=page_h)


@bp.get("/pagamentos/<int:payment_id>/recibo")
def payment(payment_id: int):
    p = db().get(Payment, payment_id)
    if p is None:
        flash("Pagamento não encontrado.", "error")
        return redirect(url_for("sales.index"))
    if p.voided:
        flash("Este pagamento foi desfeito e não tem recibo.", "error")
        return redirect(url_for("sales.detail", sale_id=p.sale_id))
    sale = p.sale
    valid = sorted((x for x in sale.payments if not x.voided), key=lambda x: (x.paid_at, x.id))
    paid_until_here = sum(x.amount_cents for x in valid if (x.paid_at, x.id) <= (p.paid_at, p.id))
    cfg = settings_svc.load(db())
    audit_event("recibo_pagamento", f"#{payment_id}")
    return render_template("receipt/payment.html", p=p, sale=sale, cfg=cfg, fmt=_format(), now=clock.now(), page_h=140 + (26 if cfg["pix_key"] and max(sale.total_cents - paid_until_here, 0) > 0 and not sale.cancelled else 0),
                           balance_after=max(sale.total_cents - paid_until_here, 0), words=reais(p.amount_cents))
