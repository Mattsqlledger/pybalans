"""Documenten in het Duits en Frans (fase 4 DT2/DT6): bedrag in woorden, taalkeuze, volledige teksten."""

import re
from pathlib import Path

import pytest

from sqlledger.num2text import document_language, language_for, num2text


@pytest.mark.parametrize("n, de, fr", [
    (0, "null", "zéro"), (1, "eins", "un"), (16, "sechzehn", "seize"), (21, "einundzwanzig", "vingt et un"),
    (71, "einundsiebzig", "soixante et onze"), (80, "achtzig", "quatre-vingts"), (91, "einundneunzig", "quatre-vingt-onze"),
    (101, "einhunderteins", "cent un"), (200, "zweihundert", "deux cents"), (280, "zweihundertachtzig", "deux cent quatre-vingts"),
    (1023, "eintausenddreiundzwanzig", "mille vingt-trois"),             # Perl: eintausendzwanzigdrei
    (2000, "zweitausend", "deux mille"),                                 # Perl: Mille
    (21345, "einundzwanzigtausenddreihundertfünfundvierzig", "vingt et un mille trois cent quarante-cinq"),
    (80000, "achtzigtausend", "quatre-vingt mille"),
    (1000000, "einemillion", "un million"), (2500000, "zweimillionenfünfhunderttausend", "deux millions cinq cent mille"),
])
def test_num2text_german_french(n, de, fr):
    assert num2text(n, "de") == de
    assert num2text(n, "fr") == fr


def test_document_language_and_perl_rule():
    assert [document_language(c, "nl") for c in ("", "Factor", "en", "US_EN", "de", "ch", "fr", "befr", "nl")] == \
        ["nl", "nl", "en", "en", "de", "de", "fr", "fr", "nl"]
    assert document_language("", "de") == "de" and document_language("Euros", "fr") == "fr"
    # zonder documenttaal van de administratie: zoals Perl (CP->new): eigen Num2text of Engels
    assert language_for("de") == "de" and language_for("Factor", "nl") == "en" and language_for("", "fr") == "fr"


def test_every_template_text_has_all_languages():
    from sqlledger_web.documents import BUILTIN, LABELS
    keys = set()
    for p in (BUILTIN / "standard").glob("*.html"):
        s = p.read_text()
        keys |= set(re.findall(r"t\('([^']+)'\)", s))
        keys |= set(re.findall(r"(?:doc_title|date_label|number_label|req_label|closing_text) = '([^']+)'", s))
    for lang, labels in LABELS.items():
        assert not keys - set(labels), (lang, sorted(keys - set(labels)))


def test_german_and_french_documents_render(tmp_path):
    from sqlledger_web.documents import DocumentRenderer
    v = {"invnumber": "7", "invdate": "01-03-2026", "duedate": "31-03-2026", "name": "Kunde GmbH", "subtotal": "10,00",
         "invtotal": "12,10", "total": "12,10", "currency": "EUR", "company": "Testbedrijf", "address": "Straat 1",
         "language_code": "", "iban": "NL00BANK0123456789", "description": ["A"], "qty": ["1"], "runningnumber": [1]}
    de = DocumentRenderer(tmp_path, 1, "de").render_html("invoice", v)
    assert "Rechnung" in de and "Rechnungsnummer" in de and "auf das Konto" in de and "Factuur" not in de
    fr = DocumentRenderer(tmp_path, 1, "nl").render_html("invoice", dict(v, language_code="fr"))
    assert "Facture" in fr and "Date de facture" in fr and "au nom de" in fr
    en = DocumentRenderer(tmp_path, 1, "nl").render_html("invoice", dict(v, language_code="Factor"))
    assert "Factuurnummer" in en                          # onbekende variant: taal van de administratie
