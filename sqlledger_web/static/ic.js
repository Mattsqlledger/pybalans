// Samengesteld artikel: verkoopprijs alleen invulbaar met "Verkoopprijs zelf bepalen" (werkt ook zonder JS via Bijwerken).
document.addEventListener("change", (e) => {
  if (!e.target.matches("[data-own-toggle]")) return;
  const price = e.target.form.querySelector("[data-own-price]");
  if (price) {
    price.readOnly = !e.target.checked;
    if (e.target.checked) price.focus();
  }
});
