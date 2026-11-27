// Keuzemenu administratie wisselen in de kopregel (AW1, AW3).
(function () {
  var sw = document.querySelector("details.switcher");
  if (!sw) return;

  // AW3: is er op deze pagina een formulier gewijzigd en nog niet verstuurd? Dan eerst vragen.
  var dirty = false;
  function counts(form) {
    return form && !form.closest(".topbar") && (form.getAttribute("method") || "").toLowerCase() === "post";   // niet form.method: een veld "method" (Instellingen, rapporten) overschaduwt dat
  }
  document.addEventListener("input", function (e) { if (counts(e.target.form)) dirty = true; });
  document.addEventListener("change", function (e) { if (counts(e.target.form)) dirty = true; });
  document.addEventListener("submit", function (e) { if (!e.target.closest(".topbar")) dirty = false; });
  function leave(e) {
    if (dirty && !window.confirm(sw.getAttribute("data-leave"))) {
      e.preventDefault();
      return false;
    }
    dirty = false;
    return true;
  }
  sw.querySelector(".switchform").addEventListener("submit", leave);
  sw.querySelectorAll("a").forEach(function (a) { a.addEventListener("click", leave); });

  // zoeken: de lijst filteren op naam, code of database (zelfde regel als routes_main._matches)
  var input = sw.querySelector(".switchsearch input");
  var found = sw.querySelector(".switchfound");
  var recent = sw.querySelector(".switchrecent");
  if (input && found) {
    var items = found.querySelectorAll("li[data-find]");
    var none = found.querySelector(".switchnone");
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      var n = 0;
      items.forEach(function (li) {
        var hit = q !== "" && li.getAttribute("data-find").indexOf(q) !== -1;
        li.hidden = !hit;
        if (hit) n++;
      });
      found.hidden = q === "";
      if (none) none.hidden = n > 0;
      if (recent) recent.hidden = q !== "";
    });
    // Enter met precies één treffer: die openen; anders naar de keuzepagina (gewone GET)
    input.form.addEventListener("submit", function (e) {
      var hits = Array.prototype.filter.call(items, function (li) { return !li.hidden; });
      var b = hits.length === 1 && hits[0].querySelector("button:not([disabled])");
      if (b) {
        e.preventDefault();
        b.click();                    // het keuzeformulier vraagt zelf of je wijzigingen mag verlaten
      } else {
        leave(e);
      }
    });
  }

  // openen: zoekveld focus; sluiten met Escape of klik ernaast
  sw.addEventListener("toggle", function () { if (sw.open && input) input.focus(); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && sw.open) { sw.open = false; sw.querySelector("summary").focus(); }
  });
  document.addEventListener("click", function (e) { if (sw.open && !sw.contains(e.target)) sw.open = false; });
})();
