"""Boekjaar instelbaar (bouwstap 2, JA1–JA8): hulpfuncties, instelling en dashboard.

Testdata uit compare/seed.sql + arap_seed.sql: W&V-boekingen in 2026 t/m juli.
"""

from datetime import date

import pytest

from sqlledger.admin import Admin
from sqlledger.dashboard import Dashboard
from sqlledger.dates import (fiscal_dates, fiscal_label, fiscal_label_for, fiscal_start_year, fiscal_year,
                             yearend_month)
from sqlledger.errors import ValidationError
from sqlledger.reports import Reports

ASOF = date(2026, 6, 30)


@pytest.mark.parametrize("value, month", [(None, 12), ("", 12), ("6", 6), (" 3 ", 3), ("12", 12), ("0", 12),
                                          ("13", 12), ("x", 12), ("1/31", 1), (7, 7)])
def test_yearend_month(value, month):
    assert yearend_month(value) == month


@pytest.mark.parametrize("d, endmonth, start, end", [
    (date(2026, 10, 13), 12, date(2026, 1, 1), date(2026, 12, 31)),
    (date(2026, 6, 30), 6, date(2025, 7, 1), date(2026, 6, 30)),
    (date(2026, 7, 1), 6, date(2026, 7, 1), date(2027, 6, 30)),
    (date(2026, 1, 31), 1, date(2025, 2, 1), date(2026, 1, 31)),
    (date(2026, 2, 1), 1, date(2026, 2, 1), date(2027, 1, 31)),
    (date(2027, 12, 31), 2, date(2027, 3, 1), date(2028, 2, 29)),       # schrikkeljaar
    (date(2026, 11, 30), 11, date(2025, 12, 1), date(2026, 11, 30)),
])
def test_fiscal_year(d, endmonth, start, end):
    assert fiscal_year(d, endmonth) == (start, end)
    assert fiscal_dates(fiscal_start_year(d, endmonth), endmonth) == (start, end)
    assert fiscal_label_for(start, end) == fiscal_label(start.year, endmonth)


def test_fiscal_label():
    assert fiscal_label(2026) == "2026" and fiscal_label(2025, 6) == "2025/2026"
    assert fiscal_label_for(date(2026, 1, 1), date(2026, 12, 31)) == "2026"
    assert fiscal_label_for(date(2025, 7, 1), date(2026, 6, 30)) == "2025/2026"
    assert fiscal_label_for(date(2025, 7, 1), date(2026, 6, 29)) is None
    assert fiscal_label_for(date(2025, 7, 2), date(2026, 7, 1)) is None
    assert fiscal_label_for(None, date(2026, 6, 30)) is None


# --------------------------------------------------------------------- instelling (JA1, JA2, JA5)

@pytest.fixture
def adm(conn, config):
    return Admin(conn, config)


def _yearend(conn):
    r = conn.execute("SELECT fldvalue FROM defaults WHERE fldname = 'yearend'").fetchone()
    return r[0] if r else None


def test_setting_default_and_save(adm, conn):
    conn.execute("DELETE FROM defaults WHERE fldname = 'yearend'")
    conn.execute("UPDATE defaults SET fldvalue = '1' WHERE fldname = 'audittrail'")
    assert adm.settings()["yearend"] == "12"
    assert adm.save_settings({"yearend": "12"}, {"yearend": "12"}) == []
    assert _yearend(conn) is None                       # niets geschreven: leeg = december
    assert adm.save_settings({"yearend": "6"}, {"yearend": "12"}) == ["yearend"]
    assert _yearend(conn) == "6" and adm.settings()["yearend"] == "6"
    row = conn.execute("""SELECT tablename, reference, formname, action FROM audittrail
                          WHERE formname = 'yearend'""").fetchone()
    assert row == ("defaults", "12 -> 6", "yearend", "saved")
    with pytest.raises(ValidationError, match="Boekjaar eindigt in"):
        adm.save_settings({"yearend": "13"}, {"yearend": "6"})
    assert _yearend(conn) == "6"


def test_setting_old_value_kept_when_not_changed(adm, conn):
    """Oude SL-waarde '1/31' blijft staan zolang de beheerder de maand niet wijzigt."""
    conn.execute("DELETE FROM defaults WHERE fldname = 'yearend'")
    conn.execute("INSERT INTO defaults VALUES ('yearend', '1/31')")
    assert adm.settings()["yearend"] == "1"
    assert adm.save_settings({"yearend": "1"}, {"yearend": "1"}) == []
    assert _yearend(conn) == "1/31"


def test_last_yearend(adm, conn, rp_post):
    assert adm.last_yearend() == date(2025, 12, 31)       # testdata
    rp_post(date(2026, 6, 30))
    assert adm.last_yearend() == date(2026, 6, 30)
    conn.execute("DELETE FROM yearend")
    assert adm.last_yearend() is None


@pytest.fixture
def rp_post(conn, config):
    return lambda d: Reports(conn, config).post_yearend(d, "0550")


# --------------------------------------------------------------------- dashboard (JA6)

@pytest.fixture
def dash(conn, config):
    return Dashboard(conn, config)


def _set_yearend(conn, month):
    conn.execute("DELETE FROM defaults WHERE fldname = 'yearend'")
    conn.execute("INSERT INTO defaults VALUES ('yearend', %s)", (str(month),))


def test_results_follow_fiscal_year(dash, conn, config):
    _set_yearend(conn, 3)
    res = dash.results(ASOF)
    assert (res.year, res.fromdate, res.todate, res.label, res.prev_label) == \
        (2026, date(2026, 4, 1), ASOF, "2026/2027", "2025/2026")
    st = Reports(conn, config).income_statement(fromdate=date(2026, 4, 1), todate=ASOF, previousyear=True)
    assert res.ytd.result == st.result[("this", 0)] and res.ytd_prev.result == st.result[("previous", 0)]
    assert [d.month for d in res.month_starts] == [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    assert res.month_starts[-1] == date(2027, 3, 1)
    assert [m[0] is None for m in res.months] == [False] * 3 + [True] * 9
    assert res.months[2][0] == res.mtd.result
    assert sum(m[0] for m in res.months if m[0] is not None) == res.ytd.result


def test_results_fiscal_first_month(dash, conn):
    """Eerste maand van het boekjaar: de W&V maakt geen maandkolom; de maand is het boekjaar tot nu."""
    _set_yearend(conn, 5)
    res = dash.results(date(2026, 6, 30))
    assert res.fromdate == date(2026, 6, 1) and res.month == 6
    assert res.mtd.result == res.ytd.result == res.months[0][0]


def test_results_fiscal_fallback(dash, conn):
    """DB9 met gebroken boekjaar: lopend boekjaar zonder boekingen → laatste boekjaar met boekingen, heel."""
    _set_yearend(conn, 6)
    res = dash.results(date(2027, 9, 1))
    assert res.fallback and res.year == 2026 and res.label == "2026/2027"
    assert (res.fromdate, res.todate) == (date(2026, 7, 1), date(2027, 6, 30))
    assert res.month == 7 and res.month_from == date(2026, 7, 1)


def test_results_calendar_unchanged(dash, conn):
    """Zonder instelling of met december: precies als voorheen (kalenderjaar)."""
    res = dash.results(ASOF)
    assert (res.year, res.fromdate, res.label, res.prev_label) == (2026, date(2026, 1, 1), "2026", "2025")
    _set_yearend(conn, 12)
    again = dash.results(ASOF)
    assert again.months == res.months and again.ytd == res.ytd
