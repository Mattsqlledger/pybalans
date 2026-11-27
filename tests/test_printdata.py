"""Documenten: sjabloonvariabelen (sqlledger.printdata), bedrag in woorden en de PDF-engine (fase 4)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import ArapPayment, UserConfig
from sqlledger.num2text import language_for, num2text
from sqlledger.printdata import PrintData, mark_status

from test_invoice import CUST, ic, inv, mk_part, mk_service, sale  # noqa: F401 (fixtures)

D = Decimal


# ------------------------------------------------------------------ bedrag in woorden

def test_num2text_english():
    assert num2text(0) == "Zero"
    assert num2text(21) == "Twenty One"
    assert num2text(100) == "One Hundred"
    assert num2text(1282) == "One Thousand Two Hundred Eighty Two"
    assert num2text(2000005) == "Two Million Five"


def test_num2text_dutch_corrected():
    assert num2text(1282, "nl") == "**eenduizendtweehonderdtweeëntachtig**"
    assert num2text(12, "nl") == "**twaalf**"           # Perl: twalf
    assert num2text(18, "nl") == "**achttien**"         # Perl: achtien
    assert num2text(10100, "nl") == "**tienduizendeenhonderd**"   # Perl: ...honderdnul
    assert num2text(45, "nl") == "**vijfenveertig**"
    assert num2text(0, "nl") == "nul"


def test_language_for():
    assert language_for("", "nl") == "nl"
    assert language_for("Factor", "nl") == "en"         # geen eigen omzetting: Engels (zoals CP->new)
    assert language_for("", "") == "en"


# ------------------------------------------------------------------ factuurgegevens

@pytest.fixture
def pd(conn, config):
    return PrintData(conn, UserConfig(login="admin", numberformat="1.000,00", dateformat="dd-mm-yy",
                                      countrycode="nl"), username="Jan", useremail="jan@example.com")


def test_invoice_vars_lines_taxes_totals(conn, inv, ic, pd):
    a = mk_part(ic, "A-1", sell="12.345")
    s = mk_service(ic, "S-1", sell="100")
    i = sale([(a, 3, "12.345"), (s, 1, "100")], notes="Levering <%invnumber%>")
    i.lines[1].discount = D("10")
    tid = inv.post_invoice(i)
    v = pd.invoice_vars(tid, "customer")
    assert v["formname"] == "invoice" and v["label"] == "Invoice"
    assert v["invdate"] == "01-03-2026" and v["duedate"] == "01-03-2026"
    assert v["number"] == ["A-1", "S-1"] and v["runningnumber"] == [1, 2]
    assert v["qty"] == ["3", "1"]
    assert v["sellprice"] == ["12,345", "100,00"]       # decimalen van de prijs, minstens 2
    assert v["discountrate"] == ["", "10"]               # 0 is leeg (Form::format_amount)
    assert v["netprice"] == ["12,345", "90,00"]
    assert v["linetotal"] == ["37,04", "90,00"]
    assert v["part"] == ["A-1", ""] and v["service"] == ["", "S-1"]    # Perl: "NULL"
    assert v["subtotal"] == "127,04"
    assert v["taxdescription"] and len(v["tax"]) == len(v["taxbase"])
    total_tax = sum(D(t.replace(".", "").replace(",", ".")) for t in v["tax"])
    assert v["invtotal"] == f"{D('127.04') + total_tax:.2f}".replace(".", ",")
    assert v["total"] == v["invtotal"] and v["paid"] == ""         # niets betaald: leeg zoals Perl
    assert v["notes"] == f"Levering {v['invnumber']}"     # <%var%> vervangen
    assert v["username"] == "Jan" and v["company"] is not None
    assert v["text_amount"].startswith("**honderd") or v["text_amount"].startswith("**een")
    assert v["fdm"] == "01-03-2026" and v["ldm"] == "31-03-2026" and v["fdy"] == "01-01-2026"
    assert v["shiptoname"] == v["name"]                  # geen afleveradres: adres van de klant


def test_invoice_vars_payment_and_credit(conn, inv, ic, pd):
    a = mk_part(ic, "B-1", sell="10")
    i = sale([(a, 2, "10")])
    i.payments = [ArapPayment(accno="1100", amount=D("5"), datepaid=date(2026, 3, 5), source="bank 1")]
    tid = inv.post_invoice(i)
    v = pd.invoice_vars(tid, "customer")
    assert v["payment"] == ["5,00"] and v["paymentdate"] == ["05-03-2026"] and v["paymentsource"] == ["bank 1"]
    assert v["paid"] == "5,00"
    inv_total = D(v["invtotal"].replace(",", "."))
    assert v["total"] == f"{inv_total - 5:.2f}".replace(".", ",")
    c = sale([(a, 1, "10")], type="credit_invoice")
    cid = inv.post_invoice(c)
    cv = pd.invoice_vars(cid, "customer")
    assert cv["formname"] == "credit_invoice" and cv["label"] == "Credit Invoice"
    assert cv["qty"] == ["1"] and cv["linetotal"] == ["10,00"]
    assert cv["shiptoname"] == cv["company"]             # creditfactuur: eigen adres als afleveradres (io.pl)


def test_invoice_vars_sorted(conn, inv, ic, pd):
    a = mk_part(ic, "Z-9", sell="1")
    b = mk_part(ic, "A-0", sell="1")
    tid = inv.post_invoice(sale([(a, 1, "1"), (b, 1, "1")]))
    assert pd.invoice_vars(tid, "customer")["number"] == ["Z-9", "A-0"]
    assert pd.invoice_vars(tid, "customer", sortby="partnumber")["number"] == ["A-0", "Z-9"]


def test_mark_status_keeps_other_flag(conn, config, inv, ic):
    a = mk_part(ic, "C-1")
    tid = inv.post_invoice(sale([(a, 1, "5")]))
    mark_status(conn, config, tid, "invoice", emailed=True)
    mark_status(conn, config, tid, "invoice", printed=True)
    assert conn.execute("SELECT printed, emailed FROM status WHERE trans_id = %s AND formname = 'invoice'",
                        (tid,)).fetchall() == [(True, True)]
    with pytest.raises(ValueError):
        mark_status(conn, config, tid, "x; DROP TABLE ar", printed=True)


# ------------------------------------------------------------------ PDF-engine

def test_renderer_standard_and_pdf(conn, inv, ic, pd, tmp_path):
    from sqlledger_web.documents import DocumentRenderer
    a = mk_part(ic, "D-1", sell="7.5")
    tid = inv.post_invoice(sale([(a, 2, "7.5")]))
    v = pd.invoice_vars(tid, "customer")
    r = DocumentRenderer(tmp_path, 1, "nl")
    html = r.render_html("invoice", v)
    assert "Factuur" in html and "D-1" in html and v["invtotal"] in html
    assert DocumentRenderer(tmp_path, 1, "en").render_html("invoice", v).count("Invoice") >= 1
    assert r.render_pdf("invoice", v)[:5] == b"%PDF-"
    assert r.source("invoice") == "standard"


def test_renderer_company_variant_and_sandbox(tmp_path):
    from jinja2.exceptions import SecurityError

    from sqlledger_web.documents import DocumentRenderer
    base = tmp_path / "templates" / "7"
    (base / "Factor").mkdir(parents=True)
    (base / "invoice.html").write_text("eigen {{ invnumber }}")
    (base / "Factor" / "invoice.html").write_text("factor {{ invnumber }} {{ name }}")
    r = DocumentRenderer(tmp_path, 7)
    assert r.render_html("invoice", {"invnumber": "1"}) == "eigen 1"
    assert r.render_html("invoice", {"invnumber": "1", "language_code": "Factor", "name": "<b>"}) == "factor 1 &lt;b&gt;"
    assert r.source("invoice", "Factor") == "variant" and r.source("invoice") == "company"
    assert r.render_html("invoice", {"invnumber": "1", "language_code": "../x"}) == "eigen 1"   # geen pad
    (base / "invoice.html").write_text("{{ ''.__class__.__mro__ }}")
    with pytest.raises(SecurityError):
        r.render_html("invoice", {})
    (base / "invoice.html").write_text('<img src="file:///etc/passwd"><p>x</p>')
    assert r.render_pdf("invoice", {})[:5] == b"%PDF-"          # externe bron genegeerd, geen fout
    with pytest.raises(ValueError):
        r.render_html("../invoice", {})


def test_renderer_company_address_without_empty_lines(tmp_path):
    from sqlledger_web.documents import DocumentRenderer
    base = tmp_path / "templates" / "3"
    base.mkdir(parents=True)
    (base / "invoice.html").write_text("{% for l in address|lines %}[{{ l }}]{% endfor %}")
    r = DocumentRenderer(tmp_path, 3)
    assert r.render_html("invoice", {"address": "\r\n9 Castle Street\r\n\r\n Reading \n"}) == "[9 Castle Street][Reading]"
