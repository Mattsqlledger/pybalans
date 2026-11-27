// Vertaalde bestandsknop (_file.html): naam van het gekozen bestand tonen.
document.addEventListener("change", function (e) {
  var input = e.target;
  if (!input.matches || !input.matches(".filepick input[type=file]")) return;
  var label = input.parentNode.querySelector("[data-filename]");
  if (label && input.files && input.files.length) label.textContent = input.files[0].name;
});
