// Klanten/leveranciers: bij het wisselen tussen bedrijf en persoon het formulier
// opnieuw laten opbouwen (zoals ct.pl). Zonder JavaScript doet "Bijwerken" hetzelfde.
// In de lijst: "alles selecteren" voor meervoudig verwijderen.
(function () {
  var form = document.querySelector("form.ctform");
  if (form) {
    var update = form.querySelector("[data-update]");
    form.querySelectorAll("[data-refresh]").forEach(function (el) {
      el.addEventListener("change", function () { if (update) update.click(); });
    });
  }
  var all = document.querySelector("[data-check-all]");
  if (all) {
    all.addEventListener("change", function () {
      document.querySelectorAll("input[name=ids]").forEach(function (cb) { cb.checked = all.checked; });
    });
  }
})();
