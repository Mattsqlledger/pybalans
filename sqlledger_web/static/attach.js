// Bijlagen (fase 4 stap 4e): een bestand slepen of kiezen in een sleepvak verstuurt het formulier
// via de knop uit data-submit (bij een nieuwe boeking "Bijwerken", anders "Bijlage toevoegen" of
// "Uploaden"). Een bestand dat naast het vak valt gaat naar het (enige) sleepvak in plaats van dat de
// browser het opent. Een bestand groter dan data-max-bytes (maximale uploadgrootte, stap 4f) wordt
// niet verstuurd: melding in het vak (de server controleert ook). Formulieren met data-confirm vragen
// eerst om bevestiging.
(function () {
  var zones = document.querySelectorAll("[data-dropzone]");

  function tooLarge(zone, file) {
    var max = parseInt(zone.getAttribute("data-max-bytes"), 10);
    var error = zone.querySelector("[data-size-error]");
    var bad = max > 0 && file.size > max;
    if (error) {
      error.textContent = bad ? zone.getAttribute("data-too-large") : "";
      error.hidden = !bad;
    }
    return bad;
  }

  function send(zone) {
    var input = zone.querySelector("input[type=file]");
    if (!input || !input.files || !input.files.length) return;
    if (tooLarge(zone, input.files[0])) {
      input.value = "";
      return;
    }
    var form = input.form;
    var button = form.querySelector(zone.getAttribute("data-submit"));
    zone.classList.add("busy");
    if (button) {
      button.hidden = false;
      if (form.requestSubmit) form.requestSubmit(button); else button.click();
    } else form.submit();
  }

  function take(zone, files) {
    var input = zone.querySelector("input[type=file]");
    if (!input || !files || !files.length) return;
    try {
      var dt = new DataTransfer();
      dt.items.add(files[0]);
      input.files = dt.files;
    } catch (e) {
      input.files = files;
    }
    send(zone);
  }

  Array.prototype.forEach.call(zones, function (zone) {
    var input = zone.querySelector("input[type=file]");
    if (input) input.addEventListener("change", function () { send(zone); });
    ["dragenter", "dragover"].forEach(function (t) {
      zone.addEventListener(t, function (e) { e.preventDefault(); zone.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      zone.addEventListener(t, function () { zone.classList.remove("over"); });
    });
    zone.addEventListener("drop", function (e) {
      e.preventDefault();
      e.stopPropagation();
      take(zone, e.dataTransfer && e.dataTransfer.files);
    });
  });

  if (zones.length) {
    // naast het vak: niet de browser het bestand laten openen
    window.addEventListener("dragover", function (e) { e.preventDefault(); });
    window.addEventListener("drop", function (e) {
      e.preventDefault();
      if (zones.length === 1) take(zones[0], e.dataTransfer && e.dataTransfer.files);
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("form[data-confirm]"), function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });
})();
