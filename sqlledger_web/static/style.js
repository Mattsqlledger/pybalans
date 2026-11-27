// Beheer > Huisstijl (HS6): live voorbeeld zonder op te slaan, kleurkiezer + snelkeuzes, contrastcontrole
// (WCAG, keuze 2d), waarschuwingen (keuzes 3c/4), "Standaard herstellen" en waarschuwing bij verlaten.
(function () {
  "use strict";
  var form = document.getElementById("styleform");
  if (!form) return;
  var frame = document.querySelector("[data-frame]");
  var sheet = document.querySelector("[data-sheet]");
  var pdf = form.querySelector("[data-pdf]");
  var save = form.querySelector("[data-save]");
  var hex = form.elements.color;
  var picker = form.querySelector("[data-color-picker]");
  var minContrast = parseFloat(form.dataset.minContrast || "4.5");
  var dirty = false, timer = null;

  function normalize(v) {
    var m = /^#?([0-9a-f]{6}|[0-9a-f]{3})$/i.exec((v || "").trim());
    if (!m) return null;
    var h = m[1].toLowerCase();
    if (h.length === 3) h = h.replace(/(.)/g, "$1$1");
    return "#" + h;
  }

  function contrast(c) {
    var ch = [1, 3, 5].map(function (i) {
      var x = parseInt(c.substr(i, 2), 16) / 255;
      return x <= 0.04045 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    });
    return 1.05 / (0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2] + 0.05);
  }

  function value(name) {
    var el = form.elements[name];
    if (!el) return "";
    if (el instanceof RadioNodeList) {
      // vinkje met verborgen "0" ervoor: de laatste aangevinkte waarde telt
      var out = "";
      Array.prototype.forEach.call(el, function (e) {
        if (e.type === "hidden" || e.checked) out = e.value;
      });
      return out;
    }
    return el.value;
  }

  function query() {
    var p = new URLSearchParams();
    ["logo_size", "logo_pos", "font", "address_pos", "show_head", "show_footer", "bron"].forEach(function (n) {
      var v = value(n);
      if (v !== "") p.set(n, v);
    });
    p.set("color", normalize(hex.value) || "#111111");
    return p;
  }

  function warn(key, on) {
    var li = form.querySelector('[data-warn="' + key + '"]');
    if (li) li.hidden = !on;
  }

  function check() {
    var c = normalize(hex.value);
    var bad = !c || contrast(c) < minContrast;
    form.querySelector("[data-contrast-error]").hidden = !bad;
    hex.setAttribute("aria-invalid", bad ? "true" : "false");
    if (save) save.disabled = bad;
    if (c && picker) picker.value = c;
    var head = value("show_head") === "1", foot = value("show_footer") === "1";
    warn("logo", !head && form.elements.has_logo.value === "1");
    warn("footer", !foot && form.elements.has_footer_text.value === "1");
    warn("legal", !head && !foot);
  }

  function refresh() {
    check();
    var q = query();
    frame.src = form.dataset.preview + "?" + q.toString();
    q.set("pdf", "1");
    pdf.href = form.dataset.preview + "?" + q.toString();
  }

  function changed() {
    dirty = true;
    clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  }

  // kop te hoog: komt de kop (of iets erin) lager dan de bovenkant van het adresblok?
  function measure() {
    var doc;
    try { doc = frame.contentDocument; } catch (e) { return; }
    if (!doc) return;
    var head = doc.querySelector(".head"), parties = doc.querySelector(".parties");
    if (!head || !parties) { warn("head", false); return; }
    var limit = parties.getBoundingClientRect().top, over = head.scrollHeight > head.clientHeight + 1;
    Array.prototype.forEach.call(head.querySelectorAll("*"), function (el) {
      if (el.getBoundingClientRect().bottom > limit + 1) over = true;
    });
    warn("head", over && value("show_head") === "1");
  }

  function scale() {
    var w = sheet.clientWidth, s = w / frame.offsetWidth;
    frame.style.transform = "scale(" + s + ")";
    sheet.style.height = Math.ceil(frame.offsetHeight * s) + "px";
  }

  form.addEventListener("input", function (e) {
    if (e.target === picker) hex.value = picker.value;
    changed();
  });
  form.addEventListener("change", changed);
  Array.prototype.forEach.call(form.querySelectorAll("[data-swatch]"), function (b) {
    b.style.backgroundColor = b.dataset.swatch;
    b.addEventListener("click", function () { hex.value = b.dataset.swatch; changed(); });
  });
  form.querySelector("[data-reset]").addEventListener("click", function () {
    Array.prototype.forEach.call(form.querySelectorAll("[data-default]"), function (el) {
      if (el.type === "radio" || el.type === "checkbox") el.checked = el.dataset.default === "1";
      else el.value = el.dataset.default;
    });
    changed();
  });
  // Logo (PB11): kiezen of slepen slaat meteen op (fetch, JSON-antwoord); daarna miniatuur en voorbeeld verversen.
  // Zonder JavaScript blijft de knop "Logo opslaan" het formulier gewoon versturen.
  var logoForm = document.querySelector("[data-logoform]");
  var logoInput = logoForm && logoForm.querySelector("input[type=file]");
  var logoPending = false;            // bestand gekozen maar (nog) niet opgeslagen
  function logoStatus(text, bad) {
    var el = logoForm.querySelector("[data-logo-status]");
    el.textContent = text || "";
    el.hidden = !text;
    el.classList.toggle("error", !!bad);
    el.classList.toggle("ok", !bad && !!text);
  }
  function logoActions() {           // rij knoppen weg als er geen knop zichtbaar is (geen lege ruimte)
    var row = logoForm.querySelector("[data-logo-save]").parentNode;
    row.hidden = !Array.prototype.some.call(row.querySelectorAll("button"), function (b) { return !b.hidden; });
  }
  function logoSaved(src) {
    var box = document.querySelector("[data-logo-preview]");
    var img = document.createElement("img");
    img.className = "logo-preview";
    img.alt = box.getAttribute("data-alt") || "Logo";
    img.src = src;
    box.textContent = "";
    box.appendChild(img);
    form.elements.has_logo.value = "1";
    var del = logoForm.querySelector("[data-logo-delete]");
    if (del) del.hidden = false;
    logoActions();
    refresh();
  }
  function sendLogo() {
    if (!logoInput.files || !logoInput.files.length) return;
    var file = logoInput.files[0];
    var max = parseInt(logoForm.dataset.maxBytes, 10);
    logoPending = true;
    if (max > 0 && file.size > max) { logoStatus(logoForm.dataset.tooLarge, true); return; }
    var data = new FormData(logoForm);
    data.set("action", "logo");
    var zone = logoForm.querySelector("[data-logo-drop]");
    zone.classList.add("busy");
    logoStatus(logoForm.dataset.busy, false);
    fetch(logoForm.getAttribute("action"), { method: "POST", body: data, credentials: "same-origin",
                                              headers: { "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 413) return { ok: false, error: logoForm.dataset.tooLarge };
        return r.json().catch(function () { return { ok: false, error: logoForm.dataset.failed }; });
      })
      .then(function (res) {
        zone.classList.remove("busy");
        if (res.ok) {
          logoPending = false;
          logoInput.value = "";
          logoStatus(res.notice, false);
          logoSaved(res.logo);
        } else logoStatus(res.error || logoForm.dataset.failed, true);
      })
      .catch(function () { zone.classList.remove("busy"); logoStatus(logoForm.dataset.failed, true); });
  }
  if (logoForm && logoInput && window.fetch && window.FormData) {
    var drop = logoForm.querySelector("[data-logo-drop]");
    logoForm.querySelector("[data-logo-save]").hidden = true;      // alleen nodig zonder JavaScript
    logoActions();
    logoInput.addEventListener("change", sendLogo);
    ["dragenter", "dragover"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      drop.addEventListener(t, function () { drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      try {
        var dt = new DataTransfer();
        dt.items.add(files[0]);
        logoInput.files = dt.files;
      } catch (err) {
        logoInput.files = files;
      }
      var name = logoForm.querySelector("[data-filename]");
      if (name) name.textContent = files[0].name;
      sendLogo();
    });
    // naast het vak losgelaten: niet de browser het bestand laten openen
    window.addEventListener("dragover", function (e) { e.preventDefault(); });
    window.addEventListener("drop", function (e) { e.preventDefault(); });
  }
  // vangnet: een gekozen maar niet opgeslagen logo (te groot, fout, nog bezig) bij de gewone Opslaan
  form.addEventListener("submit", function (e) {
    if (logoInput && logoPending && logoInput.files && logoInput.files.length &&
        !window.confirm(logoForm.dataset.unsaved)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  });

  form.addEventListener("submit", function () { dirty = false; });
  window.addEventListener("beforeunload", function (e) {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });
  frame.addEventListener("load", function () { measure(); });
  window.addEventListener("resize", scale);
  scale();
  check();
  pdf.href = form.dataset.preview + "?" + (function () { var q = query(); q.set("pdf", "1"); return q.toString(); })();
  // na "Laden" van een andere administratie staat er iets niet-opgeslagens in het formulier
  if (value("bron")) dirty = true;
})();
