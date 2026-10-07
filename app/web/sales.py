import uuid

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from sqlalchemy import select

from .. import clock
from ..config import Config
from ..domain.money import format_brl
from ..errors import BusinessError
from ..models import Customer, Payment, Sale
from ..repositories import sales as repo
from ..repositories.common import paginate
from ..schemas.inputs import sale_from_json
from ..schemas.parsing import Form
from ..services import payments, sales as svc
from ..domain.status import Status
from .helpers import (audit_event, current_user_id, db, handle_business_errors, page_number,
                      remembered_args, safe_next)

bp = Blueprint("sales", __name__)
FILTERS = ("q", "status", "de", "ate")


@bp.get("/vendas")
def index():
    args, remembered = remembered_args("vendas", FILTERS)
    f = repo.SaleFilters(q=args.get("q", ""), status=args.get("status", "") if args.get("status") in {s.value for s in Status} else "",
                         date_from=_date(args.get("de")), date_to=_date(args.get("ate")))
    page = paginate(db(), repo.sales_query(f, clock.today()), page_number(), 25)
    return render_template("sales/list.html", page=page, args=args, remembered=remembered,
                           statuses=[s for s in Status])


def _date(raw):
    return Form({"d": raw or ""}).date("d", "Data") if raw else None


@bp.get("/vendas/nova")
def new():
    import datetime
    from ..services import owners as owner_svc
    prefill = _prefill(request.args.get("refazer", ""))
    return render_template("sales/new.html", token=uuid.uuid4().hex, owners=owner_svc.list_owners(db(), only_active=True), prefill=prefill,
                           default_due=(clock.today() + datetime.timedelta(days=current_app.config["DEFAULT_DUE_DAYS"])).isoformat())


def _prefill(raw: str) -> dict | None:
    """Dados de uma venda CANCELADA para refazê-la na tela de venda. Itens inativos ou sem estoque ficam de fora (e são avisados)."""
    if not raw.isdigit() or int(raw) >= 2**31:
        return None
    old = db().get(Sale, int(raw))
    if old is None or not old.cancelled:
        return None
    items, skipped = [], 0
    for it in old.items:
        p = it.product
        if not p.active or (p.track_stock and p.stock_qty <= 0):
            skipped += 1
            continue
        items.append({"id": p.id, "name": p.name, "unit": p.unit, "code": p.code, "list": p.price_cents, "cost": p.cost_cents,
                      "stock": p.stock_qty if p.track_stock else None,
                      "qty": min(it.quantity, p.stock_qty) if p.track_stock else it.quantity,
                      "price": None if it.unit_price_cents == p.price_cents else it.unit_price_cents})
    customer = None
    if old.customer and old.customer.active:
        from ..repositories import customers as customer_repo
        row = customer_repo.summary(db(), old.customer_id, clock.today())
        customer = {"id": old.customer_id, "name": old.customer.name, "phone": old.customer.phone or "",
                    "pending_cents": row.pending, "overdue_cents": row.overdue}
    return {"number": old.id, "seller": old.seller_id, "items": items, "skipped": skipped, "customer": customer,
            "discount": f"{old.discount_cents // 100},{old.discount_cents % 100:02d}" if old.discount_cents else "",
            "notes": old.notes or ""}


@bp.post("/vendas/nova")
def create():
    """Recebe a venda em JSON e responde em JSON, para a tela manter o carrinho em caso de erro."""
    payload = request.get_json(silent=True) or {}
    try:
        sale = svc.create_sale(db(), sale_from_json(payload), current_user_id())
    except BusinessError as e:
        return jsonify(ok=False, message=e.message, field=e.field), 422
    audit_event("venda_criada", f"#{sale.id} total={sale.total_cents}")
    flash(f"Venda #{sale.id} registrada com sucesso.", "success")
    return jsonify(ok=True, redirect=url_for("sales.detail", sale_id=sale.id))


@bp.get("/vendas/<int:sale_id>")
def detail(sale_id: int):
    sale = db().get(Sale, sale_id)
    if sale is None:
        flash("Venda não encontrada.", "error")
        return redirect(url_for("sales.index"))
    from ..services import margin as margin_svc
    nets = margin_svc.item_nets(sale)
    return render_template("sales/detail.html", sale=sale, margin=margin_svc.sale_margin(sale),
                           item_margins={i.id: margin_svc.item_margin(i, nets[i.id]) for i in sale.items})


@bp.route("/vendas/<int:sale_id>/editar", methods=["GET", "POST"])
def edit(sale_id: int):
    sale = db().get(Sale, sale_id)
    if sale is None:
        flash("Venda não encontrada.", "error")
        return redirect(url_for("sales.index"))
    if request.method == "POST":
        f = Form(request.form)
        try:
            kw = {"seller_id": f.optional_int("seller_id")} if "seller_id" in request.form else {}
            svc.update_sale(db(), sale_id, f.optional_int("customer_id"), f.date("due_date", "Vencimento"),
                            f.text("notes") or None, **kw)
        except BusinessError as e:
            flash(e.message, "error")
            return render_template("sales/edit.html", sale=sale, values=request.form, error_field=e.field,
                                   customers=_active_customers(), owners=_owners_for(sale)), 422
        flash("Venda atualizada.", "success")
        return redirect(url_for("sales.detail", sale_id=sale_id))
    return render_template("sales/edit.html", sale=sale, values={}, error_field=None,
                           customers=_active_customers(), owners=_owners_for(sale))


def _active_customers():
    return list(db().scalars(select(Customer).where(Customer.active.is_(True)).order_by(Customer.name)))


def _owners_for(sale):
    """Pessoas para escolher como vendedora: as ativas, mais a atual da venda mesmo que esteja inativa."""
    from ..services import owners as owner_svc
    return [o for o in owner_svc.list_owners(db()) if o.active or o.id == sale.seller_id]


@bp.post("/vendas/<int:sale_id>/pagamentos")
@handle_business_errors
def add_payment(sale_id: int):
    f = Form(request.form)
    payment = payments.register_payment(
        db(), sale_id, f.money("amount", "Valor", required=True), f.text("method"),
        f.date("paid_at", "Data do pagamento"), f.text("note") or None, current_user_id(),
        token=f.text("request_token")[:60] or None)
    audit_event("pagamento", f"venda #{sale_id} {payment.amount_cents}")
    flash(f"Pagamento de {format_brl(payment.amount_cents)} registrado.", "success")
    return redirect(safe_next(request.form.get("next"), url_for("sales.detail", sale_id=sale_id)))


@bp.post("/vendas/<int:sale_id>/cancelar")
@handle_business_errors
def cancel(sale_id: int):
    svc.cancel_sale(db(), sale_id, request.form.get("reason", ""), current_user_id())
    audit_event("venda_cancelada", f"#{sale_id}")
    flash(f"Venda #{sale_id} cancelada. O estoque foi devolvido.", "success")
    return redirect(url_for("sales.detail", sale_id=sale_id))


@bp.post("/vendas/<int:sale_id>/excluir")
@handle_business_errors
def delete(sale_id: int):
    summary = svc.delete_sale(db(), sale_id)
    audit_event("venda_excluida", summary)
    flash(f"Venda #{sale_id} excluída definitivamente.", "success")
    return redirect(url_for("sales.index"))


@bp.post("/vendas/<int:sale_id>/corrigir")
@handle_business_errors
def fix(sale_id: int):
    """Corrigir itens ou valores: cancela esta venda (estoque volta, pagamentos estornados) e abre a nova já preenchida."""
    svc.cancel_sale(db(), sale_id, "Corrigida: refeita com os dados certos", current_user_id())
    audit_event("venda_corrigida", f"#{sale_id}")
    flash(f"Venda #{sale_id} cancelada para correção. Confira os itens abaixo e finalize de novo; "
          "os pagamentos foram desfeitos e precisam ser lançados outra vez.", "info")
    return redirect(url_for("sales.new", refazer=sale_id))


@bp.post("/pagamentos/<int:payment_id>/estornar")
@handle_business_errors
def void_payment(payment_id: int):
    payment = payments.void_payment(db(), payment_id, request.form.get("reason"), current_user_id())
    audit_event("pagamento_estornado", f"#{payment_id} venda #{payment.sale_id}")
    flash(f"Pagamento de {format_brl(payment.amount_cents)} desfeito. O valor volta a ficar em aberto e o registro continua no histórico.", "success")
    return redirect(url_for("sales.detail", sale_id=payment.sale_id))
