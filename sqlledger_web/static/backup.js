// Systeem > Back-up maken (v1.0.1 PB6): na klikken de knop uit en "Bezig met maken…"; de download loopt via fetch,
// daarna is de knop weer actief. Zonder JavaScript: gewone formulierpost (de browser downloadt het bestand).
(function () {
  var status = document.querySelector("[data-backup-status]");
  if (!status || !window.fetch || !window.URL || !URL.createObjectURL) return;
  document.querySelectorAll("form[data-download]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var buttons = document.querySelectorAll("form[data-download] button");
      var button = form.querySelector("button");
      buttons.forEach(function (b) { b.disabled = true; });
      status.classList.remove("error");
      status.removeAttribute("role");
      status.hidden = false;
      status.textContent = status.dataset.busy;
      button.setAttribute("aria-busy", "true");
      var done = function (msg, failed) {
        buttons.forEach(function (b) { b.disabled = false; });
        button.removeAttribute("aria-busy");
        status.textContent = msg;
        if (failed) { status.classList.add("error"); status.setAttribute("role", "alert"); }
      };
      fetch(form.getAttribute("action"), { method: "POST", body: new FormData(form), credentials: "same-origin" })
        .then(function (r) {
          var type = r.headers.get("content-type") || "";
          if (!r.ok || type.indexOf("text/html") === 0) {
            return r.text().then(function (html) {
              var err = new DOMParser().parseFromString(html, "text/html").querySelector(".error");
              done(err && err.textContent.trim() ? err.textContent.trim() : status.dataset.failed, true);
            });
          }
          var cd = r.headers.get("content-disposition") || "";
          var m = /filename="([^"]+)"/.exec(cd);
          var name = m ? m[1] : "backup";
          return r.blob().then(function (blob) {
            var url = URL.createObjectURL(blob);
            var a = document.createElement("a");
            a.href = url;
            a.download = name;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
            done(status.dataset.done.replace("{name}", name), false);
          });
        })
        .catch(function () { done(status.dataset.failed, true); });
    });
  });
})();
