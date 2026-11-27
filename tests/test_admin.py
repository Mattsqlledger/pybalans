"""Beheer (fase 2, A1–A8): rekeningschema, btw, instellingen, afsluiten, valuta en
koersen, afdelingen, projecten, talen, branches, betaalmethoden, audittrail, locks."""

from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import dict_row

from sqlledger import UserConfig, ValidationError
from sqlledger.admin import Account, Admin, fix_fxgainloss
from sqlledger.masterdata import MasterData, Project


@pytest.fixture
def adm(conn, config):
    return Admin(conn, config)


@pytest.fixture
def md(conn, config):
    return MasterData(conn, config)


def q(conn, sql, *params):
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()


def defaults(conn):
    return {r["fldname"]: r["fldvalue"] for r in q(conn, "SELECT * FROM defaults")}


def chart_id(conn, accno):
    return q(conn, "SELECT id FROM chart WHERE accno = %s", accno)[0]["id"]


# --- rekeningschema (A1) ---------------------------------------------------------------

def test_account_new_edit_delete(adm, conn):
    new = adm.save_account(Account(accno=" 4 99 7", description="Diverse  kosten -- test", category="E",
                                   link={"AP_amount", "AR_amount"}, gifi_accno="49'99"))
    row = q(conn, "SELECT * FROM chart WHERE id = %s", new)[0]
    assert (row["accno"], row["description"], row["link"], row["gifi_accno"]) == \
        ("4997", "Diverse kosten - test", "AR_amount:AP_amount", "4999")
    view = adm.get_account(new)
    assert view.can_delete and not view.has_transactions
    adm.save_account(Account(id=new, accno="4997", description="Diverse kosten", category="E", closed=True))
    assert q(conn, "SELECT closed, link FROM chart WHERE id = %s", new)[0] == {"closed": True, "link": ""}
    adm.delete_account(new)
    assert not q(conn, "SELECT 1 FROM chart WHERE id = %s", new)


@pytest.mark.parametrize("acc, msg", [
    (Account(accno="", category="A"), "Rekeningnummer ontbreekt"),
    (Account(accno="5", category=""), "Soort rekening"),
    (Account(accno="5", category="A", link={"AR", "AP"}), "maar voor één"),
    (Account(accno="5", category="A", link={"AR", "AR_paid"}), "hoofdrekening"),
    (Account(accno="5", category="A", link={"AR_paid", "AR_tax"}), "maar één van"),
    (Account(accno="1001", category="A"), "bestaat al"),
])
def test_account_checks(adm, acc, msg):
    with pytest.raises(ValidationError, match=msg):
        adm.save_account(acc)


def test_heading_has_no_links(adm, conn):
    new = adm.save_account(Account(accno="4998", category="E", charttype="H", link={"AP_amount"}))
    assert q(conn, "SELECT link, charttype FROM chart WHERE id = %s", new)[0] == {"link": "", "charttype": "H"}


def test_delete_account_refused(adm, conn):
    with pytest.raises(ValidationError, match="geboekt"):
        adm.delete_account(chart_id(conn, "1200"))
    with pytest.raises(ValidationError, match="standaardrekening"):
        adm.delete_account(chart_id(conn, "9220"))        # fxgainloss_accno_id
    with pytest.raises(ValidationError, match="in gebruik"):
        conn.execute("UPDATE customer SET payment_accno_id = %s WHERE id = 900", (chart_id(conn, "1119"),))
        adm.delete_account(chart_id(conn, "1119"))        # betaalrekening van een klant
    view = adm.get_account(chart_id(conn, "9220"))
    assert not view.can_delete and "fxgainloss_accno_id" in view.default_for


def test_tax_flag_keeps_history(adm, conn):
    cid = chart_id(conn, "1810")
    view = adm.get_account(cid)
    view.account.link -= {"AR_tax", "AP_tax", "IC_taxpart", "IC_taxservice"}
    adm.save_account(view.account)
    assert len(q(conn, "SELECT * FROM tax WHERE chart_id = %s", cid)) == 2     # Perl: weg
    new = adm.save_account(Account(accno="1812", description="Btw nul", category="L", link={"AR_tax"}))
    assert q(conn, "SELECT rate, validto FROM tax WHERE chart_id = %s", new) == [{"rate": 0.0, "validto": None}]


# --- btw (A2) ----------------------------------------------------------------------------

def tax_rows(adm):
    return [{k: r[k] for k in ("chart_id", "rate", "taxnumber", "validto")} for r in adm.taxes()]


def test_taxes_unchanged_and_changed(adm, conn):
    before = q(conn, "SELECT * FROM tax ORDER BY chart_id, validto")
    rows = tax_rows(adm)
    # testdata: rekening 1800 heeft twee regels zonder geldig-tot; ongewijzigd opslaan mag toch
    assert rows[0]["rate"] in (Decimal("19.00"), Decimal("21.00")) and rows[0]["validto"] is None
    adm.save_taxes(rows)
    assert q(conn, "SELECT * FROM tax ORDER BY chart_id, validto") == before
    cid = chart_id(conn, "1811")
    others = [r for r in rows if r["chart_id"] != cid]
    # BT1: 6% is gebruikt tot en met 10-06-2026 (testdata); eerder laten eindigen wordt geweigerd
    with pytest.raises(ValidationError, match="al gebruikt in boekingen tot en met 2026-06-10"):
        adm.save_taxes(others + [{"chart_id": cid, "rate": Decimal("6"), "taxnumber": "", "validto": date(2018, 12, 31)},
                                 {"chart_id": cid, "rate": Decimal("9"), "taxnumber": "", "validto": None}])
    rows = others + [
        {"chart_id": cid, "rate": Decimal("6"), "taxnumber": "", "validto": date(2026, 6, 30)},
        {"chart_id": cid, "rate": Decimal("9"), "taxnumber": "NL-LAAG", "validto": None},
        {"chart_id": cid, "rate": None, "taxnumber": "", "validto": None}]           # lege regel telt niet
    adm.save_taxes(rows, closed={cid: True})
    assert q(conn, "SELECT rate, taxnumber, validto FROM tax WHERE chart_id = %s ORDER BY validto", cid) == [
        {"rate": 0.06, "taxnumber": None, "validto": date(2026, 6, 30)},
        {"rate": 0.09, "taxnumber": "NL-LAAG", "validto": None}]
    assert q(conn, "SELECT closed FROM chart WHERE id = %s", cid)[0]["closed"] is True


@pytest.mark.parametrize("rows, msg", [
    ([{"rate": None, "validto": date(2020, 1, 1)}], "tarief"),
    ([{"rate": 9, "validto": None}, {"rate": 21, "validto": None}], "laatste"),
    ([{"rate": 9, "validto": date(2020, 1, 1)}, {"rate": 21, "validto": date(2019, 1, 1)}], "oplopen"),
    ([{"rate": -100, "validto": None}], "tussen -100% en 100%"),
    ([{"rate": 150, "validto": None}], "tussen -100% en 100%"),
])
def test_taxes_checks(adm, conn, rows, msg):
    cid = chart_id(conn, "1811")
    with pytest.raises(ValidationError, match=msg):
        adm.save_taxes([dict(r, chart_id=cid) for r in rows])


def test_taxes_negative_rate_for_reverse_charge(adm, conn):
    """Verlegde btw: een negatief tarief mag (zoals Perl; IS/IR/AA rekenen het met ``rate * ml > 0``)."""
    cid = adm.save_account(Account(accno="1572", description="Btw EU te betalen", category="L", link={"AP_tax"}))
    adm.save_taxes([{"chart_id": cid, "rate": Decimal("-21"), "taxnumber": "", "validto": None}])
    assert Decimal(str(q(conn, "SELECT rate FROM tax WHERE chart_id = %s", cid)[0]["rate"])) == Decimal("-0.21")


# --- instellingen (A3) -------------------------------------------------------------------

def test_settings_per_key(adm, conn):
    conn.execute("INSERT INTO defaults VALUES ('yearend', '6')")
    shown = adm.settings()
    before = defaults(conn)
    assert adm.save_settings(dict(shown), shown) == []
    assert defaults(conn) == before
    # intussen boekt iemand: de nummerteller loopt op; het oude scherm overschrijft hem niet
    conn.execute("UPDATE defaults SET fldvalue = 'GL-0100' WHERE fldname = 'glnumber'")
    changed = adm.save_settings(dict(shown, company="Nieuw BV", cdt="1", address="a\r\nb"), shown)
    assert sorted(changed) == ["address", "cdt", "company"]
    d = defaults(conn)
    assert (d["company"], d["cdt"], d["address"], d["glnumber"], d["yearend"]) == \
        ("Nieuw BV", "1", "a\nb", "GL-0100", "6")
    assert d["fxgain_accno_id"] == "10195"          # Perl gooide deze weg
    adm.save_settings({"cdt": ""}, {"cdt": "1"})
    assert "cdt" not in defaults(conn)
    with pytest.raises(ValidationError, match="Precisie"):
        adm.save_settings({"precision": "x"})
    with pytest.raises(ValidationError, match="koersverschillen"):
        adm.save_settings({"fxgainloss_accno_id": str(chart_id(conn, "1001"))})


def test_settings_max_upload_size(adm, conn):
    from sqlledger import attachments
    conn.execute("DELETE FROM defaults WHERE fldname = 'max_upload_size'")
    assert adm.settings()["max_upload_size"] == ""
    assert adm.save_settings({"max_upload_size": " 12 "}, {"max_upload_size": ""}) == ["max_upload_size"]
    assert defaults(conn)["max_upload_size"] == "12"
    assert adm.settings()["max_upload_size"] == "12"
    assert adm.save_settings({"max_upload_size": ""}, {"max_upload_size": "12"}) == ["max_upload_size"]
    assert "max_upload_size" not in defaults(conn)
    for bad in ("0", "26", "x", "2.5", "-1"):
        with pytest.raises(ValidationError, match="^Maximale uploadgrootte: een heel getal van 1 tot en met 25 MB"):
            adm.save_settings({"max_upload_size": bad})
    msg = "Maximale uploadgrootte: een heel getal van 1 tot en met 25 MB (leeg = 10 MB)."
    assert (attachments.MIN_MB, attachments.MAX_MB, attachments.DEFAULT_MB) == (1, 25, 10) and "25 MB" in msg
    # door SQL-Ledger 4.x geschreven waarden: getoond wat geldt, en ongewijzigd opslaan laat ze staan
    for raw, shown in (("0", ""), ("40", "25"), ("2.5", "2")):
        conn.execute("DELETE FROM defaults WHERE fldname = 'max_upload_size'")
        conn.execute("INSERT INTO defaults VALUES ('max_upload_size', %s)", (raw,))
        f = adm.settings()
        assert f["max_upload_size"] == shown
        assert adm.save_settings(dict(f), f) == []
        assert defaults(conn)["max_upload_size"] == raw


def test_closing(adm, conn):
    assert adm.closing()["closedto_date"] == date(2025, 12, 31)
    adm.save_closing(date(2026, 3, 31), revtrans=True, audittrail=False)
    d = defaults(conn)
    assert d["closedto"] == "20260331" and d["revtrans"] == "1" and "audittrail" not in d
    conn.execute("UPDATE defaults SET fldvalue = '20173112' WHERE fldname = 'closedto'")
    c = adm.closing()
    assert c["closedto"] == "20173112" and c["closedto_date"] is None
    adm.save_closing("20173112", revtrans=False, audittrail=True)    # ongeldige waarde blijft staan
    d = defaults(conn)
    assert d["closedto"] == "20173112" and "revtrans" not in d and d["audittrail"] == "1"
    adm.save_closing(None, False, True)
    assert "closedto" not in defaults(conn)


def test_fix_fxgainloss(conn):
    conn.execute("DELETE FROM defaults WHERE fldname = 'fxgainloss_accno_id'")
    assert fix_fxgainloss(conn) is None
    assert defaults(conn)["fxgainloss_accno_id"] == "10195"
    assert fix_fxgainloss(conn) is None                     # niets te doen
    conn.execute("DELETE FROM defaults WHERE fldname = 'fxgainloss_accno_id'")
    conn.execute("UPDATE defaults SET fldvalue = '10194' WHERE fldname = 'fxloss_accno_id'")
    assert "twee verschillende" in fix_fxgainloss(conn)
    assert "fxgainloss_accno_id" not in defaults(conn)


# --- audittrail en locks (A7) ---------------------------------------------------------------

def test_audit_and_locks(adm, md, conn):
    md.save_language("de", "Duits")
    rows = adm.audit_log(tablename="language")
    assert rows[0]["reference"] == "de" and rows[0]["action"] == "saved" and rows[0]["login"] == "admin"
    assert adm.audit_log(reference="D") and not adm.audit_log(reference="zz")
    assert "language" in adm.audit_actions()["tables"]
    conn.execute("INSERT INTO semaphore VALUES (5001, 'jan', 'gl', '9999999999'), (950, 'admin', 'ar', '1')")
    locks = {r["id"]: r for r in adm.locks()}
    assert locks[5001]["name"] == "Jan Jansen" and locks[5001]["kind"] == "gl" and not locks[5001]["expired"]
    assert locks[950]["expired"]
    assert adm.release_lock(5001) == 1
    assert adm.release_all_locks() == 1


# --- valuta en koersen (A5) ------------------------------------------------------------

def curr(conn):
    return [(r["curr"].strip(), r["rn"], r["prec"]) for r in q(conn, "SELECT * FROM curr ORDER BY rn")]


def test_currencies(md, conn):
    md.add_currency("gbp", 2)
    md.add_currency("JPY", 0)
    assert curr(conn) == [("EUR", 1, 2), ("USD", 2, 2), ("GBP", 3, 2), ("JPY", 4, 0)]
    with pytest.raises(ValidationError, match="bestaat al"):
        md.add_currency("EUR")
    with pytest.raises(ValidationError, match="drie letters"):
        md.add_currency("EU")
    with pytest.raises(ValidationError, match="bovenaan"):
        md.move_currency("USD", "up")                  # Perl: USD werd zo ongemerkt standaard
    with pytest.raises(ValidationError, match="bovenaan"):
        md.move_currency("EUR", "down")
    md.move_currency("JPY", "up")
    assert [c for c, *_ in curr(conn)] == ["EUR", "USD", "JPY", "GBP"]
    md.set_precision("JPY", "1")
    with pytest.raises(ValidationError, match="standaardvaluta kan niet"):
        md.delete_currency("EUR")
    with pytest.raises(ValidationError, match="in gebruik"):
        md.delete_currency("USD")                     # ar + koersen
    md.delete_currency("JPY")
    assert curr(conn) == [("EUR", 1, 2), ("USD", 2, 2), ("GBP", 3, 2)]
    objections, _ = md.default_change_check("GBP")
    assert any("geboekt" in o for o in objections) and any("koersen" in o for o in objections)
    with pytest.raises(ValidationError):
        md.set_default_currency("GBP")


def test_default_currency_empty_admin(md, conn):
    for t in ("acc_trans", "ar", "ap", "gl", "oe", "exchangerate"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("UPDATE customer SET curr = 'EUR' WHERE id = 900")
    objections, moving = md.default_change_check("USD")
    assert objections == [] and moving == {"customer": 1}
    md.set_default_currency("USD")
    assert [c for c, *_ in curr(conn)] == ["USD", "EUR"]
    assert q(conn, "SELECT curr FROM customer WHERE id = 900")[0]["curr"].strip() == "USD"


def test_exchangerates(md, conn):
    md.add_currency("GBP")
    d = date(2026, 7, 1)
    md.save_exchangerates(d, {"USD": Decimal("1.2"), "GBP": "0.85"})
    assert md.rates_on(d) == {"USD": Decimal("1.2"), "GBP": Decimal("0.85")}
    md.save_exchangerates(date(2026, 7, 2), {"USD": Decimal("1.2"), "GBP": ""}, olddate=d)   # verplaatsen
    assert md.rates_on(d) == {"USD": None, "GBP": None}
    assert md.rates_on(date(2026, 7, 2)) == {"USD": Decimal("1.2"), "GBP": None}
    md.delete_exchangerate("USD", date(2026, 7, 2))
    assert [r["transdate"] for r in md.exchangerates(curr="USD")] == [date(2026, 5, 20), date(2026, 6, 30)]
    with pytest.raises(ValidationError, match="vreemde valuta"):
        md.save_exchangerates(d, {"EUR": 1})


# --- afdelingen, projecten, talen, branches, betaalmethoden (A6) -----------------------------

def test_departments(md, conn):
    new = md.save_department("Service  desk", "C")
    rows = md.departments()
    assert [(r["description"], r["role"], r["rn"]) for r in rows] == \
        [("Verkoop", "P", 1), ("Inkoop", "C", 2), ("Service desk", "C", 3)]
    assert rows[0]["used"]                       # ar/gl
    md.move_department(new, "up")
    assert [r["id"] for r in md.departments()] == [600, new, 601]
    with pytest.raises(ValidationError, match="in gebruik"):
        md.delete_department(601)                # alleen gl (Perl keek alleen naar dpt_trans)
    md.delete_department(new)
    assert [(r["id"], r["rn"]) for r in md.departments()] == [(600, 1), (601, 2)]


def test_projects(md, conn):
    pid = md.save_project(Project(description="Nieuw", startdate=date(2026, 1, 1), customer_id=900))
    p = md.get_project(pid)
    assert p["projectnumber"] == "1" and p["customer"] == "Klant A" and not p["used"]
    with pytest.raises(ValidationError, match="bestaat al"):
        md.save_project(Project(projectnumber="P-001"))
    with pytest.raises(ValidationError, match="einddatum"):
        md.save_project(Project(projectnumber="X", startdate=date(2026, 2, 1), enddate=date(2026, 1, 1)))
    with pytest.raises(ValidationError, match="in gebruik"):
        md.delete_project(700)
    md.delete_project(pid)
    assert [p["projectnumber"] for p in md.projects()] == ["P-001", "P-002"]


def test_languages(md, conn):
    md.save_language("d e", "Duits  (Duitsland)")
    assert {r["code"]: r["description"] for r in md.languages()}["de"] == "Duits (Duitsland)"
    md.save_language("deu", "Duits", oldcode="de")
    conn.execute("UPDATE customer SET language_code = 'deu' WHERE id = 900")
    with pytest.raises(ValidationError, match="in gebruik"):
        md.save_language("ger", "Duits", oldcode="deu")
    md.save_language("deu", "Deutsch", oldcode="deu")
    with pytest.raises(ValidationError, match="bestaat al"):
        md.save_language("en", "Engels")
    with pytest.raises(ValidationError, match="in gebruik"):
        md.delete_language("deu")
    md.save_language("fr", "Frans")
    md.delete_language("fr")
    assert "fr" not in {r["code"] for r in md.languages()}


def test_business_and_paymentmethods(md, conn):
    b = md.save_business("Horeca", "5")
    assert q(conn, "SELECT discount, rn FROM business WHERE id = %s", b) == [{"discount": 0.05, "rn": 1}]
    assert md.business()[0]["discount"] == Decimal("5.00")
    conn.execute("UPDATE customer SET business_id = %s WHERE id = 900", (b,))
    with pytest.raises(ValidationError, match="in gebruik"):
        md.delete_business(b)
    p1 = md.save_paymentmethod("Contant", "0", "0.05")
    p2 = md.save_paymentmethod("Pin", "0.25")
    md.move_paymentmethod(p2, "up")
    assert [r["id"] for r in md.paymentmethods()] == [p2, p1]
    md.delete_paymentmethod(p2)
    assert [(r["id"], r["rn"]) for r in md.paymentmethods()] == [(p1, 1)]
