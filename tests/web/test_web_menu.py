"""Menu: welke rubriek openstaat en welk item gemarkeerd is (melding gebruiker 2026-10-11: Grootboek > Memoriaal
klapte Kas en bank open, omdat Koersverschilboeking = /memoriaal/nieuw?fx=1)."""

import re

import pytest

from webhelpers import login, make_user


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def state(html):
    nav = html.split('<nav class="menu"', 1)[1].split("</nav>", 1)[0]
    opened = re.findall(r"<details open>\s*<summary>([^<]+)</summary>", nav)
    current = re.findall(r'aria-current="page">([^<]+)<', nav)
    return opened, current


@pytest.mark.parametrize("url, group, item", [
    ("/memoriaal/nieuw", "Grootboek", "Memoriaal"),
    ("/memoriaal/nieuw?fx=1", "Kas en bank", "Koersverschilboeking"),
    ("/verkoop/nieuw", "Verkoop", "Boeking toevoegen"),
    ("/verkoop/nieuw?creditnota=1", "Verkoop", "Creditnota"),
    ("/inkoop/nieuw?creditnota=1", "Inkoop", "Creditnota"),
    ("/artikelen/nieuw?soort=dienst", "Artikelen", "Dienst toevoegen"),
])
def test_open_group_and_current_item(booker, url, group, item):
    assert state(booker.get(url).text) == ([group], [item])


@pytest.mark.parametrize("url, group, item", [
    ("/rapporten/btw-verkoop", "Btw", "Btw verkoop"),
    ("/rapporten/onbelast-inkoop", "Btw", "Onbelaste inkoop"),
    ("/documenten/batch?soort=invoice", "Verkoop", "Documenten in batch"),
    ("/documenten/batch?soort=vendor_invoice", "Inkoop", "Documenten in batch"),
    ("/documenten/batch?soort=vendor_invoice&van=&tot=", "Verkoop", "Documenten in batch"),
])
def test_open_group_vat_and_batch(booker, url, group, item):
    assert state(booker.get(url).text) == ([group], [item])


def test_batch_from_menu_keeps_default_filters(booker):
    """Uit het menu (?soort=) gelden dezelfde standaardfilters als zonder soort: nog niet geprint/gemaild."""
    html = booker.get("/documenten/batch?soort=vendor_invoice").text
    assert '<option value="vendor_invoice" selected>' in html
    assert re.search(r'<select name="geprint">.*?<option value="nee" selected>', html, re.S)


# --- opbouw (MN1–MN5), los van het scherm --------------------------------------------------------

from sqlledger_web.appdb import MODULES  # noqa: E402
from sqlledger_web.menu import build_menu  # noqa: E402

ALL = list(MODULES)


NL = frozenset({"vat_return", "vatform", "sepa", "nl", "gifi"})      # Nederlandse administratie met GIFI-codes


def layout(modules, role, sysadmin=False, flags=NL):
    return {g.label: [("# " if i.heading else "") + i.label for i in g.items]
            for g in build_menu(modules, role, sysadmin, flags)}


def test_order_of_groups():
    assert list(layout(ALL, "admin", True)) == [
        "Verkoop", "Inkoop", "Kas en bank", "Orders", "Offertes", "Artikelen", "Grootboek", "Btw", "Rapporten",
        "Terugkerend", "Afdelingen en projecten", "Beheer", "Systeem"]


def test_vat_group_has_reports_and_rates():
    m = layout(ALL, "admin")
    assert m["Btw"] == ["Btw-aangifte", "# Overzichten", "Btw verkoop", "Onbelaste omzet", "Btw inkoop",
                        "Onbelaste inkoop", "# Beheer", "Btw-tarieven", "Btw-rubrieken"]
    for g in ("Verkoop", "Inkoop", "Beheer"):
        assert not [i for i in m[g] if "Btw" in i or "Onbelast" in i], g
    assert "Documenten" not in m


def test_headings_only_with_items_below():
    m = layout(ALL, "readonly")
    assert m["Verkoop"] == ["# Overzichten", "Overzicht", "Openstaand", "Ouderdomsanalyse", "Documenten in batch",
                            "# Debiteurenbeheer", "Aanmaningen", "Rekeningoverzichten", "# Klanten", "Klanten zoeken"]
    assert m["Btw"][-1] == "Onbelaste inkoop"  # geen kopje Beheer zonder tarieven
    assert "Beheer" not in m
    assert m["Kas en bank"] == ["# Overzichten", "Overzicht ontvangsten", "Overzicht betalingen",
                                "Afgeletterde posten", "Betalingsregister"]


def test_vat_follows_modules():
    m = layout(["ar", "gl"], "bookkeeper")
    assert m["Btw"] == ["Btw-aangifte", "# Overzichten", "Btw verkoop", "Onbelaste omzet"]
    assert "Btw" not in layout(["gl", "cash"], "bookkeeper")
    # de beheerder houdt de tarieven, ook zonder verkoop en inkoop
    assert layout(["gl"], "admin")["Btw"] == ["# Beheer", "Btw-tarieven"]


def test_vat_return_only_when_configured():
    """BA2/BA8: Btw-aangifte alleen als er rubrieken zijn en het tijdvak niet "geen" is (kolom company.vat_return);
    de beheerder ziet Btw-rubrieken altijd."""
    m = layout(ALL, "admin", flags=frozenset({"vatform"}))
    assert m["Btw"][0] == "# Overzichten" and m["Btw"][-1] == "Btw-rubrieken"
    assert layout(ALL, "readonly")["Btw"][0] == "Btw-aangifte"


def test_batch_without_sales_and_purchases():
    m = layout(["oe", "gl"], "readonly")
    assert m["Orders"][-1] == "Documenten in batch"
    assert "Documenten in batch" not in layout(["gl"], "admin").get("Beheer", [])


def test_country_dependent_items():
    """Menu voor de tester (2026-11): RGS alleen NL, btw-rubrieken alleen met een aangifteformulier, betaalbestand alleen
    SEPA, GIFI alleen met GIFI-codes; een Zwitserse administratie ziet ze niet (directe adressen werken wel)."""
    nl = layout(ALL, "admin", True)
    ch = layout(ALL, "admin", True, flags=frozenset())
    for group, item in (("Beheer", "RGS-koppeling"), ("Systeem", "RGS-lijst"), ("Btw", "Btw-rubrieken"),
                        ("Kas en bank", "Betaalbestand maken"), ("Beheer", "GIFI-indeling")):
        assert item in nl[group] and item not in ch[group], item


def test_menu_flags_from_country():
    from sqlledger_web import production
    production._cache.clear()
    for country, gifi, want in (("NL", True, {"nl", "vatform", "sepa", "gifi"}), ("CH", False, set()),
                                ("DE", False, {"sepa"}), ("GB", False, {"vatform"}), (None, False, {"nl", "vatform", "sepa"})):
        production._cache[("x",)] = (dict(production=False, country=country, gifi=gifi), 1e18)
        orig = production._key
        production._key = lambda company: ("x",)
        try:
            assert production.menu_flags(None, {"id": 1}) == want, country
        finally:
            production._key = orig
    production._cache.clear()
