"""Facturen met artikelen en kostprijs (sqlledger.invoice + sqlledger.cogs, fase 3 F1–F9, SA7)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import ArapPayment, Part, Parts, ValidationError
from sqlledger.assembly import Assemblies, Component
from sqlledger.errors import ClosedPeriodError
from sqlledger.invoice import Invoice, InvoiceLedger, InvoiceLine

from test_parts import SETUP

D = Decimal
CUST, VEND = 900, 905


@pytest.fixture
def ic(conn, config):
    for sql in SETUP + ["DELETE FROM tax WHERE taxnumber IS NULL", "SELECT setval('invoiceid', 1000)"]:
        conn.execute(sql)
    return Parts(conn, config)


@pytest.fixture
def inv(conn, config, ic):
    return InvoiceLedger(conn, config)


def mk_part(ic, number, sell="5", cost="2", **kw):
    p = Part(kind="part", partnumber=number, description=f"Artikel {number}", unit="st", sellprice=D(sell),
             listprice=D(sell), lastcost=D(cost), inventory_accno="7001", income_accno="8520",
             expense_accno="8020", taxaccounts=["1810", "1800"])
    for k, v in kw.items():
        setattr(p, k, v)
    return ic.save(p)


def mk_service(ic, number, sell="10"):
    return ic.save(Part(kind="service", partnumber=number, description=f"Dienst {number}", sellprice=D(sell),
                        income_accno="8520", expense_accno="8100", taxaccounts=["1810", "1800"]))


def sale(lines, d=date(2026, 3, 1), **kw):
    return Invoice(vc="customer", vc_id=kw.pop("vc_id", CUST), transdate=d, arap_accno="1200",
                   lines=[InvoiceLine(parts_id=p, qty=D(q), sellprice=D(pr)) for p, q, pr in lines], **kw)


def purchase(lines, d=date(2026, 2, 1), **kw):
    return Invoice(vc="vendor", vc_id=kw.pop("vc_id", VEND), transdate=d, arap_accno="1600",
                   lines=[InvoiceLine(parts_id=p, qty=D(q), sellprice=D(pr)) for p, q, pr in lines], **kw)


def gl(conn, tid):
    """Grootboekregels van een boeking: (rekening, bedrag, datum) gesorteerd."""
    return sorted((r[0], D(str(r[1])), r[2]) for r in conn.execute(
        """SELECT c.accno, round(ac.amount::numeric, 2), ac.transdate FROM acc_trans ac
           JOIN chart c ON (c.id = ac.chart_id) WHERE ac.trans_id = %s""", (tid,)).fetchall())


def balance(conn, accno):
    r = conn.execute("""SELECT COALESCE(sum(ac.amount::numeric), 0) FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                        WHERE c.accno = %s""", (accno,)).fetchone()
    return D(str(r[0]))


def onhand(conn, pid):
    return D(str(conn.execute("SELECT onhand FROM parts WHERE id = %s", (pid,)).fetchone()[0]))


def lines_of(conn, tid):
    return conn.execute("""SELECT parts_id, qty, allocated, sellprice, assemblyitem FROM invoice
                           WHERE trans_id = %s ORDER BY id""", (tid,)).fetchall()


def test_purchase_then_sale_fifo(inv, ic, conn):
    a = mk_part(ic, "A-1")
    p1 = inv.post_invoice(purchase([(a, 10, "2")]))
    assert gl(conn, p1) == sorted([("7001", D("-20.00"), date(2026, 2, 1)), ("1800", D("-4.20"), date(2026, 2, 1)),
                                   ("1600", D("24.20"), date(2026, 2, 1))])
    assert onhand(conn, a) == 10
    s1 = inv.post_invoice(sale([(a, 4, "5")]))
    rows = gl(conn, s1)
    assert ("8020", D("-8.00"), date(2026, 3, 1)) in rows and ("7001", D("8.00"), date(2026, 3, 1)) in rows
    assert ("8520", D("20.00"), date(2026, 3, 1)) in rows and ("1810", D("4.20"), date(2026, 3, 1)) in rows
    assert ("1200", D("-24.20"), date(2026, 3, 1)) in rows
    assert onhand(conn, a) == 6
    assert balance(conn, "7001") == D("-12.00")
    assert lines_of(conn, p1)[0][2] == 4 and lines_of(conn, s1)[0][2] == -4


def state(conn):
    """Alles wat een factuur raakt, zonder regel-id's (die wisselen bij opnieuw opslaan)."""
    pos = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT id, trans_id, row_number() OVER (PARTITION BY trans_id ORDER BY id) FROM invoice").fetchall()}
    acc = sorted((r[0], r[1], D(str(r[2])), r[3], r[4], pos.get(r[5], r[5]) if r[5] and r[5] > 100 else r[5])
                 for r in conn.execute("""SELECT trans_id, chart_id, round(amount::numeric, 2), transdate, project_id, id
                                          FROM acc_trans""").fetchall())
    invr = sorted(tuple(r) for r in conn.execute(
        """SELECT trans_id, parts_id, qty, allocated, sellprice, fxsellprice, discount, description, assemblyitem
           FROM invoice""").fetchall())
    alloc = sorted((pos[r[0]], pos.get(r[1]), D(str(r[2])), D(str(r[3])), r[4]) for r in conn.execute(
        "SELECT consumer_id, supply_id, qty, amount, kind FROM x_cogs_allocation").fetchall())
    cost = sorted((pos[r[0]], D(str(r[1])), D(str(r[2]))) for r in conn.execute(
        "SELECT invoice_id, qty, amount FROM x_invoice_cost").fetchall())
    parts = sorted(tuple(r) for r in conn.execute("SELECT id, onhand, lastcost, avgcost FROM parts").fetchall())
    hdr = sorted(tuple(r) for r in conn.execute(
        "SELECT id, amount, netamount, paid, transdate, invnumber FROM ar UNION ALL "
        "SELECT id, amount, netamount, paid, transdate, invnumber FROM ap").fetchall())
    return {"acc": acc, "invoice": invr, "alloc": alloc, "cost": cost, "parts": parts, "hdr": hdr}


def same_state(a, b):
    """Vergelijk twee toestanden en toon alleen de verschillen."""
    diff = {}
    for k in a:
        if a[k] != b[k]:
            diff[k] = {"alleen voor": [x for x in a[k] if x not in b[k]], "alleen na": [x for x in b[k] if x not in a[k]]}
    assert not diff, diff


def resave(inv, tid, vc):
    i = inv.get_invoice(tid, vc)
    return inv.post_invoice(i)


def test_unchanged_resave_is_identical(inv, ic, conn):
    a, b = mk_part(ic, "A-1"), mk_part(ic, "B-1", cost="3")
    s0 = inv.post_invoice(sale([(a, 2, "5")], d=date(2026, 1, 15)))          # negatieve voorraad
    p1 = inv.post_invoice(purchase([(a, 10, "2"), (b, 5, "3.333")]))
    s1 = inv.post_invoice(sale([(a, 4, "5"), (b, 2, "9.95")]))
    before = state(conn)
    for tid, vc in ((s0, "customer"), (p1, "vendor"), (s1, "customer")):
        resave(inv, tid, vc)
        same_state(before, state(conn))


def test_negative_stock_gets_cost_from_later_purchase(inv, ic, conn):
    a = mk_part(ic, "A-1")
    s1 = inv.post_invoice(sale([(a, 3, "5")], d=date(2026, 1, 10)))
    assert not [r for r in gl(conn, s1) if r[0] in ("8020", "7001")]
    assert onhand(conn, a) == -3
    p1 = inv.post_invoice(purchase([(a, 5, "2")], d=date(2026, 2, 1)))
    rows = gl(conn, s1)
    assert ("8020", D("-6.00"), date(2026, 2, 1)) in rows and ("7001", D("6.00"), date(2026, 2, 1)) in rows
    assert lines_of(conn, s1)[0][2] == -3 and lines_of(conn, p1)[0][2] == 3
    # prijs van de inkoop wijzigen: kostprijs in de verkoop gaat mee
    i = inv.get_invoice(p1, "vendor")
    i.lines[0].sellprice = D("2.5")
    inv.post_invoice(i)
    rows = gl(conn, s1)
    assert ("8020", D("-7.50"), date(2026, 2, 1)) in rows and ("8020", D("-6.00"), date(2026, 2, 1)) not in rows
    assert balance(conn, "7001") == D("-5.00")          # 2 stuks à 2,50 over
    # inkoop verwijderen: verkoop weer zonder kostprijs
    inv.delete_invoice(p1, "vendor")
    assert not [r for r in gl(conn, s1) if r[0] in ("8020", "7001")]
    assert lines_of(conn, s1)[0][2] == 0
    assert balance(conn, "7001") == 0 and onhand(conn, a) == -3
    assert not conn.execute("SELECT 1 FROM x_cogs_allocation").fetchall()


def test_no_rounding_residue_in_stock(inv, ic, conn):
    a = mk_part(ic, "A-1")
    inv.post_invoice(purchase([(a, 3, "0.333333")]))        # 3 voor 1,00
    assert balance(conn, "7001") == D("-1.00")
    costs = []
    for n in range(3):
        s = inv.post_invoice(sale([(a, 1, "1")], d=date(2026, 3, 1 + n)))
        costs += [r[1] for r in gl(conn, s) if r[0] == "8020"]
    assert costs == [D("-0.33"), D("-0.34"), D("-0.33")]          # cumulatief afgerond
    assert balance(conn, "7001") == 0


def test_fifo_across_purchases_and_reversal_is_exact(inv, ic, conn):
    a = mk_part(ic, "A-1")
    inv.post_invoice(purchase([(a, 2, "1")], d=date(2026, 1, 5)))
    inv.post_invoice(purchase([(a, 2, "3")], d=date(2026, 1, 6)))
    s1 = inv.post_invoice(sale([(a, 3, "10")]))
    assert [r[1] for r in gl(conn, s1) if r[0] == "8020"] == [D("-5.00")]      # 2 x 1 + 1 x 3
    s2 = inv.post_invoice(sale([(a, 1, "10")], d=date(2026, 3, 2)))
    assert [r[1] for r in gl(conn, s2) if r[0] == "8020"] == [D("-3.00")]
    # eerste verkoop verwijderen en opnieuw boeken: kostprijs van de tweede verandert niet
    inv.delete_invoice(s1, "customer")
    assert [r[1] for r in gl(conn, s2) if r[0] == "8020"] == [D("-3.00")]
    s3 = inv.post_invoice(sale([(a, 3, "10")], d=date(2026, 3, 3)))
    assert [r[1] for r in gl(conn, s3) if r[0] == "8020"] == [D("-5.00")]
    assert balance(conn, "7001") == 0


def test_change_sale_quantity_keeps_existing_allocation(inv, ic, conn):
    a = mk_part(ic, "A-1")
    inv.post_invoice(purchase([(a, 2, "1")], d=date(2026, 1, 5)))
    inv.post_invoice(purchase([(a, 2, "3")], d=date(2026, 1, 6)))
    s1 = inv.post_invoice(sale([(a, 1, "10")]))
    i = inv.get_invoice(s1, "customer")
    i.lines[0].qty = D(3)
    inv.post_invoice(i)
    assert [r[1] for r in gl(conn, s1) if r[0] == "8020"] == [D("-5.00")]
    assert len(conn.execute("SELECT 1 FROM x_cogs_allocation").fetchall()) == 3     # eerste toewijzing bleef
    i = inv.get_invoice(s1, "customer")
    i.lines[0].qty = D(1)
    inv.post_invoice(i)
    assert [r[1] for r in gl(conn, s1) if r[0] == "8020"] == [D("-1.00")]
    assert onhand(conn, a) == 3


def test_customer_return_at_original_cost(inv, ic, conn):
    a = mk_part(ic, "A-1", cost="9")
    inv.post_invoice(purchase([(a, 5, "2")]))
    inv.post_invoice(sale([(a, 3, "5")]))
    cn = inv.post_invoice(sale([(a, 1, "5")], d=date(2026, 3, 5), type="credit_invoice"))
    rows = gl(conn, cn)
    assert ("8520", D("-5.00"), date(2026, 3, 5)) in rows            # omzet terug
    assert ("7001", D("-2.00"), date(2026, 3, 5)) in rows            # voorraad terug tegen kostprijs 2
    assert ("8020", D("2.00"), date(2026, 3, 5)) in rows
    assert ("1200", D("6.05"), date(2026, 3, 5)) in rows
    assert onhand(conn, a) == 3
    assert balance(conn, "7001") == D("-6.00")
    # het teruggekomen stuk gaat later weer tegen 2,00 de deur uit (FIFO: eerst de inkoop)
    s3 = inv.post_invoice(sale([(a, 3, "5")], d=date(2026, 3, 6)))
    assert sum(r[1] for r in gl(conn, s3) if r[0] == "8020") == D("-6.00")
    assert balance(conn, "7001") == 0


def test_vendor_return_at_fifo_cost(inv, ic, conn):
    a = mk_part(ic, "A-1")
    inv.post_invoice(purchase([(a, 4, "2")]))
    dn = inv.post_invoice(purchase([(a, 1, "2.50")], d=date(2026, 2, 10), type="debit_invoice"))
    rows = gl(conn, dn)
    assert ("1600", D("-3.03"), date(2026, 2, 10)) in rows
    assert ("7001", D("2.00"), date(2026, 2, 10)) in rows             # voorraad eraf tegen FIFO 2,00
    assert sum(r[1] for r in rows if r[0] == "8020") == D("0.50")     # verschil naar kostprijs
    assert onhand(conn, a) == 3 and balance(conn, "7001") == D("-6.00")
    before = state(conn)
    resave(inv, dn, "vendor")
    same_state(before, state(conn))


def test_assembly_sale_and_credit(inv, ic, conn, config):
    a, b = mk_part(ic, "O-1"), mk_part(ic, "O-2")
    sv = mk_service(ic, "D-1")
    prod = ic.save(Part(kind="assembly", partnumber="P-1", description="Product", unit="st", income_accno="8520",
                        taxaccounts=["1810"], components=[Component(parts_id=a, qty=D(2)), Component(parts_id=b, qty=D(1)),
                                                          Component(parts_id=sv, qty=D(1))]))
    inv.post_invoice(purchase([(a, 10, "1"), (b, 10, "4")]))
    Assemblies(conn, config).build(prod, D(2), transdate=date(2026, 2, 15))
    s1 = inv.post_invoice(sale([(prod, 2, "20")]))
    rows = lines_of(conn, s1)
    assert [(r[0], r[1], r[2], r[4]) for r in rows] == [(a, 4, -4, True), (b, 2, -2, True), (sv, 2, 0, True),
                                                        (prod, 2, 0, False)]
    assert sum(r[1] for r in gl(conn, s1) if r[0] == "8020") == D("-12.00")
    assert onhand(conn, prod) == 0 and onhand(conn, a) == 6
    before = state(conn)
    resave(inv, s1, "customer")
    same_state(before, state(conn))
    cn = inv.post_invoice(sale([(prod, 1, "20")], d=date(2026, 3, 9), type="credit_invoice"))
    assert onhand(conn, prod) == 1
    assert sum(r[1] for r in gl(conn, cn) if r[0] == "7001") == D("-6.00")   # onderdelen terug tegen 2x1 + 4
    assert balance(conn, "7001") == D("-44.00")      # 50 ingekocht - 12 verkocht + 6 retour


def make_legacy(conn, sale_id, purchase_id):
    """Doe alsof de toewijzingen uit Perl komen: geen vastlegging, kostprijsregels met id = inkoopregel."""
    pl = conn.execute("SELECT id FROM invoice WHERE trans_id = %s ORDER BY id LIMIT 1", (purchase_id,)).fetchone()[0]
    conn.execute("DELETE FROM x_cogs_allocation")
    conn.execute("DELETE FROM x_invoice_cost")
    conn.execute("""UPDATE acc_trans SET id = %s WHERE trans_id = %s AND chart_id IN
                    (SELECT id FROM chart WHERE accno IN ('7001', '8020'))""", (pl, sale_id))


def test_legacy_unchanged_resave_keeps_everything(inv, ic, conn):
    a = mk_part(ic, "A-1")
    p1 = inv.post_invoice(purchase([(a, 10, "2")]))
    s1 = inv.post_invoice(sale([(a, 4, "5")]))
    make_legacy(conn, s1, p1)
    before = state(conn)
    resave(inv, s1, "customer")
    same_state(before, state(conn))
    resave(inv, p1, "vendor")       # inkoopregel krijgt een nieuw id; de oude kostprijsregel wijst (zoals in Perl) nog
    after = state(conn)             # naar het oude regelnummer
    after["acc"] = [r[:5] for r in after["acc"]]
    before["acc"] = [r[:5] for r in before["acc"]]
    same_state(before, after)
    # datum van de verkoop wijzigen: Perl-kostprijs op de factuurdatum verhuist mee
    i = inv.get_invoice(s1, "customer")
    i.transdate = date(2026, 3, 2)
    inv.post_invoice(i)
    assert ("8020", D("-8.00"), date(2026, 3, 2)) in gl(conn, s1)


def test_legacy_changed_sale_is_reallocated(inv, ic, conn):
    a = mk_part(ic, "A-1")
    p1 = inv.post_invoice(purchase([(a, 10, "2")]))
    s1 = inv.post_invoice(sale([(a, 4, "5")]))
    make_legacy(conn, s1, p1)
    i = inv.get_invoice(s1, "customer")
    i.lines[0].qty = D(5)
    inv.post_invoice(i)
    assert [r[1] for r in gl(conn, s1) if r[0] == "8020"] == [D("-10.00")]
    assert lines_of(conn, p1)[0][2] == 5 and lines_of(conn, s1)[0][2] == -5
    assert balance(conn, "7001") == D("-10.00")
    assert len(conn.execute("SELECT 1 FROM x_cogs_allocation").fetchall()) == 1


def test_legacy_changed_purchase_reverses_like_perl(inv, ic, conn):
    a = mk_part(ic, "A-1")
    p1 = inv.post_invoice(purchase([(a, 10, "2")]))
    s1 = inv.post_invoice(sale([(a, 4, "5")]))
    make_legacy(conn, s1, p1)
    i = inv.get_invoice(p1, "vendor")
    i.lines[0].sellprice = D(3)
    inv.post_invoice(i)
    rows = gl(conn, s1)
    # oude kostprijs blijft staan, tegenboeking + nieuwe kostprijs (vastgelegd) erbij
    assert sum(r[1] for r in rows if r[0] == "8020") == D("-12.00")
    assert balance(conn, "7001") == D("-18.00")
    assert lines_of(conn, p1)[0][2] == 4 and lines_of(conn, s1)[0][2] == -4
    before = state(conn)
    resave(inv, s1, "customer")
    same_state(before, state(conn))


def test_returned_stock_consumed_then_return_deleted(inv, ic, conn):
    a = mk_part(ic, "A-1", cost="2")
    inv.post_invoice(purchase([(a, 1, "2")]))
    inv.post_invoice(sale([(a, 1, "5")]))
    cn = inv.post_invoice(sale([(a, 1, "5")], d=date(2026, 3, 2), type="credit_invoice"))
    s2 = inv.post_invoice(sale([(a, 1, "5")], d=date(2026, 3, 3)))
    assert [r[1] for r in gl(conn, s2) if r[0] == "8020"] == [D("-2.00")]
    inv.delete_invoice(cn, "customer")
    assert not [r for r in gl(conn, s2) if r[0] == "8020"]
    assert balance(conn, "7001") == 0 and onhand(conn, a) == -1


def test_services_taxes_and_payment(inv, ic, conn):
    sv = mk_service(ic, "D-1", "100")
    s = sale([(sv, 2, "100")])
    s.lines[0].discount = D(10)
    s.payments = [ArapPayment(accno="1100", amount=D("217.80"), datepaid=date(2026, 3, 5), source="BANK-1")]
    tid = inv.post_invoice(s)
    rows = gl(conn, tid)
    assert ("8520", D("180.00"), date(2026, 3, 1)) in rows and ("1810", D("37.80"), date(2026, 3, 1)) in rows
    assert ("1100", D("-217.80"), date(2026, 3, 5)) in rows and ("1200", D("217.80"), date(2026, 3, 5)) in rows
    h = conn.execute("SELECT amount, netamount, paid, datepaid, invoice FROM ar WHERE id = %s", (tid,)).fetchone()
    assert (D(str(h[0])), D(str(h[1])), D(str(h[2])), h[3], h[4]) == (D("217.80"), D("180"), D("217.80"),
                                                                     date(2026, 3, 5), True)
    i = inv.get_invoice(tid, "customer")
    assert i.lines[0].discount == 10 and i.payments[0].source == "BANK-1" and len(i.lines) == 1


def test_taxincluded(inv, ic, conn):
    sv = mk_service(ic, "D-1", "121")
    tid = inv.post_invoice(sale([(sv, 1, "121"), (sv, 1, "10")], taxincluded=True))
    rows = gl(conn, tid)
    assert sum(r[1] for r in rows if r[0] == "8520") == D("108.26")      # 100 + 8,26 (btw per regel)
    assert ("1810", D("22.74"), date(2026, 3, 1)) in rows and ("1200", D("-131.00"), date(2026, 3, 1)) in rows


def test_foreign_currency(inv, ic, conn):
    conn.execute("UPDATE customer SET curr = 'USD' WHERE id = 903")
    a = mk_part(ic, "A-1")
    inv.post_invoice(purchase([(a, 5, "2")]))
    s = sale([(a, 2, "10")], vc_id=903, currency="USD", exchangerate=D("0.9"))
    s.payments = [ArapPayment(accno="1100", amount=D("24.20"), datepaid=date(2026, 3, 9), exchangerate=D("0.95"))]
    tid = inv.post_invoice(s)
    rows = gl(conn, tid)
    assert ("8520", D("18.00"), date(2026, 3, 1)) in rows and ("1200", D("-21.78"), date(2026, 3, 1)) in rows
    assert ("8020", D("-4.00"), date(2026, 3, 1)) in rows                     # kostprijs in standaardvaluta
    assert sum(r[1] for r in rows) == 0
    h = conn.execute("SELECT paid FROM ar WHERE id = %s", (tid,)).fetchone()
    assert D(str(h[0])) == D("21.78")


def test_checks(inv, ic, conn, config):
    a = mk_part(ic, "A-1", checkinventory=True)
    with pytest.raises(ValidationError, match="Not enough inventory"):
        inv.post_invoice(sale([(a, 1, "5")]))
    prod = ic.save(Part(kind="assembly", partnumber="P-1", description="Product", income_accno="8520",
                        components=[Component(parts_id=a, qty=D(1))]))
    with pytest.raises(ValidationError, match="samengesteld"):
        inv.post_invoice(purchase([(prod, 1, "5")]))
    with pytest.raises(ClosedPeriodError):
        inv.post_invoice(purchase([(a, 1, "5")], d=date(2025, 12, 31)))
    p1 = inv.post_invoice(purchase([(a, 2, "5")], d=date(2026, 1, 10)))
    s1 = inv.post_invoice(sale([(a, 2, "9")], d=date(2026, 1, 20)))
    conn.execute("UPDATE defaults SET fldvalue = '20260115' WHERE fldname = 'closedto'")
    # inkoop in open periode wijzigen zou kostprijs in ... (verkoop is open) -> mag
    i = inv.get_invoice(p1, "vendor")
    with pytest.raises(Exception):
        inv.post_invoice(i)                      # inkoop zelf ligt in de afgesloten periode
    i = inv.get_invoice(s1, "customer")
    i.lines[0].qty = D(1)
    inv.post_invoice(i)
    assert onhand(conn, a) == 1


def test_lastcost_avgcost(inv, ic, conn):
    a = mk_part(ic, "A-1", cost="9")
    inv.post_invoice(purchase([(a, 2, "2")], d=date(2026, 1, 5)))
    p2 = inv.post_invoice(purchase([(a, 2, "4")], d=date(2026, 1, 6)))
    r = conn.execute("SELECT lastcost, avgcost FROM parts WHERE id = %s", (a,)).fetchone()
    assert (D(str(r[0])), D(str(r[1]))) == (D(4), D(3))
    inv.delete_invoice(p2, "vendor")
    r = conn.execute("SELECT lastcost, avgcost FROM parts WHERE id = %s", (a,)).fetchone()
    assert (D(str(r[0])), D(str(r[1]))) == (D(2), D(2))


def test_delete_reopens_order(inv, ic, conn):
    sv = mk_service(ic, "D-1")
    conn.execute("INSERT INTO oe (id, ordnumber, transdate, customer_id, closed, quotation) VALUES (7000, 'SO-1', '2026-02-01', 900, false, false)")
    s = sale([(sv, 1, "10")])
    s.order_id = 7000
    tid = inv.post_invoice(s)
    assert conn.execute("SELECT aa_id, closed FROM oe WHERE id = 7000").fetchone() == (tid, True)
    inv.delete_invoice(tid, "customer")
    assert conn.execute("SELECT aa_id, closed FROM oe WHERE id = 7000").fetchone() == (None, False)


def test_cash_discount_with_tax_correction(inv, ic, conn):
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('cdt', '1')")
    conn.execute("""INSERT INTO chart (accno, description, charttype, category, link)
                    VALUES ('8900', 'Betalingskorting', 'A', 'E', 'AR_discount:AP_discount')""")
    sv = mk_service(ic, "D-1", "100")
    s = sale([(sv, 1, "100")], cashdiscount=D(2), discountterms=10)
    s.discount = ArapPayment(accno="8900", amount=D("2.42"), datepaid=date(2026, 3, 5))
    s.payments = [ArapPayment(accno="1100", amount=D("118.58"), datepaid=date(2026, 3, 5))]
    tid = inv.post_invoice(s)
    rows = gl(conn, tid)
    assert sum(r[1] for r in rows) == 0
    assert ("1810", D("-0.42"), date(2026, 3, 5)) in rows             # 2% van 21 btw terug (Perl: 0,51)
    h = conn.execute("SELECT amount, paid FROM ar WHERE id = %s", (tid,)).fetchone()
    assert D(str(h[0])) == D("120.58")                                # factuurbedrag min btw-correctie
    i = inv.get_invoice(tid, "customer")
    assert i.discount and i.discount.amount == D("2.42") and len(i.payments) == 1
    before = state(conn)
    inv.post_invoice(i)
    same_state(before, state(conn))
