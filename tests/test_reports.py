"""Boekhoudkundige controles op de rapporten (testdata uit compare/report_seed.sql)."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger.dates import add_date
from sqlledger.reports import Reports


@pytest.fixture
def rp(conn, config):
    return Reports(conn, config)


def col0(st):
    return st.columns[0]


def test_trial_balance_debit_equals_credit(rp):
    for kwargs in ({}, {"fromdate": date(2026, 1, 1), "todate": date(2026, 6, 30)},
                   {"todate": date(2025, 12, 31)}, {"accounttype": "gifi"}):
        tb = rp.trial_balance(**kwargs)
        assert tb.total_debit == tb.total_credit
        assert tb.display[-1].kind == "total"


def test_trial_balance_beginning_plus_movement_is_ending(rp):
    tb = rp.trial_balance(fromdate=date(2026, 1, 1), todate=date(2026, 6, 30))
    for r in tb.rows:
        if r["charttype"] == "A":
            movement = (r["debit"] or 0) - (r["credit"] or 0)       # debet positief
            sign = -1 if r["category"] in ("A", "E") else 1
            if r["contra"]:
                sign = -sign
            assert round(r["endbalance"] - r["begbalance"], 2) == round(-movement * sign, 2)


def test_income_equals_current_earnings(rp):
    """Na de jaarafsluiting 2025 is het resultaat lopend boekjaar op de balans
    per 30-06-2026 gelijk aan het W&V-resultaat over 2026 t/m 30-06."""
    bs = rp.balance_sheet(todate=date(2026, 6, 30), decimalplaces=2)
    inc = rp.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 6, 30), decimalplaces=2)
    # rekening 9050 heeft 0.004: de W&V laat hem weg (0 op 2 decimalen), de bank
    # op de balans telt hem mee -- net als in SQL-Ledger. Afgerond gelijk.
    assert round(bs.result[col0(bs)], 2) == round(inc.result[col0(inc)], 2)
    # balans sluit: activa = passiva + eigen vermogen (incl. resultaat)
    c = col0(bs)
    assert bs.totals["A"][c] == bs.totals["L"][c] + bs.totals["Q"][c]


def test_yearend_excluded_from_income_statement(rp):
    inc = rp.income_statement(fromdate=date(2025, 1, 1), todate=date(2025, 12, 31), decimalplaces=2)
    # omzet 27000.10 + rente 45.67 - kosten 10800.33; de afsluitboeking telt niet mee
    assert inc.result[col0(inc)] == D("16245.44")


def test_monthly_columns_sum_to_total(rp):
    inc = rp.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 6, 30),
                              includeperiod="month", decimalplaces=2)
    total = inc.result[("this", 0)]
    months = sum(v for (y, p), v in inc.result.items() if p != 0)
    assert len(inc.columns) == 7 and months == total


def test_previous_year_columns(rp):
    inc = rp.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 6, 30),
                              previousyear=True, decimalplaces=2)
    assert inc.periods[0].previous == (date(2025, 1, 1), date(2025, 6, 30))
    assert inc.result[("previous", 0)] == D("27000.10") - D("9600.33")


def test_no_stale_subtotal_in_sections_without_heading(rp):
    """rp.pl toont in een sectie zonder kop een subtotaal met de naam van de
    laatste kop uit een vorige sectie; Python niet."""
    bs = rp.balance_sheet(todate=date(2026, 6, 30), decimalplaces=2)
    section = None
    headings = set()
    for row in bs.display:
        if row.kind == "section":
            section, headings = row.category, set()
        elif row.kind == "heading":
            headings.add(row.accno)
        elif row.kind == "subtotal":
            assert row.accno in headings, f"subtotaal {row.description} zonder kop in sectie {section}"


def test_decimalplaces_default_keeps_small_amounts(rp):
    inc = rp.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 6, 30))
    assert inc.info["decimalplaces"] == 2


def test_currency_conversion(rp):
    eur = rp.balance_sheet(todate=date(2026, 6, 30), decimalplaces=2)
    usd = rp.balance_sheet(todate=date(2026, 6, 30), decimalplaces=2, currency="USD")
    assert usd.exchangerate == D("1.25")
    assert usd.totals["A"][col0(usd)] == eur.totals["A"][col0(eur)] / D("1.25")


@pytest.mark.parametrize("d,n,unit,expected", [
    (date(2026, 3, 31), -1, "month", date(2026, 2, 28)),
    (date(2026, 3, 15), -1, "month", date(2026, 2, 15)),
    (date(2026, 1, 31), 1, "month", date(2026, 3, 3)),   # Perl telt 31 dagen op
    (date(2025, 2, 28), -1, "year", date(2024, 2, 29)),
    (date(2024, 2, 29), 1, "year", date(2025, 2, 28)),
    (date(2026, 6, 30), 5, "days", date(2026, 7, 5)),
])
def test_add_date(d, n, unit, expected):
    assert add_date(d, n, unit) == expected


def test_heading_only_in_other_period_still_gets_subtotal():
    """Kop zonder bedrag in periode 0 maar wel in een andere kolom (administratie A 2025: 'Vaste activa' alleen vorig
    jaar 30-9 met een borg): rp.pl section_display toont de regel als rekening en geeft wel het subtotaal."""
    from sqlledger.reports import StatementPeriod, _StatementBuilder
    o = {"decimalplaces": 2, "currency": "EUR", "exchangerate": D(1)}
    st = _StatementBuilder()
    h = lambda accno, desc, amt: {accno: {"accno": accno, "description": desc, "charttype": "H", "amount": amt}}
    a = lambda accno, desc, amt: {accno: {"accno": accno, "description": desc, "charttype": "A", "amount": amt}}
    st.add({**h("0100", "Vaste activa", D(-100)), **a("0150", "Borg", D(-100))}, "previous", 1, "A", o)
    st.add({**h("0999", "Liquide middelen", D(-50)), **a("1000", "Bank", D(-50))}, "this", 0, "A", o)
    st.add({**h("0999", "Liquide middelen", D(-50)), **a("1000", "Bank", D(-50))}, "previous", 0, "A", o)
    periods = {0: StatementPeriod(0, (None, date(2025, 12, 31)), (None, date(2024, 12, 31))),
               1: StatementPeriod(1, (None, date(2025, 9, 30)), (None, date(2024, 9, 30)))}
    fs = st.statement("balance_sheet", ("A",), periods, [0, 1], True, False, True, True, o, {}, None,
                      date(2025, 12, 31))
    rows = [(r.kind, r.description, [str(v) for v in r.values]) for r in fs.display]
    assert ("account", "Vaste activa", ["0", "0", "0", "0"]) in [(k, d, [str(D(x) + 0) for x in v]) for k, d, v in rows]
    sub = [r for r in fs.display if r.kind == "subtotal" and r.description == "Vaste activa"]
    assert len(sub) == 1 and sub[0].values == [D(0), D(0), D(0), D(100)]
