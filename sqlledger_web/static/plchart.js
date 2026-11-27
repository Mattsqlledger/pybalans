// Grafiek bij Winst en verlies (v1.1, GR3): kleine aanvulling op de SVG die de server tekent.
// Zonder JavaScript werkt alles ook (tooltip via <title>, keuzelijst met knop Toon).
(function () {
  var tip = null;
  function hide() { if (tip) tip.hidden = true; }
  function show(el, x, y) {
    var box = el.closest(".pl-scroll");
    if (!box) return;
    if (!tip || !box.contains(tip)) {
      tip = document.createElement("div");
      tip.className = "pl-tip";
      tip.setAttribute("role", "status");
      box.appendChild(tip);
    }
    var t = el.querySelector("title");
    if (t) t.remove();                      // geen dubbele tooltip van de browser; de tekst staat in data-tip
    tip.textContent = el.getAttribute("data-tip");
    tip.hidden = false;
    var r = box.getBoundingClientRect();
    var left = x - r.left + box.scrollLeft + 12;
    var max = box.scrollLeft + box.clientWidth - tip.offsetWidth - 4;
    tip.style.left = Math.max(box.scrollLeft + 4, Math.min(left, max)) + "px";
    tip.style.top = Math.max(0, y - r.top - tip.offsetHeight - 10) + "px";
  }
  document.addEventListener("pointerover", function (e) {
    var el = e.target.closest && e.target.closest(".plchart [data-tip]");
    if (el) show(el, e.clientX, e.clientY);
  });
  document.addEventListener("pointerout", function (e) {
    if (e.target.closest && e.target.closest(".plchart [data-tip]")) hide();
  });
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest(".plchart [data-tip]");
    if (el) show(el, e.clientX, e.clientY); else hide();
  });
  document.addEventListener("change", function (e) {
    if (e.target.matches("select[data-autosubmit]")) {
      var f = e.target.form;
      if (f.requestSubmit) f.requestSubmit(); else f.submit();
    }
  });
})();
