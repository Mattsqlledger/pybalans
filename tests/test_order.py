"""Orders en offertes (sqlledger.order, fase 3 O1–O6, ON1–ON4, SA11)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import Part, Parts, ValidationError
from sqlledger.errors import ClosedPeriodError, LockedTransactionError
from sqlledger.invoice import InvoiceLedger
from sqlledger.order import Order, OrderLedger, OrderLine

from test_invoice import CUST, VEND, mk_part, mk_service, onhand, purchase
from test_parts import SETUP

D = Decimal


@pytest.fixture
def ic(conn, config):
    for sql in SETUP + ["DELETE FROM tax WHERE taxnumber IS NULL", "SELECT setval('invoiceid', 1000)"]:
        conn.execute(sql)
    return Parts(conn, config)


@pytest.fixture
def ol(conn, config, ic):
    return OrderLedger(conn, config)


@pytest.fixture
def il(conn, config, ic):
    return InvoiceLedger(conn, config)


def order(lines, type="sales_order", d=date(2026, 3, 1), **kw):
    vc_id = kw.pop("vc_id", CUST if type.startswith("sales") else VEND)
    return Order(type=type, vc_id=vc_id, transdate=d,
                 lines=[OrderLine(parts_id=p, qty=D(q), sellprice=D(pr), **(x[0] if x else {}))
                        for p, q, pr, *x in lines], **kw)


def oe_row(conn, oid):
    return conn.execute("""SELECT ordnumber, quonumber, amount, netamount, closed, quotation, aa_id, backorder,
                                  customer_id, vendor_id, transdate FROM oe WHERE id = %s""", (oid,)).fetchone()


def items(conn, oid):
    return conn.execute("""SELECT parts_id, qty, sellprice, discount, ship, description FROM orderitems
                           WHERE trans_id = %s ORDER BY id""", (oid,)).fetchall()


def snapshot(conn, oid):
    h = conn.execute("SELECT * FROM oe WHERE id = %s", (oid,)).fetchone()
    return h, items(conn, oid), conn.execute("SELECT count(*) FROM dpt_trans WHERE trans_id = %s", (oid,)).fetchone()


def test_save_sales_order_amounts_no_stock(ol, ic, conn):
    a = mk_part(ic, "A-1")
    s = mk_service(ic, "S-1")
    oid = ol.save_order(order([(a, 3, "5"), (s, 1, "10", {"discount": D(10)})]))
    r = oe_row(conn, oid)
    # 15 + 9 = 24 netto, btw 21% = 5,04
    assert (r[2], r[3]) == (29.04, 24.0)
    assert r[0] == "1" and not r[5] and r[8] == CUST and r[9] == 0
    assert onhand(conn, a) == 0                       # O3: voorraad beweegt niet door orders
    assert [x[4] for x in items(conn, oid)] == [0, 0]
    o = ol.get_order(oid)
    assert o.type == "sales_order" and o.number == "1" and [l.discount for l in o.lines] == [0, 10]
    calc = ol.calculate(o)
    assert calc.taxes == {"1810": D("5.04")} and calc.amount == D("29.04")


def test_resave_unchanged_identical(ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, "2.5", "4.12345", {"discount": D("7.5"), "serialnumber": "X"})],
                              taxincluded=True, notes="n", reqdate=date(2026, 4, 1), ponumber="PO-9"))
    before = snapshot(conn, oid)
    o = ol.get_order(oid, lock=True)
    ol.save_order(o)
    after = snapshot(conn, oid)
    assert before[0] == after[0] and before[1] == after[1] and before[2] == after[2]


def test_taxincluded_like_oe_pm(ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 3, "12.10")], taxincluded=True))
    # OE.pm: btw = 36,30 * 0,21 / 1,21 = 6,30; netto 36,30 - 6,30
    assert oe_row(conn, oid)[2:4] == (36.3, 30.0)


def test_quotation_to_order_closes_on_save(ol, ic, conn):
    a = mk_part(ic, "A-1")
    q = ol.save_order(order([(a, 2, "5")], type="sales_quotation"))
    assert oe_row(conn, q)[1] == "1" and oe_row(conn, q)[5]
    o = ol.convert(q, "sales_order", today=date(2026, 3, 5))
    assert o.source_id == q and o.quonumber == "1" and o.ordnumber is None and o.transdate == date(2026, 3, 5)
    assert not oe_row(conn, q)[4]                      # nog niet gesloten: order nog niet opgeslagen
    oid = ol.save_order(o)
    assert oe_row(conn, q)[4] and oe_row(conn, oid)[1] == "1"
    assert ol.links(oid)["from"][0]["from_id"] == q
    # tweede keer omzetten kan niet meer
    with pytest.raises(ValidationError):
        ol.convert(q, "sales_order")
    # order verwijderen: offerte weer open
    ol.delete_order(oid)
    assert not oe_row(conn, q)[4]


def test_order_to_quotation_keeps_order_open(ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 2, "5")]))
    q = ol.save_order(ol.convert(oid, "sales_quotation"))
    assert not oe_row(conn, oid)[4] and oe_row(conn, q)[5]


def test_order_to_invoice_full(ol, il, ic, conn):
    a = mk_part(ic, "A-1")
    il.post_invoice(purchase([(a, 10, "2")]))
    oid = ol.save_order(order([(a, 4, "5")]))
    inv = ol.to_invoice(oid, today=date(2026, 3, 10))
    assert inv.order_id == oid and inv.ordnumber == "1" and inv.lines[0].orderitems_id
    assert not oe_row(conn, oid)[4]                    # pas dicht bij boeken
    tid = il.post_invoice(inv)
    r = oe_row(conn, oid)
    assert r[4] and r[6] == tid
    assert [x[4] for x in items(conn, oid)] == [4]
    assert onhand(conn, a) == 6                        # voorraad via de factuur
    assert conn.execute("SELECT count(*) FROM oe WHERE backorder").fetchone()[0] == 0
    with pytest.raises(LockedTransactionError):
        ol.save_order(ol.get_order(oid))
    with pytest.raises(LockedTransactionError):
        ol.delete_order(oid)
    with pytest.raises(LockedTransactionError):
        ol.reopen_order(oid)


def test_partial_delivery_backorder_and_delete_invoice(ol, il, ic, conn):
    a = mk_part(ic, "A-1")
    b = mk_part(ic, "B-1")
    oid = ol.save_order(order([(a, 5, "5"), (b, 2, "3")], shipto={"name": "Elders"}))
    inv = ol.to_invoice(oid)
    inv.lines[0].qty = D(3)                            # 3 van de 5 geleverd
    inv.lines = inv.lines[:1]                          # B nog niet
    tid = il.post_invoice(inv)
    assert [x[4] for x in items(conn, oid)] == [3, 0]
    bo = conn.execute("SELECT id FROM oe WHERE backorder").fetchone()[0]
    r = oe_row(conn, bo)
    assert r[0] == "1" and not r[4] and r[7] and r[10] == date(2026, 3, 1)
    assert [(x[0], x[1], x[4]) for x in items(conn, bo)] == [(a, 2, 0), (b, 2, 0)]
    assert (r[2], r[3]) == (19.36, 16.0)
    assert conn.execute("SELECT shiptoname FROM shipto WHERE trans_id = %s", (bo,)).fetchone()[0] == "Elders"
    # factuur verwijderen: order weer open, verzonden 0, onaangeroerde nabestelling weg (ON3)
    msgs = il.delete_invoice(tid, "customer")
    assert msgs == []
    r = oe_row(conn, oid)
    assert not r[4] and r[6] is None and [x[4] for x in items(conn, oid)] == [0, 0]
    assert conn.execute("SELECT count(*) FROM oe WHERE id = %s", (bo,)).fetchone()[0] == 0


def test_changed_backorder_kept(ol, il, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 5, "5")]))
    inv = ol.to_invoice(oid)
    inv.lines[0].qty = D(1)
    tid = il.post_invoice(inv)
    bo = conn.execute("SELECT id FROM oe WHERE backorder").fetchone()[0]
    o = ol.get_order(bo)
    o.lines[0].qty = D(3)
    ol.save_order(o)
    msgs = il.delete_invoice(tid, "customer")
    assert len(msgs) == 1 and conn.execute("SELECT count(*) FROM oe WHERE id = %s", (bo,)).fetchone()[0] == 1


def test_invoice_more_than_ordered_and_extra_line(ol, il, ic, conn):
    a = mk_part(ic, "A-1")
    s = mk_service(ic, "S-1")
    oid = ol.save_order(order([(a, 2, "5")]))
    inv = ol.to_invoice(oid)
    inv.lines[0].qty = D(3)
    inv.lines.append(type(inv.lines[0])(parts_id=s, qty=D(1), sellprice=D(10)))
    il.post_invoice(inv)
    assert [x[4] for x in items(conn, oid)] == [2]
    assert conn.execute("SELECT count(*) FROM oe WHERE backorder").fetchone()[0] == 0


def test_closed_order_readonly_and_reopen(ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 2, "5")], closed=True))
    with pytest.raises(LockedTransactionError):
        ol.save_order(ol.get_order(oid))
    with pytest.raises(LockedTransactionError):
        ol.delete_order(oid)
    ol.reopen_order(oid)
    assert not oe_row(conn, oid)[4]
    ol.save_order(ol.get_order(oid))
    # _force (alleen de echte-datatoets) mag wel
    conn.execute("UPDATE oe SET closed = true WHERE id = %s", (oid,))
    ol.save_order(ol.get_order(oid), _force=True)


def test_closed_period(ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 2, "5")], d=date(2025, 6, 1)))
    conn.execute("UPDATE defaults SET fldvalue = '2025-12-31' WHERE fldname = 'closedto'")
    if not conn.execute("SELECT 1 FROM defaults WHERE fldname = 'closedto'").fetchone():
        conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('closedto', '2025-12-31')")
    o = ol.get_order(oid)
    assert o.locked
    with pytest.raises(ClosedPeriodError):
        ol.save_order(o)


def test_quotation_to_invoice_direct(ol, il, ic, conn):
    a = mk_part(ic, "A-1")
    q = ol.save_order(order([(a, 2, "5")], type="sales_quotation"))
    inv = ol.to_invoice(q)
    tid = il.post_invoice(inv)
    r = oe_row(conn, q)
    assert r[4] and r[6] is None                       # offerte dicht; aa_id alleen bij orders (zoals Perl)
    assert ol.links(q)["to"][0]["invnumber"]
    il.delete_invoice(tid, "customer")
    assert not oe_row(conn, q)[4]


def test_purchase_order_to_vendor_invoice(ol, il, ic, conn):
    a = mk_part(ic, "A-1")
    rfq = ol.save_order(order([(a, 10, "2")], type="request_quotation"))
    po = ol.save_order(ol.convert(rfq, "purchase_order"))
    assert oe_row(conn, rfq)[4] and oe_row(conn, po)[9] == VEND
    inv = ol.to_invoice(po)
    inv.lines[0].qty = D(6)
    tid = il.post_invoice(inv)
    assert onhand(conn, a) == 6
    assert oe_row(conn, po)[6] == tid and [x[4] for x in items(conn, po)] == [6]
    bo = conn.execute("SELECT id FROM oe WHERE backorder").fetchone()[0]
    assert oe_row(conn, bo)[9] == VEND and [x[1] for x in items(conn, bo)] == [4]


def test_assembly_allowed_on_sales_order_not_purchase(ol, ic, conn):
    from sqlledger.assembly import Component
    a = mk_part(ic, "A-1")
    p = ic.save(Part(kind="assembly", partnumber="P-1", description="Pakket", income_accno="8520",
                     taxaccounts=["1810"], components=[Component(parts_id=a, qty=D(2))]))
    oid = ol.save_order(order([(p, 1, "12")]))
    assert items(conn, oid)[0][0] == p and onhand(conn, a) == 0
    with pytest.raises(ValidationError):
        ol.save_order(order([(p, 1, "12")], type="purchase_order"))


def test_list_orders(ol, ic, conn):
    a = mk_part(ic, "A-1")
    o1 = ol.save_order(order([(a, 1, "5")], d=date(2026, 1, 5)))
    o2 = ol.save_order(order([(a, 2, "5")], d=date(2026, 2, 5), closed=True))
    ol.save_order(order([(a, 1, "5")], type="sales_quotation"))
    assert [r["id"] for r in ol.list_orders("sales_order")] == [o1]
    assert [r["id"] for r in ol.list_orders("sales_order", open=True, closed=True)] == [o1, o2]
    assert [r["id"] for r in ol.list_orders("sales_order", closed=True, open=False)] == [o2]
    assert [r["id"] for r in ol.list_orders("sales_order", number="2")] == [o2]
    rows = ol.list_orders("sales_order", datefrom=date(2026, 2, 1), open=True, closed=True)
    assert [r["id"] for r in rows] == [o2] and rows[0]["tax"] == D("2.1")
    assert len(ol.list_orders("sales_quotation")) == 1


def test_type_cannot_change_and_validation(ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 1, "5")]))
    o = ol.get_order(oid)
    o.type = "sales_quotation"
    with pytest.raises(ValidationError):
        ol.save_order(o)
    with pytest.raises(ValidationError):
        ol.save_order(order([(a, 1, "5", {"discount": D(120)})]))
    with pytest.raises(ValidationError):
        ol.save_order(order([(a, 1, "5")], vc_id=0))


def test_prepayment_refused(ol, ic, conn):
    a = mk_part(ic, "A-1")
    oid = ol.save_order(order([(a, 1, "5")]))
    conn.execute("INSERT INTO acc_trans (trans_id, chart_id, amount, transdate) "
                 "SELECT %s, id, 1, '2026-03-01' FROM chart WHERE accno = '1100'", (oid,))
    assert ol.get_order(oid).prepaid
    with pytest.raises(ValidationError):
        ol.save_order(ol.get_order(oid))


def test_converted_quotation_cannot_be_reopened(ol, il, ic, conn):
    """F30 (golf 4): een offerte met een vervolg (factuur of order) blijft dicht; zonder vervolg weer open."""
    a = mk_part(ic, "A-1")
    q = ol.save_order(order([(a, 2, "5")], type="sales_quotation"))
    tid = il.post_invoice(ol.to_invoice(q))
    with pytest.raises(LockedTransactionError, match="Quotation has been converted"):
        ol.reopen_order(q)
    assert oe_row(conn, q)[4]
    il.delete_invoice(tid, "customer")             # vervolg weg: offerte vanzelf weer open
    assert not oe_row(conn, q)[4]
    q2 = ol.save_order(order([(a, 1, "5")], type="sales_quotation"))
    ol.save_order(ol.convert(q2, "sales_order"))
    with pytest.raises(LockedTransactionError):
        ol.reopen_order(q2)
    q3 = ol.save_order(order([(a, 1, "5")], type="sales_quotation", closed=True))
    ol.reopen_order(q3)                            # met de hand gesloten, geen vervolg: mag
    assert not oe_row(conn, q3)[4]
