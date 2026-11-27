"""Betalingsspecificatie (fase 4 stap 4b, SB7–SB8; testdata uit compare/arap_seed.sql)."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger.payments import Payment, PaymentLine, Payments
from sqlledger.paymentspec import PaymentSpecs, parse_key, spec_key, spec_vars
from sqlledger.printdata import PrintData

DAY = date(2026, 7, 1)


@pytest.fixture
def pm(conn, config):
    conn.execute("UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL")
    conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
    return Payments(conn, config)


@pytest.fixture
def ps(conn, config):
    return PaymentSpecs(conn, config)


def _audit_on(conn):
    if conn.execute("SELECT 1 FROM defaults WHERE fldname = 'audittrail'").fetchone():
        conn.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'")
    else:
        conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('audittrail', '1')")


def test_receipt_several_invoices(pm, ps, conn, config):
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=DAY, payment_accno="1100", source="R-1",
                            memo="afschrift 12", lines=[PaymentLine(3001, D("121")), PaymentLine(3006, D("100"))]))
    s = ps.find("customer", "1100", "R-1", DAY, 900)
    assert s.formname == "receipt" and s.name == "Klant A" and s.memo == "afschrift 12"
    assert [(l.invnumber, l.paid, l.discount) for l in s.lines] == [("F-3006", D("100"), 0), ("F-3001", D("121"), 0)]
    assert s.lines[0].amount == D("363.00")                       # factuurbedrag, niet het openstaande
    assert s.total == D("221") and s.key == "1100|R-1|2026-07-01|900"
    assert ps.find_key("customer", s.key).total == D("221")
    v = spec_vars(PrintData(conn, config), s)
    assert v["amount"] == "221.00" and v["invnumber"] == "F-3006, F-3001" and v["dcn"] == "R-1"
    assert v["integer_amount"] == "221" and v["decimal"] == "00" and v["text_amount"]
    assert [l["paid"] for l in v["paylines"]] == ["100.00", "121.00"] and not v["has_discount"]
    nl = spec_vars(PrintData(conn, config, doc_language="nl"), s)
    assert nl["text_amount"] == "**tweehonderdeenentwintig**"      # zoals SL (cheque)


def test_other_relation_or_day_not_included(pm, ps):
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=DAY, payment_accno="1100", source="R-1",
                            lines=[PaymentLine(3001, D("121"))]))
    assert ps.find("customer", "1100", "R-1", date(2026, 7, 2), 900) is None
    assert ps.find("customer", "1100", "R-1", DAY, 902) is None
    assert ps.find("customer", "9999", "R-1", DAY, 900) is None
    assert ps.find("customer", "1100", "R-2", DAY, 900) is None


def test_prepayment_line_vendor(pm, ps, conn, config):
    pm.post_payment(Payment(vc="vendor", vc_id=905, datepaid=DAY, payment_accno="1100", source="1001",
                            amount=D("1300"), arap_accno="1600", lines=[PaymentLine(4001, D("1210"))]))
    s = ps.find("vendor", "1100", "1001", DAY, 905)
    assert s.formname == "check"
    assert [(l.paid, l.prepayment) for l in s.lines] == [(D("1210"), False), (D("90"), True)]
    assert s.total == D("1300")
    v = spec_vars(PrintData(conn, config), s)
    assert v["paylines"][1]["amount"] == "" and v["invnumber"] == s.lines[0].invnumber


def test_discount(pm, ps, conn):
    conn.execute("INSERT INTO chart (accno, description, charttype, category, link) "
                 "VALUES ('8590', 'Korting', 'A', 'E', 'AR_discount')")
    conn.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('cdt', '1')")
    conn.execute("UPDATE tax SET validto = '2026-01-01' WHERE rate IN (0.19, 0.06)")
    conn.execute("UPDATE ar SET cashdiscount = 0.02, discountterms = 10 WHERE id = 3001")
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=DAY, payment_accno="1100", source="K-1",
                            discount_accno="8590", lines=[PaymentLine(3001, D("118.58"), D("2.00"))]))
    s = ps.find("customer", "1100", "K-1", DAY, 900)
    # korting zonder de btw-correctie (die verlaagt het factuurbedrag, geen extra korting)
    assert [(l.paid, l.discount) for l in s.lines] == [(D("118.58"), D("2.00"))]
    assert s.total == D("118.58") and s.total_discount == D("2.00")


def test_foreign_currency(pm, ps, conn):
    conn.execute("UPDATE ar SET exchangerate = 1.10, amount = 550, netamount = 550 WHERE id = 3003")
    conn.execute("UPDATE acc_trans SET amount = amount * 1.1 WHERE trans_id = 3003")
    pm.post_payment(Payment(vc="customer", vc_id=903, datepaid=date(2026, 6, 30), payment_accno="1100",
                            currency="USD", exchangerate=D("1.25"), source="USD-1", lines=[PaymentLine(3003, D("500"))]))
    s = ps.find("customer", "1100", "USD-1", date(2026, 6, 30), 903)
    assert s.currency == "USD" and s.lines[0].paid == D("500") and s.lines[0].amount == D("500")


def test_voided_payment_not_found(pm, ps):
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=DAY, payment_accno="1100", source="R-9",
                            lines=[PaymentLine(3001, D("121"))]))
    pm.void_payment("customer", "1100", "R-9", voiddate=date(2026, 7, 10))
    assert ps.find("customer", "1100", "R-9", DAY, 900) is None


def test_matches_register(pm, ps):
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=DAY, payment_accno="1100", source="R-1",
                            lines=[PaymentLine(3001, D("121")), PaymentLine(3006, D("100"))]))
    for r in pm.payment_register("customer", option="exclude"):
        key = spec_key(r["accno"], r["source"], r["datepaid"], r["vc_id"])
        s = ps.find_key("customer", key)
        assert s is not None and s.total == r["amount"], key


def test_record_audittrail(pm, ps, conn):
    _audit_on(conn)
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=DAY, payment_accno="1100", source="R-1",
                            lines=[PaymentLine(3001, D("121")), PaymentLine(3006, D("100"))]))
    conn.execute("DELETE FROM audittrail")
    ps.record(ps.find("customer", "1100", "R-1", DAY, 900), emailed=True)
    rows = conn.execute("SELECT trans_id, tablename, reference, formname, action FROM audittrail "
                        "ORDER BY trans_id").fetchall()
    assert rows == [(3001, "ar", "R-1", "receipt", "emailed"), (3006, "ar", "R-1", "receipt", "emailed")]
    assert not conn.execute("SELECT 1 FROM status WHERE formname = 'receipt'").fetchone()


def test_latest_and_parse_key(pm, ps):
    assert parse_key("x") is None and parse_key("1100|R|2026-13-01|9") is None
    assert parse_key("1100||2026-07-01|9") == ("1100", "", DAY, 9)
    pm.post_payment(Payment(vc="customer", vc_id=900, datepaid=date(2027, 1, 5), payment_accno="1100", source="R-7",
                            lines=[PaymentLine(3001, D("121"))]))
    assert ps.latest("customer").source == "R-7"


def test_document_shows_invoices(pm, ps, conn, config, tmp_path):
    """De standaardsjablonen receipt/check tonen de facturen, korting en vooruitbetaling."""
    from sqlledger import docsettings
    from sqlledger_web.documents import DocumentRenderer
    pm.post_payment(Payment(vc="vendor", vc_id=905, datepaid=DAY, payment_accno="1100", source="1001",
                            amount=D("1300"), arap_accno="1600", lines=[PaymentLine(4001, D("1210"))]))
    s = ps.find("vendor", "1100", "1001", DAY, 905)
    r = DocumentRenderer(tmp_path, None, "nl", settings=docsettings.load(conn), banks=docsettings.bank_accounts(conn))
    html = r.render_html("check", spec_vars(PrintData(conn, config), s))
    assert "I-4001" in html and "1,210.00" in html and "Vooruitbetaling" in html and "90.00" in html
    assert "Totaal betaald" in html and "Betalingsspecificatie" in html
    en = r.render_html("check", dict(spec_vars(PrintData(conn, config), s), language_code="en"))
    assert "Remittance advice" in en.replace("R E", "RE") or "Remittance advice" in en
