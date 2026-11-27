// Back-up inlezen (v1.0.1 PB1–PB5). Zonder JavaScript werkt alles ook: gewone formulierpost en een pagina die
// zichzelf ververst.
(function () {
  // 1. Uploaden met voortgang in %: XMLHttpRequest volgt de doorverwijzing naar het controlescherm.
  var form = document.querySelector("form[data-upload-progress]");
  if (form && window.FormData && window.XMLHttpRequest) {
    var block = form.querySelector("[data-progress]");
    var bar = block.querySelector("progress");
    var text = block.querySelector("[data-progress-text]");
    var button = form.querySelector("button");
    var show = function (pct, msg) {
      bar.value = pct;
      text.textContent = msg;
    };
    form.addEventListener("submit", function (e) {
      if (!form.checkValidity()) return;
      e.preventDefault();
      var xhr = new XMLHttpRequest();
      block.hidden = false;
      button.disabled = true;
      show(0, form.dataset.textUpload.replace("{percent}", "0"));
      xhr.upload.onprogress = function (ev) {
        if (!ev.lengthComputable) return;
        var pct = Math.floor(ev.loaded * 100 / ev.total);
        show(pct, form.dataset.textUpload.replace("{percent}", pct));
      };
      xhr.upload.onload = function () { show(100, form.dataset.textCheck); };
      xhr.onload = function () {
        if (xhr.status === 200 && xhr.responseURL && xhr.responseURL.indexOf("/controle/") > 0) {
          window.location.href = xhr.responseURL;
          return;
        }
        button.disabled = false;
        var msg = form.dataset.textFailed;
        try {
          var err = new DOMParser().parseFromString(xhr.responseText, "text/html").querySelector(".error");
          if (err && err.textContent.trim()) msg = err.textContent.trim();
          else if (xhr.status === 413 && xhr.responseText) {
            var d = JSON.parse(xhr.responseText);
            if (d && d.detail) msg = d.detail;
          }
        } catch (ex) { /* standaardmelding */ }
        show(0, msg);
        text.setAttribute("role", "alert");
      };
      xhr.onerror = function () {
        button.disabled = false;
        show(0, form.dataset.textFailed);
      };
      xhr.open("POST", form.getAttribute("action"));
      xhr.send(new FormData(form));
    });
  }

  // 2. Controlescherm: "Jij bent in deze administratie" alleen als "mij toevoegen" aan staat.
  var addme = document.querySelector("[data-addme]");
  var part = document.querySelector("[data-addme-part]");
  if (addme && part) {
    var sync = function () { part.hidden = !addme.checked; };
    addme.addEventListener("change", sync);
    sync();
  }

  // 3. Voortgang van het inlezen: elke seconde de stand opvragen; klaar = pagina opnieuw (toont het resultaat).
  var job = document.querySelector("[data-job-status]");
  if (job && window.fetch) {
    var jbar = job.querySelector("progress");
    var jtext = job.querySelector("[data-progress-text]");
    var phases = job.querySelectorAll(".phases li");
    var order = ["check", "restore", "upgrade", "finish"];
    var poll = function () {
      fetch(job.dataset.jobStatus, { credentials: "same-origin", cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) { setTimeout(poll, 3000); return; }
          if (!d.running) { window.location.reload(); return; }
          jbar.value = d.percent;
          jbar.textContent = d.percent + "%";
          if (jtext.textContent !== d.text) jtext.textContent = d.text;
          var now = order.indexOf(d.phase);
          for (var i = 0; i < phases.length; i++) {
            phases[i].className = i < now ? "done" : (i === now ? "now" : "");
            if (i === now) phases[i].setAttribute("aria-current", "step");
            else phases[i].removeAttribute("aria-current");
          }
          setTimeout(poll, 1000);
        })
        .catch(function () { setTimeout(poll, 3000); });
    };
    setTimeout(poll, 1000);
  }
})();
