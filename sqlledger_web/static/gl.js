// Memoriaal: totalen en verschil live, altijd een lege regel onderaan (M2),
// koers voorstellen bij een andere valuta (M7). Zonder JavaScript werkt het
// formulier ook: de knop "Bijwerken" doet dan hetzelfde op de server.
(function () {
  var form = document.querySelector("form.glform");
  if (!form) return;
  var nf = form.getAttribute("data-nf") || "1,000.00";
  var prec = parseInt(form.getAttribute("data-precision") || "2", 10);
  var body = form.querySelector("[data-lines]");
  var tpl = document.getElementById("line-template");
  var commaDecimal = nf === "1.000,00" || nf === "1000,00";

  function parse(text) {
    text = (text || "").trim();
    if (!text) return 0;
    text = text.replace(/[\s']/g, "");
    text = commaDecimal ? text.replace(/\./g, "").replace(",", ".") : text.replace(/,/g, "");
    var v = Number(text);
    return isFinite(v) ? Math.round(v * Math.pow(10, prec)) : NaN;   // in centen
  }

  function format(cents) {
    var neg = cents < 0;
    var s = Math.abs(cents).toString();
    while (s.length <= prec) s = "0" + s;
    var whole = prec ? s.slice(0, -prec) : s;
    var frac = prec ? s.slice(-prec) : "";
    var sep = { "1,000.00": ",", "1.000,00": ".", "1'000.00": "'" }[nf] || "";
    if (sep) whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, sep);
    var dec = commaDecimal ? "," : ".";
    return (neg ? "-" : "") + whole + (prec ? dec + frac : "");
  }

  function filled(tr) {
    return Array.prototype.some.call(tr.querySelectorAll("input:not([type=hidden]):not([type=checkbox]), select"),
      function (el) { return el.value.trim() !== "" && !el.classList.contains("acc"); });
  }

  function addLine() {
    var n = body.querySelectorAll("[data-line]").length + 1;
    var html = tpl.innerHTML.replace(/_N"/g, "_" + n + '"');
    var tmp = document.createElement("tbody");
    tmp.innerHTML = html;
    body.appendChild(tmp.firstElementChild);
    form.querySelector("[data-rowcount]").value = n;
  }

  function totals() {
    var dr = 0, cr = 0, bad = false;
    body.querySelectorAll("[data-line]").forEach(function (tr) {
      var d = parse(tr.querySelector("[data-debit]").value);
      var c = parse(tr.querySelector("[data-credit]").value);
      if (isNaN(d) || isNaN(c)) bad = true;
      tr.querySelector("[data-debit]").classList.toggle("invalid", isNaN(d));
      tr.querySelector("[data-credit]").classList.toggle("invalid", isNaN(c));
      dr += isNaN(d) ? 0 : d;
      cr += isNaN(c) ? 0 : c;
    });
    form.querySelector("[data-total-debit]").textContent = format(dr);
    form.querySelector("[data-total-credit]").textContent = format(cr);
    var diff = dr - cr;
    var row = form.querySelector("[data-diff-row]");
    row.hidden = diff === 0;
    form.querySelector("[data-diff-debit]").textContent = diff < 0 ? format(-diff) : "";
    form.querySelector("[data-diff-credit]").textContent = diff > 0 ? format(diff) : "";
    return !bad;
  }

  function ensureEmpty() {
    var lines = body.querySelectorAll("[data-line]");
    if (!lines.length || filled(lines[lines.length - 1])) addLine();
  }

  form.addEventListener("input", function (e) {
    if (e.target.closest("[data-line]")) { ensureEmpty(); totals(); }
  });

  // koers voorstellen bij een andere valuta
  var cur = form.querySelector("[data-currency]");
  if (cur) {
    var rateLabel = form.querySelector("[data-rate]");
    var rate = rateLabel.querySelector("input");
    var date = form.querySelector("[name=transdate]");
    var def = form.getAttribute("data-default-currency");
    var suggest = function () {
      var foreign = cur.value !== def;
      rateLabel.hidden = !foreign;
      if (!foreign || rate.value || !date.value) return;
      fetch("/memoriaal-koers?currency=" + encodeURIComponent(cur.value) + "&transdate=" + encodeURIComponent(date.value),
            { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : {}; })
        .then(function (j) { if (j.rate && !rate.value) rate.value = j.rate; });
    };
    cur.addEventListener("change", suggest);
    date.addEventListener("change", suggest);
  }

  var update = form.querySelector("[data-update]");
  if (update) update.hidden = true;     // alleen nodig zonder JavaScript
  totals();
})();
