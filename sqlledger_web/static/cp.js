// Ontvangsten/betalingen: bedragen, korting, kenmerken en totalen rekent de server
// (POST met action=calc); het script vervangt alleen het blok met de posten en
// houdt het veld waarin je bezig bent zoals het is. Relatie, datum, rekening,
// valuta en filters verversen het hele formulier (zoals cp.pl). Zonder
// JavaScript doet de knop "Bijwerken" hetzelfde.
(function () {
  var form = document.querySelector("form.cpform");
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
        var active = document.activeElement;
        var keep = active && form.contains(active) && active.name ? { name: active.name, value: active.value } : null;
        ["#cp-calc", "#cp-error"].forEach(function (sel) {
          var mine = document.querySelector(sel);
          var fresh = doc.querySelector(sel);
          if (mine && fresh) mine.replaceWith(fresh);
        });
        if (keep) {
          var el = form.querySelector('[name="' + keep.name + '"]');
          if (el) {
            if (el.type !== "checkbox") el.value = keep.value;
            el.focus();
          }
        }
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
        var dp = form.querySelector("[name=datepaid]");
        var all = form.querySelector("[name=all_vc]");
        var url = form.getAttribute("data-vc-url") + "?vc_text=" + encodeURIComponent(vc.value) +
                  "&datepaid=" + encodeURIComponent(dp ? dp.value : "") +
                  "&all_vc=" + (all && all.checked ? "1" : "");
        fetch(url, { credentials: "same-origin" }).then(function (r) { return r.text(); })
          .then(function (html) { list.innerHTML = html; });
      }, 200);
    });
  }

  var update = form.querySelector("[data-update]");
  if (update) update.hidden = true;     // alleen nodig zonder JavaScript
})();
