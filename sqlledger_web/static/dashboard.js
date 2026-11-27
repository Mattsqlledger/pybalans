// Dashboard (DB7, keuze 12): een blok waarvan het verzoek mislukt (geen verbinding, serverfout) toont de melding
// in plaats van "bezig…" te blijven staan. Een verlopen sessie geeft HX-Redirect (main.py) en laadt de hele pagina.
(function () {
  function failed(e) {
    var el = e.detail && e.detail.elt;
    if (!el || !el.matches || !el.matches("[data-dash-error]")) return;
    var p = document.createElement("p");
    p.className = "error small";
    p.textContent = el.getAttribute("data-dash-error");
    el.replaceChildren(p);
    el.classList.add("card");
  }
  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, failed);
  });
  // VP2: inklapbare blokken (details[data-remember]) onthouden open/dicht in deze browser; blokken komen via htmx
  function remember(root) {
    root = root && root.querySelectorAll ? root : document;
    var found = Array.prototype.slice.call(root.querySelectorAll("details[data-remember]"));
    if (root.matches && root.matches("details[data-remember]")) found.push(root);
    found.forEach(function (d) {
      try { if (localStorage.getItem(d.getAttribute("data-remember")) === "closed") d.open = false; } catch (x) {}
    });
  }
  // alleen een klik op de titel (ook Enter/spatie) telt: bij het invoegen door htmx vuurt de browser zelf een
  // toggle-event "open", vóór htmx:load, dat anders de bewaarde keuze zou overschrijven
  document.addEventListener("click", function (e) {
    var s = e.target.closest && e.target.closest("summary");
    var d = s && s.parentElement;
    if (!d || !d.matches("details[data-remember]")) return;
    setTimeout(function () {
      try { localStorage.setItem(d.getAttribute("data-remember"), d.open ? "open" : "closed"); } catch (x) {}
    }, 0);
  });
  // htmx:load geeft het nieuwe element (bij outerHTML is de oude target al weg)
  document.addEventListener("htmx:load", function (e) { remember(e.detail && e.detail.elt); });
  remember(document);
})();
