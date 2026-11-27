// Knoppen PDF/E-mail (fase 4, DS2): na een wijziging die nog niet is opgeslagen eerst laten opslaan,
// want het document wordt van de opgeslagen versie gemaakt.
(function () {
  document.querySelectorAll("form").forEach(function (form) {
    if (!form.querySelector(".doclink")) return;
    var dirty = false;
    var status = form.querySelector(".docstatus");
    var text = status ? status.getAttribute("data-save-first") : "";
    form.addEventListener("input", function (e) {
      if (!e.target.closest(".docmenu")) dirty = true;
    });
    form.addEventListener("click", function (e) {
      var a = e.target.closest("a.doclink");
      if (!a || !dirty) return;
      e.preventDefault();
      var p = form.querySelector(".docwarn");
      if (!p) {
        p = document.createElement("p");
        p.className = "error docwarn";
        p.setAttribute("role", "alert");
        (status || a).insertAdjacentElement("beforebegin", p);
      }
      p.textContent = text;
    });
  });
})();

// Batch en andere lijsten met vinkjes: "alles selecteren" voor de vinkjes met de naam uit data-check-all.
(function () {
  document.querySelectorAll("input[data-check-all]").forEach(function (all) {
    var name = all.getAttribute("data-check-all");
    if (!name) return;
    var form = all.closest("form") || document;
    all.addEventListener("change", function () {
      form.querySelectorAll('input[type=checkbox][name="' + name + '"]').forEach(function (cb) { cb.checked = all.checked; });
    });
  });
})();
