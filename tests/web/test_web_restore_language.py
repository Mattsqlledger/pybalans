"""Back-up inlezen: documenttaal op het controlescherm en in de administratie zelf, btw/ICP-menu meteen goed (F25)."""

import uuid

import psycopg
import pytest

from conftest import DSN
from sqlledger import newcompany
from webhelpers import csrf, form_values, login, make_user, post, restore_backup
from test_web_arap import step
from test_web_restore import dump_template, upload


@pytest.fixture
def newdb(pg_admin):
    names = []

    def make():
        names.append(f"wl_{uuid.uuid4().hex[:10]}")
        return names[-1]
    yield make
    for name in names:
        pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def setting(dbname, key="doc_language"):
    with psycopg.connect(f"{DSN} dbname={dbname}") as conn:
        return newcompany.setting(conn, key)


def open_company(client, cid):
    client.post("/administratie", data={"csrf": csrf(client, client.get("/administratie").text), "company_id": cid})


def test_language_and_vat_flags_survive_backup(client, db, newdb):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    src = newdb()
    post(client, "/systeem/administraties/aanmaken",
         {"name": "Bron", "dbname": src, "chart": "nl-eenmanszaak", "language": "", "currency": "", "yearend": "12",
          "mod_gl": "1", "mod_ar": "1", "mod_ap": "1", "add_me": "1"})
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (src,)).fetchone()
    assert setting(src) == comp["doc_language"] == "nl"                    # nieuwe administratie: ook in x_setting
    with psycopg.connect(f"{DSN} dbname={src}", autocommit=True) as conn:     # ICP-rubriek 3B (ICP1)
        conn.execute("INSERT INTO x_vat_box (chart_id, box, country) SELECT id, '3B', 'NL' FROM chart "
                     "WHERE charttype = 'A' AND id NOT IN (SELECT chart_id FROM x_vat_box) ORDER BY accno LIMIT 1")
    # documenttaal wijzigen bij Beheer > Instellingen: ook in de administratie zelf
    open_company(client, comp["id"])
    page = client.get("/beheer/instellingen").text
    assert step(client, "/beheer/instellingen", {"doc_language": "fr"}, page).status_code == 303
    assert setting(src) == "fr"
    comp = db.execute("SELECT * FROM company WHERE id = %s", (comp["id"],)).fetchone()
    assert comp["doc_language"] == "fr" and comp["vat_return"] and comp["icp"]

    # back-up maken en inlezen: taal vooringevuld uit de back-up, btw- en ICP-menu meteen aan
    page = client.get("/systeem/back-up-maken").text
    dump = client.post(f"/systeem/back-up-maken/{comp['id']}", data={"csrf": csrf(client, page)}).content
    r = upload(client, dump)
    assert form_values(r.text)["language"] == "fr"
    target = newdb()
    r = restore_backup(client, dump, name="Kopie", dbname=target, add_me="1")
    assert "Back-up ingelezen" in r.text
    kopie = db.execute("SELECT * FROM company WHERE dbname = %s", (target,)).fetchone()
    assert (kopie["doc_language"], kopie["vat_return"], kopie["icp"]) == ("fr", True, True)
    assert setting(target) == "fr"


def test_language_choice_for_sl_backup(client, db, newdb):
    """Zonder taal in de back-up: die van de gebruiker; zelf te kiezen; onbekende taal geweigerd."""
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    db.execute("UPDATE app_user SET language = 'de' WHERE login = 'anna'")
    login(client, "anna", secret)
    r = upload(client, dump_template())
    v = form_values(r.text)
    assert v["language"] == "de" and "Belegsprache" in r.text
    v.update(language="xx", action="restore")
    r2 = client.post(r.url.path, data=v)
    assert r2.status_code == 400
    target = newdb()
    r = restore_backup(client, dump_template(), name="Engels", dbname=target, language="en")
    assert "eingelesen" in r.text or "Back-up ingelezen" in r.text
    row = db.execute("SELECT * FROM company WHERE dbname = %s", (target,)).fetchone()
    assert (row["doc_language"], row["vat_return"], row["icp"]) == ("en", False, False)   # niets ingesteld
    assert setting(target) == "en"


def test_language_default_nl():
    from sqlledger_web.routes_restore import default_language
    assert default_language({"language": "en"}, {"language": "de"}) == "en"
    assert default_language({"language": ""}, {"language": "fr"}) == "fr"
    assert default_language({}, {"language": "it"}) == "nl"
