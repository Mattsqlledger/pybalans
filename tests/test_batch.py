"""Documenten > Batch: selectie (fase 4 stap 4c, SB9; sqlledger.batch).

Testdata uit sl_template (compare/arap_seed.sql): verkoopfacturen met artikelen 3001, 3002, 3007, 3008
(betaald), verkoop zonder artikelen 950 (betaald), 3003–3006, 3009, 3010; inkoop 951, 4001–4003;
status ``invoice`` voor 3001 (niet geprint) en ``reminder1``/``reminder2`` voor 3006/3007.
"""

from datetime import date

import pytest

from sqlledger import batch
from sqlledger.batch import BatchFilter, Source
from sqlledger.printdata import mark_status

INVOICE = (Source("ar", invoice=True, negative=False),)
TRANS = (Source("ar", invoice=False, negative=False),)


def ids(conn, sources, formname="invoice", **flt):
    rows, _more = batch.find(conn, sources, formname, BatchFilter(**flt))
    return [r.id for r in rows]


def test_kind_and_order(conn):
    assert ids(conn, INVOICE) == [3007, 3002, 3001, 3008]                  # op datum
    assert ids(conn, TRANS, "transaction") == [950, 3006, 3005, 3004, 3003, 3010, 3009]
    assert ids(conn, (Source("ap", invoice=False, negative=False),), "transaction") == [4003, 951, 4002, 4001]
    assert ids(conn, (Source("ar", invoice=True, negative=True),), "credit_invoice") == []


def test_filters(conn):
    assert ids(conn, INVOICE, state="closed") == [3008]
    assert ids(conn, INVOICE, state="open") == [3007, 3002, 3001]
    assert ids(conn, INVOICE, name="cees") == [3007, 3002]
    assert ids(conn, INVOICE, name="K004") == [3008]                        # relatienummer
    assert ids(conn, INVOICE, number="3001") == [3001]
    assert ids(conn, INVOICE, datefrom=date(2026, 6, 1), dateto=date(2026, 6, 25)) == [3002, 3001]
    # inkoop zonder "betaald" (NULL) telt als open
    ap = (Source("ap", invoice=False, negative=False),)
    assert 951 in ids(conn, ap, "transaction", state="open")
    assert 951 not in ids(conn, ap, "transaction", state="closed")


def test_injection_is_just_text(conn):
    assert ids(conn, INVOICE, name="x' OR '1'='1") == []
    assert ids(conn, INVOICE, number="%") == [3007, 3002, 3001, 3008]       # LIKE-teken zoals Form::like


def test_printed_and_emailed(conn, config):
    mark_status(conn, config, 3002, "invoice", printed=True)
    mark_status(conn, config, 3007, "invoice", emailed=True)
    mark_status(conn, config, 3008, "packing_list", printed=True)          # andere soort telt niet
    assert ids(conn, INVOICE, printed="yes") == [3002]
    assert ids(conn, INVOICE, printed="no") == [3007, 3001, 3008]
    assert ids(conn, INVOICE, emailed="yes") == [3007]
    assert ids(conn, INVOICE, printed="no", emailed="no") == [3001, 3008]
    rows, _ = batch.find(conn, INVOICE, "invoice", BatchFilter())
    st = {r.id: (r.printed, r.emailed) for r in rows}
    assert st[3002] == (True, False) and st[3007] == (False, True) and st[3008] == (False, False)
    # reminder-status van 3006/3007 is geen factuurstatus
    assert ids(conn, TRANS, "transaction", printed="yes") == []


def test_row_contents_and_limit(conn):
    rows, more = batch.find(conn, INVOICE, "invoice", BatchFilter(), limit=2)
    assert more and [r.id for r in rows] == [3007, 3002]
    r = rows[1]
    assert (r.number, r.name, r.vcnumber, r.email, str(r.amount), r.curr, r.closed, r.table) == \
        ("F-3002", "Bakkerij Cees", "K002", "cees@example.test", "272.5", "EUR", False, "ar")


def test_orders_and_packing_list_from_two_sources(conn):
    conn.execute("""INSERT INTO oe (id, ordnumber, quonumber, transdate, customer_id, vendor_id, amount, netamount,
                                    curr, quotation, closed)
                    VALUES (5001, 'O-1', '', '2026-06-02', 902, 0, 50, 50, 'EUR', false, false),
                           (5002, 'O-2', '', '2026-06-03', 900, 0, 70, 70, 'EUR', false, true),
                           (5003, '', 'Q-1', '2026-06-04', 902, 0, 80, 80, 'EUR', true, false),
                           (5004, 'P-1', '', '2026-06-05', 0, 905, 90, 90, 'EUR', false, false)""")
    so = (Source("oe", quotation=False, vc="customer"),)
    assert ids(conn, so, "sales_order") == [5001, 5002]
    assert ids(conn, so, "sales_order", state="open") == [5001]
    assert ids(conn, so, "sales_order", state="closed") == [5002]
    assert ids(conn, (Source("oe", quotation=True, vc="customer"),), "sales_quotation") == [5003]
    assert ids(conn, (Source("oe", quotation=False, vc="vendor"),), "purchase_order") == [5004]
    rows, _ = batch.find(conn, (Source("oe", quotation=True, vc="customer"),), "sales_quotation", BatchFilter())
    assert rows[0].number == "Q-1" and rows[0].table == "oe"
    pl = INVOICE + so
    assert ids(conn, pl, "packing_list", datefrom=date(2026, 6, 1)) == [5001, 5002, 3002, 3001, 3008]
    assert ids(conn, pl, "packing_list", number="O-") == [5001, 5002]


def test_bad_filter(conn):
    with pytest.raises(ValueError):
        batch.find(conn, INVOICE, "invoice", BatchFilter(state="x"))
    with pytest.raises(ValueError):
        batch.find(conn, (Source("gl"),), "invoice", BatchFilter())
