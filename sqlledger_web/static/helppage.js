// Pagina Help: een link naar een taak (#k16) klapt die taak open (golf 4, HL10)
(function () {
  function open() {
    var el = location.hash && document.getElementById(location.hash.slice(1));
    if (el && el.tagName === "DETAILS") { el.open = true; el.scrollIntoView(); }
  }
  window.addEventListener("hashchange", open);
  document.addEventListener("DOMContentLoaded", open);
})();
