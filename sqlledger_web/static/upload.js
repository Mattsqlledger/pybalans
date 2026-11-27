// Inkoopfactuur uploaden (fase 4 stap 4e): leverancier en datum verversen het formulier (voorstellen),
// totaal, btw-rekening en btw-bedrag laten netto en btw door de server uitrekenen (action=calc, alleen
// het blok #upload-calc wordt vervangen). Zonder JavaScript doen de knoppen Uploaden/Bijwerken hetzelfde.
(function () {
  var form = document.querySelector("form.uploadform");
  if (!form) return;

  var busy = null;
  function calc() {
    var data = new FormData(form);
    data.set("action", "calc");
    data.delete("bestand");
    if (busy) busy.abort();
    busy = new AbortController();
    fetch(form.getAttribute("action"), { method: "POST", body: data, credentials: "same-origin", signal: busy.signal })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("#upload-calc");
        var mine = form.querySelector("#upload-calc");
        if (fresh && mine) mine.replaceWith(fresh);
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

  // leverancier zoeken: keuzelijst bij het typen (zelfde lijst als het gewone inkoopscherm)
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

  ["[data-update]", "[data-upload]"].forEach(function (sel) {   // alleen nodig zonder JavaScript
    var b = form.querySelector(sel);
    if (b) b.hidden = true;
  });
})();
