// Zoeklijst (v1.0.1 PB17) in plaats van de keuzelijst van de browser (<datalist>), voor elk invoerveld met
// list="…": relaties, rekeningen, artikelen. Eén lijst per pagina, onder (of boven) het veld:
// - eerst wat met de invoer begint, dan wat het bevat (hoofdletters maken niet uit, ook op nummer); de getypte tekst
//   vet; elke keuze één keer leesbaar ("4000 – Huur": nummer uit data-n, geen dubbele naam);
// - ongeveer 10 regels met schuifbalk, hoogstens 50, daaronder "nog n – typ verder";
// - pijltjes, Enter, Escape en Tab, muis en aanraken; ARIA-combobox (role=combobox/listbox, aria-activedescendant);
// - groepen (data-group op de opties, bv. "Eerder gebruikt" en "Alle rekeningen") als kop zolang er niets getypt is;
// - een gekozen regel zet precies de waarde van de optie in het veld (de server vindt dan één relatie/rekening;
//   de controle daar blijft als vangnet) en geeft "input" en "change" zoals de browser.
// Lijsten met data-remote worden door de pagina zelf gevuld (fetch bij het typen, al gefilterd door de server); die
// worden niet nog eens gefilterd, alleen gesorteerd. Zonder JavaScript blijft het een gewoon tekstveld met <datalist>.
(function () {
  "use strict";
  var MAX = 50;
  var MORE = document.body.getAttribute("data-msg-more") || "+{n}";
  var seq = 0;
  var box = null, ul = null, more = null;      // gedeelde lijst
  var cur = null;                               // veld waar de lijst nu bij hoort
  var items = [];                               // getoonde keuzes: {value, el}
  var active = -1;
  var pressing = null;                          // veld waarvan de lijst nu wordt aangeraakt
  var held = null;                              // daardoor vastgehouden "change"

  function norm(s) { return (s || "").toLocaleLowerCase(); }

  function build() {
    if (box) return;
    box = document.createElement("div");
    box.className = "combo-box";
    box.hidden = true;
    ul = document.createElement("ul");
    ul.className = "combo-list";
    ul.setAttribute("role", "listbox");
    ul.id = "combo-listbox";
    more = document.createElement("div");
    more.className = "combo-more";
    more.hidden = true;
    box.appendChild(ul);
    box.appendChild(more);
    document.body.appendChild(box);
    // muis/aanraken: het veld houdt de focus
    box.addEventListener("mousedown", function (e) { e.preventDefault(); });
    // aanraken: het veld verliest de focus al bij het tikken (de browser geeft dan "change" met de getypte tekst);
    // die change wordt vastgehouden tot duidelijk is of er een regel gekozen is
    box.addEventListener("pointerdown", function () { pressing = cur; });
    ul.addEventListener("click", function (e) {
      var li = e.target.closest("li[role=option]");
      if (li && cur) choose(parseInt(li.getAttribute("data-i"), 10));
    });
  }

  // ------------------------------------------------------------------ opties lezen en ordenen

  function options(input) {
    var list = document.getElementById(input.getAttribute("data-combo-list"));
    if (!list) return { opts: [], remote: false, more: 0 };
    var out = [], extra = 0;
    Array.prototype.forEach.call(list.querySelectorAll("option"), function (o) {
      if (o.hasAttribute("data-more")) { extra = parseInt(o.getAttribute("data-more"), 10) || 0; return; }
      var value = o.value, text = (o.textContent || "").trim();
      var n = (o.getAttribute("data-n") || "").trim(), name = o.getAttribute("data-name");
      if (name === null) name = n && value.indexOf(n + " ") === 0 ? value.slice(n.length + 1) : "";
      name = name.trim();
      var label = n ? (name ? n + " – " + name : n) : (text || value);
      var hint = !n && text && text !== value ? value : "";
      if (value) out.push({ value: value, label: label, hint: hint, n: norm(n), name: norm(name || text),
                            all: norm(value + " " + text), group: o.getAttribute("data-group") || "" });
    });
    return { opts: out, remote: list.hasAttribute("data-remote"), more: extra };
  }

  function rank(opts, q, remote) {
    if (!q) return opts.slice();
    var begin = [], contain = [], rest = [];
    opts.forEach(function (o) {
      if (o.all.indexOf(q) === 0 || o.n.indexOf(q) === 0 || o.name.indexOf(q) === 0 || norm(o.label).indexOf(q) === 0) begin.push(o);
      else if (o.all.indexOf(q) >= 0 || norm(o.label).indexOf(q) >= 0) contain.push(o);
      else if (remote) rest.push(o);          // de server vond hem (bv. op streepjescode): wel tonen
    });
    return begin.concat(contain, rest);
  }

  // ------------------------------------------------------------------ tonen

  function mark(li, text, q) {
    var i = q ? norm(text).indexOf(q) : -1;
    if (i < 0) { li.appendChild(document.createTextNode(text)); return; }
    li.appendChild(document.createTextNode(text.slice(0, i)));
    var b = document.createElement("b");
    b.textContent = text.slice(i, i + q.length);
    li.appendChild(b);
    li.appendChild(document.createTextNode(text.slice(i + q.length)));
  }

  function render(input) {
    build();
    var q = norm(input.value.trim());
    var data = options(input);
    var found = rank(data.opts, q, data.remote);
    if (q) {                                    // bij typen: over alles, elke rekening één keer (PB26)
      var seen = {};
      found = found.filter(function (o) { if (seen[o.value]) return false; seen[o.value] = true; return true; });
    }
    var hidden = Math.max(0, found.length - MAX) + data.more;
    found = found.slice(0, MAX);
    ul.textContent = "";
    items = [];
    active = -1;
    if (!found.length) { close(); return; }
    var base = input.id || ("combo" + (++seq));
    var group = null;
    found.forEach(function (o, i) {
      if (!q && o.group && o.group !== group) {   // groepskop, bv. "Eerder gebruikt" / "Alle rekeningen"
        group = o.group;
        var h = document.createElement("li");
        h.className = "combo-group";
        h.setAttribute("role", "presentation");
        h.textContent = group;
        ul.appendChild(h);
      }
      var li = document.createElement("li");
      li.setAttribute("role", "option");
      li.id = base + "-opt" + i;
      li.setAttribute("data-i", i);
      li.setAttribute("aria-selected", "false");
      mark(li, o.label, q);
      if (o.hint) {
        var s = document.createElement("span");
        s.className = "combo-hint";
        s.textContent = o.hint;
        li.appendChild(s);
      }
      ul.appendChild(li);
      items.push({ value: o.value, el: li });
    });
    more.hidden = !hidden;
    more.textContent = hidden ? MORE.replace("{n}", hidden) : "";
    open(input);
  }

  function place() {
    if (!cur || box.hidden) return;
    var r = cur.getBoundingClientRect();
    var vv = window.visualViewport;
    var vh = vv ? vv.height : window.innerHeight, vw = document.documentElement.clientWidth;
    var width = Math.min(Math.max(r.width, 300), vw - 16);
    var left = Math.min(Math.max(8, r.left), vw - width - 8);
    var below = vh - r.bottom - 8, above = r.top - 8;
    var up = below < 180 && above > below;
    box.style.left = left + "px";
    box.style.width = width + "px";
    box.style.maxHeight = Math.max(120, Math.min(up ? above : below, 360)) + "px";
    if (up) { box.style.top = ""; box.style.bottom = (vh - r.top + 2) + "px"; }
    else { box.style.bottom = ""; box.style.top = (r.bottom + 2) + "px"; }
  }

  function open(input) {
    if (cur && cur !== input) cur.setAttribute("aria-expanded", "false");
    cur = input;
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", ul.id);
    input.removeAttribute("aria-activedescendant");
    box.hidden = false;
    box.scrollTop = 0;
    place();
  }

  function close() {
    if (box) box.hidden = true;
    if (cur) {
      cur.setAttribute("aria-expanded", "false");
      cur.removeAttribute("aria-activedescendant");
    }
    active = -1;
  }

  function isOpen(input) { return box && !box.hidden && cur === input; }

  function move(to) {
    if (!items.length) return;
    if (active >= 0) items[active].el.setAttribute("aria-selected", "false");
    active = Math.max(0, Math.min(items.length - 1, to));
    var li = items[active].el;
    li.setAttribute("aria-selected", "true");
    cur.setAttribute("aria-activedescendant", li.id);
    var top = li.offsetTop, bottom = top + li.offsetHeight;
    var view = box.clientHeight - (more.hidden ? 0 : more.offsetHeight);
    if (top < box.scrollTop) box.scrollTop = top;
    else if (bottom > box.scrollTop + view) box.scrollTop = bottom - view;
  }

  function choose(i) {
    var input = cur, it = items[i];
    if (!input || !it) return;
    close();
    input._comboTyped = false;
    var wasHeld = held === input;
    held = null;
    if (input.value !== it.value || wasHeld) {
      input.value = it.value;
      input._comboChosen = it.value;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function released() {
    setTimeout(function () {
      var input = pressing;
      pressing = null;
      if (!input) return;
      if (held === input) {                     // niets gekozen (bv. alleen geschoven): change alsnog doorgeven
        held = null;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (document.activeElement !== input) close();
    }, 350);
  }
  document.addEventListener("pointerup", released, true);
  document.addEventListener("pointercancel", released, true);

  // ------------------------------------------------------------------ velden koppelen

  function enhance(input) {
    if (input._combo || !input.getAttribute("list")) return;
    input._combo = true;
    input.setAttribute("data-combo-list", input.getAttribute("list"));
    input.removeAttribute("list");                  // geen keuzelijst van de browser meer
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("autocomplete", "off");
    watch(document.getElementById(input.getAttribute("data-combo-list")));
  }

  // lijsten die de pagina zelf vult: opnieuw tonen zodra de nieuwe opties er zijn
  var watched = [];
  function watch(list) {
    if (!list || watched.indexOf(list) >= 0 || !window.MutationObserver) return;
    watched.push(list);
    new MutationObserver(function () {
      var input = document.activeElement;
      if (input && input._combo && input._comboTyped && input.getAttribute("data-combo-list") === list.id) render(input);
    }).observe(list, { childList: true, subtree: true });
  }

  function scan(root) {
    if (root.matches && root.matches("input[list]")) enhance(root);
    if (root.querySelectorAll) Array.prototype.forEach.call(root.querySelectorAll("input[list]"), enhance);
  }

  function combo(el) { return el && el._combo ? el : null; }

  document.addEventListener("input", function (e) {
    var input = combo(e.target);
    if (!input || !e.isTrusted) return;
    input._comboTyped = true;
    input._comboChosen = null;
    var list = document.getElementById(input.getAttribute("data-combo-list"));
    if (list && list.hasAttribute("data-remote") && !isOpen(input)) return;   // wacht op de server
    render(input);
  });

  document.addEventListener("keydown", function (e) {
    var input = combo(e.target);
    if (!input || e.altKey || e.ctrlKey || e.metaKey) return;
    var shown = isOpen(input);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!shown) { render(input); if (isOpen(input)) move(0); }
      else move(active + 1);
    } else if (e.key === "ArrowUp") {
      if (shown) { e.preventDefault(); move(active - 1); }
    } else if (e.key === "PageDown" && shown) {
      e.preventDefault(); move(active + 10);
    } else if (e.key === "PageUp" && shown) {
      e.preventDefault(); move(active - 10);
    } else if (e.key === "Enter") {
      if (shown && active >= 0) { e.preventDefault(); choose(active); }
      else if (shown) close();
    } else if (e.key === "Escape") {
      if (shown) { e.preventDefault(); close(); input._comboTyped = false; }
    } else if (e.key === "Tab") {
      if (shown && active >= 0) choose(active);
      else if (shown) close();
    }
  });

  // klikken in een leeg veld met een vaste lijst (rekeningen): alles tonen, zoals de browser deed
  document.addEventListener("click", function (e) {
    var input = combo(e.target);
    if (!input || input.value || isOpen(input) || input.disabled || input.readOnly) return;
    var list = document.getElementById(input.getAttribute("data-combo-list"));
    if (list && !list.hasAttribute("data-remote")) render(input);
  });

  document.addEventListener("focusout", function (e) {
    var input = combo(e.target);
    if (input && isOpen(input)) setTimeout(function () {
      if (document.activeElement !== input && pressing !== input) close();
    }, 120);
  });

  // dubbele "change" voorkomen: na een gekozen regel geeft de browser bij het verlaten nog een eigen change
  document.addEventListener("change", function (e) {
    var input = combo(e.target);
    if (input && e.isTrusted && pressing === input) {   // tikken in de lijst: eerst kijken wat er gekozen wordt
      held = input;
      e.stopImmediatePropagation();
      return;
    }
    if (input && e.isTrusted && input._comboChosen !== null && input._comboChosen !== undefined &&
        input._comboChosen === input.value) {
      e.stopImmediatePropagation();
    }
    if (input && e.isTrusted) input._comboChosen = null;
  }, true);

  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);
  if (window.visualViewport) window.visualViewport.addEventListener("resize", place);

  scan(document);
  if (window.MutationObserver) {
    new MutationObserver(function (list) {
      list.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) { if (n.nodeType === 1) scan(n); });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
})();
