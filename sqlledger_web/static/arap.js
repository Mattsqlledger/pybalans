// Verkoop/inkoop: altijd een lege regel en betaalregel onderaan; btw en totalen
// laat het scherm door de server uitrekenen (POST met action=calc) en vervangt
// alleen die delen. Relatie, datum, termijn en valuta verversen het hele formulier
// (zoals aa.pl). Zonder JavaScript doet de knop "Bijwerken" hetzelfde.
(function () {
  var form = document.querySelector("form.arapform");
  if (!form) return;

  function filled(tr) {
    return Array.prototype.some.call(tr.querySelectorAll("input:not([type=hidden]), select"), function (el) {
      return el.tagName !== "SELECT" && el.type !== "date" && !el.classList.contains("acc") && el.value.trim() !== "";
    });
  }

  function ensureEmpty(body, rowSel, tplId, countSel) {
    var rows = body.querySelectorAll(rowSel);
    if (rows.length && !filled(rows[rows.length - 1])) return;
    var n = rows.length + 1;
    var tmp = document.createElement("tbody");
    tmp.innerHTML = document.getElementById(tplId).innerHTML.replace(/_N"/g, "_" + n + '"');
    var tr = tmp.firstElementChild;
    if (rowSel === "[data-line]" && rows.length) {      // rekening van de regel erboven voorstellen
      var prev = rows[rows.length - 1].querySelector(".acc");
      tr.querySelector(".acc").value = prev ? prev.value : "";
    }
    body.appendChild(tr);
    form.querySelector(countSel).value = n;
  }

  var lines = form.querySelector("[data-lines]");
  var pays = form.querySelector("[data-pays]");
  form.addEventListener("input", function (e) {
    if (lines && e.target.closest("[data-line]")) ensureEmpty(lines, "[data-line]", "line-template", "[data-rowcount]");
    if (pays && e.target.closest("[data-pay]")) ensureEmpty(pays, "[data-pay]", "pay-template", "[data-paidcount]");
    if (e.target.matches("[data-tax]")) {        // zelf ingevuld bedrag: niet meer berekenen
      var cb = e.target.closest("tr").querySelector("input[type=checkbox]");
      if (cb) cb.checked = false;
    }
  });

  var busy = null;
  function calc() {
    var data = new FormData(form);
    data.set("action", "calc");
    data.delete("bijlage");                // bestand alleen met Bijwerken/Opslaan versturen
    if (busy) busy.abort();
    busy = new AbortController();
    fetch(form.getAttribute("action"), { method: "POST", body: data, credentials: "same-origin", signal: busy.signal })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        ["#arap-calc", "#arap-paid", "#arap-credit", "#arap-error"].forEach(function (sel) {
          var mine = form.querySelector(sel) || document.querySelector(sel);
          var fresh = doc.querySelector(sel);
          if (mine && fresh) mine.replaceWith(fresh);
        });
      })
      .catch(function () {});
  }

  function refresh() {
    var b = form.querySelector("[data-update]");
    if (b) { b.hidden = false; b.click(); } else form.submit();
  }

  form.addEventListener("change", function (e) {
    if (e.target.matches("[data-refresh]")) refresh();
    else if (e.target.matches("[data-calc]")) calc();
  });

  // relatie zoeken: keuzelijst bij het typen
  var vc = form.querySelector("[data-vc]");
  var list = document.getElementById("vclist");
  var timer = null;
  if (vc && list) {
    vc.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var td = form.querySelector("[name=transdate]");
        var url = form.getAttribute("data-vc-url") + "?vc_text=" + encodeURIComponent(vc.value) +
                  "&transdate=" + encodeURIComponent(td ? td.value : "");
        fetch(url, { credentials: "same-origin" }).then(function (r) { return r.text(); })
          .then(function (html) { list.innerHTML = html; });
      }, 200);
    });
  }

  var update = form.querySelector("[data-update]");
  if (update) update.hidden = true;     // alleen nodig zonder JavaScript
})();
