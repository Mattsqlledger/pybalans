// Databasenaam voorstellen uit de naam van de administratie (eerste start keuze 7; zelfde regel als
// routes_system.suggest_dbname). Zodra iemand de databasenaam zelf wijzigt, blijft die staan.
document.addEventListener("DOMContentLoaded", () => {
  const src = document.querySelector("[data-dbname-source]");
  const dst = document.querySelector("[data-dbname-target]");
  if (!src || !dst) return;
  let own = dst.value !== "";
  dst.addEventListener("input", () => { own = dst.value !== ""; });
  src.addEventListener("input", () => {
    if (own) return;
    let t = src.value.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase()
      .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    if (t && !/^[a-z]/.test(t)) t = "adm_" + t;
    dst.value = t.slice(0, 63).replace(/_+$/, "");
  });
});
