"""GIFI-indeling (bouwstap 3, GF1–GF3, GF5): bibliotheek en import/export."""

import pytest
from psycopg.rows import dict_row

from sqlledger import NotFoundError, ValidationError
from sqlledger.admin import Admin
from sqlledger.importer import Importer, export_rows


@pytest.fixture
def adm(conn, config):
    return Admin(conn, config)


def q(conn, sql, *params):
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()


def gifi_of(conn, accno):
    return q(conn, "SELECT gifi_accno FROM chart WHERE accno = %s", accno)[0]["gifi_accno"]


def test_list_counts_and_orphans(adm, conn):
    rows = {r["accno"]: r for r in adm.gifi_codes()}
    assert rows["0200"]["accounts"] == 1 and rows["0200"]["description"]
    assert adm.gifi_orphans() == []
    conn.execute("UPDATE chart SET gifi_accno = '010' WHERE accno IN ('0200', '0205')")
    conn.execute("UPDATE chart SET gifi_accno = '0200' WHERE accno = '0010'")
    rows = {r["accno"]: r for r in adm.gifi_codes()}
    assert rows["0200"]["accounts"] == 1 and rows["0205"]["accounts"] == 0
    assert [(o["accno"], o["gifi_accno"]) for o in adm.gifi_orphans()] == [("0200", "010"), ("0205", "010")]
    # code toevoegen lost de dode verwijzing op
    adm.save_gifi("010", "Immateriële activa")
    assert adm.gifi_orphans() == []
    assert [a["accno"] for a in adm.get_gifi("010")["accounts"]] == ["0200", "0205"]


def test_add_cleans_and_checks(adm, conn):
    assert adm.save_gifi(" 0 2'0 ", "Goodwill  --  oud") == "020"
    assert q(conn, "SELECT description FROM gifi WHERE accno = '020'") == [{"description": "Goodwill - oud"}]
    with pytest.raises(ValidationError, match="bestaat al"):
        adm.save_gifi("020", "Nog een keer")
    with pytest.raises(ValidationError, match="Code ontbreekt"):
        adm.save_gifi("  ", "x")
    with pytest.raises(ValidationError, match="Omschrijving ontbreekt"):
        adm.save_gifi("021", " ")
    with pytest.raises(NotFoundError):
        adm.save_gifi("022", "x", "bestaat-niet")


def test_rename_takes_accounts_along_and_stays_text(adm, conn):
    """GF3: de rekeningen gaan mee, in één transactie; "0200" blijft tekst (Perl ``*= 1`` maakte er 200 van)."""
    adm.save_gifi("200", "Lijkt op 0200")
    conn.execute("UPDATE chart SET gifi_accno = '0200' WHERE accno = '0205'")
    assert adm.save_gifi("020A", "Goodwill", "0200") == "020A"
    assert gifi_of(conn, "0200") == "020A" and gifi_of(conn, "0205") == "020A"
    assert q(conn, "SELECT accno FROM gifi WHERE accno IN ('0200', '020A', '200') ORDER BY accno") == \
        [{"accno": "020A"}, {"accno": "200"}]
    assert adm.get_gifi("200")["accounts"] == []
    # naar een bestaande code: geweigerd, niets gewijzigd
    with pytest.raises(ValidationError, match="bestaat al"):
        adm.save_gifi("200", "x", "020A")
    assert gifi_of(conn, "0200") == "020A"
    # alleen de omschrijving
    adm.save_gifi("020A", "Goodwill (nieuw)", "020A")
    assert adm.get_gifi("020A")["description"] == "Goodwill (nieuw)"


def test_delete_only_when_unused(adm, conn):
    """GF2: strenger dan SL (dat keek alleen naar boekingen)."""
    with pytest.raises(ValidationError, match="in gebruik bij rekening 0200"):
        adm.delete_gifi("0200")
    conn.execute("UPDATE chart SET gifi_accno = NULL WHERE accno = '0200'")
    adm.delete_gifi("0200")
    assert q(conn, "SELECT 1 FROM gifi WHERE accno = '0200'") == []
    with pytest.raises(NotFoundError):
        adm.delete_gifi("0200")


def test_import_new_update_same(conn, config):
    imp = Importer(conn, config, "gifi")
    res = imp.preview(["accno", "description"], [["0200", "Andere omschrijving"], ["030", "Goodwill"],
                                                 ["0205", ""], [20, "Getal"]])
    assert [(i.key, i.status) for i in res.items] == [("0200", "update"), ("030", "new"), ("0205", "same"),
                                                      ("20", "new")]
    assert "voorloopnul" in res.items[3].warning
    assert q(conn, "SELECT 1 FROM gifi WHERE accno = '030'") == []            # proefrun
    res = imp.run(["accno", "description"], [["0200", "Andere omschrijving"], ["030", "Goodwill"]], chosen=[1, 2])
    assert (res.added, res.updated) == (1, 1)
    assert q(conn, "SELECT description FROM gifi WHERE accno IN ('0200', '030') ORDER BY accno") == \
        [{"description": "Andere omschrijving"}, {"description": "Goodwill"}]
    assert gifi_of(conn, "0200") == "0200"
    res = imp.preview(["accno", "description"], [["031", ""]])
    assert res.items[0].status == "error"                       # nieuwe code zonder omschrijving
    with pytest.raises(ValidationError):
        imp.preview(["description"], [["x"]])                   # sleutelkolom ontbreekt


def test_export(conn, config):
    header, rows = export_rows(conn, config, "gifi")
    assert header == ["accno", "description"]
    assert rows[0][0] == "0000" and len(rows) == q(conn, "SELECT count(*) AS n FROM gifi")[0]["n"]
