"""Dashboard (fase 4 stap 5b, DB1–DB13; testdata uit compare/seed.sql + arap_seed.sql).

Elk getal moet gelijk zijn aan het rapport waar het dashboard naar doorklikt (DB7): open posten = ouderdomsanalyse
per factuur, bank = balans, resultaat = winst en verlies.
"""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger.amounts import round_amount
from sqlledger.dashboard import BUCKETS, Dashboard
from sqlledger.errors import ValidationError
from sqlledger.reports import Reports
from sqlledger.reports_arap import BUCKETS as AGING_BUCKETS

ASOF = date(2026, 6, 30)
ALL = ["ar", "ap", "cash", "oe", "quotations", "ic", "gl", "reports", "recurring", "projects"]


@pytest.fixture
def dash(conn, config):
    return Dashboard(conn, config)


@pytest.mark.parametrize("arap", ["ar", "ap"])
@pytest.mark.parametrize("today", [ASOF, date(2026, 5, 31), date(2026, 3, 1), date(2026, 10, 11)])
def test_open_items_equal_aging(dash, conn, config, arap, today):
    """Per factuur hetzelfde openstaande bedrag als de ouderdomsanalyse (die toont factuurvaluta)."""
    oi = dash.open_items(arap, today, rows=True)
    aging = Reports(conn, config).aging(arap, todate=today, buckets=AGING_BUCKETS)
    want = {r["id"]: (sum(r[b] for b in AGING_BUCKETS), r["exchangerate"]) for r in aging.rows}
    have = {r["id"]: r["open"] for r in oi.rows}
    assert set(have) == set(want)
    for i, (amount, rate) in want.items():
        assert round_amount(have[i] / rate, 2) == amount
    assert oi.total == sum(have.values()) and oi.count == len(have)
    assert sum(oi.buckets.values()) == oi.total


def test_buckets_from_due_date(dash):
    oi = dash.open_items("ar", ASOF, rows=True)
    by = {r["invnumber"]: r["bucket"] for r in oi.rows}
    assert by["F-3001"] == "current"            # vervalt 09-07
    assert by["F-3009"] == "d30"                # vervallen 29-06 (1 dag)
    assert by["F-3003"] == "d30"                # 03-06: 27 dagen
    assert by["F-3006"] == "older"              # 15-04: 76 dagen
    assert by["F-3007"] == "older"
    assert "F-3005" not in by                   # betaald
    assert oi.overdue_count == oi.count - 1 and oi.overdue == oi.total - oi.buckets["current"]
    assert list(oi.buckets) == list(BUCKETS)


def test_open_items_soon(dash):
    ap = dash.open_items("ap", date(2026, 7, 14), rows=True)
    soon = [r["invnumber"] for r in ap.rows if 0 >= (date(2026, 7, 14) - r["duedate"]).days >= -7]
    assert soon == ["I-4001"] and ap.soon_count == 1 and ap.soon == D("1210")
    assert dash.open_items("ap", date(2026, 7, 12)).soon_count == 0


def test_open_items_future_payment_counts_as_open(dash, conn):
    """Betaling ná de peildatum: op die datum nog open (zoals de ouderdomsanalyse, afwijking 193)."""
    before = dash.open_items("ar", date(2026, 4, 20), rows=True)
    assert "F-3005" in {r["invnumber"] for r in before.rows}


def test_bank_equals_balance_sheet(dash, conn, config):
    accounts = dash.bank(ASOF)
    assert [a.accno for a in accounts] == ["1100"]
    bs = Reports(conn, config).balance_sheet(todate=ASOF)
    assert accounts[0].balance == bs.accounts["A"]["1100"]["amounts"][("this", 0)]
    assert accounts[0].active and accounts[0].open_lines == 0 and accounts[0].statement_to is None


def test_bank_only_asset_payment_accounts(dash, conn):
    """DB13: rekening-courant (L) en W&V-rekeningen met de betaalkoppeling tellen niet mee; een rekening zonder
    saldo en zonder recente mutaties is niet actief."""
    conn.execute("""INSERT INTO chart (accno, description, charttype, category, link)
                    VALUES ('T1650', 'rc directie', 'A', 'L', 'AR_paid:AP_paid'),
                           ('T4499', 'Betalingsverschillen', 'A', 'E', 'AR_paid:AP_paid'),
                           ('T1001', 'Kas', 'A', 'A', 'AR_paid:AP_paid')""")
    accounts = {a.accno: a for a in dash.bank(ASOF)}
    assert set(accounts) == {"T1001", "1100"}
    assert not accounts["T1001"].active and accounts["T1001"].balance == 0


def test_results_equal_income_statement(dash, conn, config):
    res = dash.results(ASOF)
    st = Reports(conn, config).income_statement(fromdate=date(2026, 1, 1), todate=ASOF, previousyear=True)
    assert res.year == 2026 and not res.fallback and res.todate == ASOF
    assert res.ytd.income == st.totals["I"][("this", 0)] and res.ytd.expense == st.totals["E"][("this", 0)]
    assert res.ytd.result == st.result[("this", 0)]
    month = Reports(conn, config).income_statement(fromdate=date(2026, 6, 1), todate=ASOF)
    assert res.month == 6 and res.mtd.result == month.result[("this", 0)]
    assert len(res.months) == 12 and res.months[6][0] is None and res.months[5][0] == res.mtd.result
    assert sum(m[0] for m in res.months if m[0] is not None) == res.ytd.result


def test_results_fallback_to_last_year_with_bookings(dash):
    """DB9: lopend jaar zonder boekingen → laatste jaar met boekingen, heel jaar, maand = laatste maand."""
    res = dash.results(date(2028, 3, 1))
    assert res.fallback and res.year == 2026 and res.todate == date(2026, 12, 31)
    assert res.month == 7 and res.month_to == date(2026, 7, 31)
    assert all(m[0] is not None for m in res.months)


def test_results_january(dash, conn):
    """Alleen januari: de W&V maakt geen maandkolom; de maand is dan het jaar tot nu."""
    res = dash.results(date(2026, 1, 31))
    assert res.month == 1 and res.mtd.result == res.ytd.result == res.months[0][0]


def test_vat_period_setting(dash):
    assert dash.vat_period() == "quarter"
    assert dash.save_vat_period("month", "jan") is True
    assert dash.vat_period() == "month"
    assert dash.save_vat_period("month") is False
    with pytest.raises(ValidationError):
        dash.save_vat_period("week")


def test_vat_period_default_is_stored_when_saved(dash, conn):
    """Startlijst (eerste start keuze 12): ook de standaard kwartaal opslaan legt het tijdvak vast."""
    assert dash.save_vat_period("quarter") is False             # niets gewijzigd
    assert conn.execute("SELECT value FROM x_setting WHERE key = 'vat_return_period'").fetchone()[0] == "quarter"
    assert dash.save_vat_period("quarter") is False


def test_start_list(dash, conn):
    """Startlijst "Aan de slag" (eerste start keuze 12): automatische controle, niet nodig, verbergen, modules."""
    assert dash.start_list(ALL) is None                          # bestaande SL-database: geen startlijst (ES10 A)
    dash.enable_start_list("jan")
    items = {i.key: i for i in dash.start_list(ALL)}
    assert list(items) == ["company", "style", "vat", "bank", "opening", "invoice"]    # logo + huisstijl samen (HS10)
    # testdata: boekingen in gl en ar, nog geen huisstijl, tijdvak of IBAN
    assert items["opening"].done and items["invoice"].done
    assert not (items["vat"].done or items["bank"].done or items["style"].done)
    paid = conn.execute("SELECT id, description FROM chart WHERE link LIKE '%%AR_paid%%' ORDER BY accno").fetchall()
    bank = next((i for i, d in paid if "bank" in d.lower()), paid[0][0])
    assert items["bank"].target == bank                          # een bank zonder IBAN, vóór de kas (ES11)
    assert [i.key for i in dash.start_list(["gl"])] == ["company", "style", "opening"]
    dash.start_mark("style", "jan")
    dash.start_mark("style")                                     # twee keer mag
    assert {i.key: i for i in dash.start_list(ALL)}["style"].skipped
    with pytest.raises(ValidationError):
        dash.start_mark("bestaatniet")
    dash.save_vat_period("quarter")
    assert {i.key: i for i in dash.start_list(ALL)}["vat"].done
    dash.start_mark("hidden")
    assert dash.start_list(ALL) is None


def test_start_list_style(dash, conn):
    """HS10 A: klaar zodra de huisstijl één keer is opgeslagen (ook ongewijzigd); documentinstellingen tellen niet;
    een oud "niet nodig" bij logo telt als niet nodig."""
    from sqlledger import docsettings, docstyle
    dash.enable_start_list()
    s = docsettings.DocSettings(taxnumber="NL001234567B01")
    docsettings.save(conn, s)
    assert not {i.key: i for i in dash.start_list(ALL)}["style"].done
    docstyle.save(conn, docstyle.DocStyle())                    # ongewijzigd opslaan = "zo is het goed"
    assert {i.key: i for i in dash.start_list(ALL)}["style"].done
    conn.execute("DELETE FROM x_doc_setting WHERE key LIKE 'style.%%'")
    conn.execute("INSERT INTO x_task_done (kind, period_end) VALUES ('start_logo', '2000-01-01')")
    assert {i.key: i for i in dash.start_list(ALL)}["style"].skipped


def test_start_list_disappears_when_everything_done(dash):
    dash.enable_start_list()
    for key in ("company", "style", "vat", "bank"):              # rest is klaar in de testdata
        dash.start_mark(key)
    assert dash.start_list(["gl", "ar", "cash"]) is None


def test_vat_return_quarter(dash):
    vr = dash.vat_return(date(2026, 7, 10))
    assert (vr.fromdate, vr.todate, vr.deadline, vr.number) == \
        (date(2026, 4, 1), date(2026, 6, 30), date(2026, 7, 31), 2)
    assert dash.vat_return(date(2026, 7, 31)) is not None
    assert dash.vat_return(date(2026, 8, 3)) is None        # herinnering: termijn voorbij → vervallen
    assert dash.vat_done(date(2026, 6, 30), "jan") is True
    assert dash.vat_return(date(2026, 7, 10)) is None
    assert dash.vat_done(date(2026, 6, 30)) is False
    assert dash.vat_return(date(2027, 4, 10)) is None       # 1e kwartaal 2027: geen boekingen


def test_vat_return_month_year_none(dash):
    dash.save_vat_period("month")
    vr = dash.vat_return(date(2026, 7, 2))
    assert (vr.fromdate, vr.todate, vr.deadline, vr.number) == (date(2026, 6, 1), date(2026, 6, 30), date(2026, 7, 31), 6)
    dash.save_vat_period("year")
    vr = dash.vat_return(date(2027, 2, 1))
    assert (vr.fromdate, vr.todate, vr.deadline) == (date(2026, 1, 1), date(2026, 12, 31), date(2027, 3, 31))
    assert dash.vat_return(date(2027, 4, 1)) is None
    dash.save_vat_period("none")
    assert dash.vat_return(date(2026, 7, 2)) is None


def test_todo(dash, conn):
    kinds = {t.kind: t for t in dash.todo(ALL, ASOF)}
    assert set(kinds) >= {"ar_overdue", "ap_due"}
    assert "vat" not in kinds                               # 1e kwartaal: termijn 30 april voorbij → vervallen
    assert "vat" in {t.kind for t in dash.todo(ALL, date(2026, 7, 10))}
    ar = dash.open_items("ar", ASOF)
    assert (kinds["ar_overdue"].count, kinds["ar_overdue"].amount) == (ar.overdue_count, ar.overdue)
    assert dash.todo([], ASOF) == []
    only_ar = {t.kind for t in dash.todo(["ar"], ASOF)}
    assert only_ar == {"ar_overdue"}


def test_todo_parts_orders_recurring(dash, conn):
    conn.execute("UPDATE parts SET rop = onhand + 5 WHERE id = (SELECT min(id) FROM parts)")
    conn.execute("""INSERT INTO oe (ordnumber, transdate, reqdate, customer_id, vendor_id, amount, netamount,
                                    closed, quotation, curr)
                    VALUES ('SO-LAAT', '2026-06-01', '2026-06-10', (SELECT min(id) FROM customer), 0, 10, 10,
                            false, false, 'EUR')""")
    tid = conn.execute("SELECT min(id) FROM gl").fetchone()[0]
    conn.execute("""INSERT INTO recurring (id, reference, startdate, nextdate, enddate, repeat, unit, howmany, payment)
                    VALUES (%s, 'R1', '2026-06-01', '2026-06-29', '2027-01-01', 1, 'months', 12, false)""", (tid,))
    kinds = {t.kind: t.count for t in dash.todo(ALL, ASOF)}
    assert kinds["parts_short"] >= 1 and kinds["orders_late"] == 1 and kinds["recurring"] == 1
    kinds = {t.kind for t in dash.todo(["ar", "ap"], ASOF)}
    assert not kinds & {"parts_short", "orders_late", "recurring"}
