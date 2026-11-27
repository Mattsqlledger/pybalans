"""RGS-lijst inlezen en voorstellen (sqlledger.rgs, REKENINGSCHEMA_ANALYSE.md RS13 keuze A)."""

import pytest

from rgsfile import make_xlsx
from sqlledger import rgs
from sqlledger.rgs import RgsFileError


@pytest.fixture(scope="module")
def lst():
    return rgs.parse_xlsx(make_xlsx(), "RGS 3.8-def.xlsx")


def test_parse(lst):
    assert lst.version == "3.8" and lst.source == "RGS 3.8-def.xlsx" and len(lst.codes) == 111
    c = lst.codes["BVorDebHad"]
    assert (c.short, c.dc, c.level, c.mkb) == ("Handelsdebiteuren", "D", 4, True)
    assert not lst.codes["BLimKasKas"].mkb
    assert lst.history == {"3.7": {"BLimBanRbb": "BLimBanRba", "BVorDebHdb": "BVorDebHad"}}


def test_parse_errors():
    with pytest.raises(RgsFileError, match="geen leesbaar Excel"):
        rgs.parse_xlsx(b"geen xlsx", "x.xlsx")
    with pytest.raises(RgsFileError, match="Te weinig"):
        rgs.parse_xlsx(make_xlsx(codes=[("BLim", "Liquide middelen", "", "D", 2)]))


def test_store_and_load_newest(tmp_path, lst):
    assert rgs.load_list(str(tmp_path)) is None
    rgs.save_list(str(tmp_path), lst)
    rgs.save_list(str(tmp_path), rgs.parse_xlsx(make_xlsx("3.10")))
    assert rgs.load_list(str(tmp_path)).version == "3.10"           # 3.10 na 3.8 (niet als tekst sorteren)
    assert rgs.load_list(str(tmp_path), "3.8").codes["BLim"].short == "Liquide middelen"


@pytest.mark.parametrize("acc, expected", [
    ({"accno": "1200", "description": "Debiteuren", "category": "A", "link": "AR"}, "BVorDebHad"),
    ({"accno": "1600", "description": "Crediteuren", "category": "L", "link": "AP"}, "BSchCreHac"),
    ({"accno": "1100", "description": "Rabobank", "category": "A", "link": "AR_paid:AP_paid"}, "BLimBanRba"),
    ({"accno": "1000", "description": "Kas", "category": "A", "link": "AR_paid:AP_paid"}, "BLimKasKas"),
    ({"accno": "8000", "description": "Omzet hoog", "category": "I", "link": "AR_amount:IC_sale"}, "WOmzNopOlh"),
    ({"accno": "4100", "description": "Huur", "category": "E", "link": ""}, "WBedHuiHuu"),
    ({"accno": "1000", "description": "Liquide middelen", "category": "A", "charttype": "H"}, "BLim"),
])
def test_suggest_first(lst, acc, expected):
    got = rgs.suggest(acc, lst)
    assert got and got[0].code == expected and len(got) <= 3


def test_suggest_keeps_side_and_previous_version(lst):
    # een kostenrekening krijgt nooit een balanscode, een balansrekening nooit een W-code
    assert all(c.code.startswith("W") for c in rgs.suggest({"description": "Kas", "category": "E"}, lst))
    assert all(c.code.startswith("B") for c in rgs.suggest({"description": "Huur", "category": "A"}, lst))
    # koppeling met RGS 3.7 → via het versus-blad naar de 3.8-code, als eerste voorstel
    acc = {"description": "Iets heel anders", "category": "A"}
    assert rgs.suggest(acc, lst, previous=("3.7", "BLimBanRbb"))[0].code == "BLimBanRba"
    assert rgs.previous_code({"3.6": "X", "3.7": "Y"}, "3.8") == ("3.7", "Y")
    assert rgs.previous_code({"3.8": "Z"}, "3.8") is None


# --- alfa/bèta herkennen (v1.0.1 PB13: waarschuwen, niet weigeren) ---------------------------------

def test_prerelease_signs():
    assert rgs.prerelease_signs("RGS 3.8-def.xlsx", "Totaal-RGS3.8-def",
                                "Bijgaand de definitieve versie van RGS 3.8 op basis van NT20-FT20") == []
    assert rgs.prerelease_signs("RGS 3.9 bèta.xlsx", "Totaal-RGS3.9-def") == ["filename"]
    assert rgs.prerelease_signs("RGS 3.9.xlsx", "Totaal-RGS3.9-beta") == ["sheet"]
    assert rgs.prerelease_signs("RGS 3.9.xlsx", "Totaal-RGS3.9") == ["sheet"]
    assert rgs.prerelease_signs("RGS 3.9-alfa.xlsx", "Totaal-RGS3.9-alfa",
                                "Bijgaand de alfaversie van RGS 3.9 ter consultatie") == ["filename", "sheet", "recap"]
    # Recap noemt "beta" verderop (Zorg-RGS…-beta) maar de eerste zin zegt definitief: geen teken
    assert rgs.prerelease_signs("x.xlsx", "Totaal-RGS3.8-def", "Bijgaand de definitieve versie (Zorg: beta)") == []


def test_parse_marks_prerelease():
    assert rgs.parse_xlsx(make_xlsx(recap="Bijgaand de definitieve versie van RGS 3.8"), "RGS 3.8-def.xlsx") \
        .prerelease == []
    lst = rgs.parse_xlsx(make_xlsx("3.9", suffix="beta", recap="Bijgaand de bèta-versie van RGS 3.9"),
                         "RGS 3.9-beta.xlsx")
    assert lst.version == "3.9" and lst.prerelease == ["filename", "sheet", "recap"]
    assert rgs.RgsList.from_json(lst.to_json()).prerelease == lst.prerelease


OFFICIAL = __import__("pathlib").Path("~/sqlledger-test/rgs/RGS 3.8-def.xlsx").expanduser()


@pytest.mark.skipif(not OFFICIAL.exists(), reason="officieel RGS-bestand niet aanwezig")
def test_official_file_is_final():
    lst = rgs.parse_xlsx(OFFICIAL.read_bytes(), OFFICIAL.name)
    assert lst.version == "3.8" and lst.prerelease == []


def test_list_and_delete_versions(tmp_path, lst):
    d = str(tmp_path)
    rgs.save_list(d, lst)
    rgs.save_list(d, rgs.parse_xlsx(make_xlsx("3.10")))
    assert [v.version for v in rgs.list_versions(d)] == ["3.8", "3.10"]
    assert not rgs.delete_list(d, "../3.8") and not rgs.delete_list(d, "4.0")
    assert rgs.delete_list(d, "3.10") and rgs.load_list(d).version == "3.8"
