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

/* Estoque e tamanhos: mostra só os campos que fazem sentido para o que a pessoa marcou. */
(function () {
  "use strict";
  var track = document.getElementById("f-track");
  if (!track) return;
  var boxes = Array.prototype.slice.call(document.querySelectorAll("input[data-size]"));
  function sync() {
    var any = boxes.some(function (b) { return b.checked; });
    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-only]"), function (el) {
      el.hidden = !track.checked || (el.hasAttribute("data-unsized-only") && any);
    });
    boxes.forEach(function (b) {
      var field = b.closest(".size-opt").querySelector("[data-size-stock]");
      if (field) field.hidden = !(track.checked && b.checked);
    });
  }
  track.addEventListener("change", sync);
  boxes.forEach(function (b) { b.addEventListener("change", sync); });
  sync();
})();
