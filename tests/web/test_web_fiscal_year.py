"""Boekjaar instelbaar in de webapp (bouwstap 2, JA1–JA8): instellingen, periode "Boekjaar" in rapporten,
links van het dashboard en de boeking. De bedragen zelf: tests/test_fiscal_year.py en test_web_reports.py."""

import re
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from sqlledger.dates import fiscal_start_year, fiscal_year
from webhelpers import login, make_user, nohelp
from test_web_am import defaults, redirected, submit
from test_web_reports import web_amounts


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def admin(client, db, company):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "admin")])
    login(client, "baas", secret)
    return client


@pytest.fixture
def reader(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    return client


def set_yearend(cdb, month):
    cdb.execute("DELETE FROM defaults WHERE fldname = 'yearend'")
    if month is not None:
        cdb.execute("INSERT INTO defaults VALUES ('yearend', %s)", (str(month),))


# --------------------------------------------------------------------- instellingen (JA2, JA5)

def test_settings_yearend(admin, cdb):
    set_yearend(cdb, None)
    page = admin.get("/beheer/instellingen").text
    assert re.search(r'<select name="yearend">.*?<option value="12" selected>december</option>', page, re.S)
    assert "laatste jaarafsluiting is op 31-12-" in page          # testdata: afsluiting 31-12-2025
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen"), "/beheer/instellingen?ok=am_saved")
    assert "yearend" not in defaults(cdb)                          # niet gewijzigd: niets geschreven
    redirected(submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"yearend": "6"}),
               "/beheer/instellingen?ok=am_saved")
    assert defaults(cdb)["yearend"] == "6"
    assert '<option value="6" selected>juni</option>' in admin.get("/beheer/instellingen").text
    r = submit(admin, "/beheer/instellingen", "/beheer/instellingen", {"yearend": "13"})
    assert r.status_code == 400 and "Boekjaar eindigt in: kies een maand" in r.text
    assert defaults(cdb)["yearend"] == "6"


def test_settings_no_warning_without_closings(admin, cdb):
    cdb.execute("DELETE FROM yearend")
    assert "laatste jaarafsluiting" not in nohelp(admin.get("/beheer/instellingen").text)


# --------------------------------------------------------------------- rapporten (JA3 B, JA4)

def test_report_fiscal_choice_only_for_broken_year(reader, cdb):
    page = reader.get("/rapporten/winst-en-verlies").text
    assert 'value="fiscal"' not in page and 'name="fyear"' not in page
    assert f'name="fromdate" value="{date.today().year}-01-01"' in page        # zoals voorheen
    set_yearend(cdb, 6)
    page = reader.get("/rapporten/winst-en-verlies").text
    assert '<option value="fiscal">Boekjaar</option>' in page
    fy = fiscal_start_year(date.today(), 6)
    assert f'<option value="{fy}" selected>{fy}/{fy + 1}</option>' in page
    assert f'<option value="{fy - 1}">{fy - 1}/{fy}</option>' in page
    # standaardperiode: begin van het lopende boekjaar t/m vandaag
    assert f'name="fromdate" value="{fiscal_year(date.today(), 6)[0].isoformat()}"' in page


def test_report_fiscal_period_dates(reader, cdb):
    set_yearend(cdb, 6)
    fiscal = reader.get("/rapporten/winst-en-verlies",
                        params={"run": "1", "period": "fiscal", "fyear": "2025"}).text
    dates = reader.get("/rapporten/winst-en-verlies",
                       params={"run": "1", "period": "", "fromdate": "2025-07-01", "todate": "2026-06-30"}).text
    assert web_amounts(fiscal) == web_amounts(dates) and web_amounts(fiscal)
    assert "01-07-2025" in fiscal and "30-06-2026" in fiscal
    assert '<option value="fiscal" selected>Boekjaar</option>' in fiscal
    # balans per einde boekjaar
    bal = reader.get("/rapporten/balans", params={"run": "1", "period": "fiscal", "fyear": "2025"}).text
    per = reader.get("/rapporten/balans", params={"run": "1", "period": "", "todate": "2026-06-30"}).text
    assert web_amounts(bal) == web_amounts(per) and web_amounts(bal)


def test_report_fiscal_falls_back_to_year(reader, cdb):
    """Boekjaar weer gelijk aan het kalenderjaar (bv. bladwijzer van vroeger): "Boekjaar" wordt "Jaar"."""
    fiscal = reader.get("/rapporten/winst-en-verlies", params={"run": "1", "period": "fiscal", "fyear": "2026"}).text
    year = reader.get("/rapporten/winst-en-verlies", params={"run": "1", "period": "year", "year": "2026"}).text
    assert web_amounts(fiscal) == web_amounts(year)
    assert '<option value="year" selected>Jaar</option>' in fiscal


def test_report_prefs_do_not_store_yearend(reader, cdb, db):
    set_yearend(cdb, 6)
    reader.get("/rapporten/winst-en-verlies", params={"run": "1", "period": "fiscal", "fyear": "2025"})
    params = db.execute("SELECT params FROM report_pref WHERE report = 'winst-en-verlies'").fetchone()["params"]
    assert params["period"] == "fiscal" and params["fyear"] == "2025" and "_yearend" not in params


def test_income_statement_chart_label(reader, cdb):
    """Grafiek bij winst en verlies: een heel gebroken boekjaar heet "2025/2026"."""
    set_yearend(cdb, 6)
    page = reader.get("/rapporten/winst-en-verlies", params={"run": "1", "period": "fiscal", "fyear": "2025",
                                                             "weergave": "grafiek"}).text
    assert "2025/2026" in page


# --------------------------------------------------------------------- dashboard en boeking (JA6)

def test_dashboard_bank_links_follow_fiscal_year(reader, cdb):
    page = reader.get("/dashboard/blok/bank").text
    assert "period=year&amp;year=" in page
    set_yearend(cdb, 6)
    page = reader.get("/dashboard/blok/bank").text
    assert f"period=fiscal&amp;fyear={fiscal_start_year(date.today(), 6)}" in page


def test_dashboard_result_labels(reader, cdb):
    set_yearend(cdb, 3)
    page = reader.get("/dashboard/blok/resultaat").text
    fy = fiscal_start_year(date.today(), 3)
    assert f"{fy}/{fy + 1}" in page or f"{fy - 1}/{fy}" in page      # lopend boekjaar of DB9-terugval
