// Datumvelden (v1.0.1 PB19), voor alle <input type="date"> van de app, ook regels die later worden toegevoegd:
// - een onvolledige of ongeldige datum geeft een eigen melding in de taal van de gebruiker (setCustomValidity; de
//   melding van de browser volgt de taal van de browser en zegt niet wat er mis is);
// - een ×-knopje maakt een niet-verplicht veld leeg (in filters betekent leeg "geen grens").
// De teksten staan op <body> (data-msg-date, data-msg-date-required, data-msg-date-clear). Zonder JavaScript gewoon het veld van de browser.
(function () {
  "use strict";
  var body = document.body;
  var MSG = body.getAttribute("data-msg-date") || "";
  var MSG_REQ = body.getAttribute("data-msg-date-required") || MSG;
  var CLEAR = body.getAttribute("data-msg-date-clear") || "×";
  var SEL = 'input[type="date"]';

  function isDate(el) { return el && el.matches && el.matches(SEL); }

  function check(input) {
    var bad = input.validity && input.validity.badInput;
    input.setCustomValidity(bad ? (input.required ? MSG_REQ : MSG) : "");
    return !bad;
  }

  function showClear(input) {
    var b = input._dateClear;
    if (b) b.hidden = input.disabled || input.readOnly || (input.value === "" && !(input.validity && input.validity.badInput));
  }

  function enhance(input) {
    if (input._dateDone) return;
    input._dateDone = true;
    if (input.required || input.type !== "date") return;
    var wrap = document.createElement("span");
    wrap.className = "datefield";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var b = document.createElement("button");
    b.type = "button";
    b.className = "dateclear";
    b.textContent = "×";
    b.setAttribute("aria-label", CLEAR);
    b.title = CLEAR;
    wrap.appendChild(b);
    input._dateClear = b;
    b.addEventListener("click", function () {
      input.value = "";
      input.setCustomValidity("");
      showClear(input);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      input.focus();
    });
    showClear(input);
  }

  function scan(root) {
    if (isDate(root)) enhance(root);
    if (root.querySelectorAll) Array.prototype.forEach.call(root.querySelectorAll(SEL), enhance);
  }

  ["input", "change", "keyup", "blur"].forEach(function (t) {
    document.addEventListener(t, function (e) {
      if (!isDate(e.target)) return;
      check(e.target);
      showClear(e.target);
    }, true);
  });
  // bij versturen: de eigen melding staat er al als de browser het veld afkeurt
  document.addEventListener("invalid", function (e) { if (isDate(e.target)) check(e.target); }, true);
  document.addEventListener("submit", function (e) {
    Array.prototype.forEach.call(e.target.querySelectorAll ? e.target.querySelectorAll(SEL) : [], check);
  }, true);
  // knoppen versturen het formulier pas na deze klik: eerst de velden nalopen
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button, input[type=submit]");
    if (b && b.form) Array.prototype.forEach.call(b.form.querySelectorAll(SEL), check);
  }, true);

  scan(document);
  if (window.MutationObserver) {
    new MutationObserver(function (list) {
      list.forEach(function (m) { Array.prototype.forEach.call(m.addedNodes, function (n) { if (n.nodeType === 1) scan(n); }); });
    }).observe(document.body, { childList: true, subtree: true });
  }
})();
