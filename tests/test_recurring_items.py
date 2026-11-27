"""Herhalen van facturen met artikelen en orders (sqlledger.recurring, fase 3 R1, RH1–RH6)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import ArapPayment, Part, Recurring, ValidationError
from sqlledger.assembly import Assemblies, Component
from sqlledger.invoice import InvoiceLedger, InvoiceLine
from sqlledger.order import OrderLedger
from sqlledger.recurring import QUOTATION, RecurringTransactions

from test_invoice import CUST, VEND, gl, lines_of, mk_part, mk_service, onhand, purchase, sale
from test_order import order
from test_parts import SETUP

D = Decimal


@pytest.fixture
def ic(conn, config):
    from sqlledger import Parts
    for sql in SETUP + ["DELETE FROM tax WHERE taxnumber IS NULL", "SELECT setval('invoiceid', 1000)"]:
        conn.execute(sql)
    return Parts(conn, config)


@pytest.fixture
def il(conn, config, ic):
    return InvoiceLedger(conn, config)


@pytest.fixture
def ol(conn, config, ic):
    return OrderLedger(conn, config)


@pytest.fixture
def rt(conn, config, ic):
    return RecurringTransactions(conn, config)


def sched(start, howmany=3, **kw):
    return Recurring(startdate=start, repeat=1, unit="months", howmany=howmany, **kw)


def nextdate(conn, tid):
    return conn.execute("SELECT nextdate FROM recurring WHERE id = %s", (tid,)).fetchone()[0]


def ar_row(conn, tid):
    return conn.execute("SELECT invnumber, transdate, duedate, amount, netamount, paid, description FROM ar "
                        "WHERE id = %s", (tid,)).fetchone()


# --- verkoopfactuur ---------------------------------------------------------------------

def test_sale_invoice_copy(rt, il, ic, conn):
    a = mk_part(ic, "A-1")
    s = mk_service(ic, "S-1")
    il.post_invoice(purchase([(a, 10, "2")]))
    inv = sale([(a, 2, "7"), (s, 1, "10")], description="Onderhoud maart", duedate=date(2026, 3, 15), terms=14)
    inv.lines[0].discount = D(10)
    inv.lines[0].deliverydate = date(2026, 3, 5)
    s1 = il.post_invoice(inv)
    rt.save(s1, sched(date(2026, 3, 1)))
    assert nextdate(conn, s1) == date(2026, 4, 1)
    conn.execute("UPDATE parts SET sellprice = 99 WHERE id = %s", (a,))           # RH1: prijs van het origineel
    (r,) = rt.process([s1])
    assert r.ok, r.reason
    assert r.transdate == date(2026, 4, 1) and r.nextdate == date(2026, 5, 1) and r.number != ar_row(conn, s1)[0]
    h = ar_row(conn, r.trans_id)
    assert (h[1], h[2], D(str(h[4])), h[6]) == (date(2026, 4, 1), date(2026, 4, 15), D("22.60"), "Onderhoud maart")
    rows = conn.execute("""SELECT parts_id, qty, fxsellprice, discount, deliverydate FROM invoice
                           WHERE trans_id = %s ORDER BY id""", (r.trans_id,)).fetchall()
    assert [(p, D(str(q)), D(str(f)), d) for p, q, f, _, d in rows] == [
        (a, 2, D(7), date(2026, 4, 5)), (s, 1, D(10), None)]                     # RH4 leverdatum mee
    assert onhand(conn, a) == 6
    assert ("8020", D("-4.00"), date(2026, 4, 1)) in gl(conn, r.trans_id)       # FIFO-kostprijs
    assert conn.execute("SELECT count(*) FROM recurring WHERE id = %s", (r.trans_id,)).fetchone()[0] == 0


def test_sale_invoice_tax_as_new_invoice(rt, il, ic, conn):
    """RH2: btw zoals een nieuwe factuur op de nieuwe datum (btw-rekening die de klant niet meer heeft telt niet)."""
    s = mk_service(ic, "S-1")
    s1 = il.post_invoice(sale([(s, 1, "100")]))
    assert ar_row(conn, s1)[3] == 121
    conn.execute("DELETE FROM customertax WHERE customer_id = %s", (CUST,))
    rt.save(s1, sched(date(2026, 3, 1)))
    (r,) = rt.process([s1])
    assert r.ok, r.reason
    assert ar_row(conn, r.trans_id)[3] == 100
    il.post_invoice(il.get_invoice(s1, "customer"))                     # het origineel houdt zijn btw
    assert ar_row(conn, s1)[3] == 121


def test_not_enough_inventory_skips(rt, il, ic, conn):
    """RH3: verkoopkopie zonder voorraad (voorraad controleren) wordt overgeslagen, datum blijft staan."""
    a = mk_part(ic, "A-1", checkinventory=True)
    il.post_invoice(purchase([(a, 2, "2")]))
    s1 = il.post_invoice(sale([(a, 2, "5")]))
    rt.save(s1, sched(date(2026, 3, 1)))
    n = conn.execute("SELECT count(*) FROM ar").fetchone()[0]
    (r,) = rt.process([s1])
    assert not r.ok and r.reason == "Not enough inventory in Row 1"
    assert nextdate(conn, s1) == date(2026, 4, 1)
    assert conn.execute("SELECT count(*) FROM ar").fetchone()[0] == n and onhand(conn, a) == 0
    il.post_invoice(purchase([(a, 5, "2")], d=date(2026, 3, 20)))
    (r,) = rt.process([s1])
    assert r.ok and onhand(conn, a) == 3


def test_assembly_sale_copy(rt, il, ic, conn, config):
    a = mk_part(ic, "O-1")
    prod = ic.save(Part(kind="assembly", partnumber="P-1", description="Product", unit="st", income_accno="8520",
                        taxaccounts=["1810"], components=[Component(parts_id=a, qty=D(2))]))
    il.post_invoice(purchase([(a, 10, "1")]))
    Assemblies(conn, config).build(prod, D(3), transdate=date(2026, 2, 15))
    s1 = il.post_invoice(sale([(prod, 1, "20")]))
    rt.save(s1, sched(date(2026, 3, 1)))
    (r,) = rt.process([s1])
    assert r.ok, r.reason
    assert [(x[0], x[1], x[4]) for x in lines_of(conn, r.trans_id)] == [(a, 2, True), (prod, 1, False)]
    assert onhand(conn, prod) == 1
    assert sum(x[1] for x in gl(conn, r.trans_id) if x[0] == "8020") == D("-2.00")


# --- inkoopfactuur ----------------------------------------------------------------------

def test_purchase_invoice_copy_with_payment(rt, il, ic, conn):
    a = mk_part(ic, "A-1")
    p = purchase([(a, 4, "2.5")], d=date(2026, 2, 1), duedate=date(2026, 3, 3))
    p.payments = [ArapPayment(accno="1100", amount=D("12.10"), datepaid=date(2026, 2, 10), source="BANK")]
    p1 = il.post_invoice(p)
    rt.save(p1, sched(date(2026, 2, 1), payment=True, reference="INK-REC"))
    (r,) = rt.process([p1])
    assert r.ok, r.reason
    h = conn.execute("SELECT invnumber, transdate, duedate, paid, datepaid FROM ap WHERE id = %s",
                     (r.trans_id,)).fetchone()
    assert h == ("INK-REC", date(2026, 3, 1), date(2026, 3, 31), 12.1, date(2026, 3, 10))
    assert onhand(conn, a) == 8
    assert ("1100", D("12.10"), date(2026, 3, 10)) in gl(conn, r.trans_id)


def test_foreign_invoice_needs_rate(rt, il, ic, conn):
    conn.execute("UPDATE customer SET curr = 'USD' WHERE id = 903")
    s = mk_service(ic, "S-1")
    s1 = il.post_invoice(sale([(s, 1, "10")], vc_id=903, currency="USD", exchangerate=D("0.9")))
    rt.save(s1, sched(date(2026, 3, 1)))
    (r,) = rt.process([s1])
    assert not r.ok and r.reason.startswith("Exchange rate missing for USD")
    conn.execute("INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2026-04-01', 0.8)")
    (r,) = rt.process([s1])
    assert r.ok and ("8520", D("8.00"), date(2026, 4, 1)) in gl(conn, r.trans_id)


def test_invoice_from_order_copy_not_linked(rt, il, ol, ic, conn):
    """RH6: een factuur uit een order wordt gekopieerd zonder koppeling; de order blijft zoals hij is."""
    s = mk_service(ic, "S-1")
    oid = ol.save_order(order([(s, 2, "10")]))
    inv = ol.to_invoice(oid, today=date(2026, 3, 2))
    s1 = il.post_invoice(inv)
    rt.save(s1, sched(date(2026, 3, 2)))
    links = conn.execute("SELECT count(*) FROM x_oe_link").fetchone()[0]
    (r,) = rt.process([s1])
    assert r.ok, r.reason
    assert conn.execute("SELECT aa_id, closed FROM oe WHERE id = %s", (oid,)).fetchone() == (s1, True)
    assert conn.execute("SELECT count(*) FROM x_oe_link").fetchone()[0] == links
    assert conn.execute("SELECT count(*) FROM oe").fetchone()[0] == 1           # geen nabestelling


# --- orders -----------------------------------------------------------------------------

def test_order_copy(rt, ol, ic, conn):
    a = mk_part(ic, "A-1")
    o = order([(a, 5, "7", {"reqdate": date(2026, 3, 20), "discount": D(5)})], reqdate=date(2026, 3, 15),
              description="Wekelijkse levering")
    oid = ol.save_order(o)
    # origineel: deels geleverd, nabestelling en gesloten (RH5: de kopie neemt dat niet over)
    conn.execute("UPDATE orderitems SET ship = 3 WHERE trans_id = %s", (oid,))
    conn.execute("UPDATE oe SET closed = true, backorder = true WHERE id = %s", (oid,))
    rt.save(oid, sched(date(2026, 3, 1), payment=True, reference="SO-REC"))
    assert not rt.get(oid).payment                                                  # orders: geen betaling
    conn.execute("UPDATE parts SET sellprice = 99 WHERE id = %s", (a,))
    (r,) = rt.process([oid])
    assert r.ok, r.reason
    assert r.number == "SO-REC" and r.module == "oe"
    h = conn.execute("""SELECT ordnumber, transdate, reqdate, closed, backorder, aa_id, quotation, customer_id,
                               description, netamount FROM oe WHERE id = %s""", (r.trans_id,)).fetchone()
    assert h == ("SO-REC", date(2026, 4, 1), date(2026, 4, 15), False, False, None, False, CUST,
                 "Wekelijkse levering", 33.25)
    rows = conn.execute("SELECT parts_id, qty, sellprice, discount, ship, reqdate FROM orderitems WHERE trans_id = %s",
                        (r.trans_id,)).fetchall()
    assert rows == [(a, 5, 7, 0.05, 0, date(2026, 4, 20))]
    assert onhand(conn, a) == 0                                                     # orders: geen voorraad
    assert conn.execute("SELECT count(*) FROM x_oe_link").fetchone()[0] == 0
    (r2,) = rt.process([oid])
    assert r2.ok and r2.number != "SO-REC"                                          # T4: kenmerk bestaat al


def test_purchase_order_copy(rt, ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 3, "2")], type="purchase_order"))
    rt.save(oid, sched(date(2026, 3, 1)))
    (r,) = rt.process([oid])
    assert r.ok, r.reason
    assert conn.execute("SELECT vendor_id, customer_id, quotation FROM oe WHERE id = %s",
                        (r.trans_id,)).fetchone() == (VEND, 0, False)


def test_quotation_not_scheduled(rt, ol, ic, conn):
    s = mk_service(ic, "S-1")
    qid = ol.save_order(order([(s, 1, "10")], type="sales_quotation"))
    with pytest.raises(ValidationError, match="Quotations"):
        rt.save(qid, sched(date(2026, 3, 1)))
    # schema uit Perl-data op een offerte: niet in de lijst, verwerken weigert
    conn.execute("""INSERT INTO recurring (id, reference, startdate, nextdate, enddate, repeat, unit, howmany, payment)
                    VALUES (%s, NULL, '2026-03-01', '2026-04-01', '2026-05-01', 1, 'months', 3, false)""", (qid,))
    assert qid not in {x.id for x in rt.list()}
    (r,) = rt.process([qid])
    assert not r.ok and r.reason == QUOTATION
