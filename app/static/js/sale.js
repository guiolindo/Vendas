/* Tela de venda. Calcula só para mostrar; quem decide é o servidor. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var pos = $("pos");
  var csrf = document.querySelector("meta[name=csrf-token]").content;
  var DRAFT = "vendas:rascunho";

  var state = { cart: [], customer: null, mode: "full", results: [], active: 0, picking: null };
  function lineName(i) { return i.name + (i.size ? " · " + i.size : ""); }
  function sizeStockText(p) { return p.sizes.map(function (s) { return s.size + " " + (s.stock === null ? "" : s.stock); }).join(" · "); }

  // ── utilidades
  function debounce(fn, ms) { var t; return function () { var a = arguments; clearTimeout(t); t = setTimeout(function () { fn.apply(null, a); }, ms); }; }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function getJSON(url) { return fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }); }
  function linePrice(i) { return i.price == null ? i.list : i.price; }

  // ── totais
  function subtotal() { return state.cart.reduce(function (s, i) { return s + i.qty * linePrice(i); }, 0); }
  function discount() {
    var raw = $("discount").value.trim(), sub = subtotal();
    if (!raw) return { cents: 0, ok: true };
    if ($("discount-type").value === "percent") {
      var p = parseFloat(raw.replace("%", "").replace(",", "."));
      if (isNaN(p) || p < 0 || p > 100) return { cents: 0, ok: false, msg: "Desconto em % deve ficar entre 0 e 100." };
      return { cents: Math.round(sub * p / 100), ok: true };
    }
    var c = Money.parse(raw);
    if (c === null) return { cents: 0, ok: false, msg: "Desconto inválido." };
    if (c > sub) return { cents: 0, ok: false, msg: "O desconto não pode ser maior que o subtotal." };
    return { cents: c, ok: true };
  }
  function total() { return Math.max(subtotal() - discount().cents, 0); }
  // margem prevista: só dos itens com custo informado, com o desconto rateado (mesma conta do servidor)
  function margin() {
    var sub = subtotal(), tot = total(), rev = 0, cost = 0, none = 0;
    state.cart.forEach(function (i) {
      if (!i.cost) { none += 1; return; }
      rev += sub ? (i.qty * linePrice(i)) * tot / sub : 0; cost += i.qty * i.cost;
    });
    return { amount: Math.round(rev - cost), pct: rev > 0 ? (rev - cost) * 100 / rev : null, none: none };
  }
  function paidNow() {
    if (state.mode === "full") return total();
    if (state.mode === "later") return 0;
    var v = Money.parse($("paid").value); return v == null ? 0 : v;
  }

  // ── produtos
  var loadProducts = debounce(function () {
    getJSON(pos.dataset.apiProducts + "?q=" + encodeURIComponent($("product-search").value) ).then(function (list) {
      state.results = list; state.active = 0; renderResults();
    });
  }, 120);

  function renderResults() {
    var box = $("product-results");
    if (state.picking) { renderSizePicker(state.picking); return; }
    if (!state.results.length) {
      box.innerHTML = '<div class="empty">' + ($("product-search").value ? "Nenhum produto encontrado." : "Nenhum produto cadastrado. <a href='/produtos/novo'>Cadastrar produto</a>") + "</div>";
      return;
    }
    var head = $("product-search").value ? "" : '<div class="small muted" style="padding:.5rem 1rem">Mais vendidos</div>';
    box.innerHTML = head + state.results.map(function (p, idx) {
      var cls = p.stock === null ? "" : p.stock <= 0 ? "out" : (p.stock <= 3 ? "low" : "");
      return '<button type="button" class="result' + (idx === state.active ? " active" : "") + '" role="option" data-idx="' + idx + '">' +
        '<span class="r-name">' + esc(p.name) + '</span><span class="r-price">' + Money.format(p.price_cents) + "</span>" +
        '<span class="r-meta">' + esc(p.code) + (p.sku ? " · " + esc(p.sku) : "") + '</span><span class="r-stock ' + cls + '">' +
        (p.stock === null ? (p.sizes.length ? "Tamanhos: " + p.sizes.map(function (s) { return esc(s.size); }).join(" ") : "")
          : p.stock <= 0 ? "Sem estoque" : p.sizes.length ? esc(sizeStockText(p)) : p.stock + " " + esc(p.unit) + " em estoque") + "</span></button>";
    }).join("");
    $("product-search").setAttribute("aria-expanded", "true");
  }

  // Produto com tamanhos: primeiro pergunta o tamanho; só depois entra no carrinho.
  function renderSizePicker(p) {
    var box = $("product-results");
    box.innerHTML = '<div class="size-picker" role="group" aria-label="Tamanho de ' + esc(p.name) + '">' +
      '<div class="sp-head"><strong>' + esc(p.name) + '</strong><span class="muted small">Qual tamanho?</span></div><div class="sp-sizes">' +
      p.sizes.map(function (s) {
        var out = s.stock !== null && s.stock <= 0;
        return '<button type="button" class="size-btn" data-size="' + esc(s.size) + '"' + (out ? " disabled" : "") + '><span class="sb-size">' + esc(s.size) + '</span>' +
          '<span class="sb-stock">' + (s.stock === null ? "" : out ? "acabou" : s.stock + " em estoque") + "</span></button>";
      }).join("") + '</div><button type="button" class="link-button" data-back>‹ Voltar à lista</button></div>';
    var first = box.querySelector(".size-btn:not([disabled])"); if (first) first.focus();
  }
  function addProduct(p, size) {
    if (p.sizes && p.sizes.length && !size) { state.picking = p; renderResults(); updateResultsVisibility(); return; }
    var stock = p.stock;
    if (size) { var row = p.sizes.filter(function (s) { return s.size === size; })[0]; stock = row ? row.stock : null; }
    state.picking = null;
    if (stock !== null && stock <= 0) { flash("“" + p.name + (size ? " · " + size : "") + "” está sem estoque."); renderResults(); return; }
    var line = state.cart.filter(function (i) { return i.id === p.id && (i.size || null) === (size || null); })[0];
    if (line) {
      if (line.stock !== null && line.qty >= line.stock) { flash("Só há " + line.stock + " " + line.unit + " de “" + lineName(line) + "” em estoque."); }
      else line.qty += 1;
    } else {
      state.cart.push({ id: p.id, name: p.name, size: size || null, unit: p.unit, list: p.price_cents, cost: p.cost_cents || 0, price: null, qty: 1, stock: stock });
    }
    $("product-search").value = ""; loadProducts();
    if (phone.matches) $("product-search").blur(); else $("product-search").focus();  // no celular, o teclado não deve cobrir o carrinho
    changed();
  }

  // ── carrinho
  function renderCart() {
    var cart = $("cart");
    $("cart-empty").hidden = state.cart.length > 0;
    $("cart-count").textContent = state.cart.length ? state.cart.reduce(function (s, i) { return s + i.qty; }, 0) + " un. em " + state.cart.length + (state.cart.length === 1 ? " produto" : " produtos") : "";
    cart.innerHTML = state.cart.map(function (i, idx) {
      return '<div class="cart-row" data-idx="' + idx + '"><div><div class="c-name">' + esc(i.name) + (i.size ? ' <span class="size-tag">' + esc(i.size) + '</span>' : '') + '</div>' +
        '<div class="c-unit">' + (i.price != null ? 'Preço alterado · tabela ' + Money.format(i.list) : Money.format(i.list) + " cada") + '</div></div>' +
        '<div class="money-input" title="Preço unitário"><input class="price-edit" data-act="price" value="' + Money.plain(linePrice(i)) + '" inputmode="decimal" aria-label="Preço unitário de ' + esc(lineName(i)) + '"></div>' +
        '<div class="stepper"><button type="button" data-act="dec" aria-label="Diminuir">−</button>' +
        '<input data-act="qty" value="' + i.qty + '" inputmode="numeric" aria-label="Quantidade de ' + esc(lineName(i)) + '">' +
        '<button type="button" data-act="inc" aria-label="Aumentar">+</button></div>' +
        '<div class="c-total">' + Money.format(i.qty * linePrice(i)) + '</div>' +
        '<button type="button" class="icon-btn" data-act="del" aria-label="Remover ' + esc(lineName(i)) + '">×</button></div>';
    }).join("");
  }

  // ── cliente
  var findCustomers = debounce(function () {
    var q = $("customer-search").value.trim(), box = $("customer-results");
    if (!q) { box.hidden = true; return; }
    getJSON(pos.dataset.apiCustomers + "?q=" + encodeURIComponent(q)).then(function (list) {
      box.innerHTML = list.map(function (c) {
        return '<button type="button" class="result" role="option" data-id="' + c.id + '"><span class="r-name">' + esc(c.name) + '</span>' +
          '<span class="r-stock ' + (c.overdue_cents ? "out" : "") + '">' + (c.pending_cents ? "deve " + Money.format(c.pending_cents) : "") + '</span>' +
          '<span class="r-meta">' + esc(c.phone) + "</span></button>";
      }).join("") + '<button type="button" class="result" data-new="1"><span class="r-name">＋ Cadastrar “' + esc(q) + '” como novo cliente</span></button>';
      box.hidden = false; box._list = list;
    });
  }, 150);

  function setCustomer(c) {
    state.customer = c; $("customer-chip").hidden = !c; $("customer-search").hidden = !!c; $("customer-results").hidden = true;
    if (c) {
      $("chip-name").textContent = c.name;
      $("chip-info").textContent = c.overdue_cents ? "Atenção: já tem " + Money.format(c.overdue_cents) + " vencido"
        : (c.pending_cents ? "Já deve " + Money.format(c.pending_cents) : (c.phone || "Sem pendências"));
    } else { $("customer-search").value = ""; }
    changed();
  }

  // ── pagamento / resumo
  function dateBR(iso) { var p = iso.split("-"); return p[2] + "/" + p[1]; }
  function renderPayment() {
    var t = total(), paid = paidNow(), remaining = t - paid, mode = state.mode;
    $("amount-field").hidden = mode !== "partial";
    $("method-field").hidden = mode === "later";
    $("due-field").hidden = !(remaining > 0 || mode === "later");
    $("customer-hint").textContent = (mode === "full") ? "(opcional se for pago na hora)" : "(obrigatório: vai ficar saldo a receber)";
    var s = $("pay-summary"); s.className = "summary-line";
    if (!state.cart.length) { s.textContent = "Adicione produtos para ver o resumo."; return; }
    if (mode === "full") { s.className += " ok"; s.textContent = "Venda paga por completo: " + Money.format(t) + "."; }
    else if (mode === "later") { s.className += " warn"; s.textContent = Money.format(t) + " ficam a receber" + ($("due").value ? " até " + dateBR($("due").value) : "") + "."; }
    else if (paid <= 0) { s.textContent = "Digite quanto o cliente pagou agora."; }
    else if (paid > t) { s.className += " warn"; s.textContent = "O valor pago passa do total da venda (" + Money.format(t) + ")."; }
    else if (paid === t) { s.className += " ok"; s.textContent = "Pagou o total: a venda fica quitada."; }
    else { s.className += " warn"; s.textContent = "Pago " + Money.format(paid) + " · restam " + Money.format(remaining) + ($("due").value ? " para " + dateBR($("due").value) : "") + "."; }
  }

  var phone = window.matchMedia("(max-width: 860px)");
  function updateResultsVisibility() {
    var s = $("product-search");
    $("product-results").classList.toggle("show", document.activeElement === s || !!s.value || !!state.picking || state.cart.length === 0);
  }
  function changed() {
    pos.classList.toggle("is-empty", state.cart.length === 0);
    renderCart(); renderTotals(); renderPayment(); saveDraft(); updateResultsVisibility();
  }
  function renderTotals() {
    var d = discount();
    $("t-subtotal").textContent = Money.format(subtotal());
    $("t-total").textContent = Money.format(total());
    var m = margin(), ml = $("margin-line");
    ml.hidden = state.cart.length === 0 || m.pct === null;
    if (!ml.hidden) {
      $("t-margin").textContent = Money.format(m.amount) + " (" + m.pct.toFixed(1).replace(".", ",") + "%)" + (m.none ? " · " + m.none + " sem custo" : "");
      $("t-margin").className = "money " + (m.amount < 0 ? "owe-late" : "in-money");
    }
    $("discount").classList.toggle("invalid", !d.ok);
    $("finish").disabled = state.cart.length === 0;
    $("finish").textContent = state.cart.length ? "Concluir venda · " + Money.format(total()) : "Concluir venda";
  }

  function flash(msg) { showError(msg); setTimeout(function () { if ($("sale-error").textContent === msg) $("sale-error").hidden = true; }, 4000); }
  function showError(msg) { var e = $("sale-error"); e.textContent = msg; e.hidden = false; if (phone.matches) e.scrollIntoView({ block: "center", behavior: "smooth" }); }

  // ── rascunho: sobrevive a recarregar a página
  function saveDraft() {
    try { sessionStorage.setItem(DRAFT, JSON.stringify({ cart: state.cart, customer: state.customer, mode: state.mode })); } catch (e) {}
  }
  function loadDraft() {
    try {
      var d = JSON.parse(sessionStorage.getItem(DRAFT) || "null");
      if (d && d.cart && d.cart.length) { state.cart = d.cart; state.customer = d.customer; state.mode = d.mode || "full"; }
    } catch (e) {}
  }

  // ── envio
  function finish() {
    var btn = $("finish");
    if (btn.disabled) return;
    $("sale-error").hidden = true;
    var d = discount();
    if (!d.ok) { showError(d.msg); $("discount").focus(); return; }
    var remaining = total() - paidNow();
    var seller = sellerValue();
    if (document.querySelector("#seller-pick") && !seller) { showError("Escolha quem está vendendo."); var first = document.querySelector("#seller-pick input"); if (first) first.focus(); return; }
    if (remaining > 0 && !state.customer) { showError("Escolha o cliente: a venda vai ficar com saldo a receber."); $("customer-search").focus(); return; }
    if (state.mode === "partial" && paidNow() <= 0) { showError("Digite quanto o cliente pagou agora, ou escolha “Pagar depois”."); $("paid").focus(); return; }
    var payload = {
      client_token: pos.dataset.token,
      items: state.cart.map(function (i) { return { product_id: i.id, size: i.size || null, quantity: i.qty, price: i.price == null ? null : Money.plain(i.price) }; }),
      customer_id: state.customer ? state.customer.id : null, seller_id: seller || null,
      discount: $("discount").value.trim(), discount_type: $("discount-type").value,
      paid: Money.plain(paidNow()), payment_method: state.mode === "later" ? "" : $("method").value,
      due_date: remaining > 0 ? $("due").value : "", sale_date: $("sale-date").value, notes: $("notes").value
    };
    btn.disabled = true; btn.textContent = "Registrando…";
    fetch("/vendas/nova", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify(payload)
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }, function () { return { status: r.status, body: null }; }); })
      .then(function (res) {
        if (!res.body) {  // resposta que não é do sistema de vendas: página velha demais ou sessão encerrada
          showError(res.status === 400 || res.status === 401 ? "A página ficou aberta por muito tempo. Atualize a página e tente de novo: os itens da venda ficam guardados." : "Não foi possível registrar a venda. Tente de novo.");
          renderTotals(); return;
        }
        if (res.body.ok) { try { sessionStorage.removeItem(DRAFT); } catch (e) {} window.location = res.body.redirect; return; }
        showError(res.body.message || "Não foi possível registrar a venda."); renderTotals();
        var f = { customer: "customer-search", due_date: "due", paid: "paid", discount: "discount" }[res.body.field]; if (f) $(f).focus();
        if (res.body.field === "seller") { var s0 = document.querySelector("#seller-pick input"); if (s0) s0.focus(); }
      })
      .catch(function () { showError("Sem conexão com o sistema. A venda não foi registrada; tente de novo."); renderTotals(); });
  }

  // ── eventos
  $("product-search").addEventListener("input", function () { state.picking = null; loadProducts(); updateResultsVisibility(); });
  $("product-search").addEventListener("focus", updateResultsVisibility);
  $("product-search").addEventListener("blur", function () { setTimeout(updateResultsVisibility, 200); });
  $("product-search").addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); var n = state.results.length; if (!n) return;
      state.active = (state.active + (e.key === "ArrowDown" ? 1 : n - 1)) % n; renderResults();
      var el = document.querySelector("#product-results .active"); if (el) el.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) { finish(); return; }
      var p = state.results[state.active]; if (p) addProduct(p);
    }
  });
  $("product-results").addEventListener("click", function (e) {
    var sz = e.target.closest("[data-size]"); if (sz && state.picking) { addProduct(state.picking, sz.dataset.size); return; }
    if (e.target.closest("[data-back]")) { state.picking = null; renderResults(); $("product-search").focus(); return; }
    var b = e.target.closest("[data-idx]"); if (b) addProduct(state.results[+b.dataset.idx]);
  });
  $("product-results").addEventListener("keydown", function (e) {
    if (e.key === "Escape" && state.picking) { state.picking = null; renderResults(); $("product-search").focus(); }
  });

  $("cart").addEventListener("click", function (e) {
    var b = e.target.closest("[data-act]"); if (!b) return;
    var row = b.closest(".cart-row"), i = state.cart[+row.dataset.idx], act = b.dataset.act;
    if (act === "inc") { if (i.stock === null || i.qty < i.stock) i.qty++; else flash("Só há " + i.stock + " " + i.unit + " de “" + lineName(i) + "” em estoque."); }
    else if (act === "dec") { i.qty = Math.max(1, i.qty - 1); }
    else if (act === "del") { state.cart.splice(+row.dataset.idx, 1); }
    else return;
    changed();
  });
  $("cart").addEventListener("change", function (e) {
    var row = e.target.closest(".cart-row"); if (!row) return;
    var i = state.cart[+row.dataset.idx];
    if (e.target.dataset.act === "qty") {
      var q = parseInt(e.target.value, 10);
      if (isNaN(q) || q < 1) q = 1;
      if (i.stock !== null && q > i.stock) { flash("Só há " + i.stock + " " + i.unit + " de “" + lineName(i) + "” em estoque."); q = i.stock; }
      i.qty = q;
    } else if (e.target.dataset.act === "price") {
      var c = Money.parse(e.target.value);
      if (c === null) flash("Preço inválido."); else i.price = (c === i.list ? null : c);
    }
    changed();
  });
  $("cart").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); e.target.blur(); $("product-search").focus(); }
  });

  $("customer-search").addEventListener("input", findCustomers);
  $("customer-results").addEventListener("click", function (e) {
    var b = e.target.closest(".result"); if (!b) return;
    if (b.dataset.new) {
      var name = $("customer-search").value.trim();
      fetch("/api/clientes", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ name: name }) })
        .then(function (r) { return r.json(); }).then(function (j) { if (j.ok) setCustomer(j.customer); else showError(j.message); });
    } else {
      var c = $("customer-results")._list.filter(function (x) { return x.id === +b.dataset.id; })[0]; setCustomer(c);
    }
  });
  $("chip-clear").addEventListener("click", function () { setCustomer(null); $("customer-search").focus(); });
  document.addEventListener("click", function (e) { if (!e.target.closest(".customer-pick")) $("customer-results").hidden = true; });

  document.querySelectorAll("input[name=mode]").forEach(function (r) {
    r.addEventListener("change", function () {
      state.mode = r.value; if (state.mode === "partial") setTimeout(function () { $("paid").focus(); }, 0); changed();
    });
  });
  ["discount", "discount-type", "paid", "due"].forEach(function (id) { $(id).addEventListener("input", function () { renderTotals(); renderPayment(); }); });
  $("discount-type").addEventListener("change", function () { renderTotals(); renderPayment(); });
  document.querySelectorAll(".quick-dates [data-days]").forEach(function (b) {
    b.addEventListener("click", function () {
      var d = new Date(pos.dataset.today + "T12:00:00"); d.setDate(d.getDate() + +b.dataset.days);
      $("due").value = d.toISOString().slice(0, 10); renderPayment();
    });
  });
  $("finish").addEventListener("click", finish);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(); }
    var tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "/" && tag !== "input" && tag !== "textarea" && tag !== "select") { e.preventDefault(); $("product-search").focus(); }
  });

  // ── quem está vendendo: lembra a última escolha neste aparelho
  var SELLER_KEY = "vendas.vendedor";
  function sellerInputs() { return Array.prototype.slice.call(document.querySelectorAll("#seller-pick input")); }
  function sellerValue() { var c = sellerInputs().filter(function (i) { return i.checked; })[0]; return c ? c.value : ""; }
  function pickSeller(id) { sellerInputs().forEach(function (i) { i.checked = (i.value === String(id)); }); }
  sellerInputs().forEach(function (i) {
    i.addEventListener("change", function () { try { localStorage.setItem(SELLER_KEY, i.value); } catch (e) {}
      var err = $("sale-error"); if (err && !err.hidden && /quem está vendendo/.test(err.textContent)) err.hidden = true; });
  });
  try { if (!sellerValue()) pickSeller(localStorage.getItem(SELLER_KEY) || ""); } catch (e) {}

  // ── início
  $("due").value = pos.dataset.defaultDue;
  var prefill = null;
  try { prefill = pos.dataset.prefill ? JSON.parse(pos.dataset.prefill) : null; } catch (e) {}
  if (prefill) {  // refazer uma venda cancelada: começa já com os itens, o cliente, o desconto e a observação dela
    state.cart = prefill.items.map(function (i) { return { id: i.id, name: i.name, unit: i.unit, list: i.list, cost: i.cost || 0, price: i.price, qty: i.qty, stock: i.stock, size: i.size || null }; });
    state.customer = prefill.customer; state.mode = "full";
    $("discount").value = prefill.discount || ""; $("notes").value = prefill.notes || "";
    if (prefill.seller) pickSeller(prefill.seller);
  } else loadDraft();
  var radio = document.querySelector("input[name=mode][value=" + state.mode + "]"); if (radio) radio.checked = true;
  if (state.customer) setCustomer(state.customer);
  else if (pos.dataset.preselect) {
    getJSON(pos.dataset.apiCustomers + "?id=" + encodeURIComponent(pos.dataset.preselect)).then(function (l) { if (l[0]) setCustomer(l[0]); });
  }
  renderResults(); loadProducts(); changed();
})();
