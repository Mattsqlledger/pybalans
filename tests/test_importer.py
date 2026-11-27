"""Import en export (sqlledger.importer, FASE4_ANALYSE.md stap 7, keuzes 1–12)."""

import io
from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import dict_row

from sqlledger import Contacts, ValidationError
from sqlledger.importer import (FIELDS, ImportFailed, ImportFileError, Importer, auto_map, export_rows,
                                parse_number, read_file)

SEQUENCES = ["SELECT setval('addressid', (SELECT max(id) FROM address))",
             "SELECT setval('contactid', (SELECT max(id) FROM contact))"]


@pytest.fixture
def db(conn):
    for sql in SEQUENCES:
        conn.execute(sql)
    return conn


def one(conn, sql, *params):
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchone()


def csv_bytes(text, enc="utf-8"):
    return text.encode(enc)


def test_read_csv_variants():
    h, rows = read_file(csv_bytes('﻿customernumber;name;notes\n"K1";"Bakker ""de"" Jong";"regel 1\nregel 2"\n\n'))
    assert h == ["customernumber", "name", "notes"]
    assert rows == [["K1", 'Bakker "de" Jong', "regel 1\nregel 2"]]
    h, rows = read_file(csv_bytes("a,b\n1,2\n"))
    assert h == ["a", "b"] and rows == [["1", "2"]]
    h, rows = read_file(csv_bytes("a\tb\n1\t2\n"))
    assert rows == [["1", "2"]]
    h, rows = read_file("name;city\nCafé;Zürich\n".encode("cp1252"))
    assert rows == [["Café", "Zürich"]]
    h, rows = read_file(csv_bytes("a;b;c\n1\n"))           # korte rij aangevuld
    assert rows == [["1", "", ""]]
    with pytest.raises(ImportFileError):
        read_file(b"")


def test_read_xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["partnumber", "description", "sellprice", "priceupdate"])
    ws.append(["A-1", "Bout", 1.25, date(2026, 1, 5)])
    buf = io.BytesIO()
    wb.save(buf)
    h, rows = read_file(buf.getvalue(), "artikelen.xlsx")
    assert h == ["partnumber", "description", "sellprice", "priceupdate"]
    assert rows[0][:3] == ["A-1", "Bout", 1.25] and rows[0][3].date() == date(2026, 1, 5)


def test_auto_map_and_numbers():
    m = auto_map("customer", ["Klantnummer", "NAME", "e-mail", "Onbekend", "name"],
                 aliases={"city": ["Ville"]})
    assert m == ["customernumber", "name", "email", "", ""]          # dubbele kolom maar één keer
    assert auto_map("customer", ["Ville"], aliases={"city": ["Ville"]}) == ["city"]
    assert auto_map("customer", ["Naam"], saved={"Naam": "notes"}) == ["notes"]
    assert parse_number("1.234,56", "1.000,00") == Decimal("1234.56")
    assert parse_number("1234.56", "1.000,00") == Decimal("1234.56")
    assert parse_number("1.000", "1.000,00") == Decimal("1000")
    assert parse_number("1,234.56", "1,000.00") == Decimal("1234.56")
    assert parse_number(2.5, "1.000,00") == Decimal("2.5")
    assert set(f.name for f in FIELDS["service"]).isdisjoint({"inventory_accno", "rop", "bin"})


def test_customers_preview_changes_nothing_then_import(db, config):
    before = one(db, "SELECT count(*) AS n FROM customer")["n"]
    header = ["customernumber", "name", "city", "curr", "discount", "taxaccounts", "terms"]
    rows = [["", "Nieuwe Klant BV", "Utrecht", "EUR", "2,5", "", "14"],
            ["", "", "Zonder naam", "", "", "", ""],
            ["", "Foute Valuta", "", "XYZ", "", "", ""]]
    imp = Importer(db, config, "customer", numberformat="1.000,00")
    res = imp.preview(header, rows)
    assert [i.status for i in res.items] == ["new", "error", "error"]
    assert "Naam ontbreekt" in res.items[1].error and "valuta" in res.items[2].error
    assert one(db, "SELECT count(*) AS n FROM customer")["n"] == before          # proefrun teruggedraaid
    res = imp.run(header, rows, chosen=[1])
    assert res.added == 1
    c = one(db, "SELECT * FROM customer WHERE name = 'Nieuwe Klant BV'")
    assert c["discount"] == pytest.approx(0.025) and c["terms"] == 14 and c["customernumber"]
    # fout in een gekozen rij: niets geïmporteerd
    with pytest.raises(ImportFailed):
        imp.run(header, rows + [["", "Nog Een", "", "", "", "", ""]], chosen=[3, 4])
    assert one(db, "SELECT count(*) AS n FROM customer")["n"] == before + 1


def test_update_only_given_columns_and_empty_cell_keeps_value(db, config):
    lib = Contacts(db, config)
    cid = lib.save(Relation_with(lib, name="Jansen", city="Gouda", email="j@x.nl", phone="0182"))
    number = one(db, "SELECT customernumber FROM customer WHERE id = %s", cid)["customernumber"]
    imp = Importer(db, config, "customer")
    res = imp.preview(["customernumber", "city", "email"], [[number, "Delft", ""]])
    assert res.items[0].status == "update" and res.items[0].changes == ["city"]
    imp.run(["customernumber", "city", "email"], [[number, "Delft", ""]], chosen=[1])
    r = lib.get("customer", cid)
    assert (r.city, r.email, r.phone) == ("Delft", "j@x.nl", "0182")
    res = imp.preview(["customernumber", "city"], [[number, "Delft"]])
    assert res.items[0].status == "same" and not res.items[0].selected


def Relation_with(lib, **kw):
    r = lib.new("customer")
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_duplicate_key_in_file(db, config):
    res = Importer(db, config, "partsgroup").preview(["partsgroup"], [["Bouten"], ["bouten"]])
    assert [i.status for i in res.items] == ["new", "error"] and "rij 1" in res.items[1].error


def test_parts_services_and_groups(db, config):
    Importer(db, config, "partsgroup").run(["partsgroup", "code"], [["Gereedschap", "G1"]], chosen=[1])
    assert one(db, "SELECT code FROM partsgroup WHERE partsgroup = 'Gereedschap'")["code"] == "G1"
    inv, inc, exp = (one(db, "SELECT accno FROM chart WHERE ':' || link || ':' LIKE %s ORDER BY accno LIMIT 1",
                         f"%:{k}:%")["accno"] for k in ("IC", "IC_sale", "IC_cogs"))
    header = ["partnumber", "description", "partsgroup", "sellprice", "inventory_accno", "income_accno",
              "expense_accno"]
    imp = Importer(db, config, "part")
    res = imp.preview(header, [["IMP-1", "Hamer", "Gereedschap", "12.50", inv, inc, exp],
                               ["IMP-2", "Zaag", "Bestaat Niet", "8", inv, inc, exp]])
    assert [i.status for i in res.items] == ["new", "new"], [i.error for i in res.items]
    assert "Bestaat Niet" in res.items[1].warning and not res.items[0].warning          # groep zoals SL aangemaakt
    assert one(db, "SELECT count(*) AS n FROM partsgroup WHERE partsgroup = 'Bestaat Niet'")["n"] == 0
    imp.run(header, [["IMP-1", "Hamer", "Gereedschap", "12.50", inv, inc, exp]], chosen=[1])
    p = one(db, """SELECT p.sellprice, g.partsgroup FROM parts p JOIN partsgroup g ON g.id = p.partsgroup_id
                   WHERE partnumber = 'IMP-1'""")
    assert Decimal(str(p["sellprice"])) == Decimal("12.5") and p["partsgroup"] == "Gereedschap"
    # een artikelnummer als dienst importeren: geweigerd
    res = Importer(db, config, "service").preview(["partnumber", "description"], [["IMP-1", "Hamer"]])
    assert res.items[0].status == "error" and "artikel" in res.items[0].error
    # de testadministratie heeft geen opbrengstrekening voor diensten (IC_income): eerst via de rekeningimport
    Importer(db, config, "coa").run(["accno", "description", "category", "link"],
                                    [["8550", "Omzet diensten", "I", "AR_amount:IC_income"]], chosen=[1])
    res = Importer(db, config, "service").run(["partnumber", "description", "sellprice"],
                                              [["SRV-1", "Advies", "95"]], chosen=[1])
    assert res.added == 1
    assert one(db, "SELECT inventory_accno_id FROM parts WHERE partnumber = 'SRV-1'")["inventory_accno_id"] is None


def test_chart_of_accounts(db, config):
    imp = Importer(db, config, "coa")
    res = imp.run(["accno", "description", "category", "link"], [["9901", "Importrekening", "E", "AP_amount"]],
                  chosen=[1])
    assert res.added == 1
    row = one(db, "SELECT category, link, charttype FROM chart WHERE accno = '9901'")
    assert (row["category"], row["link"], row["charttype"]) == ("E", "AP_amount", "A")
    res = imp.preview(["accno", "description"], [["9901", "Importrekening"]])
    assert res.items[0].status == "same"
    res = imp.preview(["accno", "link"], [["9901", "AR_amount:AP_amount:AR_paid"]])
    assert res.items[0].status == "error"                      # zelfde controles als het scherm
    with pytest.raises(ValidationError):
        imp.preview(["description"], [["x"]])                  # sleutelkolom ontbreekt


def test_general_ledger(db, config):
    acc = [r[0] for r in db.execute(
        "SELECT accno FROM chart WHERE charttype = 'A' AND NOT coalesce(closed, false) ORDER BY accno LIMIT 2")]
    header = ["reference", "transdate", "description", "accno", "debit", "credit"]
    rows = [["IMP-GL-1", "2026-03-01", "Herindeling", acc[0], "100", ""],
            ["IMP-GL-2", "2026-03-02", "Scheef", acc[0], "50", ""],
            ["IMP-GL-1", "", "", acc[1], "", "100"],            # niet-opeenvolgend: hoort bij IMP-GL-1
            ["IMP-GL-2", "", "", acc[1], "", "40"]]
    imp = Importer(db, config, "gl", dateformat="dd-mm-yyyy")
    res = imp.preview(header, rows)
    assert [(i.key, i.rows, i.status) for i in res.items] == [("IMP-GL-1", [1, 3], "new"),
                                                              ("IMP-GL-2", [2, 4], "error")]
    imp.run(header, rows, chosen=[1])
    g = one(db, "SELECT id, transdate FROM gl WHERE reference = 'IMP-GL-1'")
    assert g["transdate"] == date(2026, 3, 1)
    assert one(db, "SELECT sum(amount) AS s FROM acc_trans WHERE trans_id = %s", g["id"])["s"] == 0
    res = imp.preview(header, rows[:1] + rows[2:3])
    assert res.items[0].warning and not res.items[0].selected          # referentie bestaat al


@pytest.mark.parametrize("kind", ["customer", "vendor", "part", "service", "partsgroup", "coa", "gifi"])
def test_export_then_import_changes_nothing(db, config, kind):
    """Keuze 11: een export ongewijzigd terug importeren = alles ongewijzigd."""
    header, rows = export_rows(db, config, kind)
    assert header == [f.name for f in FIELDS[kind]]
    if not rows:
        pytest.skip(f"geen {kind} in de testdata")
    res = Importer(db, config, kind).preview(header, [[str(c) for c in r] for r in rows])
    assert {i.status for i in res.items} == {"same"}, [(i.key, i.status, i.changes, i.error) for i in res.items
                                                       if i.status != "same"][:5]
