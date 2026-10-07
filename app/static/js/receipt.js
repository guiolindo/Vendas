/* Comprovante: só o botão de imprimir. */
document.addEventListener("click", function (e) {
  if (e.target.closest("[data-print]")) window.print();
});
