/* Comportamentos comuns a todas as telas. As regras de negócio ficam no servidor. */
(function () {
  "use strict";

  // ── Dinheiro: mesmas convenções do servidor (vírgula = decimal; 1.234 = mil duzentos e trinta e quatro)
  var Money = {
    parse: function (text) {
      var s = String(text == null ? "" : text).replace(/R\$/g, "").replace(/\s/g, "");
      if (!s || s[0] === "-") return null;
      if (s.indexOf(",") >= 0) s = s.replace(/\./g, "").replace(",", ".");
      else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
      if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
      return Math.round(parseFloat(s) * 100);
    },
    format: function (cents) {
      var neg = cents < 0, v = Math.abs(cents);
      var reais = Math.floor(v / 100), c = String(v % 100).padStart(2, "0");
      return (neg ? "-" : "") + "R$ " + String(reais).replace(/\B(?=(\d{3})+(?!\d))/g, ".") + "," + c;
    },
    plain: function (cents) { return Money.format(cents).replace("R$ ", ""); }
  };
  window.Money = Money;

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t.closest("[data-close]")) { t.closest(".toast").remove(); return; }
    if (t.closest("[data-menu]")) {
      var side = document.getElementById("side"), open = side.classList.toggle("open");
      t.closest("[data-menu]").setAttribute("aria-expanded", open);
      return;
    }
    if (t.closest("[data-print]")) { window.print(); return; }
    var fill = t.closest("[data-fill]");
    if (fill) {
      var input = fill.closest("form").querySelector("[name=amount]");
      input.value = fill.getAttribute("data-fill"); input.dispatchEvent(new Event("input")); input.focus();
      return;
    }
    var row = t.closest("tr[data-href]");
    if (row && !t.closest("a, button, input, select, summary, details, form")) window.location = row.getAttribute("data-href");
  });

  // Avisos de sucesso somem sozinhos; erros ficam até serem fechados.
  document.querySelectorAll("[data-toast=success], [data-toast=info]").forEach(function (el) {
    setTimeout(function () { el.remove(); }, 5000);
  });

  // Confirmação só em ações destrutivas (forms marcados com data-confirm).
  document.addEventListener("submit", function (e) {
    var f = e.target, msg = f.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    // evita clique duplo: desabilita os botões depois que o envio começou
    setTimeout(function () {
      f.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; });
    }, 0);
  });
  window.addEventListener("pageshow", function () {
    document.querySelectorAll("button[type=submit]:disabled").forEach(function (b) { b.disabled = false; });
  });

  // Valor de pagamento não pode passar do que falta (o servidor valida de novo).
  document.querySelectorAll("input[data-max]").forEach(function (input) {
    var max = parseInt(input.getAttribute("data-max"), 10);
    function check() {
      var v = Money.parse(input.value);
      if (input.value && v === null) input.setCustomValidity("Digite um valor válido, como 300 ou 300,50.");
      else if (v !== null && v <= 0) input.setCustomValidity("O valor precisa ser maior que zero.");
      else if (v !== null && v > max) input.setCustomValidity("O valor não pode ser maior que o valor restante (" + Money.format(max) + ").");
      else input.setCustomValidity("");
    }
    input.addEventListener("input", check); check();
  });

  // Campos de dinheiro: "300" vira "300,00" ao sair do campo.
  document.addEventListener("focusout", function (e) {
    var el = e.target;
    if (el.matches && el.matches(".money-input input")) {
      var v = Money.parse(el.value);
      if (v !== null) el.value = Money.plain(v);
    }
  });

  var kind = document.querySelector("[data-stock-kind]");
  if (kind) kind.addEventListener("change", function () {
    document.querySelector("[data-stock-label]").textContent =
      kind.value === "ajuste" ? "Quantidade contada (o total que existe agora)" : "Quantidade que chegou";
  });

  // Atalhos: N = nova venda; "/" = foco na busca da página.
  document.addEventListener("keydown", function (e) {
    var tag = (e.target.tagName || "").toLowerCase();
    if (e.ctrlKey || e.metaKey || e.altKey || tag === "input" || tag === "textarea" || tag === "select") return;
    if (e.key === "n" && !document.getElementById("pos")) {
      var link = document.querySelector(".new-sale"); if (link) window.location = link.href;
    }
    if (e.key === "/") {
      var s = document.querySelector("[data-search]"); if (s) { e.preventDefault(); s.focus(); s.select(); }
    }
  });
})();
