"""Betalingen en ontvangsten (sqlledger.payments; testdata uit compare/arap_seed.sql)."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger import ClosedPeriodError, NotFoundError, ValidationError
from sqlledger.payments import Payment, PaymentLine, Payments


@pytest.fixture
def pm(conn, config):
    conn.execute("UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL")
    conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
    return Payments(conn, config)


def balance(conn, trans_id):
    """Som van alle journaalregels van een boeking (moet 0 zijn)."""
    return D(str(conn.execute("SELECT coalesce(round(sum(amount)::numeric, 6), 0) FROM acc_trans "
                              "WHERE trans_id = %s", (trans_id,)).fetchone()[0]))


def arap_balance(conn, trans_id, accno):
    return D(str(conn.execute("""SELECT coalesce(round(sum(a.amount)::numeric, 6), 0) FROM acc_trans a
                                 JOIN chart c ON c.id = a.chart_id WHERE a.trans_id = %s AND c.accno = %s""",
                              (trans_id, accno)).fetchone()[0]))


def test_open_invoices_and_suggestions(pm):
    invs = pm.open_invoices("customer", 900, datepaid=date(2026, 7, 1))
    assert [i.invnumber for i in invs] == ["F-3006", "F-3009", "F-3001"]   # op datum, nummer
    f3006 = next(i for i in invs if i.invnumber == "F-3006")
    assert f3006.due == D("263.00") and f3006.paid_suggested == D("263.00")
    assert not f3006.calcdiscount
    # niet goedgekeurd (3010) en volledig betaald (3005) staan er niet in
    assert {i.id for i in invs}.isdisjoint({3005, 3010})


def test_open_vc(pm):
    names = [r["name"] for r in pm.open_vc("customer")]
    assert "Klant A" in names and len(names) == len(set(names))


def test_post_payment_multiple_invoices(pm, conn):
    res = pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                                  source="R-1", lines=[PaymentLine(3001, D("121")), PaymentLine(3006, D("100"))]))
    assert res.trans_ids == [3001, 3006] and res.overpayment_id is None and res.source == "R-1"
    row = conn.execute("SELECT paid, datepaid FROM ar WHERE id = 3001").fetchone()
    assert D(str(row[0])) == D("121") and row[1] == date(2026, 7, 1)
    assert D(str(conn.execute("SELECT paid FROM ar WHERE id = 3006").fetchone()[0])) == D("200")
    for tid in (3001, 3006):
        assert balance(conn, tid) == 0
    assert arap_balance(conn, 3001, "1200") == 0
    # volgende voorgestelde kenmerk
    assert pm.next_source("customer", "1100") == "R-2"


def test_overpayment_creates_deposit(pm, conn):
    res = pm.post_payment(Payment(vc="vendor", vc_id=905, datepaid=date(2026, 7, 1), payment_accno="1100",
                                  source="1001", amount=D("1300"), arap_accno="1600",
                                  lines=[PaymentLine(4001, D("1210"))]))
    dep = res.overpayment_id
    row = conn.execute("SELECT amount, paid, vendor_id, transdate FROM ap WHERE id = %s", (dep,)).fetchone()
    assert D(str(row[0])) == 0 and D(str(row[1])) == D("90") and row[2] == 905
    assert balance(conn, dep) == 0
    assert arap_balance(conn, dep, "1600") == D("-90")    # crediteur in debet: vooruitbetaald


def test_cash_discount_with_tax_correction(pm, conn):
    conn.execute("INSERT INTO chart (accno, description, charttype, category, link) "
                 "VALUES ('8590', 'Korting', 'A', 'E', 'AR_discount')")
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('cdt', '1')")
    conn.execute("UPDATE tax SET validto = '2026-01-01' WHERE rate IN (0.19, 0.06)")
    conn.execute("UPDATE ar SET cashdiscount = 0.02, discountterms = 10 WHERE id = 3001")
    inv = next(i for i in pm.open_invoices("customer", 900, datepaid=date(2026, 7, 1)) if i.id == 3001)
    assert inv.calcdiscount and inv.discount == D("2.00") and inv.paid_suggested == D("119.00")
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                            discount_accno="8590", lines=[PaymentLine(3001, D("118.58"), D("2.00"))]))
    amount, paid = (D(str(v)) for v in conn.execute("SELECT amount, paid FROM ar WHERE id = 3001").fetchone())
    # btw over de korting (21% van 2,00) verlaagt het factuurbedrag; volledig voldaan
    assert amount == D("120.58") and paid == D("120.58")
    assert balance(conn, 3001) == 0
    assert arap_balance(conn, 3001, "1200") == 0
    tax = conn.execute("""SELECT a.amount FROM acc_trans a JOIN chart c ON c.id = a.chart_id
                          WHERE a.trans_id = 3001 AND c.accno = '1810' AND a.transdate = '2026-07-01'""").fetchone()
    assert D(str(tax[0])) == D("-0.42")


def test_foreign_currency_gain(pm, conn):
    # USD 500 tegen 1,10 = EUR 550
    conn.execute("UPDATE ar SET exchangerate = 1.10, amount = 550, netamount = 550 WHERE id = 3003")
    conn.execute("UPDATE acc_trans SET amount = amount * 1.1 WHERE trans_id = 3003")
    pm.post_payment(Payment(vc="customer", vc_id=903, datepaid=date(2026, 6, 30), payment_accno="1100",
                            currency="USD", exchangerate=D("1.25"), lines=[PaymentLine(3003, D("500"))]))
    assert balance(conn, 3003) == 0
    gain = conn.execute("SELECT amount FROM acc_trans WHERE trans_id = 3003 AND chart_id = 10195").fetchone()
    assert D(str(gain[0])) == D("75")          # 500 x (1,25 - 1,10) koerswinst
    assert D(str(conn.execute("SELECT paid FROM ar WHERE id = 3003").fetchone()[0])) == D("550")


def test_validations(pm):
    base = dict(vc="customer", vc_id=900, payment_accno="1100")
    with pytest.raises(ClosedPeriodError):
        pm.post_payment(Payment(datepaid=date(2025, 12, 31), lines=[PaymentLine(3001, D("1"))], **base))
    with pytest.raises(ValidationError, match="Exchange rate"):
        pm.post_payment(Payment(vc="customer", vc_id=903, payment_accno="1100", datepaid=date(2026, 7, 1),
                                currency="USD", lines=[PaymentLine(3003, D("1"))]))
    with pytest.raises(ValidationError, match="less than"):
        pm.post_payment(Payment(datepaid=date(2026, 7, 1), amount=D("10"), lines=[PaymentLine(3001, D("121"))],
                                **base))
    with pytest.raises(ValidationError, match="another customer"):
        pm.post_payment(Payment(datepaid=date(2026, 7, 1), lines=[PaymentLine(3002, D("1"))], **base))
    with pytest.raises(ValidationError, match="not linked"):
        pm.post_payment(Payment(vc="customer", vc_id=900, payment_accno="1200", datepaid=date(2026, 7, 1),
                                lines=[PaymentLine(3001, D("1"))]))
    with pytest.raises(ValidationError, match="discount account"):
        pm.post_payment(Payment(datepaid=date(2026, 7, 1), lines=[PaymentLine(3001, D("100"), D("2"))], **base))
    with pytest.raises(NotFoundError):
        pm.post_payment(Payment(datepaid=date(2026, 7, 1), lines=[PaymentLine(99999, D("1"))], **base))


def test_failed_payment_leaves_nothing(pm, conn):
    before = conn.execute("SELECT count(*) FROM acc_trans").fetchone()[0]
    with pytest.raises(ValidationError):
        pm.post_payment(Payment(vc="customer", vc_id=900, payment_accno="1100", datepaid=date(2026, 7, 1),
                                lines=[PaymentLine(3001, D("121")), PaymentLine(3002, D("1"))]))
    assert conn.execute("SELECT count(*) FROM acc_trans").fetchone()[0] == before
    assert conn.execute("SELECT fldvalue FROM defaults WHERE fldname = 'receipt_1100'").fetchone() is None


def test_credit_note_offset_in_one_payment(pm, conn):
    # creditnota van 21,00 voor klant 900, verrekend met F-3001 (121,00): betaling 100,00
    conn.execute("""INSERT INTO ar (id, invnumber, transdate, duedate, customer_id, netamount, amount, paid,
                                    curr, approved, exchangerate)
                    VALUES (3100, 'C-3100', '2026-06-26', '2026-06-26', 900, -17.36, -21, 0, 'EUR', true, 1)""")
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate)
                    SELECT 3100, id, 21, date '2026-06-26' FROM chart WHERE accno = '1200'
                    UNION ALL SELECT 3100, id, -21, date '2026-06-26' FROM chart WHERE accno = '8520'""")
    inv = {i.id: i for i in pm.open_invoices("customer", 900, datepaid=date(2026, 7, 1))}
    assert inv[3100].due == D("-21.00")
    res = pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                                  lines=[PaymentLine(3001, D("121")), PaymentLine(3100, D("-21"))]))
    assert res.overpayment_id is None
    for tid in (3001, 3100):
        amount, paid = conn.execute("SELECT amount, paid FROM ar WHERE id = %s", (tid,)).fetchone()
        assert amount == paid and balance(conn, tid) == 0
    bank = conn.execute("""SELECT round(sum(a.amount)::numeric, 2) FROM acc_trans a JOIN chart c ON c.id = a.chart_id
                           WHERE c.accno = '1100' AND a.transdate = '2026-07-01'""").fetchone()[0]
    assert bank == D("-100.00")


# ------------------------------------------------------------ storneren / heruitgeven

def snapshot(conn, tid):
    a = conn.execute("SELECT amount, paid, datepaid FROM ar WHERE id = %s", (tid,)).fetchone()
    return D(str(a[0])), D(str(a[1])), a[2]


def per_date_balance(conn, tid):
    return {r[0]: D(str(r[1])) for r in conn.execute(
        "SELECT transdate, round(sum(amount)::numeric, 6) FROM acc_trans WHERE trans_id = %s GROUP BY 1", (tid,))}


def test_void_restores_invoice(pm, conn):
    before = snapshot(conn, 3006)          # deelbetaling 100 op 20-04 (BANK-306)
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                            source="R-9", lines=[PaymentLine(3001, D("121")), PaymentLine(3006, D("263"))]))
    assert pm.void_payment("customer", "1100", "R-9", voiddate=date(2026, 7, 10)) == [3001, 3006]
    assert snapshot(conn, 3006) == before
    assert snapshot(conn, 3001) == (D("121"), D("0"), None)
    for tid in (3001, 3006):
        assert all(v == 0 for v in per_date_balance(conn, tid).values())
        assert arap_balance(conn, tid, "1200") == -(snapshot(conn, tid)[0] - snapshot(conn, tid)[1])
    # weer open, en niet nog eens te storneren
    assert {i.id for i in pm.open_invoices("customer", 900, datepaid=date(2026, 7, 10))} >= {3001, 3006}
    with pytest.raises(NotFoundError):
        pm.void_payment("customer", "1100", "R-9")


def test_void_with_discount_and_tax_correction(pm, conn):
    conn.execute("INSERT INTO chart (accno, description, charttype, category, link) "
                 "VALUES ('8590', 'Korting', 'A', 'E', 'AR_discount')")
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('cdt', '1')")
    conn.execute("UPDATE tax SET validto = '2026-01-01' WHERE rate IN (0.19, 0.06)")
    before = snapshot(conn, 3001)
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                            discount_accno="8590", source="K-1", lines=[PaymentLine(3001, D("118.58"), D("2"))]))
    pm.void_payment("customer", "1100", "K-1", voiddate=date(2026, 7, 2))
    assert snapshot(conn, 3001) == before
    assert per_date_balance(conn, 3001)[date(2026, 7, 2)] == 0
    tax = conn.execute("""SELECT round(sum(a.amount)::numeric, 2) FROM acc_trans a JOIN chart c ON c.id = a.chart_id
                          WHERE a.trans_id = 3001 AND c.accno = '1810'""").fetchone()[0]
    assert tax == D("21.00")


def test_void_foreign_and_deposit(pm, conn):
    conn.execute("UPDATE ar SET exchangerate = 1.10, amount = 550, netamount = 550 WHERE id = 3003")
    conn.execute("UPDATE acc_trans SET amount = amount * 1.1 WHERE trans_id = 3003")
    res = pm.post_payment(Payment(vc="customer", vc_id=903, datepaid=date(2026, 6, 30), payment_accno="1100",
                                  currency="USD", exchangerate=D("1.25"), source="USD-1", amount=D("600"),
                                  arap_accno="1200", lines=[PaymentLine(3003, D("500"))]))
    pm.void_payment("customer", "1100", "USD-1", voiddate=date(2026, 7, 1))
    assert snapshot(conn, 3003) == (D("550"), D("0"), None)
    dep = snapshot(conn, res.overpayment_id)
    assert dep[1] == 0
    for tid in (3003, res.overpayment_id):
        assert all(v == 0 for v in per_date_balance(conn, tid).values())
    # de storno van de bank staat tegen de oorspronkelijke koers in payment
    rate = conn.execute("SELECT exchangerate FROM payment WHERE trans_id = 3003 ORDER BY id DESC").fetchone()[0]
    assert D(str(rate)) == D("1.25")


def test_void_partial_selection_and_closed_period(pm, conn):
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                            source="R-5", lines=[PaymentLine(3001, D("121")), PaymentLine(3009, D("10"))]))
    with pytest.raises(ClosedPeriodError):
        pm.void_payment("customer", "1100", "R-5", voiddate=date(2025, 12, 31))
    assert pm.void_payment("customer", "1100", "R-5", trans_ids=[3009], voiddate=date(2026, 7, 2)) == [3009]
    assert snapshot(conn, 3001)[1] == D("121")
    assert snapshot(conn, 3009)[1] == 0


def test_reissue_moves_all_lines(pm, conn):
    conn.execute("UPDATE ar SET exchangerate = 1.10, amount = 550, netamount = 550 WHERE id = 3003")
    conn.execute("UPDATE acc_trans SET amount = amount * 1.1 WHERE trans_id = 3003")
    pm.post_payment(Payment(vc="customer", vc_id=903, datepaid=date(2026, 6, 30), payment_accno="1100",
                            currency="USD", exchangerate=D("1.25"), source="USD-1", lines=[PaymentLine(3003, D("500"))]))
    lines_before = conn.execute("SELECT count(*) FROM acc_trans WHERE trans_id = 3003").fetchone()[0]
    assert pm.reissue_payment("customer", "1100", "USD-1", "USD-2", new_date=date(2026, 7, 3)) == [3003]
    bal = per_date_balance(conn, 3003)
    assert date(2026, 6, 30) not in bal and bal[date(2026, 7, 3)] == 0
    assert conn.execute("SELECT count(*) FROM acc_trans WHERE trans_id = 3003").fetchone()[0] == lines_before
    assert snapshot(conn, 3003)[2] == date(2026, 7, 3)
    assert conn.execute("SELECT count(*) FROM acc_trans WHERE source = 'USD-1'").fetchone()[0] == 0
    assert pm.next_source("customer", "1100") == "USD-3"
    # alleen nieuw kenmerk, datum blijft
    pm.reissue_payment("customer", "1100", "USD-2", "USD-7")
    assert snapshot(conn, 3003)[2] == date(2026, 7, 3)


def test_reissue_same_day_two_payments_is_unambiguous(pm, conn):
    # twee betalingen op dezelfde dag op dezelfde factuur, met ander kenmerk
    for src, amt in (("A-1", "100"), ("A-2", "21")):
        pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                                source=src, lines=[PaymentLine(3001, D(amt))]))
    pm.reissue_payment("customer", "1100", "A-2", "A-3", new_date=date(2026, 7, 5))
    bal = per_date_balance(conn, 3001)
    assert bal[date(2026, 7, 1)] == 0 and bal[date(2026, 7, 5)] == 0
    assert arap_balance(conn, 3001, "1200") == 0
    with pytest.raises(ValidationError):
        pm.reissue_payment("customer", "1100", "A-1", "")


# --- stap 6: verdeling, controles, betalingenlijst -------------------------------------

def test_allocate_credit_notes_first_then_oldest(pm):
    invs = pm.open_invoices("customer", 902, datepaid=date(2026, 7, 1))
    lines = Payments.allocate(invs, D("100"))
    assert [(l.trans_id, l.paid) for l in lines] == [(3007, D("65.40")), (3004, D("34.60"))]
    assert all(l.expected_due is not None for l in lines)


def test_allocate_discount_only_when_fully_paid(pm, conn):
    conn.execute("UPDATE ar SET cashdiscount = 0.02, discountterms = 30 WHERE id = 3001")
    inv = [i for i in pm.open_invoices("customer", 900, datepaid=date(2026, 7, 1)) if i.id == 3001]
    full = Payments.allocate(inv, inv[0].paid_suggested)
    assert full[0].discount == inv[0].discount and full[0].paid == inv[0].paid_suggested
    part = Payments.allocate(inv, D("50"))
    assert part[0].discount == 0 and part[0].paid == D("50")
    whole = Payments.allocate(inv, inv[0].due)              # volle bedrag: geen korting genomen
    assert whole[0].discount == 0 and whole[0].paid == inv[0].due


def test_payment_exceeding_due_refused(pm):
    with pytest.raises(ValidationError, match="exceeds the amount due"):
        pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                                source="R-1", lines=[PaymentLine(3009, D("10.01"))]))


def test_changed_meanwhile_refused(pm, conn):
    from sqlledger.payments import ChangedError
    with pytest.raises(ChangedError):
        pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                                source="R-1", lines=[PaymentLine(3006, D("100"), expected_due=D("300"))]))


def test_payment_register_and_void_flag(pm):
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2026, 7, 1), payment_accno="1100",
                            source="R-5", lines=[PaymentLine(3001, D("121")), PaymentLine(3009, D("10"))]))
    rows = [r for r in pm.payment_register("customer", payment_accno="1100") if r["source"] == "R-5"]
    assert len(rows) == 1 and rows[0]["amount"] == D("131") and sorted(rows[0]["trans_ids"]) == [3001, 3009]
    assert not rows[0]["void"]
    pm.void_payment("customer", "1100", "R-5", voiddate=date(2026, 7, 2))
    rows = [r for r in pm.payment_register("customer", option="all") if r["source"] == "R-5"]
    assert len(rows) == 2 and all(r["void"] for r in rows) and [r["reversal"] for r in rows] == [False, True]
    assert not [r for r in pm.payment_register("customer", option="exclude") if r["source"] == "R-5"]
    assert [r["source"] for r in pm.payment_register("customer", source_from="R-5", source_to="R-5")] == ["R-5", "R-5"]
