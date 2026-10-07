/* Prévia da margem enquanto a pessoa digita custo e preço. Só mostra; o servidor é quem calcula. */
(function () {
  "use strict";
  var cost = document.getElementById("f-cost"), price = document.getElementById("f-price"), box = document.getElementById("margin-preview");
  if (!cost || !price || !box) return;
  function show() {
    var c = Money.parse(cost.value), p = Money.parse(price.value);
    box.className = "margin-preview";
    if (p === null || p <= 0) { box.hidden = true; return; }
    box.hidden = false;
    if (c === null || c <= 0) { box.textContent = "Informe quanto o produto custou para ver a sua margem."; box.className += " none"; return; }
    var m = p - c, pct = (m * 100 / p).toFixed(1).replace(".", ",");
    box.className += m < 0 ? " loss" : " gain";
    box.textContent = (m < 0 ? "Prejuízo de " : "Você ganha ") + Money.format(Math.abs(m)) + " por unidade (" + pct + "% do preço de venda).";
  }
  cost.addEventListener("input", show); price.addEventListener("input", show); show();
})();

/* "Controlar estoque": esconde os campos de quantidade quando o produto não terá estoque. */
(function () {
  "use strict";
  var box = document.getElementById("f-track");
  if (!box) return;
  function sync() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-only]"), function (el) { el.hidden = !box.checked; });
  }
  box.addEventListener("change", sync); sync();
})();
