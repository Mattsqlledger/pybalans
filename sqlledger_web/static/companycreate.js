// Nieuwe administratie (v1.0.1 PB24/PB25): de uitleg onder "Land en schema" en de eerste keuze van
// Standaardvaluta ("EUR (volgens het land)") wisselen mee met het gekozen schema. Zonder JavaScript: de tekst van
// het bij laden gekozen schema.
document.addEventListener("DOMContentLoaded", () => {
  const chart = document.querySelector("[data-chart]");
  const help = document.querySelector("[data-chart-help]");
  const cur = document.querySelector("[data-currency-default]");
  if (!chart) return;
  const sync = () => {
    const opt = chart.options[chart.selectedIndex];
    if (!opt) return;
    if (help) help.textContent = opt.dataset.help || "";
    if (cur) cur.textContent = cur.dataset.label.replace("{code}", opt.dataset.currency || "");
  };
  chart.addEventListener("change", sync);
  sync();
});
