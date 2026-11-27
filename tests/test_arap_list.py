"""Overzicht verkoop-/inkoopboekingen (sqlledger.arap_list; testdata compare/arap_seed.sql)."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger import ArapLedger, ArapSearch


@pytest.fixture
def lg(conn, config):
    conn.execute("UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL")
    return ArapLedger(conn, config)


def test_summary_totals(lg):
    r = lg.list_transactions("customer", ArapSearch())
    assert len(r.rows) == len({x["id"] for x in r.rows})
    assert r.totals["amount"] == sum(x["amount"] for x in r.rows)
    assert r.totals["due"] == r.totals["amount"] - r.totals["paid"]
    assert r.display[-1].kind == "total"
    assert "F-3010" not in [x["invnumber"] for x in r.rows]        # niet goedgekeurd


def test_open_closed_and_outstanding(lg):
    open_ = {x["invnumber"] for x in lg.list_transactions("customer", ArapSearch(open=True)).rows}
    closed = {x["invnumber"] for x in lg.list_transactions("customer", ArapSearch(closed=True)).rows}
    assert open_.isdisjoint(closed) and "F-3005" in closed and "F-3001" in open_
    # openstaand per 31-05: F-3002 (juni) valt weg, F-3006 had toen 100 betaald
    out = lg.list_transactions("customer", ArapSearch(outstanding=True, transdateto=date(2026, 5, 31)))
    f3006 = next(x for x in out.rows if x["invnumber"] == "F-3006")
    assert f3006["paid"] == D("100") and "F-3002" not in {x["invnumber"] for x in out.rows}


def test_detail_counts_invoice_once(lg):
    s = lg.list_transactions("customer", ArapSearch())
    d = lg.list_transactions("customer", ArapSearch(summary=False))
    assert len(d.rows) > len(s.rows)
    assert d.totals["amount"] == s.totals["amount"]           # Perl telde per journaalregel
    assert d.totals["debit"] == d.totals["credit"]


def test_filters_and_sort(lg):
    assert [x["vc_id"] for x in lg.list_transactions("customer", ArapSearch(name="bakkerij")).rows] == [902] * 3
    assert {x["invnumber"] for x in lg.list_transactions("customer", ArapSearch(memo="termijn")).rows} == {"F-3006"}
    rows = lg.list_transactions("customer", ArapSearch(sort="amount", direction="DESC")).rows
    assert [x["amount"] for x in rows] == sorted((x["amount"] for x in rows), reverse=True)
    with pytest.raises(ValueError):
        lg.list_transactions("customer", ArapSearch(sort="amount; DROP TABLE ar"))


def test_currency_columns_and_subtotals(lg):
    r = lg.list_transactions("customer", ArapSearch(l_curr=True, l_subtotal=True, sort="name"))
    assert r.currencies == ["USD"] and "USD_due" in r.columns
    subs = [d for d in r.display if d.kind == "subtotal"]
    assert len(subs) == len({x["name"] for x in r.rows})
    i, j = r.columns.index("USD_due"), r.columns.index("USD_paid")
    usd = [d for d in subs if d.values[r.columns.index("USD_amount")]]
    assert usd and all(d.values[i] == d.values[r.columns.index("USD_amount")] - d.values[j] for d in usd)


def test_find_and_list_names(lg, conn):
    conn.execute("UPDATE customer SET enddate = '2026-05-31' WHERE id = 904")
    names = [r["name"] for r in lg.find_names("customer", name="")]
    assert "Kassaklant" not in names and names == sorted(names)            # beëindigd
    assert "Kassaklant" in [r["name"] for r in lg.find_names("customer", name="", transdate=date(2026, 5, 1))]
    assert [r["id"] for r in lg.find_names("customer", number="K002")] == [902]
    assert [r["id"] for r in lg.find_names("customer", name='"Klant A"')] == [900]
    openv = {r["id"] for r in lg.all_names("vendor", open_invoices=True)}
    assert openv == {901, 905}
    assert 904 in {r["id"] for r in lg.all_names("customer", transdate=date(2026, 6, 15), include_id=904)}
