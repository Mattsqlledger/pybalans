"""Importeren van verkoopfacturen, orders en betalingen (BEHEER_EXTRA_ANALYSE.md §3, IM1–IM8)."""

from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import dict_row

from sqlledger import ValidationError
from sqlledger.importer import FIELDS, ImportFailed, Importer


def one(conn, sql, *params):
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchone()


def imp(conn, config, kind):
    return Importer(conn, config, kind, "1.000,00", "dd-mm-yyyy")


INV = ["invnumber", "customernumber", "transdate", "partnumber", "qty", "sellprice", "discount"]


def test_grouping_by_number_and_relation():
    """IM2 A: zelfde nummer + relatie = één document, ook niet-opeenvolgend; zonder nummer: bij het vorige
    document tenzij relatie of datum anders is."""
    i = Importer(None, None, "sales_invoice")
    recs = [{"invnumber": "A", "customernumber": "K1", "transdate": "1-1-2026"},
            {"partnumber": "P"},                                                    # vervolg van A
            {"customernumber": "K2", "transdate": "1-1-2026"},                     # nieuw, zonder nummer
            {"partnumber": "P"},                                                    # vervolg van K2
            {"invnumber": "A", "partnumber": "P"},                                  # terug naar A
            {"customernumber": "K2", "transdate": "2-1-2026"},                     # andere datum: nieuw
            {"invnumber": "A", "customernumber": "K3"},                            # zelfde nummer, andere klant
            {"customernumber": "k3"}]                                               # zelfde relatie: vervolg
    groups = i._items(recs)
    assert [(g[0].key, g[0].rows) for g in groups] == [("A", [1, 2, 5]), ("", [3, 4]), ("", [6]), ("A", [7, 8])]


def test_sales_invoices_preview_then_import(conn, config):
    i = imp(conn, config, "sales_invoice")
    rows = [["IMP-1", "K001", "05-01-2026", "KOEK-1", "2", "", ""],
            ["", "", "", "BOEK-1", "1", "20", ""],
            ["", "K002", "06-01-2026", "KOEK-1", "1", "", ""],
            ["IMP-1", "", "", "KOEK-1", "3", "", "10"]]
    res = i.preview(INV, rows)
    a, b = res.items
    assert (a.key, a.rows, a.label, a.status) == ("IMP-1", [1, 2, 4], "Klant A", "new")
    # 2×10 + 20 + 3×10 −10% = 67 netto (IM5: btw zoals op het scherm)
    assert a.amounts[0] == "EUR" and a.amounts[1] == Decimal("67.00") and a.amounts[3] == a.amounts[1] + a.amounts[2]
    assert b.status == "new" and b.label == "Bakkerij Cees" and b.key            # volgend nummer
    assert one(conn, "SELECT count(*) AS n FROM ar WHERE invnumber = 'IMP-1'")["n"] == 0     # proefrun

    res = i.run(INV, rows, [1, 3])
    assert res.added == 2
    ar = one(conn, "SELECT id, netamount, amount, duedate, transdate, customer_id FROM ar WHERE invnumber = 'IMP-1'")
    assert Decimal(str(ar["netamount"])) == Decimal("67") and ar["transdate"] == date(2026, 1, 5)
    assert ar["duedate"] == date(2026, 1, 19)                                     # termijn 14 dagen van K001
    lines = conn.cursor(row_factory=dict_row).execute(
        "SELECT description, qty, fxsellprice, discount FROM invoice WHERE trans_id = %s ORDER BY id",
        (ar["id"],)).fetchall()
    assert [l["description"] for l in lines] == ["Roomboterkoek", "Receptenboek", "Roomboterkoek"]
    assert [Decimal(str(l["fxsellprice"])) for l in lines] == [10, 20, 10] and lines[2]["discount"] == 0.1
    assert one(conn, "SELECT count(*) AS n FROM ar WHERE invnumber = %s", b.key)["n"] == 1


def test_invoice_errors_per_document(conn, config):
    """IM3/IM6: bestaand nummer, onbekende klant of artikel = fout voor dat document; importeren is alles of niets."""
    i = imp(conn, config, "sales_invoice")
    rows = [["F-3001", "K001", "05-01-2026", "KOEK-1", "1", "", ""],
            ["N-1", "K999", "05-01-2026", "KOEK-1", "1", "", ""],
            ["N-2", "K001", "05-01-2026", "XX", "1", "", ""],
            ["N-3", "K001", "", "KOEK-1", "1", "", ""],
            ["N-4", "K001", "05-01-2026", "", "1", "", ""],
            ["N-5", "K001", "05-01-2026", "KOEK-1", "1", "", ""]]
    res = i.preview(INV, rows)
    assert res.items[0].label == "Klant A"
    assert [it.error for it in res.items] == [
        "Factuurnummer F-3001 bestaat al.", "Klant K999 bestaat niet.", "Rij 3: artikel XX bestaat niet.",
        "Datum ontbreekt.", "Rij 5: artikelnummer ontbreekt.", ""]
    with pytest.raises(ImportFailed):
        i.run(INV, rows, [6, 2])
    assert one(conn, "SELECT count(*) AS n FROM ar WHERE invnumber = 'N-5'")["n"] == 0
    with pytest.raises(ValidationError, match="Artikelnummer"):
        i.preview(["invnumber", "customernumber", "transdate"], [["X", "K001", "05-01-2026"]])


def test_invoice_defaults_and_payment(conn, config):
    """Korting van de klant (VW7), btw inclusief uit het bestand, meteen betaald (paid_1)."""
    conn.execute("UPDATE customer SET discount = 0.05 WHERE customernumber = 'K002'")
    i = imp(conn, config, "sales_invoice")
    h = ["invnumber", "customernumber", "transdate", "partnumber", "qty", "taxincluded", "paid_1", "datepaid_1"]
    res = i.run(h, [["IMP-9", "K002", "05-01-2026", "KOEK-1", "10", "ja", "50", "07-01-2026"]], [1])
    assert res.added == 1
    ar = one(conn, "SELECT id, amount, netamount, paid, taxincluded, datepaid FROM ar WHERE invnumber = 'IMP-9'")
    assert ar["taxincluded"] and Decimal(str(ar["amount"])) == Decimal("95.00")      # 10×10 −5%, incl. btw
    assert Decimal(str(ar["paid"])) == Decimal("50") and ar["datepaid"] == date(2026, 1, 7)
    assert one(conn, "SELECT discount FROM invoice WHERE trans_id = %s", ar["id"])["discount"] == 0.05


def test_invoice_foreign_currency_needs_rate(conn, config):
    i = imp(conn, config, "sales_invoice")
    h = ["invnumber", "customernumber", "transdate", "curr", "exchangerate", "partnumber"]
    res = i.preview(h, [["U-1", "K003", "05-01-2026", "USD", "", "KOEK-1"],
                        ["U-2", "K003", "05-01-2026", "USD", "0,9", "KOEK-1"]])
    assert res.items[0].error == "Koers ontbreekt voor USD op 2026-01-05."
    assert res.items[1].status == "new" and res.items[1].amounts[0] == "USD"


def test_orders(conn, config):
    """Verkoop- en inkooporders; bestaand nummer per soort (Perl-fout IM.pm:703: zocht zonder soort)."""
    so = imp(conn, config, "sales_order")
    h = ["ordnumber", "customernumber", "transdate", "reqdate", "partnumber", "qty", "orderdescription"]
    assert so.run(h, [["O-1", "K001", "05-01-2026", "20-01-2026", "KOEK-1", "4", "Bestelling"]], [1]).added == 1
    o = one(conn, "SELECT amount, netamount, reqdate, description, quotation FROM oe WHERE ordnumber = 'O-1'")
    assert Decimal(str(o["netamount"])) == Decimal("40") and o["reqdate"] == date(2026, 1, 20)
    assert o["description"] == "Bestelling" and not o["quotation"]
    res = so.preview(h, [["O-1", "K001", "05-01-2026", "", "KOEK-1", "1", ""]])
    assert res.items[0].error == "Ordernummer O-1 bestaat al."

    po = imp(conn, config, "purchase_order")
    hp = ["ordnumber", "vendornumber", "transdate", "partnumber", "qty", "sellprice"]
    res = po.run(hp, [["O-1", "L001", "05-01-2026", "KOEK-1", "5", "3"], ["", "L002", "05-01-2026", "BOEK-1", "1", "7"]],
                 [1, 2])
    assert res.added == 2
    assert one(conn, "SELECT count(*) AS n FROM oe WHERE ordnumber = 'O-1'")["n"] == 2      # verkoop én inkoop
    assert one(conn, "SELECT netamount FROM oe WHERE ordnumber = 'O-1' AND vendor_id > 0")["netamount"] == 15
    assert "vendornumber" in {f.name for f in FIELDS["purchase_order"]}
    assert po.preview(hp, [["X", "K001", "05-01-2026", "KOEK-1", "1", "1"]]).items[0].error == \
        "Leverancier K001 bestaat niet."


PAY = ["datepaid", "amount", "invnumber", "dcn", "customernumber", "vendornumber"]


def test_payments_match_on_number_or_dcn(conn, config):
    """IM7 A: op factuurnummer (+ relatie), anders betalingskenmerk; nooit alleen op bedrag."""
    i = imp(conn, config, "payment")
    rows = [["10-01-2026", "100", "F-3001", "", "", ""],
            ["10-01-2026", "21", "", "DCN1", "", ""],              # zelfde factuur via kenmerk: rest
            ["10-01-2026", "1", "F-3001", "", "", ""],             # nu volledig betaald
            ["10-01-2026", "5", "F-3001", "", "K002", ""],         # nummer hoort bij een andere klant
            ["10-01-2026", "99999", "F-3002", "", "", ""],
            ["10-01-2026", "1", "F2026-001", "", "", ""],          # al betaald
            ["10-01-2026", "121", "", "", "", ""],                 # alleen een bedrag
            ["10-01-2026", "100", "I-4001", "", "", "L002"]]       # inkoopfactuur
    res = i.preview(PAY, rows)
    assert [(it.key, it.status, it.error) for it in res.items] == [
        ("F-3001", "new", ""), ("F-3001", "new", ""), ("F-3001", "error", "Factuur F-3001 staat niet open."),
        ("F-3001", "error", "Geen factuur gevonden voor F-3001 bij K002."),
        ("F-3002", "error", "Het bedrag 99.999,00 is meer dan wat openstaat (222,50)."),
        ("F2026-001", "error", "Factuur F2026-001 staat niet open."),
        ("", "error", "Factuurnummer of betalingskenmerk ontbreekt."), ("I-4001", "new", "")]
    assert res.items[1].amounts == ("EUR", Decimal("21"), Decimal("21.00"))
    assert one(conn, "SELECT paid FROM ar WHERE invnumber = 'F-3001'")["paid"] == 0           # proefrun

    res = i.run(PAY, rows, [1, 2, 8])
    assert res.added == 3
    assert one(conn, "SELECT paid FROM ar WHERE invnumber = 'F-3001'")["paid"] == 121
    assert one(conn, "SELECT paid FROM ap WHERE invnumber = 'I-4001'")["paid"] == 100


def test_payment_side_and_ambiguity(conn, config):
    conn.execute("UPDATE ap SET invnumber = 'F-3004' WHERE invnumber = 'I-4003'")   # zelfde nummer in ar en ap
    i = imp(conn, config, "payment")
    h = ["datepaid", "credit", "debit", "invnumber", "vendornumber"]
    res = i.preview(h, [["10-01-2026", "", "", "F-3004", ""],
                        ["10-01-2026", "10", "5", "F-3004", ""],
                        ["10-01-2026", "10", "", "F-3004", ""],             # credit = ontvangst: ar
                        ["10-01-2026", "", "10", "F-3004", ""],             # debet = betaling: ap
                        ["10-01-2026", "10", "", "F-3004", "L001"]])
    errors = [it.error for it in res.items]
    assert errors[:2] == ["Vul precies één bedrag in (Bedrag, Credit of Debet)."] * 2
    assert errors[2:4] == ["", ""] and res.items[2].label == "Bakkerij Cees" and res.items[3].label == "Leverancier B"
    assert errors[4] == "Credit is een ontvangst van een klant, Debet een betaling aan een leverancier."
    res = imp(conn, config, "payment").preview(["datepaid", "amount", "invnumber"], [["10-01-2026", "10", "F-3004"]])
    assert res.items[0].error == \
        "Meer dan één factuur gevonden voor F-3004; vul het klant- of leveranciersnummer in."
    with pytest.raises(ValidationError, match="bedrag"):
        i.preview(["datepaid", "invnumber"], [["10-01-2026", "F-3004"]])
    with pytest.raises(ValidationError, match="factuurnummer"):
        i.preview(["datepaid", "amount"], [["10-01-2026", "1"]])
