/* Dica dos gráficos (toque, mouse e teclado). O texto vem do servidor e entra sempre via textContent. */
(function () {
  "use strict";
  var tip = document.createElement("div");
  tip.className = "chart-tip"; tip.hidden = true; tip.setAttribute("role", "status");
  document.body.appendChild(tip);

  function fill(data) {
    tip.textContent = "";
    var title = document.createElement("div"); title.className = "tt"; title.textContent = data.t; tip.appendChild(title);
    data.r.forEach(function (r) {
      var row = document.createElement("div"); row.className = "tr";
      var key = document.createElement("span"); key.className = "tk"; key.style.background = r[2];
      var val = document.createElement("strong"); val.textContent = r[1];
      var name = document.createElement("span"); name.className = "tn"; name.textContent = r[0];
      row.appendChild(key); row.appendChild(val); row.appendChild(name); tip.appendChild(row);
    });
  }
  function place(x, y) {
    tip.hidden = false;
    var w = tip.offsetWidth, h = tip.offsetHeight, vw = document.documentElement.clientWidth;
    var left = Math.min(Math.max(8, x - w / 2), vw - w - 8), top = y - h - 14;
    if (top < 8) top = y + 18;
    tip.style.left = left + window.scrollX + "px"; tip.style.top = top + window.scrollY + "px";
  }
  function show(el, x, y) {
    try { fill(JSON.parse(el.getAttribute("data-tip"))); } catch (e) { return; }
    place(x, y);
  }
  document.addEventListener("pointermove", function (e) {
    var el = e.target.closest && e.target.closest(".hit");
    if (el) show(el, e.clientX, e.clientY); else if (e.pointerType !== "touch") tip.hidden = true;
  });
  document.addEventListener("pointerdown", function (e) {
    var el = e.target.closest && e.target.closest(".hit");
    if (el) show(el, e.clientX, e.clientY); else tip.hidden = true;
  });
  document.addEventListener("focusin", function (e) {
    if (e.target.classList && e.target.classList.contains("hit")) {
      var r = e.target.getBoundingClientRect(); show(e.target, r.left + r.width / 2, r.top + Math.min(r.height / 2, 60));
    }
  });
  document.addEventListener("focusout", function () { tip.hidden = true; });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") tip.hidden = true; });
  window.addEventListener("scroll", function () { tip.hidden = true; }, { passive: true });
})();
