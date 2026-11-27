"""Terugkerende boekingen in de webapp (fase 2, T1–T8): schema bij een boeking, lijst, verwerken."""

import re
from datetime import date, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_arap import page_fields
from webhelpers import csrf, login, make_user


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def save(client, tid, **data):
    page = client.get(f"/herhalen/{tid}").text
    values = {"reference": "", "description": "", "startdate": "", "repeat": "1", "unit": "months",
              "howmany": "", "action": "save", **data}
    return client.post(f"/herhalen/{tid}", data={"csrf": csrf(client, page), **values}, follow_redirects=False)


def schedule(cdb, tid):
    return cdb.execute("SELECT * FROM recurring WHERE id = %s", (tid,)).fetchone()


def text(r):
    return re.sub(r"\s+", " ", r.text)


# --- knoppen en rechten ------------------------------------------------------------------

def test_buttons_and_menu(booker):
    page = booker.get("/memoriaal/20101").text
    assert 'href="/herhalen/20101">Herhalen</a>' in page
    assert 'href="/terugkerend"' in page                                    # menu actief
    new = booker.get("/memoriaal/nieuw").text
    assert "Sla de boeking eerst op" in new and "/herhalen/" not in new
    assert 'href="/herhalen/3006">Herhalen</a>' in booker.get("/verkoop/3006").text
    assert 'href="/herhalen/4002">Herhalen</a>' in booker.get("/inkoop/4002").text


def test_rights_and_modules(client, db, company, cdb):
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "jan")])
    login(client, "kijk")
    assert client.get("/terugkerend").status_code == 403
    assert client.get("/herhalen/20101").status_code == 403
    assert "/herhalen/" not in client.get("/memoriaal/20101").text


def test_module_off(booker, db, company):
    db.execute("UPDATE company SET modules = array_remove(modules, 'recurring') WHERE id = %s", (company["id"],))
    assert booker.get("/terugkerend").status_code == 403
    assert booker.get("/herhalen/20101").status_code == 403
    assert "/herhalen/" not in booker.get("/memoriaal/20101").text


# --- schema (T1) ---------------------------------------------------------------------------

def test_save_schedule_gl(booker, cdb):
    page = booker.get("/herhalen/20101").text
    assert 'name="startdate" value="2026-01-31"' in page                   # standaard de boekdatum
    assert 'name="payment"' not in page                                     # alleen verkoop/inkoop
    r = save(booker, 20101, startdate="2026-01-31", howmany="4", reference="OMZ", description="Omzet")
    assert r.status_code == 303 and r.headers["location"] == "/memoriaal/20101?ok=recurring_saved"
    s = schedule(cdb, 20101)
    assert (s["startdate"], s["nextdate"], s["enddate"], s["unit"], s["howmany"]) == \
        (date(2026, 1, 31), date(2026, 2, 28), date(2026, 4, 30), "months", 4)
    page = booker.get("/memoriaal/20101?ok=recurring_saved").text
    assert "Herhaalschema opgeslagen." in page and "Herhaalschema actief" in page
    page = booker.get("/herhalen/20101").text
    assert 'name="nextdate" value="2026-02-28"' in page and "Schema verwijderen" in page
    assert "28-02-2026, 31-03-2026, 30-04-2026" in page or "2026-02-28, 2026-03-31, 2026-04-30" in page


@pytest.mark.parametrize("data, msg", [
    (dict(startdate="", howmany="3"), "Vul de startdatum in."),
    (dict(startdate="2027-01-01", howmany="0"), "Vul bij Aantal keer een getal"),
    (dict(startdate="2027-01-01", howmany="3", repeat="0"), "Vul bij Elke een getal"),
    (dict(startdate="2027-01-01", howmany="-1"), "Vul bij Aantal keer een getal"),
    (dict(startdate="2027-01-01", howmany="3", unit="fortnights"), "Kies dagen, weken, maanden of jaren."),
])
def test_validation(booker, cdb, data, msg):
    r = save(booker, 20101, **data)
    assert r.status_code == 400 and msg in r.text
    assert schedule(cdb, 20101) is None


def test_ar_payment_and_delete(booker, cdb):
    page = booker.get("/herhalen/3006").text
    assert 'name="payment"' in page
    r = save(booker, 3006, startdate="2027-01-01", howmany="2", payment="1")
    assert r.headers["location"] == "/verkoop/3006?ok=recurring_saved"
    assert schedule(cdb, 3006)["payment"] is True
    page = booker.get("/herhalen/3006").text
    r = booker.post("/herhalen/3006", data={"csrf": csrf(booker, page), "action": "delete"}, follow_redirects=False)
    assert r.headers["location"] == "/verkoop/3006?ok=recurring_deleted" and schedule(cdb, 3006) is None


def test_invoice_with_parts_schedule(booker, cdb):
    page = booker.get("/herhalen/3001").text
    assert 'name="startdate"' in page and "Verkoopfactuur" in page and 'href="/verkoopfactuur/3001"' in page
    r = save(booker, 3001, startdate="2027-01-01", howmany="2")
    assert r.headers["location"] == "/verkoopfactuur/3001?ok=recurring_saved"
    assert booker.get("/herhalen/999999").status_code == 404


# --- lijst en verwerken (T2) ------------------------------------------------------------

def test_list_and_process(booker, cdb):
    today = date.today()
    late = today - timedelta(days=3)
    save(booker, 20101, startdate=late.isoformat(), howmany="3", nextdate="")        # start na boekdatum
    save(booker, 3006, startdate=(today + timedelta(days=30)).isoformat(), howmany="2")
    page = booker.get("/terugkerend").text
    assert re.search(r'name="sel" value="20101" checked', page)                      # te laat: aangevinkt
    assert re.search(r'name="sel" value="3006"(?! checked)', page)                    # toekomst: niet
    assert "te laat (3 dagen)" in page
    fields = {**page_fields(page), "csrf": csrf(booker, page)}
    r = booker.post("/terugkerend", data=fields)
    assert r.status_code == 200
    t = text(r)
    assert "Verwerkt: 1, overgeslagen: 0" in t
    m = re.search(r'geboekt <a href="/memoriaal/(\d+)">(GL-\d+)</a>', t)
    assert m
    copy = cdb.execute("SELECT transdate, description FROM gl WHERE id = %s", (int(m.group(1)),)).fetchone()
    assert copy == {"transdate": late, "description": "OMZ-2026-01"}
    assert schedule(cdb, 20101)["nextdate"] > today
    # hetzelfde scherm nog eens versturen (dubbele POST): niets dubbel geboekt
    n = cdb.execute("SELECT count(*) AS n FROM gl").fetchone()["n"]
    r = booker.post("/terugkerend", data=fields)
    assert "intussen gewijzigd" in r.text and "Verwerkt: 0, overgeslagen: 1" in text(r)
    assert cdb.execute("SELECT count(*) AS n FROM gl").fetchone()["n"] == n


def test_process_with_edited_date_and_skip(booker, cdb):
    save(booker, 3003, startdate="2027-03-15", howmany="3")                           # USD, geen koers
    save(booker, 4002, startdate="2027-01-10", howmany="2", payment="1")
    page = booker.get("/terugkerend").text
    fields = {**page_fields(page), "csrf": csrf(booker, page)}
    fields.update({"sel": ["3003", "4002"], "next_4002": "2027-01-12"})
    r = booker.post("/terugkerend", data=fields)
    t = text(r)
    assert "Koers USD op 2027-03-15 ontbreekt" in t and "Verwerkt: 1, overgeslagen: 1" in t
    m = re.search(r'geboekt <a href="/inkoop/(\d+)">', t)
    row = cdb.execute("SELECT transdate, duedate, paid FROM ap WHERE id = %s", (int(m.group(1)),)).fetchone()
    assert row == {"transdate": date(2027, 1, 12), "duedate": date(2027, 2, 11), "paid": 300}
    assert schedule(cdb, 4002)["nextdate"] == date(2027, 2, 10)                    # schema schuift niet mee
    assert schedule(cdb, 3003)["nextdate"] == date(2027, 3, 15)


def test_nothing_selected(booker, cdb):
    save(booker, 20101, startdate="2027-01-15", howmany="2")
    page = booker.get("/terugkerend").text
    r = booker.post("/terugkerend", data={"csrf": csrf(booker, page)})
    assert r.status_code == 400 and "Vink minstens één schema aan." in r.text


def test_far_behind_needs_confirmation_and_stop(booker, cdb):
    """v1.0.1 PB16: meer dan één termijn achter = niet standaard aangevinkt en alleen met "toch verwerken";
    Stoppen haalt de volgende datum weg (schema afgelopen, boeking blijft)."""
    start = date.today() - timedelta(days=100)
    save(booker, 20101, startdate=start.isoformat(), howmany="12", nextdate=start.isoformat())
    page = booker.get("/terugkerend").text
    assert re.search(r'name="sel" value="20101"(?! checked)', page)
    assert "loopt 4 termijnen achter" in page and 'name="behind_ok" value="20101"' in page
    fields = {**page_fields(page), "csrf": csrf(booker, page), "sel": "20101"}
    fields.pop("behind_ok", None)
    n = cdb.execute("SELECT count(*) AS n FROM gl").fetchone()["n"]
    r = booker.post("/terugkerend", data=fields)
    assert "loopt 4 termijnen achter" in r.text and "Verwerkt: 0, overgeslagen: 1" in text(r)
    assert cdb.execute("SELECT count(*) AS n FROM gl").fetchone()["n"] == n
    r = booker.post("/terugkerend", data={**fields, "behind_ok": "20101"})
    assert "Verwerkt: 1, overgeslagen: 0" in text(r)
    from sqlledger.recurring import add_interval
    assert schedule(cdb, 20101)["nextdate"] == add_interval(start, 1, "months")     # één termijn, zoals altijd
    # stoppen
    page = booker.get("/terugkerend").text
    r = booker.post("/terugkerend/20101/stoppen", data={"csrf": csrf(booker, page), "back": "/terugkerend"})
    assert r.url.path == "/terugkerend" and "Herhaalschema gestopt." in r.text
    assert schedule(cdb, 20101)["nextdate"] is None
    assert cdb.execute("SELECT 1 FROM gl WHERE id = 20101").fetchone()
