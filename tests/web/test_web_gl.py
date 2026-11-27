"""Memoriaal, koersverschilboeking, journaaloverzicht en jaarafsluiting in de webapp (stap 4)."""

import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

import sqlledger
from sqlledger import GeneralLedger, TransactionSearch, UserConfig
from webhelpers import csrf, login, make_user

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "compare"))
from run_compare import gl_rows_python  # noqa: E402
from test_web_reports import web_amounts  # noqa: E402


@pytest.fixture
def cdb(settings, company_db):
    """Directe verbinding met de administratie (controle)."""
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def lines(*rows):
    """Formuliervelden voor regels (rekening, debet, credit[, extra velden])."""
    data = {"rowcount": str(len(rows))}
    for i, r in enumerate(rows, 1):
        accno, debit, credit, *extra = r
        data.update({f"accno_{i}": accno, f"debit_{i}": debit, f"credit_{i}": credit})
        for k, v in (extra[0] if extra else {}).items():
            data[f"{k}_{i}"] = v
    return data


def submit(client, data, page="/memoriaal/nieuw", **kw):
    html = client.get(page).text
    return client.post("/memoriaal", data={"csrf": csrf(client, html), **data}, follow_redirects=False, **kw)


def posted_id(r):
    assert r.status_code == 303, r.text[:2000]
    m = re.fullmatch(r"/memoriaal/(\d+)\?ok=gl_posted", r.headers["location"])
    assert m, r.headers["location"]
    return int(m.group(1))


def acc(cdb, trans_id):
    return cdb.execute("""SELECT c.accno, ac.amount::numeric AS amount, ac.source, ac.memo, ac.project_id,
                                 ac.fx_transaction, ac.cleared
                          FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                          WHERE trans_id = %s ORDER BY c.accno, ac.amount""", (trans_id,)).fetchall()


BASE = {"transdate": "2026-07-01", "description": "Kasstorting", "action": "post"}


# --- invoeren ---------------------------------------------------------------------

def test_new_form(booker):
    page = booker.get("/memoriaal/nieuw").text
    assert "Memoriaalboeking toevoegen" in page and 'list="accounts"' in page
    assert '<option value="1001 Kas" data-n="1001" data-name="Kas">' in page          # zoekveld rekening (M5)
    assert page.count("<tr data-line>") == 5               # 4 regels + sjabloon
    assert "P-001" in page and "Verkoop" in page          # projecten en afdelingen bestaan
    assert 'name="currency"' in page                     # EUR en USD


def test_post_new(booker, cdb, db):
    r = submit(booker, {**BASE, **lines(("1001 Kas", "1.250,50", "", {"source": "K1", "project": "700"}),
                                         ("1100", "", "1.250,50", {"memo": "storting"}))})
    tid = posted_id(r)
    rows = acc(cdb, tid)
    assert [(x["accno"], x["amount"], x["source"], x["memo"], x["project_id"]) for x in rows] == [
        ("1001", Decimal("-1250.5"), "K1", None, 700), ("1100", Decimal("1250.5"), None, "storting", None)]
    gl = cdb.execute("SELECT * FROM gl WHERE id = %s", (tid,)).fetchone()
    assert gl["reference"] == "GL-0100" and gl["description"] == "Kasstorting"   # volgende nummer (M6)
    assert gl["employee_id"] == 501
    page = booker.get(f"/memoriaal/{tid}?ok=gl_posted").text
    assert "Boeking opgeslagen." in page and "Memoriaalboeking wijzigen" in page and "GL-0100" in page
    assert db.execute("SELECT detail FROM audit_log WHERE action = 'gl_posted'").fetchone()["detail"]["id"] == tid


def test_errors_keep_input(booker, cdb):
    before = cdb.execute("SELECT count(*) AS n FROM gl").fetchone()["n"]
    r = submit(booker, {**BASE, **lines(("1001", "10", ""), ("1100", "", "9,99"))})
    assert r.status_code == 400 and "Debet en credit zijn niet gelijk." in r.text
    assert 'value="9,99"' in r.text and ">Kasstorting</textarea>" in r.text
    r = submit(booker, {**BASE, **lines(("1001", "10,004", ""), ("1100", "", "10,004"))})
    assert "meer dan 2 decimalen" in r.text                                   # M15
    r = submit(booker, {**BASE, "transdate": "2025-12-31", **lines(("1001", "10", ""), ("1100", "", "10"))})
    assert "afgesloten periode" in r.text
    r = submit(booker, {**BASE, **lines(("ZZ99 Bestaat niet", "10", ""), ("1100", "", "10"))})
    assert "Onbekende rekening: ZZ99" in r.text
    r = submit(booker, {**BASE, **lines(("1001", "tien", ""), ("1100", "", "10"))})
    assert "Ongeldig bedrag: tien" in r.text
    r = submit(booker, {**BASE, **lines(("", "10", ""), ("1100", "", "10"))})
    assert "Regel 1: kies een rekening." in r.text
    assert cdb.execute("SELECT count(*) AS n FROM gl").fetchone()["n"] == before


def test_update_without_js_adds_row(booker):
    r = submit(booker, {**BASE, "action": "update", **lines(("1001", "1", ""), ("1100", "", "1"),
                                                             ("1200", "2", ""), ("1300", "", "2"))})
    assert r.status_code == 200 and r.text.count("<tr data-line>") == 6     # 4 gevuld + 1 leeg + sjabloon


def test_new_number(booker, cdb):
    r = submit(booker, {**BASE, "action": "new_number", **lines(("1001", "1", ""))})
    assert 'value="GL-0100"' in r.text
    assert cdb.execute("SELECT fldvalue FROM defaults WHERE fldname = 'glnumber'").fetchone()["fldvalue"] == "GL-0100"


def test_foreign_currency(booker, cdb):
    data = {**BASE, "currency": "USD", **lines(("1001", "100", ""), ("1100", "", "100"))}
    r = submit(booker, data)
    assert r.status_code == 400 and "Vul de koers in." in r.text
    tid = posted_id(submit(booker, {**data, "exchangerate": "0,9"}))
    assert cdb.execute("SELECT curr, exchangerate FROM gl WHERE id = %s", (tid,)).fetchone() == \
        {"curr": "USD", "exchangerate": 0.9}
    amounts = sorted((x["accno"], x["amount"], x["fx_transaction"]) for x in acc(cdb, tid))
    assert amounts == [("1001", Decimal(-100), False), ("1001", Decimal(10), True),
                       ("1100", Decimal(-10), True), ("1100", Decimal(100), False)]
    assert booker.get("/memoriaal-koers", params={"currency": "USD", "transdate": "2026-07-01"}).json() == {"rate": "0,9"}
    assert booker.get("/memoriaal-koers", params={"currency": "USD", "transdate": "2026-07-02"}).json() == {"rate": ""}


def test_fx_adjustment(booker, cdb):
    page = booker.get("/memoriaal/nieuw?fx=1").text
    assert "Koersverschilboeking toevoegen" in page and 'name="fx_1"' in page and 'name="currency"' not in page
    tid = posted_id(submit(booker, {**BASE, "fxadj": "1", **lines(("1100", "5", "", {"fx": "1"}),
                                                                    ("8520", "", "5"))}, page="/memoriaal/nieuw?fx=1"))
    rows = {x["accno"]: x for x in acc(cdb, tid)}
    assert rows["1100"]["fx_transaction"] and rows["1100"]["cleared"] == date(2026, 7, 1)
    assert not rows["8520"]["fx_transaction"] and rows["8520"]["cleared"] is None
    page = booker.get(f"/memoriaal/{tid}").text
    assert "Koersverschilboeking" in page and 'name="fx_1" value="1" checked' in page


# --- wijzigen, kopiëren, verwijderen ------------------------------------------------

def test_edit_needs_confirmation_and_keeps_schedule(booker, cdb):
    tid = posted_id(submit(booker, {**BASE, **lines(("1001", "10", ""), ("1100", "", "10"))}))
    cdb.execute("INSERT INTO recurring (id, reference, startdate, nextdate, repeat, unit, howmany, payment) "
                "VALUES (%s, 'x', '2026-07-01', '2026-08-01', 1, 'months', 12, '0')", (tid,))
    page = booker.get(f"/memoriaal/{tid}").text
    assert "Herhaalschema actief" in page and "maanden" in page
    data = {**BASE, "id": str(tid), "reference": "GL-0100", **lines(("1001", "20", ""), ("1100", "", "20"))}
    r = submit(booker, data, page=f"/memoriaal/{tid}")
    assert r.status_code == 200 and "U wijzigt een bestaande boeking." in r.text
    assert acc(cdb, tid)[0]["amount"] == Decimal(-10)
    r = submit(booker, {**data, "action": "post_confirmed"}, page=f"/memoriaal/{tid}")
    assert posted_id(r) == tid
    assert [x["amount"] for x in acc(cdb, tid)] == [Decimal(-20), Decimal(20)]
    assert cdb.execute("SELECT howmany FROM recurring WHERE id = %s", (tid,)).fetchone()["howmany"] == 12


def test_post_as_new_and_delete(booker, cdb):
    tid = posted_id(submit(booker, {**BASE, **lines(("1001", "10", ""), ("1100", "", "10"))}))
    data = {**BASE, "id": str(tid), "reference": "GL-0100", **lines(("1001", "30", ""), ("1100", "", "30"))}
    new = posted_id(submit(booker, {**data, "action": "post_as_new"}, page=f"/memoriaal/{tid}"))
    assert new != tid and [x["amount"] for x in acc(cdb, new)] == [Decimal(-30), Decimal(30)]
    assert [x["amount"] for x in acc(cdb, tid)] == [Decimal(-10), Decimal(10)]
    r = submit(booker, {**data, "action": "delete"}, page=f"/memoriaal/{tid}")
    assert "verwijderen?" in r.text and acc(cdb, tid)
    r = submit(booker, {**data, "action": "delete_confirmed"}, page=f"/memoriaal/{tid}")
    assert r.status_code == 303 and r.headers["location"] == "/memoriaal/nieuw?ok=gl_deleted"
    assert not acc(cdb, tid) and not cdb.execute("SELECT 1 FROM gl WHERE id = %s", (tid,)).fetchone()


def test_copy_link(booker):
    page = booker.get("/memoriaal/nieuw?kopie=5001").text
    assert 'name="id" value=""' in page and "MEM-BANK" in page and 'value="2,50"' in page


def test_closed_period_is_readonly(booker, cdb):
    cdb.execute("UPDATE defaults SET fldvalue = '20260630' WHERE fldname = 'closedto'")
    page = booker.get("/memoriaal/5001").text
    assert "afgesloten periode" in page and "alleen lezen" in page and 'value="post"' not in page
    assert "Kopiëren naar nieuwe boeking" in page


def test_lock_by_other_user(booker, client, db, company, cdb):
    make_user(db, "ander", memberships=[(company["id"], "bookkeeper", "admin")])
    page = booker.get("/memoriaal/5001").text            # jan opent en lockt
    assert 'value="post"' in page
    assert cdb.execute("SELECT login FROM semaphore WHERE id = 5001").fetchone()["login"] == "jan"
    booker.post("/uitloggen", data={"csrf": csrf(booker, page)})
    login(client, "ander")
    page = client.get("/memoriaal/5001").text
    assert "in gebruik door Jan Jansen" in page and 'value="post"' not in page
    r = submit(client, {**BASE, "id": "5001", "action": "post_confirmed",
                        **lines(("1100", "2,50", ""), ("8520", "", "2,50"))}, page="/memoriaal/nieuw")
    assert r.status_code == 400 and "in gebruik door Jan Jansen" in r.text


def test_readonly_role(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly")])
    login(client, "lees")
    assert client.get("/memoriaal/nieuw").status_code == 403
    page = client.get("/memoriaal/5001").text
    assert "alleen-lezen" in page and 'value="post"' not in page and "?kopie=" not in page
    html = client.get("/").text
    assert 'href="/memoriaal/nieuw"' not in html and 'href="/rapporten/journaal"' in html
    r = client.post("/memoriaal", data={"csrf": csrf(client, html), **BASE, **lines(("1001", "1", ""))})
    assert r.status_code == 403
    assert client.get("/boekingen/5001", follow_redirects=False).headers["location"] == "/memoriaal/5001"


def test_module_off(booker, db, company):
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (["ar", "ap", "reports"], company["id"]))
    assert booker.get("/memoriaal/nieuw").status_code == 403
    assert booker.get("/memoriaal/nieuw?fx=1").status_code == 403
    assert 'href="/memoriaal/nieuw"' not in booker.get("/").text


# --- journaaloverzicht ------------------------------------------------------------

@pytest.mark.parametrize("params,search", [
    ({}, {}),
    ({"splitledger": "1"}, {"splitledger": True}),
    ({"splitledger": "1", "l_subtotal": "1"}, {"splitledger": True, "l_subtotal": True}),
    ({"accno": "1100"}, {"accno": "1100"}),
    ({"l_subtotal": "1", "sort": "reference", "direction": "DESC"},
     {"l_subtotal": True, "sort": "reference", "direction": "DESC"}),
    ({"category": "E"}, {"category": "E"}),
])
def test_journal_matches_library(booker, settings, company_db, params, search):
    q = {"run": "1", "period": "year", "year": "2026", **params}
    page = booker.get("/rapporten/journaal", params=q).text
    assert 'class="error"' not in page
    with sqlledger.connect(**settings.company_conninfo(company_db)) as conn:
        cfg = UserConfig(login="jan", numberformat="1.000,00", dateformat="dd-mm-yy", countrycode="nl")
        out = GeneralLedger(conn, cfg).list_transactions(TransactionSearch(
            datefrom=date(2026, 1, 1), dateto=date(2026, 12, 31), **{"sort": "transdate", **search}))
    expected = [vals for kind, vals in gl_rows_python(out, "1.000,00") if vals]
    assert web_amounts(page) == expected
    if not (search.get("accno") or search.get("category")):
        assert 'href="/memoriaal/5001"' in page          # doorklikken naar het bewerkscherm


# --- jaarafsluiting ---------------------------------------------------------------

@pytest.fixture
def admin(client, db, company):
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "admin")])
    login(client, "baas", secret)
    return client


def test_yearend(admin, cdb, settings, company_db):
    page = admin.get("/beheer/jaarafsluiting").text
    assert "Jaarafsluiting" in page and 'value="0400"' in page
    html = page
    data = {"csrf": csrf(admin, html), "todate": "2026-06-30", "accno": "0550", "method": "accrual",
            "reference": "", "description": "Afsluiting H1"}
    r = admin.post("/beheer/jaarafsluiting", data={**data, "action": "preview"})
    assert r.status_code == 200 and "Voorbeeld per" in r.text and "Jaarafsluiting boeken" in r.text
    with sqlledger.connect(**settings.company_conninfo(company_db)) as conn:
        pre = sqlledger.Reports(conn, UserConfig(login="admin")).yearend_preview(date(2026, 6, 30), "0550")
    assert len(pre.lines) > 1 and pre.earnings
    before = cdb.execute("SELECT count(*) AS n FROM yearend").fetchone()["n"]
    r = admin.post("/beheer/jaarafsluiting", data={**data, "action": "post"}, follow_redirects=False)
    assert r.status_code == 303
    tid = int(re.search(r"/memoriaal/(\d+)", r.headers["location"]).group(1))
    assert cdb.execute("SELECT transdate FROM yearend WHERE trans_id = %s", (tid,)).fetchone()["transdate"] == \
        date(2026, 6, 30)
    assert len(acc(cdb, tid)) == len(pre.lines)
    assert cdb.execute("SELECT count(*) AS n FROM yearend").fetchone()["n"] == before + 1
    # voorstel daarna: de resultaatrekening van deze afsluiting (golf 2 F4)
    assert '<option value="0550" selected>' in admin.get("/beheer/jaarafsluiting").text
    r = admin.post("/beheer/jaarafsluiting", data={**data, "action": "preview"})
    assert "worden eerst verwijderd" in r.text
    r = admin.post("/beheer/jaarafsluiting", data={**data, "todate": "2025-12-31", "action": "preview"})
    assert "valt in de afgesloten periode (t/m 31-12-" in r.text and 'value="post"' not in r.text
    assert "Er is niets af te sluiten" not in r.text       # voorbeeld zonder de bestaande afsluiting 2025
    r = admin.post("/beheer/jaarafsluiting", data={**data, "todate": "2025-12-31", "action": "post"})
    assert r.status_code == 400 and "afgesloten periode" in r.text
    r = admin.post("/beheer/jaarafsluiting", data={**data, "accno": "1001", "action": "preview"})
    assert "Kies de rekening voor het resultaat." in r.text


def test_yearend_admin_only(booker):
    assert booker.get("/beheer/jaarafsluiting").status_code == 403
    assert 'href="/beheer/jaarafsluiting"' not in booker.get("/").text
