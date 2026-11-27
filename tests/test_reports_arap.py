"""Controles op ouderdomsanalyse, aanmaningen, btw, betalingen en jaarafsluiting
(testdata uit compare/arap_seed.sql)."""

from datetime import date
from decimal import Decimal as D

import pytest

from sqlledger import ValidationError
from sqlledger.reports import Reports


@pytest.fixture
def rp(conn, config):
    return Reports(conn, config)


def test_aging_totals_and_no_double_count(rp):
    a = rp.aging("ar", todate=date(2026, 6, 30))
    # Bakkerij Cees (902) heeft twee contactpersonen; elke factuur telt één keer
    assert sorted(r["invnumber"] for r in a.rows if r["vc_id"] == 902) == ["F-3002", "F-3004", "F-3007"]
    for curr, t in a.totals.items():
        rows = [r for r in a.rows if r["curr"] == curr]
        assert t["total"] == sum(r[b] for r in rows for b in a.buckets)
    # USD-factuur 500 tegen koers 1.10
    usd = [r for r in a.rows if r["curr"] == "USD"]
    assert usd[0]["c30"] == D("454.55")


def test_aging_single_bucket_and_overdue(rp):
    all_ = rp.aging("ar", todate=date(2026, 6, 30))
    one = rp.aging("ar", todate=date(2026, 6, 30), buckets=["c0"])
    assert sum(t["total"] for t in one.totals.values()) <= sum(t["total"] for t in all_.totals.values())
    # op vervaldatum: F-3001 (verval 09-07) is nog niet vervallen en valt weg
    over = rp.aging("ar", todate=date(2026, 6, 30), overdue=True)
    assert "F-3001" not in [r["invnumber"] for r in over.rows]


def test_reminder_levels_roundtrip(rp):
    before = {r["invnumber"]: r["level"] for r in rp.reminders(asof=date(2026, 6, 30)).rows}
    assert before["F-3006"] == 2 and before["F-3007"] == 3 and before["F-3002"] == 1
    ids = {r["invnumber"]: r["id"] for r in rp.reminders(asof=date(2026, 6, 30)).rows}
    rp.save_reminder_levels({ids["F-3006"]: 3, ids["F-3007"]: 0})
    after = {r["invnumber"]: r["level"] for r in rp.reminders(asof=date(2026, 6, 30)).rows}
    assert after["F-3006"] == 4 and after["F-3007"] == 1


def test_tax_report_totals(rp):
    t = rp.tax_report("ar", accounts=["1810", "1811"], fromdate=date(2026, 1, 1), todate=date(2026, 6, 30))
    assert t.total["tax"] == sum(a["tax"] for a in t.account_totals)
    assert t.total["total"] == t.total["netamount"] + t.total["tax"]
    assert {a["accno"] for a in t.account_totals} == {"1810", "1811"}
    # niet goedgekeurde factuur 3010 telt niet mee
    assert 3010 not in [r["id"] for r in t.rows]


def test_payments_totals(rp):
    p = rp.payments("ar", accounts=["1100"], fromdate=date(2026, 1, 1), todate=date(2026, 6, 30))
    assert p.total == sum(x["paid"] for a in p.accounts for x in a["payments"])
    sources = {x["source"] for a in p.accounts for x in a["payments"]}
    assert {"BANK-302", "BANK-306", "KASSA"} <= sources


def test_yearend_balanced_and_closes_income(conn, rp):
    tid = rp.post_yearend(date(2026, 6, 30), "0550", description="Afsluiting H1")
    total = conn.execute("SELECT SUM(amount::numeric) FROM acc_trans WHERE trans_id = %s", (tid,)).fetchone()[0]
    assert total == 0
    tb = rp.trial_balance(todate=date(2026, 6, 30))
    for r in tb.rows:
        if r["charttype"] == "A" and r["category"] in ("I", "E"):
            assert round(r["endbalance"], 2) == 0, r["accno"]
    # opnieuw boeken vervangt de vorige afsluiting op dezelfde datum
    tid2 = rp.post_yearend(date(2026, 6, 30), "0550")
    assert conn.execute("SELECT count(*) FROM yearend WHERE transdate = '2026-06-30'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM gl WHERE id = %s", (tid,)).fetchone()[0] == 0
    assert tid2 != tid


def test_yearend_validation(rp):
    with pytest.raises(ValidationError):
        rp.post_yearend(date(2026, 6, 30), "1100")          # geen eigen-vermogenrekening
    rp.post_yearend(date(2026, 12, 31), "0550")
    with pytest.raises(ValidationError, match="Nothing to do"):
        rp.post_yearend(date(2027, 1, 1), "0550")          # alles al afgesloten
