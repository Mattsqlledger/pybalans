// Afletteren: aangevinkt, afgeletterd saldo en verschil rekent de server (POST met
// action=calc); het script vervangt alleen het blok met de saldi. Regels die samen
// afgeletterd worden (zelfde data-group) gaan samen aan/uit. Zonder JavaScript doen
// de knoppen "Bijwerken" en "Alles aan-/uitvinken" hetzelfde.
(function () {
  var form = document.querySelector("form.rcform");
  if (!form) return;

  var busy = null;
  function calc() {
    var data = new FormData(form);
    data.set("action", "calc");
    if (busy) busy.abort();
    busy = new AbortController();
    fetch(form.getAttribute("action"), { method: "POST", body: data, credentials: "same-origin", signal: busy.signal })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        ["#rc-sum", "#rc-error"].forEach(function (sel) {
          var mine = document.querySelector(sel);
          var fresh = doc.querySelector(sel);
          if (mine && fresh) mine.replaceWith(fresh);
        });
      })
      .catch(function () {});
  }

  var boxes = Array.prototype.slice.call(form.querySelectorAll("input[name=sel]"));
  var all = form.querySelector("[data-allbox]");

  function mark(box) {
    var tr = box.closest("tr");
    if (tr) tr.classList.toggle("checked", box.checked);
  }

  form.addEventListener("change", function (e) {
    var t = e.target;
    if (t === all) {
      boxes.forEach(function (b) { b.checked = all.checked; mark(b); });
      calc();
    } else if (t.name === "sel") {
      var g = t.getAttribute("data-group");
      boxes.forEach(function (b) {
        if (b.getAttribute("data-group") === g) { b.checked = t.checked; mark(b); }
      });
      if (all) all.checked = boxes.every(function (b) { return b.checked; });
      calc();
    } else if (t.matches("[data-calc]")) {
      calc();
    }
  });

  // Enter in het afschriftsaldo: niet opslaan, alleen doorrekenen
  var bal = form.querySelector("[name=statementbalance]");
  if (bal) bal.addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); calc(); }
  });

  if (all) all.hidden = false;
  form.querySelectorAll("[data-update], [data-nojs]").forEach(function (b) { b.hidden = true; });
})();
