"""VAT Return Verenigd Koninkrijk (sqlledger.vatforms.GB, BTW_LANDEN_ANALYSE.md BL1–BL14 en UK1–UK6)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import GeneralLedger, GLTransaction, JournalLine, ValidationError
from sqlledger.vatforms import GB, NL, gb_deadline, gb_round
from sqlledger.vatreturn import BoxAmount, VatReturns, free_period, last_closed, period_of, recent_periods

from test_vatreturn import CUST, CUST2, VEND, al, boxes, one_rate, sale  # noqa: F401  (fixtures)

D = Decimal
Q1 = period_of("quarter", date(2031, 2, 1))


def add_tax_account(conn, accno: str, rate: str, link: str = "AP_tax:IC_taxpart:IC_taxservice") -> int:
    cid = conn.execute("""INSERT INTO chart (accno, description, charttype, category, link)
                          VALUES (%s, %s, 'A', 'L', %s) RETURNING id""", (accno, f"VAT {accno}", link)).fetchone()[0]
    conn.execute("INSERT INTO tax (chart_id, rate) VALUES (%s, %s)", (cid, D(rate)))
    return cid


@pytest.fixture
def gb(conn, config):
    """Administratie met de Britse aangifte: 1810/1811 verkoop → box 1, 1800/1801 inkoop → box 4, plus een paar
    verlegde btw (diensten uit het buitenland, 2204/2214) en verlegd binnenland (2205/2215)."""
    add_tax_account(conn, "2204", "-0.20")
    add_tax_account(conn, "2214", "0.20")
    add_tax_account(conn, "2205", "-0.20")
    add_tax_account(conn, "2215", "0.20")
    v = VatReturns(conn, config)
    v.save_country("GB", 0, "t")
    v = VatReturns(conn, config)
    ids = {a["accno"]: a["id"] for a in v.tax_accounts()}
    v.save_settings({ids["1810"]: "1", ids["1811"]: "1", ids["1800"]: "4", ids["1801"]: "4",
                     ids["2204"]: "1", ids["2214"]: "4", ids["2205"]: "1R", ids["2215"]: "4"}, "1850", "4999", "t")
    return v


# ------------------------------------------------------------------ land en instellingen (BL2)

def test_country_default_and_switch(conn, config):
    v = VatReturns(conn, config)
    assert v.country == "NL" and not v.country_saved()
    with pytest.raises(ValidationError):
        v.save_country("XX")
    with pytest.raises(ValidationError):
        v.save_country("NL", 1)                     # NL kent geen verschoven kwartalen
    assert v.save_country("GB", 2, "t") == ["vat_country", "vat_quarter_offset"]
    v = VatReturns(conn, config)
    assert (v.country, v.quarter_offset(), v.form is GB) == ("GB", 2, True)
    # koppelingen per land: wat bij NL staat, is bij GB niet zichtbaar
    ids = {a["accno"]: a["id"] for a in v.tax_accounts()}
    VatReturns(conn, config, country="NL").save_settings({ids["1810"]: "1A"}, "", "")
    assert v.boxes() == {} and not v.configured()
    with pytest.raises(ValidationError, match="Onbekende rubriek"):
        v.save_settings({ids["1810"]: "1A"}, "", "")
    v.save_settings({ids["1810"]: "1"}, "", "")
    assert v.boxes() == {ids["1810"]: "1"} and VatReturns(conn, config, country="NL").boxes() == {ids["1810"]: "1A"}


def test_suggestions():
    assert GB.suggest({"description": "VAT on Sales (20.0%)", "link": "AR_tax:AP_tax", "rate": D("0.2")}) == "1"
    assert GB.suggest({"description": "VAT on Purchases (5%)", "link": "AR_tax:AP_tax", "rate": D("0.05")}) == "4"
    assert GB.suggest({"description": "VAT - Payments to/from HMC&E", "link": "AR_tax", "rate": D(0)}) == "none"
    assert GB.suggest({"description": "ICT Costs", "link": "AP_tax", "rate": D(0), "category": "E"}) == "none"
    assert GB.suggest({"description": "VAT due, domestic reverse charge 20%", "link": "AP_tax",
                       "rate": D("-0.2")}) == "1R"
    assert GB.suggest({"description": "VAT due on services from abroad 20%", "link": "AP_tax",
                       "rate": D("-0.2")}) == "1"
    assert GB.suggest({"description": "Postponed import VAT 20%", "link": "AR_tax:AP_tax",
                       "rate": D("-0.2")}) == "1R"


def test_settlement_may_be_unused_tax_account(conn, config):
    """Administratie C: 1520 "Payments to/from HMC&E" staat in SQL-Ledger als btw-rekening; als afdrachtrekening mag dat als
    hij niet in de aangifte telt."""
    v = VatReturns(conn, config, country="GB")
    ids = {a["accno"]: a["id"] for a in v.tax_accounts()}
    with pytest.raises(ValidationError, match="geen btw-rekening"):
        v.save_settings({ids["1801"]: "4"}, "1801", "")
    v.save_settings({ids["1801"]: "none"}, "1801", "")
    assert v.accounts_setting() == ("1801", "")


# ------------------------------------------------------------------ tijdvakken (BL7)

def test_stagger_quarters():
    p = period_of("quarter", date(2026, 3, 15), 1)          # kwartalen eindigend apr/jul/okt/jan
    assert (p.fromdate, p.todate, p.number) == (date(2026, 2, 1), date(2026, 4, 30), 0)
    p = period_of("quarter", date(2026, 1, 10), 1)
    assert (p.fromdate, p.todate) == (date(2025, 11, 1), date(2026, 1, 31))
    p = period_of("quarter", date(2026, 12, 31), 2)         # mei/aug/nov/feb
    assert (p.fromdate, p.todate) == (date(2026, 12, 1), date(2027, 2, 28))
    assert last_closed("quarter", date(2026, 5, 3), 1).todate == date(2026, 4, 30)
    keys = [x.todate for x in recent_periods("quarter", date(2026, 5, 3), 3, 2)]
    assert keys == [date(2026, 5, 31), date(2026, 2, 28), date(2025, 11, 30), date(2025, 8, 31)]
    assert free_period(date(2026, 2, 1), date(2026, 4, 30)).kind == "quarter"
    assert period_of("quarter", date(2026, 3, 15)) == period_of("quarter", date(2026, 3, 15), 0)


def test_deadline_one_month_and_seven_days():
    assert gb_deadline(date(2026, 3, 31)) == date(2026, 5, 7)
    assert gb_deadline(date(2026, 1, 31)) == date(2026, 3, 7)       # 28 februari + 7
    assert gb_deadline(date(2026, 12, 31)) == date(2027, 2, 7)
    assert NL.deadline(date(2026, 3, 31)) == date(2026, 4, 30)


# ------------------------------------------------------------------ rekenen (UK1)

def test_sales_purchases_boxes(gb, al):
    sale(al, "100.60", {"1810": "21.13"})
    sale(al, "50", {"1811": "4.50"})
    sale(al, "40.40", {"1800": "8.48"}, vc="vendor", vc_id=VEND)
    rep = gb.report(Q1)
    assert boxes(rep) == {"1": (0, D("25.63")), "6": (D("150.60"), 0), "4": (0, D("8.48")), "7": (D("40.40"), 0)}
    # hele bedragen (gebruiker 2026-10-13): box 1 omlaag, box 4 omhoog, waardes afgekapt
    assert (rep.boxes["6"].base, rep.boxes["7"].base, rep.boxes["1"].tax, rep.boxes["4"].tax) == (150, 40, 25, 9)
    assert rep.totals == {"3": 25, "5": 16} and rep.total_exact == D("17.15")


def test_untaxed_sales_and_purchases_in_6_and_7(gb, al):
    sale(al, "300", {})
    sale(al, "200", {}, vc_id=CUST2)
    sale(al, "70.99", {}, vc="vendor", vc_id=VEND)          # UK1: ook inkoop zonder btw in box 7
    assert gb.save_customer_box(CUST2, "8", "t")            # goederen Noord-Ierland → EU: box 6 én 8
    with pytest.raises(ValidationError):
        gb.save_customer_box(CUST2, "3B")
    rep = gb.report(Q1)
    assert boxes(rep) == {"6": (D("500"), 0), "8": (D("200"), 0), "7": (D("70.99"), 0)}
    assert (rep.boxes["7"].base, rep.default_count, rep.customer_count) == (70, 2, 1)   # 300 verkoop + 70,99 inkoop
    assert VatReturns(gb.conn, gb.config, country="NL").customer_box(CUST2) == ""


def test_reverse_charge_services_and_domestic(gb, al):
    # diensten uit het buitenland: btw in 1 en 4, waarde in 6 en 7 (VAT Notice 700/12 §4.6)
    sale(al, "1000", {"2204": "-200", "2214": "200"}, vc="vendor", vc_id=VEND)
    # verlegd binnenland (bouw/CIS): btw in 1 en 4, waarde alleen in 7
    sale(al, "500", {"2205": "-100", "2215": "100"}, vc="vendor", vc_id=VEND)
    rep = gb.report(Q1)
    assert boxes(rep) == {"1": (0, D("300")), "4": (0, D("300")), "6": (D("1000"), 0), "7": (D("1500"), 0)}
    assert rep.totals["5"] == 0


def test_rounding_6_to_9_truncated():
    b = {c.code: BoxAmount() for c in GB.boxes}
    b["1"].tax_exact, b["4"].tax_exact = D("10.555"), D("20.10")
    b["6"].base_exact, b["7"].base_exact, b["8"].base_exact = D("99.99"), D("-12.70"), D("0.50")
    t = gb_round(b)
    assert (b["6"].base, b["7"].base, b["8"].base) == (99, -12, 0)
    assert (b["1"].tax, b["4"].tax) == (10, 21) and t == {"3": 10, "5": -11}


def test_gl_with_vat(gb, conn, config):
    GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=date(2031, 1, 20), reference="r1", description="Receipt",
        lines=[JournalLine(accno="8100", debit=D("50")), JournalLine(accno="1800", debit=D("10")),
               JournalLine(accno="1100", credit=D("60"))]))
    assert boxes(gb.report(Q1)) == {"4": (0, D("10")), "7": (D("50"), 0)}


# ------------------------------------------------------------------ btw-boeking (BL9)

def test_proposal_register_and_compare(gb, al, conn, config):
    sale(al, "100.60", {"1810": "20.12"})
    sale(al, "40", {"1800": "8"}, vc="vendor", vc_id=VEND)
    rep = gb.report(Q1)
    prop = gb.proposal(rep)
    assert {(ln["accno"], ln["amount"], ln["memo"]) for ln in prop["lines"]} == {
        ("1810", D("-20.12"), "1"), ("1800", D("8"), "4"), ("1850", D("12"), "5"), ("4999", D("0.12"), "")}
    gid = GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=prop["transdate"], description="VAT Return",
        lines=[JournalLine(accno=ln["accno"], debit=-ln["amount"] if ln["amount"] < 0 else D(0),
                           credit=ln["amount"] if ln["amount"] > 0 else D(0)) for ln in prop["lines"]]))
    gb.register(rep, gid, "t")
    again = gb.report(Q1)
    assert again.settlement_excluded == 1 and again.stored["gl_id"] == gid and again.difference is None
    assert conn.execute("SELECT country FROM x_vat_return").fetchone()[0] == "GB"
    assert VatReturns(conn, config, country="NL").stored(Q1) is None        # vastgelegd per land
    sale(al, "10", {"1810": "2"}, d=date(2031, 3, 15))
    diff = gb.report(Q1).difference
    assert diff.total == (D(12), D(14)) and diff.boxes["1"] == (0, 0, D(20), D(22))     # 20,12 → 20; 22,12 → 22
