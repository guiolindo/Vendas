/* Mostra, enquanto a pessoa digita, o que ainda falta na senha. O servidor é quem valida de verdade. */
(function () {
  "use strict";
  var list = document.getElementById("pw-rules");
  if (!list) return;
  var input = document.querySelector('input[aria-describedby="pw-rules"]');
  if (!input) return;
  var tests = { len: function (v) { return v.length >= 8; }, letter: function (v) { return /[A-Za-zÀ-ÿ]/.test(v); }, digit: function (v) { return /[0-9]/.test(v); } };
  function sync() {
    Array.prototype.forEach.call(list.querySelectorAll("[data-rule]"), function (li) {
      var ok = tests[li.dataset.rule](input.value);
      li.classList.toggle("ok", ok);
      li.setAttribute("data-state", ok ? "ok" : "falta");
    });
  }
  input.addEventListener("input", sync); sync();
})();
