"""Btw-tarieven en facturen (vraag gebruiker 2026-10-10): btw 0, tarief wijzigen met een nieuwe periode
(oude facturen houden hun tarief) en het overschrijven van een gebruikt tarief (beslispunt BT1 = A: geweigerd)."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger import Part, Parts
from sqlledger import ValidationError
from sqlledger.admin import Admin, with_new_rate
from sqlledger.invoice import Invoice, InvoiceLedger, InvoiceLine
from sqlledger.payments import Payment, PaymentLine, Payments
from sqlledger.paymentspec import PaymentSpecs
from sqlledger.printdata import PrintData

SETUP = ["UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'",
         "DELETE FROM tax WHERE taxnumber IS NULL", "SELECT setval('invoiceid', 1000)",
         "INSERT INTO chart (accno, description, charttype, category, link) "
         "VALUES ('1812', 'Btw 0%', 'A', 'L', 'AR_tax:AP_tax:IC_taxpart:IC_taxservice')",
         "INSERT INTO tax (chart_id, rate) SELECT id, 0 FROM chart WHERE accno = '1812'",
         "INSERT INTO customertax (customer_id, chart_id) SELECT 900, id FROM chart WHERE accno = '1812'",
         "INSERT INTO vendortax (vendor_id, chart_id) SELECT 905, id FROM chart WHERE accno = '1812'"]
OLD, NEW = date(2026, 3, 1), date(2026, 7, 15)
KINDS = [("customer", 900, "1200", "invoice"), ("vendor", 905, "1600", "vendor_invoice")]


@pytest.fixture
def setup(conn, config):
    for sql in SETUP:
        conn.execute(sql)
    ic = Parts(conn, config)
    ids = {}
    for number, sell, taxes in (("S-21", "100", ["1810", "1800"]), ("S-0", "100", ["1812"]), ("S-N", "50", [])):
        ids[number] = ic.save(Part(kind="service", partnumber=number, description=f"Dienst {number}",
                                   sellprice=D(sell), income_accno="8520", expense_accno="8100", taxaccounts=taxes))
    return InvoiceLedger(conn, config), ids


def gl(conn, tid):
    return sorted((r[0], D(str(r[1]))) for r in conn.execute(
        """SELECT c.accno, round(ac.amount::numeric, 2) FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
           WHERE ac.trans_id = %s""", (tid,)).fetchall())


def invoice(vc, vc_id, arap, d, lines):
    return Invoice(vc=vc, vc_id=vc_id, transdate=d, arap_accno=arap,
                   lines=[InvoiceLine(parts_id=p, qty=D(q), sellprice=D(pr)) for p, q, pr in lines])


def printed(conn, config, tid, vc, formname):
    v = PrintData(conn, config).invoice_vars(tid, vc, formname)
    return v["taxrate"], v["tax"], v["invtotal"]


def tax_rows(adm, accno, *rows):
    """Alle btw-regels uit Beheer, met voor ``accno`` de opgegeven (tarief, geldig tot)."""
    cid = next(r["chart_id"] for r in adm.taxes() if r["accno"] == accno)
    keep = [dict(chart_id=r["chart_id"], rate=r["rate"], taxnumber=r["taxnumber"], validto=r["validto"])
            for r in adm.taxes() if r["chart_id"] != cid]
    return keep + [dict(chart_id=cid, rate=D(rate) if rate is not None else None, taxnumber=None, validto=v)
                   for rate, v in rows]


@pytest.mark.parametrize("vc,vc_id,arap,formname", KINDS)
def test_zero_rate(setup, conn, config, vc, vc_id, arap, formname):
    inv, ids = setup
    t = inv.post_invoice(invoice(vc, vc_id, arap, OLD, [(ids["S-0"], 1, "100"), (ids["S-N"], 2, "50")]))
    table = "ar" if vc == "customer" else "ap"
    amount, net = conn.execute(f"SELECT amount, netamount FROM {table} WHERE id = %s", (t,)).fetchone()
    assert D(str(amount)) == D(str(net)) == 200
    booked = gl(conn, t)
    assert not [a for a, _ in booked if a.startswith("18")]            # geen btw-regel van 0
    # op de factuur een regel "Btw 0%" met 0,00, zoals IS.pm ("process 0 taxes")
    assert printed(conn, config, t, vc, formname) == (["0"], ["0.00"], "200.00")
    inv.post_invoice(inv.get_invoice(t, vc))
    assert gl(conn, t) == booked
    Payments(conn, config).post_payment(Payment(vc=vc, vc_id=vc_id, datepaid=date(2026, 3, 5),
                                                payment_accno="1100", source="Z-1", lines=[PaymentLine(t, D("200"))]))
    assert PaymentSpecs(conn, config).find(vc, "1100", "Z-1", date(2026, 3, 5), vc_id).total == 200


@pytest.mark.parametrize("vc,vc_id,arap,formname", KINDS)
def test_rate_change_with_period_keeps_old_invoices(setup, conn, config, vc, vc_id, arap, formname):
    inv, ids = setup
    acc = "1810" if vc == "customer" else "1800"
    old = inv.post_invoice(invoice(vc, vc_id, arap, OLD, [(ids["S-21"], 1, "100")]))
    booked = gl(conn, old)
    assert printed(conn, config, old, vc, formname) == (["21"], ["21.00"], "121.00")
    adm = Admin(conn, config)
    adm.save_taxes(tax_rows(adm, acc, (21, date(2026, 6, 30)), (23, None)))
    # oude factuur: zelfde tarief op het document, bij doorrekenen en na opnieuw opslaan
    assert printed(conn, config, old, vc, formname) == (["21"], ["21.00"], "121.00")
    assert inv.calculate(inv.get_invoice(old, vc)).taxes == {acc: D("21.00")}
    inv.post_invoice(inv.get_invoice(old, vc))
    assert gl(conn, old) == booked
    # nieuwe factuur na de ingangsdatum: nieuw tarief
    new = inv.post_invoice(invoice(vc, vc_id, arap, NEW, [(ids["S-21"], 1, "100")]))
    assert printed(conn, config, new, vc, formname) == (["23"], ["23.00"], "123.00")
    assert [r["rate"] for r in adm.taxes() if r["accno"] == acc] == [D(21), D(23)]


@pytest.mark.parametrize("vc,vc_id,arap,formname", KINDS)
def test_overwriting_used_rate_is_refused(setup, conn, config, vc, vc_id, arap, formname):
    """BT1 (A): een gebruikt tarief overschrijven of weghalen wordt geweigerd; oude facturen blijven 21%."""
    inv, ids = setup
    acc = "1810" if vc == "customer" else "1800"
    old = inv.post_invoice(invoice(vc, vc_id, arap, OLD, [(ids["S-21"], 1, "100")]))
    adm = Admin(conn, config)
    before = conn.execute("SELECT * FROM tax ORDER BY chart_id, validto").fetchall()
    for rows in (tax_rows(adm, acc, (25, None)),                               # overschrijven
                 tax_rows(adm, acc, (21, date(2026, 2, 1)), (25, None)),       # einddatum vóór de laatste boeking
                 tax_rows(adm, acc, (None, None))):                            # leegmaken = weghalen
        with pytest.raises(ValidationError, match=r"al gebruikt in boekingen tot en met 2026-06-\d\d\. Voeg"):
            adm.save_taxes(rows)
    assert conn.execute("SELECT * FROM tax ORDER BY chart_id, validto").fetchall() == before
    assert printed(conn, config, old, vc, formname) == (["21"], ["21.00"], "121.00")
    # nummer wijzigen mag wel; een nieuw tarief per datum ná de laatste boeking ook
    rows = tax_rows(adm, acc, (21, None))
    rows[-1]["taxnumber"] = "NL-HOOG"
    adm.save_taxes(rows)
    cid = rows[-1]["chart_id"]
    with pytest.raises(ValidationError):                      # ingangsdatum vóór de laatste boeking
        adm.save_taxes(with_new_rate(tax_rows(adm, acc, (21, None)), cid, D(25), date(2026, 3, 2)))
    adm.save_taxes(with_new_rate(tax_rows(adm, acc, (21, None)), cid, D(25), date(2026, 7, 1)))
    assert [(r["rate"], r["validto"]) for r in adm.taxes() if r["accno"] == acc] == [
        (D(21), date(2026, 6, 30)), (D(25), None)]
    assert printed(conn, config, old, vc, formname) == (["21"], ["21.00"], "121.00")


def test_zero_rate_used_is_protected_and_unused_account_is_free(setup, conn, config):
    inv, ids = setup
    adm = Admin(conn, config)
    adm.save_taxes(tax_rows(adm, "1812", (5, None)))          # nog niet gebruikt: vrij te wijzigen
    adm.save_taxes(tax_rows(adm, "1812", (0, None)))
    inv.post_invoice(invoice("customer", 900, "1200", OLD, [(ids["S-0"], 1, "100")]))
    with pytest.raises(ValidationError, match="tarief van 0% van rekening 1812"):
        adm.save_taxes(tax_rows(adm, "1812", (5, None)))      # 0% boekt geen regel, maar is wel gebruikt


def test_with_new_rate():
    rows = [{"chart_id": 1, "rate": D(6), "taxnumber": "L", "validto": date(2018, 12, 31)},
            {"chart_id": 1, "rate": D(9), "taxnumber": "L", "validto": None},
            {"chart_id": 2, "rate": D(21), "taxnumber": None, "validto": None}]
    out = with_new_rate(rows, 1, D(10), date(2027, 1, 1))
    assert [(r["chart_id"], r["rate"], r["validto"], r["taxnumber"]) for r in out] == [
        (1, D(6), date(2018, 12, 31), "L"), (1, D(9), date(2026, 12, 31), "L"), (1, D(10), None, "L"),
        (2, D(21), None, None)]
    assert rows[1]["validto"] is None                          # invoer ongewijzigd
