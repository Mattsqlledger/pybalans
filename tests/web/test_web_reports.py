"""Rapporten in de webapp (stap 3).

De bedragen op het scherm moeten precies zijn wat de vergelijkingsfuncties in
compare/run_compare.py uit de bibliotheek halen; die zijn tegen de schermen van
rp.pl, ca.pl en aa.pl bewezen (R8). Zo toetsen deze tests dat de webtabel niets
weglaat, verschuift of zelf uitrekent.
"""

import html
import io
import re
import sys
from datetime import date
from pathlib import Path

import pytest

import sqlledger
from sqlledger import ArapLedger, ArapSearch, ChartOfAccounts, Reports, UserConfig
from sqlledger_web import routes_reports
from sqlledger_web.reportdefs import REPORTS, statement_columns
from webhelpers import login, make_user

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "compare"))
from run_compare import account_rows_python, arap_rows_python, report_rows_python  # noqa: E402

ROW_RE = re.compile(r'<tr class="k-(\w+)">(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<td class="([^"]*)">(.*?)</td>', re.S)


def table_rows(page: str):
    """(soort, [(class, tekst)]) per regel van de rapporttabel(len)."""
    out = []
    for kind, body in ROW_RE.findall(page):
        cells = [(cls, html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()) for cls, txt in CELL_RE.findall(body)]
        out.append((kind, cells))
    return out


def web_amounts(page: str):
    rows = [[t for cls, t in cells if "num" in cls and t] for _, cells in table_rows(page)]
    return [r for r in rows if r]


def nonempty(rows):
    return [r for r in rows if r]


@pytest.fixture
def lib(settings, company_db):
    """Bibliotheek op dezelfde administratie, in de notatie van de testgebruiker."""
    conn = sqlledger.connect(**settings.company_conninfo(company_db))
    yield lambda nf="1.000,00": (conn, UserConfig(login="lees", numberformat=nf, dateformat="dd-mm-yy",
                                                  countrycode="nl"))
    conn.close()


@pytest.fixture
def reader(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    assert client.get("/").status_code == 200
    return client


def set_numberformat(db, nf):
    db.execute("UPDATE app_user SET numberformat = %s WHERE login = 'lees'", (nf,))


Y2026 = {"run": "1", "period": "year", "year": "2026"}


# --- alle rapporten -----------------------------------------------------------

def test_every_report_renders(reader):
    for key, rep in REPORTS.items():
        r = reader.get(f"/rapporten/{key}")
        assert r.status_code == 200, key
        if not rep.autorun:
            assert "Kies de opties" in r.text, key
        r = reader.get(f"/rapporten/{key}", params={**Y2026, "accno": "1100"})
        assert r.status_code == 200, key
        assert 'class="error"' not in r.text, (key, re.findall(r'class="error"[^>]*>([^<]*)', r.text))
        # testdata: geen inkoop met/zonder btw in 2026; geen tijdkaarten (uren-per-project: test_web_jc)
        if key not in ("onbelast-inkoop", "btw-inkoop", "uren-per-project"):
            assert "Niets gevonden" not in r.text, key


def test_menu_links_to_reports(reader):
    page = reader.get("/").text
    for key in REPORTS:
        if key == "aanmaningen":        # menu: werkscherm /aanmaningen (stap 4a); de lijst is daar gelinkt
            assert 'href="/aanmaningen"' in page
            continue
        assert f'href="/rapporten/{key}"' in page, key


# --- bedragen gelijk aan de bewezen schermfuncties --------------------------------

@pytest.mark.parametrize("nf", ["1.000,00", "1,000.00"])
def test_trial_balance_matches_library(reader, db, lib, nf):
    set_numberformat(db, nf)
    page = reader.get("/rapporten/proefbalans", params={**Y2026, "l_heading": "1", "l_subtotal": "1"}).text
    rep = Reports(*lib(nf)).trial_balance(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31))
    expected = nonempty([[c for c in r[2:] if c] for r in report_rows_python("trial_balance", rep, nf)])
    assert web_amounts(page) == expected
    # doorklikken naar de mutaties van een rekening met dezelfde periode
    assert "/rapporten/rekeningmutaties?accno=1100&amp;fromdate=2026-01-01&amp;todate=2026-12-31&amp;run=1" in page


def _statement_expected(rep, nf):
    rows = report_rows_python("income_statement", rep, nf)
    shown = [d for d in rep.display if any([d.description] + [v for v in d.values if v is not None])]
    assert len(rows) == len(shown)
    return nonempty([[c for c in r[1:] if c] for r, d in zip(rows, shown) if d.kind != "heading"])


def test_income_statement_matches_library(reader, db, lib):
    page = reader.get("/rapporten/winst-en-verlies", params={
        **Y2026, "includeperiod": "quarter", "previousyear": "1", "l_heading": "1", "l_subtotal": "1",
        "l_accno": "1"}).text
    rep = Reports(*lib()).income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31),
                                           includeperiod="quarter", previousyear=True)
    assert web_amounts(page) == _statement_expected(_chrono(rep, "income"), "1.000,00")
    assert "Resultaat" in page and "Opbrengsten" in page
    assert len(rep.columns) == 10 and page.count('<th class="num">') == 10


def _chrono(rep, kind, reverse=False):
    """Het bibliotheekrapport in de kolomvolgorde van het scherm (PB22)."""
    order = statement_columns(rep.columns, kind, reverse)
    pos = {c: i for i, c in enumerate(rep.columns)}
    for d in rep.display:
        if d.values:
            d.values = [d.values[pos[c]] for c in order]
    rep.columns = order
    return rep


def _headers(page):
    return [" ".join(html.unescape(h).split()) for h in re.findall(r'<th class="num">(.*?)</th>', page, re.S)]


def test_statement_columns_order():
    """PB22: periodes van oud naar nieuw, geheel als laatste; per periode dit jaar, dan vorig jaar."""
    sl = [("this", 0), ("previous", 0), ("this", 4), ("previous", 4), ("this", 3), ("previous", 3),
          ("this", 2), ("previous", 2), ("this", 1), ("previous", 1)]
    assert statement_columns(sl, "income") == [("this", 1), ("previous", 1), ("this", 2), ("previous", 2),
                                                ("this", 3), ("previous", 3), ("this", 4), ("previous", 4),
                                                ("this", 0), ("previous", 0)]
    assert statement_columns(sl, "income", reverse=True)[:2] == [("previous", 0), ("this", 0)]
    assert statement_columns([("this", 0)], "income") == [("this", 0)]
    # balans: periode i ligt i maanden (kwartalen) vóór de peildatum (periode 0)
    bs = [("this", 0), ("this", 1), ("this", 2), ("this", 3)]
    assert statement_columns(bs, "balance") == [("this", 3), ("this", 2), ("this", 1), ("this", 0)]


def test_income_statement_months_january_first(reader, lib):
    params = {**Y2026, "includeperiod": "month", "l_heading": "1", "l_subtotal": "1"}
    page = reader.get("/rapporten/winst-en-verlies", params=params).text
    heads = _headers(page)
    assert len(heads) == 13, heads
    assert heads[0].startswith("01-01-2026") and heads[1].startswith("01-02-2026"), heads
    assert heads[-2].startswith("01-12-2026") and heads[-1].startswith("01-01-2026") and heads[-1].endswith("31-12-2026")
    rep = Reports(*lib()).income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31),
                                           includeperiod="month")
    assert web_amounts(page) == _statement_expected(_chrono(rep, "income"), "1.000,00")
    # CSV volgt dezelfde volgorde (Excel en PDF gebruiken dezelfde tabel)
    csv = reader.get("/rapporten/winst-en-verlies", params={**params, "export": "csv"}).text
    first = csv.splitlines()[0]
    assert first.index("01-02-2026") < first.index("01-12-2026")
    # "Omgekeerde weergave": de volgorde van SQL-Ledger (geheel vooraan, december → januari)
    page = reader.get("/rapporten/winst-en-verlies", params={**params, "reversedisplay": "1"}).text
    heads = _headers(page)
    assert heads[0].endswith("31-12-2026") and heads[1].startswith("01-12-2026") and heads[-1].startswith("01-01-2026")


def test_income_statement_quarters_previous_year(reader):
    page = reader.get("/rapporten/winst-en-verlies", params={**Y2026, "includeperiod": "quarter",
                                                              "previousyear": "1"}).text
    heads = _headers(page)
    assert len(heads) == 10, heads
    assert heads[0].startswith("01-01-2026") and heads[1].startswith("01-01-2025"), heads   # Q1 2026, Q1 2025
    assert heads[6].startswith("01-10-2026") and heads[7].startswith("01-10-2025"), heads   # Q4
    assert heads[8].startswith("01-01-2026") and heads[8].endswith("31-12-2026"), heads     # geheel 2026
    assert heads[9].startswith("01-01-2025") and heads[9].endswith("31-12-2025"), heads


def test_balance_sheet_quarters_oldest_first(reader, lib):
    page = reader.get("/rapporten/balans", params={"run": "1", "period": "", "todate": "2026-12-31",
                                                    "includeperiod": "quarter", "l_heading": "1",
                                                    "l_subtotal": "1"}).text
    heads = _headers(page)
    assert heads == ["31-03-2026", "30-06-2026", "30-09-2026", "31-12-2026"], heads
    rep = Reports(*lib()).balance_sheet(todate=date(2026, 12, 31), includeperiod="quarter")
    assert web_amounts(page) == _statement_expected(_chrono(rep, "balance"), "1.000,00")


def test_balance_sheet_matches_library(reader, db, lib):
    page = reader.get("/rapporten/balans", params={"run": "1", "period": "", "todate": "2026-06-30",
                                                    "previousyear": "1", "l_heading": "1",
                                                    "l_subtotal": "1"}).text
    rep = Reports(*lib()).balance_sheet(todate=date(2026, 6, 30), previousyear=True)
    assert web_amounts(page) == _statement_expected(rep, "1.000,00")
    assert "Resultaat lopend boekjaar" in page


@pytest.mark.parametrize("summary", ["1", "0"])
def test_aging_matches_library(reader, lib, summary):
    page = reader.get("/rapporten/ouderdom-verkoop", params={
        "run": "1", "period": "", "todate": "2026-12-31", "summary": summary,
        "buckets": ["c0", "c30", "c60", "c90"]}).text
    rep = Reports(*lib()).aging("ar", todate=date(2026, 12, 31), buckets=["c0", "c30", "c60", "c90"],
                                summary=summary == "1")
    assert web_amounts(page) == nonempty([a for _, a in arap_rows_python(rep, "1.000,00")])
    if summary == "0":
        assert 'href="/boekingen/3007"' in page


def test_tax_and_payments_match_library(reader, lib):
    rp = Reports(*lib())
    page = reader.get("/rapporten/btw-verkoop", params={**Y2026, "summary": "1", "l_subtotal": "1",
                                                         "sort": "name"}).text
    accounts = [a["accno"] for a in rp.tax_accounts("ar")["taxaccounts"]]
    rep = rp.tax_report("ar", accounts=accounts, fromdate=date(2026, 1, 1), todate=date(2026, 12, 31),
                        sort="name", l_subtotal=True)
    assert rep.rows and web_amounts(page) == nonempty([a for _, a in arap_rows_python(rep, "1.000,00")])

    page = reader.get("/rapporten/ontvangsten", params=Y2026).text
    accounts = [a["accno"] for a in rp.payment_accounts("ar")]
    rep = rp.payments("ar", accounts=accounts, fromdate=date(2026, 1, 1), todate=date(2026, 12, 31),
                      sort="transdate")   # rp.pl: standaard op datum
    assert web_amounts(page) == nonempty([a for _, a in arap_rows_python(rep, "1.000,00")])
    assert "1100 Interbank" in page


@pytest.mark.parametrize("params,search", [
    ({}, {}),
    ({"summary": "0"}, {"summary": False}),
    ({"l_subtotal": "1", "sort": "name", "l_curr": "1"}, {"l_subtotal": True, "sort": "name", "l_curr": True}),
])
def test_arap_list_matches_library(reader, lib, params, search):
    page = reader.get("/rapporten/overzicht-verkoop", params={**Y2026, **params}).text
    rep = ArapLedger(*lib()).list_transactions("customer", ArapSearch(
        transdatefrom=date(2026, 1, 1), transdateto=date(2026, 12, 31), **search))
    assert rep.rows and web_amounts(page) == nonempty([a for _, a in arap_rows_python(rep, "1.000,00")])


def test_outstanding_and_reminders(reader, lib):
    page = reader.get("/rapporten/openstaand-verkoop", params={"run": "1", "period": "", "todate": "2026-12-31"}).text
    rep = ArapLedger(*lib()).list_transactions("customer", ArapSearch(
        transdatefrom=None, transdateto=date(2026, 12, 31), outstanding=True))
    # openstaand: "Van" standaard leeg, dus ook facturen van eerdere jaren (golf 2 F2)
    assert web_amounts(page) == nonempty([a for _, a in arap_rows_python(rep, "1.000,00")])
    assert 'name="fromdate" value=""' in reader.get("/rapporten/openstaand-inkoop").text
    assert f'name="fromdate" value="{date.today().year}-01-01"' in reader.get("/rapporten/overzicht-verkoop").text
    page = reader.get("/rapporten/aanmaningen", params={"run": "1", "asof": "2026-12-31"}).text
    rep = Reports(*lib()).reminders(asof=date(2026, 12, 31))
    assert len([k for k, _ in table_rows(page) if k == "row"]) == len(rep.rows)


@pytest.mark.parametrize("args", [
    {"accno": "1100"},
    {"accno": "1100", "fromdate": "2026-03-05", "l_subtotal": "1", "sort": "reference", "direction": "DESC"},
    {"accno": "0235", "fromdate": "2026-06-01"},
    {"gifi_accno": "1110"},
    {"accno": "1200", "fromdate": "2026-03-05", "l_subtotal": "1", "sort": "transdate"},
])
def test_account_mutations_match_library(reader, lib, args):
    page = reader.get("/rapporten/rekeningmutaties", params={"run": "1", "period": "", "fromdate": "",
                                                              "todate": "", **args}).text
    a = dict(args)
    accno = a.pop("accno", None)
    kw = {"fromdate": date.fromisoformat(a["fromdate"]) if "fromdate" in a else None,
          "gifi_accno": a.get("gifi_accno"), "sort": a.get("sort"), "direction": a.get("direction", "ASC"),
          "l_subtotal": bool(a.get("l_subtotal"))}
    out = ChartOfAccounts(*lib()).account_transactions(accno, **kw)
    expected = nonempty([amts for _, amts in account_rows_python(out, "1.000,00")])
    assert out.rows and web_amounts(page) == expected


def test_account_mutations_pagination(reader, monkeypatch):
    monkeypatch.setattr(routes_reports, "PAGE_SIZE", 3)
    params = {"run": "1", "period": "", "accno": "1200", "fromdate": "2026-03-05"}
    first = table_rows(reader.get("/rapporten/rekeningmutaties", params=params).text)
    assert [k for k, _ in first][0] == "balance" and [k for k, _ in first][-1] == "total"
    assert len([k for k, _ in first if k == "row"]) == 3
    second = reader.get("/rapporten/rekeningmutaties", params={**params, "page": "2"}).text
    kinds = [k for k, _ in table_rows(second)]
    assert "balance" not in kinds and kinds[-1] == "total"
    assert "pagina 2 /" in second
    # export geeft altijd alles
    csv_text = reader.get("/rapporten/rekeningmutaties", params={**params, "export": "csv"}).text
    monkeypatch.setattr(routes_reports, "PAGE_SIZE", 1000)
    everything = table_rows(reader.get("/rapporten/rekeningmutaties", params=params).text)
    assert len(everything) > len(first)
    assert len(csv_text.strip().splitlines()) == 1 + len(everything)


# --- export, onthouden, toegang, boeking ----------------------------------------------

def test_exports(reader, db):
    params = {**Y2026, "l_heading": "1", "l_subtotal": "1"}
    r = reader.get("/rapporten/proefbalans", params={**params, "export": "csv"})
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="proefbalans.csv"' in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0] == "Rekening;Omschrijving;Beginsaldo;Debet;Credit;Eindsaldo"
    assert any(line.startswith("1100;Interbank;") and ",00" in line for line in lines)
    r = reader.get("/rapporten/proefbalans", params={**params, "export": "xlsx"})
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(r.content)).active
    values = [row for row in ws.iter_rows(values_only=True)]
    assert values[0][0] == "Proefbalans"
    row1100 = next(v for v in values if v and v[0] == "1100")
    assert isinstance(row1100[3], float)
    assert [r["action"] for r in db.execute(
        "SELECT action FROM audit_log WHERE action = 'report_exported'")] == ["report_exported"] * 2


def test_csv_has_no_formulas(reader, db, settings, company_db):
    import psycopg
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True) as c:
        c.execute("UPDATE customer SET name = '=HYPERLINK(1)' WHERE id = 900")
    r = reader.get("/rapporten/overzicht-verkoop", params={**Y2026, "export": "csv"})
    assert "'=HYPERLINK(1)" in r.text and ";=HYPERLINK" not in r.text


def test_preferences_remembered(reader):
    reader.get("/rapporten/winst-en-verlies", params={"run": "1", "period": "year", "year": "2025",
                                                       "includeperiod": "month"})
    page = reader.get("/rapporten/winst-en-verlies").text
    assert re.search(r'<option value="year" selected>', page)
    assert re.search(r"<option selected>2025</option>", page)
    assert re.search(r'name="includeperiod" value="month" checked', page)
    # andere rapporten houden hun eigen instellingen
    assert '<option value="" selected>' in reader.get("/rapporten/proefbalans").text


def test_module_off_and_unknown(reader, db, company):
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["gl", "reports"], company["id"]))
    assert reader.get("/rapporten/ouderdom-verkoop").status_code == 403
    assert reader.get("/rapporten/proefbalans").status_code == 200
    assert 'href="/rapporten/ouderdom-verkoop"' not in reader.get("/").text
    assert reader.get("/rapporten/bestaat-niet").status_code == 404


def test_invalid_date_shows_error(reader):
    r = reader.get("/rapporten/proefbalans", params={"run": "1", "period": "", "fromdate": "2026-13-45"})
    assert r.status_code == 200 and "Ongeldige datum" in r.text


def test_booking_page(reader):
    r = reader.get("/boekingen/3007", follow_redirects=False)       # factuur met artikelen: factuurscherm (F9)
    assert r.headers["location"] == "/verkoopfactuur/3007"
    page = reader.get("/verkoopfactuur/3007").text
    assert "F-3007" in page and "Bakkerij Cees" in page and "alleen lezen" in page
    page = reader.get("/boekingen/5001").text          # memoriaal: bewerkscherm (stap 4), hier alleen-lezen
    assert "Memoriaalboeking" in page and "MEM-BANK" in page and "alleen lezen" in page
    assert reader.get("/boekingen/999999").status_code == 404


def test_not_logged_in_redirects(client, db, company):
    make_user(db, "anna")                   # zonder gebruikers: naar /welkom (eerste start)
    r = client.get("/rapporten/proefbalans", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_method_default_from_settings(reader, lib):
    """VW1: "Rapporten standaard op kasbasis" (defaults.method) is de beginstand van "Stelsel"; zonder
    instelling Factuurstelsel; een eigen keuze in de url gaat voor."""
    conn, _ = lib()
    radio = re.compile(r'<input type="radio" name="method" value="(\w+)"([^>]*)>')

    def checked(page):
        return [v for v, rest in radio.findall(page) if "checked" in rest]

    assert checked(reader.get("/rapporten/winst-en-verlies").text) == ["accrual"]
    with conn.cursor() as cur:
        cur.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('method', 'cash')")
    conn.commit()
    try:
        for key in ("winst-en-verlies", "balans", "btw-verkoop"):
            assert checked(reader.get(f"/rapporten/{key}").text) == ["cash"], key
        page = reader.get("/rapporten/winst-en-verlies", params={**Y2026, "method": "accrual"}).text
        assert checked(page) == ["accrual"]
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM defaults WHERE fldname = 'method'")
        conn.commit()
