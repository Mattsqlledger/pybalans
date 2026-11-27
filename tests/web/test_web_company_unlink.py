"""Pakket 3 F27b: een uitgezette administratie ontkoppelen (rij weg, sjablonen- en mailmap weg, database blijft,
naam leesbaar in het logboek)."""

import html
import json
from pathlib import Path

import pytest

from webhelpers import login, make_user, nohelp, post


@pytest.fixture
def anna(client, db, company):
    secret = make_user(db, "anna", sysadmin=True, totp=True, memberships=[(company["id"], "admin")])
    login(client, "anna", secret)
    return db.execute("SELECT * FROM app_user WHERE login = 'anna'").fetchone()


def test_unlink_needs_inactive_and_typed_name(client, db, company, company_db, settings, pg_admin, anna):
    cid = company["id"]
    edit = f"/systeem/administraties/{cid}"
    url = f"{edit}/ontkoppelen"
    db.execute("INSERT INTO audit_log (company_id, action) VALUES (%s, 'login_ok')", (cid,))
    assert "Ontkoppelen kan alleen als de administratie uit staat" in nohelp(client.get(edit).text)
    r = client.get(url)
    assert r.status_code == 400 and "eerst uit" in html.unescape(r.text)

    db.execute("UPDATE company SET active = false WHERE id = %s", (cid,))
    assert "Administratie ontkoppelen" in nohelp(client.get(edit).text)
    page = client.get(url).text
    assert "Ja, administratie ontkoppelen" in page and "anna" in page

    # verkeerde naam: niets gebeurt
    r = post(client, url, {"confirm_name": "testbedrijf"})
    assert r.status_code == 400 and db.execute("SELECT 1 FROM company WHERE id = %s", (cid,)).fetchone()

    data = Path(settings.data_dir)
    for d in (data / "templates" / str(cid), data / "mail" / str(cid)):
        d.mkdir(parents=True)
        (d / "x.txt").write_text("x")
    r = post(client, url, {"confirm_name": "Testbedrijf"})
    assert r.url.path == "/systeem/administraties" and "De database staat nog op de databaseserver" in r.text
    assert not db.execute("SELECT 1 FROM company WHERE id = %s", (cid,)).fetchone()
    assert not db.execute("SELECT 1 FROM membership WHERE company_id = %s", (cid,)).fetchone()
    assert not (data / "templates" / str(cid)).exists() and not (data / "mail" / str(cid)).exists()
    assert pg_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (company_db,)).fetchone()
    d = db.execute("SELECT detail FROM audit_log WHERE action = 'company_unlinked'").fetchone()["detail"]
    assert (d["target"], d["dbname"], d["users"]) == ("Testbedrijf", company_db, ["anna"])
    log = client.get("/systeem/logboek?action=login_ok").text
    assert "Testbedrijf" in log                                        # oude regels houden de naam


def test_unlink_refused_while_restore_pending(client, db, company, settings, anna):
    db.execute("UPDATE company SET active = false WHERE id = %s", (company["id"],))
    runs = Path(settings.data_dir) / "restore-runs"
    runs.mkdir()
    (runs / "job1.json").write_text(json.dumps({"job": "job1", "dbname": company["dbname"], "created": True}))
    r = client.get(f"/systeem/administraties/{company['id']}/ontkoppelen")
    assert r.status_code == 400 and "inleestaak" in r.text
