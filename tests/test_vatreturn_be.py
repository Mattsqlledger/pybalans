"""Periodieke btw-aangifte België (sqlledger.vatforms.BE, BTW_LANDEN_ANALYSE.md BL1–BL14 en BE1–BE6)."""

from datetime import date
from decimal import Decimal

import pytest

from sqlledger import GeneralLedger, GLTransaction, JournalLine, ValidationError
from sqlledger.arap import ArapLine, ArapTransaction
from sqlledger.extensions import MIGRATIONS
from sqlledger.vatforms import BE, be_adjust, be_deadline, be_round
from sqlledger.vatreturn import BoxAmount, VatReturns, period_of, split_expense

from test_vatreturn import CUST, CUST2, VEND, al, boxes, one_rate  # noqa: F401  (fixtures)
from test_vatreturn_gb import add_tax_account

D = Decimal
Q1 = period_of("quarter", date(2031, 2, 1))


def add_account(conn, accno: str, category: str, link: str = "") -> int:
    return conn.execute("""INSERT INTO chart (accno, description, charttype, category, link)
                           VALUES (%s, %s, 'A', %s, %s) RETURNING id""",
                        (accno, f"Rekening {accno}", category, link)).fetchone()[0]


@pytest.fixture
def be(conn, config):
    """Belgische aangifte: 1810 21 % → 03, 1811 6 % → 01, 1800/1801 → 59; verlegde paren (−21 %) 4586 IC-verwerving,
    4588 IC-diensten, 4587 binnenland, 4557 invoer, met de aftrek op 4119 (+21 %, 59). Inkoop: 6040 handelsgoederen
    (81), 8100 diensten en diverse goederen (82, voorstel), 2300 installaties (83)."""
    for accno, rate in (("1810", "0.21"), ("1811", "0.06"), ("1800", "0.21"), ("1801", "0.06")):
        conn.execute("UPDATE tax SET rate = %s WHERE chart_id = (SELECT id FROM chart WHERE accno = %s)", (D(rate), accno))
    for accno in ("4586", "4588", "4587", "4557"):
        add_tax_account(conn, accno, "-0.21")
    add_tax_account(conn, "4119", "0.21")
    add_account(conn, "6040", "E", "AP_amount")
    add_account(conn, "2300", "A", "AP_amount")
    v = VatReturns(conn, config)
    v.save_country("BE", 0, "t")
    v = VatReturns(conn, config)
    ids = {a["accno"]: a["id"] for a in v.tax_accounts()}
    v.save_settings({ids["1810"]: "03", ids["1811"]: "01", ids["1800"]: "59", ids["1801"]: "59",
                     ids["4586"]: "86", ids["4588"]: "88", ids["4587"]: "87", ids["4557"]: "57",
                     ids["4119"]: "59"}, "1850", "4999", "t")
    acc = {a["accno"]: a["id"] for a in v.expense_accounts()}
    v.save_expense_boxes({acc["6040"]: "81", acc["2300"]: "83"}, "t")
    return v


def post(al, vc, lines, taxes, d=date(2031, 2, 10), vc_id=None):
    return al.post_transaction(ArapTransaction(
        vc=vc, vc_id=vc_id or (CUST if vc == "customer" else VEND), transdate=d,
        arap_accno="1200" if vc == "customer" else "1600",
        lines=[ArapLine(accno=a, amount=D(n)) for a, n in lines.items()],
        taxes={k: D(v) for k, v in taxes.items()}))


def sale(al, net, taxes, **kw):
    return post(al, "customer", {"8520": net}, taxes, **kw)


def purchase(al, lines, taxes, **kw):
    return post(al, "vendor", lines, taxes, **kw)


# ------------------------------------------------------------------ instellingen

def test_suggestions():
    assert BE.suggest({"description": "Te betalen btw 21 %", "link": "AR_tax", "rate": D("0.21")}) == "03"
    assert BE.suggest({"description": "TVA à payer 12 %", "link": "AR_tax", "rate": D("0.12")}) == "02"
    assert BE.suggest({"description": "Te betalen btw 6 %", "link": "AR_tax", "rate": D("0.06")}) == "01"
    assert BE.suggest({"description": "Terug te vorderen btw 21 %", "link": "AP_tax", "rate": D("0.21")}) == "59"
    assert BE.suggest({"description": "Rekening-courant btw", "link": "", "rate": D(0)}) == "none"
    assert BE.suggest({"description": "Verschuldigde btw intracommunautaire verwerving goederen 21 %",
                       "link": "AP_tax", "rate": D("-0.21")}) == "86"
    assert BE.suggest({"description": "Verschuldigde btw intracommunautaire diensten 21 %",
                       "link": "AP_tax", "rate": D("-0.21")}) == "88"
    assert BE.suggest({"description": "TVA due importations autoliquidées 21 %", "rate": D("-0.21")}) == "57"
    assert BE.suggest({"description": "Verschuldigde btw verlegd binnenland (medecontractant) 21 %",
                       "rate": D("-0.21")}) == "87"
    assert [BE.expense_suggest({"accno": a, "link": ln}) for a, ln in (
        ("604000", ""), ("340000", "IC"), ("612000", ""), ("230000", ""), ("1234", "IC"))] == [
        "81", "81", "82", "83", "81"]


def test_expense_accounts_and_save(be):
    acc = {a["accno"]: a for a in be.expense_accounts()}
    assert {"8100", "6040", "2300", "4999"} <= set(acc) and not {"1200", "1600", "1100", "1810"} & set(acc)
    assert (acc["6040"]["box"], acc["2300"]["box"], acc["8100"]["box"], acc["8100"]["suggested"]) == ("81", "83", "", "82")
    with pytest.raises(ValidationError, match="Onbekende rubriek"):
        be.save_expense_boxes({acc["8100"]["id"]: "54"})
    with pytest.raises(ValidationError, match="Onbekende rekening"):
        be.save_expense_boxes({1: "82"})
    assert be.save_expense_boxes({acc["8100"]["id"]: "82", acc["6040"]["id"]: "81"}) == ["8100"]
    # alleen voor België; andere landen hebben geen indeling per kostenrekening
    assert VatReturns(be.conn, be.config, country="NL").expense_accounts() == []


def test_migration_21_moves_old_rooster_54(conn):
    cid = conn.execute("SELECT id FROM chart WHERE accno = '1810'").fetchone()[0]
    conn.execute("UPDATE tax SET rate = 0.12 WHERE chart_id = %s", (cid,))
    VatReturns(conn).boxes()                    # schema bijwerken
    conn.execute("INSERT INTO x_vat_box (chart_id, box, country) VALUES (%s, '54', 'BE')", (cid,))
    conn.execute(MIGRATIONS[20])                # nogmaals: herhaalbaar
    assert conn.execute("SELECT box FROM x_vat_box WHERE country = 'BE'").fetchone()[0] == "02"


# ------------------------------------------------------------------ rekenen

def test_sales_and_purchases_per_rooster(be, al):
    sale(al, "1000", {"1810": "210"})
    sale(al, "100.55", {"1811": "6.03"})
    purchase(al, {"6040": "400", "8100": "100"}, {"1800": "105"})
    purchase(al, {"2300": "2000"}, {"1800": "420"})
    rep = be.report(Q1)
    assert boxes(rep) == {"03": (D(1000), 0), "01": (D("100.55"), 0), "54": (0, D("216.03")),
                          "81": (D(400), 0), "82": (D(100), 0), "83": (D(2000), 0), "59": (0, D(525))}
    # hele bedragen: grondslag en verschuldigde btw omlaag, aftrek omhoog
    assert (rep.boxes["01"].base, rep.boxes["54"].tax, rep.boxes["59"].tax) == (100, 216, 525)
    assert rep.totals == {"XX": 216, "YY": 525, "71": 0, "72": 309, "saldo": -309}


def test_untaxed_sales_customer_rooster_and_purchases(be, al):
    sale(al, "300", {})                                     # standaard 47
    sale(al, "200", {}, vc_id=CUST2)
    assert be.save_customer_box(CUST2, "46", "t")           # IC-levering
    with pytest.raises(ValidationError):
        be.save_customer_box(CUST2, "1E")
    purchase(al, {"8100": "70.99"}, {})                     # BE2: ook inkoop zonder btw in 81–83
    rep = be.report(Q1)
    assert boxes(rep) == {"47": (D(300), 0), "46": (D(200), 0), "82": (D("70.99"), 0)}
    assert (rep.default_count, rep.customer_count) == (1, 1)


def test_issued_credit_notes_48_49_64(be, al):
    """BE1: uitgereikte creditnota's niet verrekenen in 01–03/46 maar in 48/49 en de btw in 64."""
    sale(al, "1000", {"1810": "210"})
    sale(al, "-100", {"1810": "-21"})
    be.save_customer_box(CUST2, "46", "t")
    sale(al, "500", {}, vc_id=CUST2)
    sale(al, "-50", {}, vc_id=CUST2)
    rep = be.report(Q1)
    assert boxes(rep) == {"03": (D(1000), 0), "54": (0, D(210)), "49": (D(100), 0), "64": (0, D(21)),
                          "46": (D(500), 0), "48": (D(50), 0)}
    assert rep.totals["saldo"] == 189 and rep.total_exact == D(189)


def test_received_credit_note_63_85(be, al):
    """Ontvangen creditnota: btw in 63, bedrag in 85, en 81–83 "rekening houdend met de ontvangen creditnota's"."""
    purchase(al, {"8100": "1000"}, {"1800": "210"})
    purchase(al, {"8100": "-200"}, {"1800": "-42"})
    rep = be.report(Q1)
    assert boxes(rep) == {"82": (D(800), 0), "59": (0, D(210)), "85": (D(200), 0), "63": (0, D(42))}
    assert rep.totals == {"XX": 42, "YY": 210, "71": 0, "72": 168, "saldo": -168}


def test_reverse_charge_pairs(be, al):
    """BL5: IC-verwerving goederen 86 + 55, IC-diensten 88 + 55, verlegd binnenland 87 + 56, invoer 87 + 57; de aftrek
    in 59 en de grondslag ook in 81–83 (aankopen "aftrekbare btw niet inbegrepen")."""
    purchase(al, {"6040": "1000"}, {"4586": "-210", "4119": "210"})
    purchase(al, {"8100": "500"}, {"4588": "-105", "4119": "105"})
    purchase(al, {"2300": "300"}, {"4587": "-63", "4119": "63"})
    purchase(al, {"6040": "200"}, {"4557": "-42", "4119": "42"})
    rep = be.report(Q1)
    assert boxes(rep) == {"86": (D(1000), 0), "88": (D(500), 0), "87": (D(500), 0),
                          "55": (0, D(315)), "56": (0, D(63)), "57": (0, D(42)), "59": (0, D(420)),
                          "81": (D(1200), 0), "82": (D(500), 0), "83": (D(300), 0)}
    assert rep.totals["saldo"] == 0


def test_received_ic_credit_note_84(be, al):
    purchase(al, {"6040": "1000"}, {"4586": "-210", "4119": "210"})
    purchase(al, {"6040": "-100"}, {"4586": "21", "4119": "-21"})
    rep = be.report(Q1)
    assert boxes(rep) == {"86": (D(1000), 0), "55": (0, D(210)), "59": (0, D(210)), "81": (D(900), 0),
                          "84": (D(100), 0), "64": (0, D(21)), "63": (0, D(21))}


def test_gl_purchase_split_by_account(be, conn, config):
    GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=date(2031, 1, 20), reference="r1", description="Bonnetje",
        lines=[JournalLine(accno="8100", debit=D("30")), JournalLine(accno="2300", debit=D("70")),
               JournalLine(accno="1800", debit=D("21")), JournalLine(accno="1100", credit=D("121"))]))
    assert boxes(be.report(Q1)) == {"59": (0, D(21)), "82": (D(30), 0), "83": (D(70), 0)}


def test_purchase_payment_not_counted(be, al, conn, config):
    tid = purchase(al, {"8100": "100"}, {"1800": "21"})
    # betaling op dezelfde dag (bank AP_paid, crediteuren AP) telt niet als kostenregel
    conn.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate)
                    SELECT %s, id, -121, date '2031-02-10' FROM chart WHERE accno = '1100'
                    UNION ALL SELECT %s, id, 121, date '2031-02-10' FROM chart WHERE accno = '1600'""", (tid, tid))
    assert boxes(be.report(Q1))["82"] == (D(100), 0)


def test_split_expense():
    box = {1: "81", 2: "82", 3: "82"}.get
    assert split_expense(D("-100"), {1: D("-33.33"), 2: D("-33.33"), 3: D("-33.34")}, box, "82") == {
        "81": D("-33.33"), "82": D("-66.67")}
    assert split_expense(D("-10"), {}, box, "82") == {"82": D("-10")}
    assert split_expense(D(0), {1: D(5)}, box, "82") == {}


def test_adjust_and_rounding():
    b = {"01": [D(-10), 0], "44": [D(-5), 0], "54": [0, D("-0.60")], "59": [0, D(-2)], "86": [D(-7), 0],
         "81": [D(-12), 0]}
    be_adjust(b)
    assert b == {"49": [D(10), 0], "48": [D(5), 0], "64": [0, D("0.60")], "63": [0, D(2)], "84": [D(7), 0],
                 "81": [D(-12), 0], "85": [D(5), 0]}
    x = {c.code: BoxAmount() for c in BE.boxes}
    x["03"].base_exact, x["54"].tax_exact, x["59"].tax_exact, x["64"].tax_exact = (D("99.99"), D("20.99"),
                                                                                    D("10.01"), D("0.10"))
    t = be_round(x)
    assert (x["03"].base, x["54"].tax, x["59"].tax, x["64"].tax) == (99, 20, 11, 1)
    assert t == {"XX": 20, "YY": 12, "71": 8, "72": 0, "saldo": 8}


def test_deadline():
    assert be_deadline(date(2031, 3, 31), "quarter") == date(2031, 4, 25)
    assert be_deadline(date(2031, 3, 31), "month") == date(2031, 4, 20)
    assert be_deadline(date(2031, 12, 31), "month") == date(2032, 1, 20)


# ------------------------------------------------------------------ btw-boeking (BL9)

def test_proposal_register_and_compare(be, al, conn, config):
    sale(al, "1000.50", {"1810": "210.11"})
    purchase(al, {"8100": "100"}, {"1800": "21"})
    rep = be.report(Q1)
    assert rep.totals["71"] == 189 and rep.total_exact == D("189.11")
    prop = be.proposal(rep)
    assert {(ln["accno"], ln["amount"], ln["memo"]) for ln in prop["lines"]} == {
        ("1810", D("-210.11"), "03"), ("1800", D("21"), "59"), ("1850", D(189), "saldo"), ("4999", D("0.11"), "")}
    gid = GeneralLedger(conn, config).post_transaction(GLTransaction(
        transdate=prop["transdate"], description="Btw-aangifte",
        lines=[JournalLine(accno=ln["accno"], debit=-ln["amount"] if ln["amount"] < 0 else D(0),
                           credit=ln["amount"] if ln["amount"] > 0 else D(0)) for ln in prop["lines"]]))
    be.register(rep, gid, "t")
    again = be.report(Q1)
    assert again.settlement_excluded == 1 and again.difference is None
    assert conn.execute("SELECT country FROM x_vat_return").fetchone()[0] == "BE"
    sale(al, "-100", {"1810": "-21"}, d=date(2031, 3, 15))
    diff = be.report(Q1).difference
    assert diff.total == (D(189), D(168)) and set(diff.boxes) == {"49", "64"}
