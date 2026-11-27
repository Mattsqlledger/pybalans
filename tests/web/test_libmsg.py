"""Bibliotheekmeldingen: elke f-string-melding uit sqlledger/ heeft een sjabloon in
sqlledger_web.libmsg, en de meldingen komen in de taal van de gebruiker."""

import ast
import re
from pathlib import Path

import pytest

import sqlledger
from sqlledger_web import i18n
from sqlledger_web.libmsg import TEMPLATES, lib_message

LIB = Path(sqlledger.__file__).parent
SQL = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)


def _skeleton(node: ast.JoinedStr) -> str:
    return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)


def library_messages() -> dict:
    """f-string-meldingen in sqlledger/: raise X(f"…"), warnings.append(f"…"), reason=f"…" en
    raise X("…") (vaste tekst) -> vindplaats."""
    found = {}
    for path in sorted(LIB.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            cands = []
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                cands.append(node.exc.args[0])
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr == "append" and "warnings" in ast.unparse(f.value):
                    cands.extend(node.args[:1])
                cands.extend(k.value for k in node.keywords if k.arg == "reason")
            elif isinstance(node, ast.Assign) and any("warnings" in ast.unparse(t) for t in node.targets) \
                    and isinstance(node.value, ast.List):
                cands.extend(node.value.elts)
            for c in cands:
                if isinstance(c, ast.JoinedStr) and not SQL.match(_skeleton(c)):
                    found.setdefault(_skeleton(c), f"{path.name}:{node.lineno}")
    return found


def test_every_library_fstring_has_template():
    templates = {re.sub(r"\{\w+\}", "{}", t) for t in TEMPLATES}
    missing = {s: w for s, w in library_messages().items() if s not in templates}
    assert len(library_messages()) > 100
    assert not missing, missing


def test_placeholders_subset():
    for source, (nl, en) in TEMPLATES.items():
        names = set(re.findall(r"\{(\w+)\}", source))
        for t in (nl, en):
            assert set(re.findall(r"\{(\w+)\}", t)) <= names, (source, t)


@pytest.fixture
def en():
    token = i18n.current_language.set("en")
    yield
    i18n.current_language.reset(token)


@pytest.mark.parametrize("text, nl, english", [
    ("Valuta USD is in gebruik: ar (3).", "Valuta USD is in gebruik: ar (3).", "Currency USD is in use: ar (3)."),
    ("customer 12 not found", "Relatie 12 niet gevonden.", "Contact 12 not found."),
    ("ar 12 not found", "Boeking 12 niet gevonden.", "Transaction 12 not found."),
    ("GL transaction 7 not found", "Boeking 7 niet gevonden.", "Transaction 7 not found."),
    ("Unknown vendor 3", "Onbekende leverancier (3).", "Unknown vendor (3)."),
    ("Kies bij verkoop maar één van omzet/kosten, betaling, korting of btw.",
     "Kies bij verkoop maar één van omzet/kosten, betaling, korting of btw.",
     "For AR choose only one of income/expense, payment, discount or VAT."),
    ("Afschrift 12 was al helemaal ingelezen; er is niets toegevoegd.",
     "Afschrift 12 was al helemaal ingelezen; er is niets toegevoegd.",
     "Statement 12 was already imported completely; nothing was added."),
    ("leeg bestand", "Leeg bestand.", "Empty file."),
    ("leeg bestand (x.xml)", "Leeg bestand (x.xml).", "Empty file (x.xml)."),
    ("Vaste regel: rekeningnummer NL01 en trefwoord “huur” → 4100 (btw 1520)",
     "Vaste regel: rekeningnummer NL01 en trefwoord “huur” → 4100 (btw 1520)",
     "Bank rule: account number NL01 and keyword “huur” → 4100 (VAT 1520)"),
    ("Factuurnummer in de omschrijving: 2026-0012, 2026-0014",
     "Factuurnummer in de omschrijving: 2026-0012, 2026-0014",
     "Invoice number in the description: 2026-0012, 2026-0014"),
    ("Betalingskenmerk van de factuur: 2026-0012", "Betalingskenmerk van de factuur: 2026-0012",
     "Payment reference of the invoice: 2026-0012"),
    ("Het bestand is groter dan 12 MB.", "Het bestand is groter dan 12 MB.", "The file is larger than 12 MB."),
    ("iets heel anders", "iets heel anders", "iets heel anders"),
])
def test_lib_message(text, nl, english):
    assert lib_message(text) == nl
    token = i18n.current_language.set("en")
    try:
        assert lib_message(ValueError(text)) == english
    finally:
        i18n.current_language.reset(token)
