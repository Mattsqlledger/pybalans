"""Btw-aangifte (sqlledger.vatreturn, BTW_ANALYSE.md BA1–BA9)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import ArapLedger, GeneralLedger, GLTransaction, JournalLine, ValidationError
from sqlledger.arap import ArapLine, ArapTransaction
from sqlledger.invoice import Invoice, InvoiceLedger, InvoiceLine
from sqlledger.vatreturn import (BoxAmount, VatReturns, free_period, last_closed, period_of, recent_periods,
                                 round_boxes, split_base)

from test_invoice import mk_part, mk_service, ic  # noqa: F401  (fixture)

D = Decimal
CUST, CUST2, VEND = 902, 903, 905
Q1 = period_of("quarter", date(2031, 2, 1))


@pytest.fixture(autouse=True)
def one_rate(conn):
    # sl_template heeft per rekening twee tarieven zonder einddatum (zoals test_invoice): de oude weg
    conn.execute("UPDATE tax SET rate = 0.09, taxnumber = 'NL-VLAAG' WHERE chart_id = (SELECT id FROM chart WHERE accno = '1801')")
    conn.execute("DELETE FROM tax WHERE taxnumber IS NULL")


@pytest.fixture
def vr(conn, config):
    v = VatReturns(conn, config)
    accs = {a["accno"]: a for a in v.tax_accounts()}
    v.save_settings({a["id"]: a["suggested"] for a in accs.values()}, "1850", "4999", login="t")
    return v


@pytest.fixture
def al(conn, config):
    return ArapLedger(conn, config)


def sale(al, net, taxes, d=date(2031, 2, 10), vc_id=CUST, vc="customer"):
    return al.post_transaction(ArapTransaction(
        vc=vc, vc_id=vc_id, transdate=d, arap_accno="1200" if vc == "customer" else "1600",
        lines=[ArapLine(accno="8520" if vc == "customer" else "8100", amount=D(net))],
        taxes={k: D(v) for k, v in taxes.items()}))


def boxes(rep):
    return {c: (b.base_exact, b.tax_exact) for c, b in rep.boxes.items() if b.base_exact or b.tax_exact}


# ------------------------------------------------------------------ instellingen

def test_suggestions_and_settings(conn, config):
    v = VatReturns(conn, config)
    assert not v.configured()
    sugg = {a["accno"]: a["suggested"] for a in v.tax_accounts()}
    assert sugg == {"1800": "5B", "1801": "5B", "1810": "1A", "1811": "1B"}
    assert v.suggest_accounts() == ("1850", "")
    ids = {a["accno"]: a["id"] for a in v.tax_accounts()}
    with pytest.raises(ValidationError, match="Onbekende rubriek"):
        v.save_settings({ids["1810"]: "9Z"}, "", "")
    with pytest.raises(ValidationError, match="geen btw-rekening"):
        v.save_settings({}, "1810", "")
    with pytest.raises(ValidationError, match="bestaat niet"):
        v.save_settings({}, "0000", "")
    changed = v.save_settings({ids["1810"]: "1A", ids["1800"]: "none"}, "1850", "", login="t")
    assert changed == ["1810", "1800", "vat_settlement_accno"]
    assert v.configured() and v.accounts_setting() == ("1850", "")
    assert v.save_settings({ids["1810"]: "1A"}, "1850", "") == []


def test_periods():
    assert last_closed("quarter", date(2026, 10, 11)) == period_of("quarter", date(2026, 9, 30))
    p = last_closed("month", date(2026, 1, 5))
    assert (p.fromdate, p.todate, p.number) == (date(2025, 12, 1), date(2025, 12, 31), 12)
    assert last_closed("year", date(2026, 3, 1)).year == 2025
    assert len(recent_periods("quarter", date(2026, 10, 11), 4)) == 5
    assert free_period(date(2026, 7, 1), date(2026, 9, 30)).kind == "quarter"
    assert free_period(date(2026, 7, 2), date(2026, 9, 30)).kind == "free"
    with pytest.raises(ValidationError):
        free_period(date(2026, 9, 30), date(2026, 7, 1))


# ------------------------------------------------------------------ rekenen

def test_sale_purchase_and_rounding(vr, al):
    sale(al, "100.60", {"1810": "21.13"})
    sale(al, "50", {"1811": "4.50"})
    sale(al, "40.40", {"1800": "8.48"}, vc="vendor", vc_id=VEND)
    sale(al, "10", {"1810": "2.10"}, d=date(2031, 4, 1))                   # ander tijdvak
    rep = vr.report(Q1)
    assert boxes(rep) == {"1A": (D("100.60"), D("21.13")), "1B": (D("50"), D("4.50")),
                          "5B": (D("40.40"), D("8.48"))}
    # BA5: in uw voordeel
    assert (rep.boxes["1A"].base, rep.boxes["1A"].tax, rep.boxes["5B"].tax) == (100, 21, 9)
    assert rep.totals == {"5A": 25, "5B": 9, "5C": 16, "5D": 0, "5G": 16}
    assert rep.total_exact == D("17.15")


def test_credit_note_and_negative_rounding():
    b = {c: BoxAmount() for c in ("1A", "1B", "1C", "1D", "1E", "2A", "3A", "3B", "3C", "4A", "4B", "5B")}
    b["1A"].base_exact, b["1A"].tax_exact = D("-10.40"), D("-2.18")
    b["5B"].tax_exact = D("-0.40")
    t = round_boxes(b)
    assert (b["1A"].base, b["1A"].tax, b["5B"].tax) == (-11, -3, 0)
    assert t["5G"] == -3


def test_untaxed_sales_customer_box(vr, al):
    sale(al, "300", {})
    sale(al, "200", {}, vc_id=CUST2)
    sale(al, "70", {}, vc="vendor", vc_id=VEND)                            # inkoop zonder btw doet niet mee
    assert vr.save_customer_box(CUST2, "3B", "t")
    assert not vr.save_customer_box(CUST2, "3B", "t")
    with pytest.raises(ValidationError):
        vr.save_customer_box(CUST2, "1A")
    rep = vr.report(Q1)
    assert boxes(rep) == {"1E": (D("300"), 0), "3B": (D("200"), 0)}
    assert (rep.default_count, rep.customer_count) == (1, 1)
    vr.save_customer_box(CUST2, "")
    assert vr.customer_box(CUST2) == ""


def test_split_multiple_rates(vr, al):
    sale(al, "150", {"1810": "21", "1811": "4.50"})                        # 100 + 50
    sale(al, "245.10", {"1800": "11.28", "1801": "17.23"}, vc="vendor", vc_id=VEND)
    rep = vr.report(Q1)
    assert boxes(rep)["1A"] == (D("100"), D("21")) and boxes(rep)["1B"] == (D("50"), D("4.50"))
    assert boxes(rep)["5B"] == (D("245.10"), D("28.51"))
    assert not rep.split_warnings


def test_split_base_rules():
    # btw ÷ tarief, centen naar het hoogste tarief
    bases, untaxed, warn = split_base(D("245.10"), {1: D("11.28"), 2: D("17.23")}, {1: D("0.21"), 2: D("0.09")})
    assert bases == {1: D("53.66"), 2: D("191.44")} and untaxed == 0 and not warn
    # groot verschil: rest zonder btw + melding
    bases, untaxed, warn = split_base(D("500"), {1: D("21"), 2: D("9")}, {1: D("0.21"), 2: D("0.09")})
    assert bases == {1: D("100"), 2: D("100")} and untaxed == D("300") and warn
    # 0%-rekening krijgt het restant
    bases, untaxed, warn = split_base(D("300"), {1: D("21"), 3: D("0")}, {1: D("0.21"), 3: D("0")})
    assert bases == {1: D("100"), 3: D("200")} and not warn
    # één rekening: het nettobedrag
    assert split_base(D("80"), {1: D("16.80")}, {1: D("0.21")}) == ({1: D("80")}, 0, False)
    # factuurregels per btw-rekening, regel zonder btw, afronding naar de grootste
    lines = [(D("100"), frozenset({1})), (D("50"), frozenset({2})), (D("20"), frozenset())]
    bases, untaxed, warn = split_base(D("170.01"), {1: D("21"), 2: D("4.50")}, {1: D("0.21"), 2: D("0.09")}, lines)
    assert bases == {1: D("100.01"), 2: D("50")} and untaxed == D("20") and not warn


def test_split_base_reverse_charge_pair():
    # inkoop EU (credit positief: netto -200): het paar -21 % (4B) en +21 % (5B) krijgt elk het nettobedrag,
    # de te betalen helft met het teken van verkoop
    bases, untaxed, warn = split_base(D("-200"), {7: D("42"), 8: D("-42")}, {7: D("-0.21"), 8: D("0.21")})
    assert bases == {7: D("200"), 8: D("-200")} and untaxed == 0 and not warn
    # twee tarieven tegelijk (21 % en 9 %): per helft verdeeld
    bases, untaxed, warn = split_base(D("-300"), {7: D("21"), 8: D("-21"), 9: D("18"), 10: D("-18")},
                                      {7: D("-0.21"), 8: D("0.21"), 9: D("-0.09"), 10: D("0.09")})
    assert bases == {7: D("100"), 8: D("-100"), 9: D("200"), 10: D("-200")} and not warn
    # met factuurregels (artikel met beide btw-rekeningen)
    lines = [(D("-200"), frozenset({7, 8}))]
    assert split_base(D("-200"), {7: D("42"), 8: D("-42")}, {7: D("-0.21"), 8: D("0.21")}, lines) == (
        {7: D("200"), 8: D("-200")}, 0, False)


def test_invoice_with_parts(vr, conn, config, ic):  # noqa: F811
    p21 = mk_part(ic, "P21", taxaccounts=["1810"])
    s9 = mk_service(ic, "S9")
    conn.execute("DELETE FROM partstax WHERE parts_id = %s", (s9,))
    conn.execute("INSERT INTO partstax (parts_id, chart_id) SELECT %s, id FROM chart WHERE accno = '1811'", (s9,))
    free = mk_service(ic, "VRIJ")
    conn.execute("DELETE FROM partstax WHERE parts_id = %s", (free,))
    inv = InvoiceLedger(conn, config)
    inv.post_invoice(Invoice(vc="customer", vc_id=CUST, transdate=date(2031, 3, 1), arap_accno="1200",
                             lines=[InvoiceLine(parts_id=p21, qty=D(2), sellprice=D("50")),
                                    InvoiceLine(parts_id=s9, qty=D(1), sellprice=D("40")),
                                    InvoiceLine(parts_id=free, qty=D(1), sellprice=D("15"))]))
    rep = vr.report(Q1)
    assert boxes(rep) == {"1A": (D("100"), D("21")), "1B": (D("40"), D("3.60")), "1E": (D("15"), 0)}
    assert rep.transactions[0].note == "default"


def test_gl_receipt(vr, conn, config):
    GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=date(2031, 1, 20), reference="kas1", description="Kassabon",
        lines=[JournalLine(accno="8100", debit=D("64.22")), JournalLine(accno="1801", debit=D("5.78")),
               JournalLine(accno="1100", credit=D("70"))]))
    rep = vr.report(Q1)
    assert boxes(rep) == {"5B": (D("64.22"), D("5.78"))}
    assert rep.transactions[0].kind == "gl"


def test_unmapped_account_reported(vr, al):
    ids = {a["accno"]: a["id"] for a in vr.tax_accounts()}
    vr.conn.execute("DELETE FROM x_vat_box WHERE chart_id = %s", (ids["1811"],))
    sale(al, "50", {"1811": "4.50"})
    rep = vr.report(Q1)
    assert [a["accno"] for a in rep.unmapped] == ["1811"] and "1B" not in boxes(rep)


# ------------------------------------------------------------------ btw-boeking

def test_proposal_register_and_compare(vr, al, conn, config):
    sale(al, "100.60", {"1810": "21.13"})
    sale(al, "40.40", {"1800": "8.48"}, vc="vendor", vc_id=VEND)
    rep = vr.report(Q1)
    prop = vr.proposal(rep)
    assert prop["transdate"] == date(2031, 3, 31)
    lines = {(ln["accno"], ln["amount"]) for ln in prop["lines"]}
    assert lines == {("1810", D("-21.13")), ("1800", D("8.48")), ("1850", D("12")), ("4999", D("0.65"))}
    assert sum(ln["amount"] for ln in prop["lines"]) == 0
    gl = GeneralLedger(conn, config)
    gid = gl.post_transaction(GLTransaction(
        transdate=prop["transdate"], description="Btw-aangifte",
        lines=[JournalLine(accno=ln["accno"], debit=-ln["amount"] if ln["amount"] < 0 else D(0),
                           credit=ln["amount"] if ln["amount"] > 0 else D(0)) for ln in prop["lines"]]))
    vr.register(rep, gid, "t")
    again = vr.report(Q1)                                                   # BA7: btw-boeking telt niet mee
    assert again.settlement_excluded == 1 and again.totals == rep.totals
    assert again.stored["gl_id"] == gid and again.difference is None
    cur = conn.execute("SELECT 1 FROM x_task_done WHERE kind = 'vat' AND period_end = '2031-03-31'")
    assert cur.fetchone()
    # later ingevoerde boeking: verschil met de boeking die het veroorzaakt
    tid = sale(al, "10", {"1810": "2.10"}, d=date(2031, 3, 15))
    diff = vr.report(Q1).difference
    assert diff.total == (D(12), D(14))
    assert diff.boxes["1A"] == (D(100), D(110), D(21), D(23))
    assert [(t["trans_id"], t["status"], t["tax"]) for t in diff.transactions] == [(tid, "new", D("2.10"))]


def test_proposal_empty_period(vr):
    with pytest.raises(ValidationError, match="niets te boeken"):
        vr.proposal(vr.report(Q1))


def test_proposal_needs_accounts(conn, config, al):
    v = VatReturns(conn, config)
    ids = {a["accno"]: a["id"] for a in v.tax_accounts()}
    v.save_settings({ids["1810"]: "1A"}, "", "")
    sale(al, "10.10", {"1810": "2.12"})
    with pytest.raises(ValidationError, match="afdrachtrekening"):
        v.proposal(v.report(Q1))
    v.save_settings({}, "1850", "")
    with pytest.raises(ValidationError, match="afrondingsverschillen"):
        v.proposal(v.report(Q1))


def test_country_suggestion_unavailable(conn, config):
    """§12 VP3: een land zonder aangifteformulier (CH) of een onbekend land geeft geen Nederlands voorstel meer."""
    from sqlledger.extensions import ensure_schema
    v = VatReturns(conn, config)
    assert v.suggest_country() == "NL" and v.unavailable() is None            # EUR, geen schema-land
    ensure_schema(conn)
    conn.execute("INSERT INTO x_setting (key, value) VALUES ('country', 'CH')")
    assert v.suggest_country() == "CH" and v.unavailable() == "CH"
    conn.execute("UPDATE x_setting SET value = 'GB' WHERE key = 'country'")
    assert v.suggest_country() == "GB" and v.unavailable() is None
    conn.execute("DELETE FROM x_setting WHERE key = 'country'")
    conn.execute("CREATE SCHEMA rma")
    conn.execute("CREATE TABLE rma.import_report (id serial, report jsonb)")
    assert v.suggest_country() == "" and v.unavailable() == ""                 # RMA + EUR: onbekend
    v.save_country("NL")
    assert v.unavailable() is None                                              # gekozen: niets meer te melden
