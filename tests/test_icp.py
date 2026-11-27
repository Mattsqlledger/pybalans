"""ICP-opgaaf (sqlledger.icp, ICP_ANALYSE.md ICP1–ICP11)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import ArapLedger, GeneralLedger, GLTransaction, JournalLine, ValidationError
from sqlledger.arap import ArapLine, ArapTransaction
from sqlledger.icp import IcpReturns, check_vat, normalize_vat, round_euro
from sqlledger.invoice import Invoice, InvoiceLedger, InvoiceLine
from sqlledger.vatreturn import VatReturns, period_of

from test_invoice import mk_part, mk_service, ic  # noqa: F401  (fixture)

D = Decimal
CUST, CUST2, VEND = 902, 903, 905
Q1 = period_of("quarter", date(2031, 2, 1))


@pytest.fixture(autouse=True)
def one_rate(conn):
    conn.execute("UPDATE tax SET rate = 0.09, taxnumber = 'NL-VLAAG' WHERE chart_id = (SELECT id FROM chart WHERE accno = '1801')")
    conn.execute("DELETE FROM tax WHERE taxnumber IS NULL")


@pytest.fixture
def vr(conn, config):
    v = VatReturns(conn, config)
    v.save_settings({a["id"]: a["suggested"] for a in v.tax_accounts()}, "1850", "4999", login="t")
    return v


@pytest.fixture
def icp(conn, config, vr):
    return IcpReturns(conn, config)


def set_customer(conn, cid, taxnumber, name=None):
    conn.execute("UPDATE customer SET taxnumber = %s WHERE id = %s", (taxnumber, cid))
    if name:
        conn.execute("UPDATE customer SET name = %s WHERE id = %s", (name, cid))


def sale(conn, config, net, vc_id=CUST, d=date(2031, 2, 10), taxes=None):
    return ArapLedger(conn, config).post_transaction(ArapTransaction(
        vc="customer", vc_id=vc_id, transdate=d, arap_accno="1200",
        lines=[ArapLine(accno="8520", amount=D(net))], taxes={k: D(v) for k, v in (taxes or {}).items()}))


# ------------------------------------------------------------------ btw-nummer (ICP4)

@pytest.mark.parametrize("text, expected", [
    ("DE 123.456.789", ("DE", "123456789", "")),
    ("be0123456789", ("BE", "0123456789", "")),
    ("FRXX123456789", ("FR", "XX123456789", "")),
    ("EL123456789", ("EL", "123456789", "")),
    ("XI123456789", ("XI", "123456789", "")),
    ("ATU12345678", ("AT", "U12345678", "")),
    ("", ("", "", "missing")),
    ("123456789", ("", "123456789", "no_country")),
    ("GR123456789", ("GR", "123456789", "greece")),
    ("GB123456789", ("GB", "123456789", "not_eu")),
    ("NL123456789B01", ("NL", "123456789B01", "domestic")),
    ("DE12345678", ("DE", "12345678", "format")),
])
def test_check_vat(text, expected):
    assert check_vat(text) == expected


def test_normalize_and_rounding():
    assert normalize_vat(" de-123 456 789 ") == "DE123456789"
    assert round_euro(D("10.50")) == 11 and round_euro(D("10.49")) == 10 and round_euro(D("-2.50")) == -3


# ------------------------------------------------------------------ instellingen (ICP1/ICP2)

def test_configured_and_period(icp, vr, conn):
    assert not icp.configured()
    assert icp.period_kind() == "quarter"
    conn.execute("INSERT INTO x_setting (key, value) VALUES ('vat_return_period', 'month')")
    assert icp.period_kind() == "month"
    assert icp.save_period("quarter", "t") and icp.period_kind() == "quarter"
    assert not icp.save_period("quarter", "t")
    with pytest.raises(ValidationError):
        icp.save_period("year")
    vr.save_customer_box(CUST, "3B")
    assert icp.configured()


def test_customer_kind(icp):
    assert icp.customer_kind(CUST) == ""
    assert icp.save_customer_kind(CUST, "abc", "t") and icp.customer_kind(CUST) == "abc"
    assert icp.save_customer_kind(CUST, "goods") and icp.customer_kind(CUST) == ""
    assert not icp.save_customer_kind(CUST, "")
    with pytest.raises(ValidationError):
        icp.save_customer_kind(CUST, "x")


def test_require_vat_number(icp, vr):
    icp.require_vat_number("customer", "", "", [])                       # niet in 3B: niets nodig
    with pytest.raises(ValidationError, match="moet een btw-nummer hebben"):
        icp.require_vat_number("customer", "", "3B", [])
    with pytest.raises(ValidationError, match="geen Nederlands nummer"):
        icp.require_vat_number("customer", "NL123456789B01", "3B", [])
    with pytest.raises(ValidationError, match="EL"):
        icp.require_vat_number("customer", "GR123456789", "3B", [])
    with pytest.raises(ValidationError, match="geen geldig EU-btw-nummer"):
        icp.require_vat_number("customer", "DE1", "3B", [])
    icp.require_vat_number("customer", "DE123456789", "3B", [])
    # btw-rekening op 3B (klant) of 4B (leverancier)
    ids = {a["accno"]: a["id"] for a in vr.tax_accounts()}
    vr.save_settings({ids["1811"]: "3B", ids["1801"]: "4B"}, "1850", "4999")
    with pytest.raises(ValidationError, match="relatie in rubriek 3B"):
        icp.require_vat_number("customer", "", "", ["1811"])
    with pytest.raises(ValidationError, match="relatie in rubriek 4B"):
        icp.require_vat_number("vendor", "", "", ["1801"])
    icp.require_vat_number("vendor", "", "", ["1800"])


# ------------------------------------------------------------------ berekenen (ICP3–ICP7)

def test_report_merges_and_rounds(icp, vr, conn, config):
    vr.save_customer_box(CUST, "3B")
    vr.save_customer_box(CUST2, "3B")
    set_customer(conn, CUST, "DE123456789", "Kunde A")
    set_customer(conn, CUST2, "de 123 456 789", "Kunde A filiaal")       # zelfde nummer: samengevoegd (ICP7)
    sale(conn, config, "100.50")
    sale(conn, config, "200.20", vc_id=CUST2)
    sale(conn, config, "-50.10")                                         # creditnota
    sale(conn, config, "999", d=date(2031, 4, 2))                        # ander tijdvak
    rep = icp.report(Q1)
    assert len(rep.lines) == 1
    ln = rep.lines[0]
    assert (ln.country, ln.number, ln.problem) == ("DE", "123456789", "")
    assert ln.exact["goods"] == D("250.60") and ln.rounded["goods"] == 251
    assert ln.name == "Kunde A, Kunde A filiaal" and len(ln.transactions) == 3
    assert rep.box3b_exact == D("250.60") and rep.unexplained == 0 and not rep.problems


def test_services_abc_and_problem(icp, vr, conn, config):
    vr.save_customer_box(CUST, "3B")
    vr.save_customer_box(CUST2, "3B")
    set_customer(conn, CUST, "FRXX123456789")
    icp.save_customer_kind(CUST, "services")
    sale(conn, config, "80")
    set_customer(conn, CUST2, "")                                        # geen nummer: melding, telt wel mee
    icp.save_customer_kind(CUST2, "abc")
    sale(conn, config, "40.40", vc_id=CUST2)
    rep = icp.report(Q1)
    fr = rep.line("FRXX123456789")
    assert fr.exact == {"goods": 0, "services": D("80"), "abc": 0}
    bad = rep.line(f"?{CUST2}")
    assert bad.problem == "missing" and bad.exact["abc"] == D("40.40") and rep.problems == [bad]
    assert rep.totals == {"goods": 0, "services": 80, "abc": 40}
    assert rep.unexplained == 0


def test_invoice_lines_goods_and_services(icp, vr, conn, config, ic):  # noqa: F811
    # SQL-Ledger schrijft geen btw-regel van 0: ICP-verkoop zonder btw loopt via de rubriek bij de klant (BA4)
    vr.save_customer_box(CUST, "3B")
    part = mk_part(ic, "P1")
    svc = mk_service(ic, "S1")
    for p in (part, svc):
        conn.execute("DELETE FROM partstax WHERE parts_id = %s", (p,))
    set_customer(conn, CUST, "BE0123456789")
    InvoiceLedger(conn, config).post_invoice(Invoice(
        vc="customer", vc_id=CUST, transdate=date(2031, 3, 1), arap_accno="1200",
        lines=[InvoiceLine(parts_id=part, qty=D(3), sellprice=D("100")),
               InvoiceLine(parts_id=svc, qty=D(1), sellprice=D("50.25"))]))
    icp.save_customer_kind(CUST, "abc")                                  # ABC: de leveringen naar de ABC-lijst
    rep = icp.report(Q1)
    be = rep.line("BE0123456789")
    assert be.exact == {"goods": 0, "services": D("50.25"), "abc": D("300")}
    assert rep.box3b_exact == D("350.25") and rep.unexplained == 0


def test_gl_in_3b_is_unassigned(icp, vr, conn, config):
    ids = {a["accno"]: a["id"] for a in vr.tax_accounts()}
    vr.save_settings({ids["1811"]: "3B"}, "1850", "4999")
    GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=date(2031, 1, 20), reference="m1", description="Correctie",
        lines=[JournalLine(accno="1100", debit=D("10")), JournalLine(accno="8520", credit=D("10")),
               JournalLine(accno="1811", credit=D("0.01")), JournalLine(accno="4999", debit=D("0.01"))]))
    rep = icp.report(Q1)
    assert [u["number"] for u in rep.unassigned] == ["m1"] and rep.unexplained == rep.box3b_exact != 0


# ------------------------------------------------------------------ vastleggen (ICP8)

def test_register_with_vat_and_difference(icp, vr, conn, config):
    vr.save_customer_box(CUST, "3B")
    set_customer(conn, CUST, "DE123456789")
    sale(conn, config, "100")
    sale(conn, config, "50", taxes={"1810": "10.50"}, vc_id=CUST2)
    vat = vr.report(Q1)
    assert icp.register_with_vat(vat, 4711, "t")
    rep = icp.report(Q1)
    assert rep.stored["gl_id"] == 4711 and rep.difference is None
    assert icp.due(date(2031, 4, 5)) is None
    sale(conn, config, "20.60", d=date(2031, 3, 30))
    diff = icp.report(Q1).difference
    assert [(x["key"], x["old"]["goods"], x["now"]["goods"]) for x in diff.lines] == [("DE123456789", 100, 121)]


def test_register_with_vat_other_period(icp, vr, conn, config):
    vr.save_customer_box(CUST, "3B")
    set_customer(conn, CUST, "DE123456789")
    icp.save_period("month")
    sale(conn, config, "100")
    assert not icp.register_with_vat(vr.report(Q1), 1, "t")             # btw per kwartaal, ICP per maand
    feb = period_of("month", date(2031, 2, 1))
    due = icp.due(date(2031, 3, 3))
    assert due["period"] == feb and due["deadline"] == date(2031, 3, 31)
    assert icp.due(date(2031, 4, 1)) is None                # februari vervallen; maart zonder bedragen
    assert icp.due(date(2031, 3, 31)) is not None
    icp.register(icp.report(feb), None, "t")
    assert icp.due(date(2031, 3, 3)) is None
