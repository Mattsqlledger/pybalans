// Rapportformulier: periodevelden tonen bij de gekozen soort; afdrukknop.
(function () {
  function sync(root) {
    root.querySelectorAll("[data-period]").forEach(function (set) {
      var sel = set.querySelector("[data-period-select]");
      var kind = sel.value || "dates";
      set.querySelectorAll("[data-show]").forEach(function (el) {
        var show = el.getAttribute("data-show").split(" ").indexOf(kind) >= 0;
        el.hidden = !show;
        el.querySelectorAll("input, select").forEach(function (i) { i.disabled = !show; });
      });
    });
  }
  document.addEventListener("change", function (e) {
    if (e.target.matches("[data-period-select]")) sync(document);
  });
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-print]")) window.print();
  });
  sync(document);
})();
