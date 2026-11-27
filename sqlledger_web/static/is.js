// Factuur met artikelen: altijd een lege regel en betaalregel onderaan; regelbedragen, btw en totalen
// laat het scherm door de server uitrekenen (POST met action=calc) en vervangt alleen die delen.
// Relatie, artikel, datum, termijn en valuta verversen het hele formulier (zoals is.pl/ir.pl).
// Zonder JavaScript doet de knop "Bijwerken" hetzelfde.
(function () {
  var form = document.querySelector("form.isform");
  if (!form) return;

  function filled(tr) {
    return Array.prototype.some.call(tr.querySelectorAll("input:not([type=hidden])"), function (el) {
      return el.type !== "date" && el.value.trim() !== "";
    });
  }

  function ensureEmpty(body, rowSel, tplId, countSel) {
    var rows = body.querySelectorAll(rowSel);
    if (rows.length && !filled(rows[rows.length - 1])) return;
    var n = rows.length + 1;
    var tmp = document.createElement("tbody");
    tmp.innerHTML = document.getElementById(tplId).innerHTML.replace(/_N"/g, "_" + n + '"').replace(/"N"/g, '"' + n + '"');
    body.appendChild(tmp.firstElementChild);
    form.querySelector(countSel).value = n;
  }

  var lines = form.querySelector("[data-lines]");
  var pays = form.querySelector("[data-pays]");
  form.addEventListener("input", function (e) {
    if (lines && e.target.closest("[data-line]")) ensureEmpty(lines, "[data-line]", "line-template", "[data-rowcount]");
    if (pays && e.target.closest("[data-pay]")) ensureEmpty(pays, "[data-pay]", "pay-template", "[data-paidcount]");
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
        ["#is-calc", "#is-sum", "#is-paid", "#is-credit", "#is-error"].forEach(function (sel) {
          var mine = form.querySelector(sel) || document.querySelector(sel);
          var fresh = doc.querySelector(sel);
          if (mine && fresh) mine.replaceWith(fresh);
        });
        form.querySelectorAll("[data-lt]").forEach(function (td) {
          var fresh = doc.querySelector('[data-lt="' + td.getAttribute("data-lt") + '"]');
          td.textContent = fresh ? fresh.textContent : "";
        });
        form.querySelectorAll("[data-warn]").forEach(function (el) { el.remove(); });
        doc.querySelectorAll("[data-warn]").forEach(function (w) {
          var qty = form.querySelector('[name="qty_' + w.getAttribute("data-warn") + '"]');
          if (qty) { qty.insertAdjacentElement("afterend", w); qty.insertAdjacentHTML("afterend", "<br>"); }
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

  function suggest(input, list, url) {
    var timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        fetch(url(input.value), { credentials: "same-origin" }).then(function (r) { return r.text(); })
          .then(function (html) { list.innerHTML = html; });
      }, 200);
    });
  }
  var vc = form.querySelector("[data-vc]");
  var vclist = document.getElementById("vclist");
  if (vc && vclist) suggest(vc, vclist, function (v) {
    var td = form.querySelector("[name=transdate]");
    return form.getAttribute("data-vc-url") + "?vc_text=" + encodeURIComponent(v) + "&transdate=" + encodeURIComponent(td ? td.value : "");
  });
  var itemlist = document.getElementById("itemlist");
  form.addEventListener("focusin", function (e) {
    if (e.target.matches("[data-item]") && !e.target.dataset.bound) {
      e.target.dataset.bound = "1";
      suggest(e.target, itemlist, function (v) { return form.getAttribute("data-item-url") + "?text=" + encodeURIComponent(v); });
    }
  });

  var update = form.querySelector("[data-update]");
  if (update) update.hidden = true;     // alleen nodig zonder JavaScript
})();
