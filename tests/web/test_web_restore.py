"""Back-up inlezen (FASE6_ANALYSE.md stap 6a; v1.0.1 PB1–PB5, PB14): Systeem > Administraties > Back-up inlezen en
de eerste start, in twee stappen (uploaden → controlescherm → inlezen als achtergrondtaak met voortgang)."""

import gzip
import html
import io
import os
import subprocess
import time
import uuid

import psycopg
import pytest

from sqlledger import slbackup
from sqlledger_web import restorejobs
from webhelpers import csrf, form_values, login, make_user, restore_backup


@pytest.fixture
def newdb(pg_admin):
    name = f"wr_{uuid.uuid4().hex[:10]}"
    yield name
    pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def dump_template() -> bytes:
    env = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": "55432", "PGUSER": "postgres"}
    return gzip.compress(subprocess.run(["pg_dump", "-O", "-x", "sl_template"], env=env, capture_output=True,
                                        check=True).stdout)


def post_backup(client, url, data: bytes, **fields):
    return restore_backup(client, data, url, **fields)


def upload(client, data, url="/systeem/back-up", filename="backup.sql.gz"):
    page = client.get(url).text
    return client.post(url, data={"csrf": csrf(client, page)},
                       files={"bestand": (filename, io.BytesIO(data), "application/gzip")})


def test_restore_from_system(client, db, newdb):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    assert 'href="/systeem/back-up"' in client.get("/systeem/administraties").text
    r = post_backup(client, "/systeem/back-up", dump_template(), name="Teruggezet BV", code="TB",
                    dbname=newdb, add_me="1")
    assert r.status_code == 200 and "Back-up ingelezen" in r.text and newdb in r.text
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (newdb,)).fetchone()
    assert comp["name"] == "Teruggezet BV"
    assert db.execute("SELECT role FROM membership WHERE company_id = %s", (comp["id"],)).fetchone()["role"] == "admin"
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'company_restored'").fetchone()
    assert log["detail"]["kind"] == "pg_dump" and log["detail"]["version_to"] == "3.2.4"
    # dezelfde database nog eens: geweigerd op het controlescherm, niets aangeraakt
    r = post_backup(client, "/systeem/back-up", dump_template(), name="Ander", dbname=newdb)
    assert r.status_code == 400 and "bestaat al een database" in r.text


def test_refuses_non_backup_and_needs_sysadmin(client, db, newdb, pg_admin, settings):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = post_backup(client, "/systeem/back-up", b"CREATE ROLE x SUPERUSER;", name="X", dbname=newdb)
    assert r.status_code == 400 and "geen back-up" in r.text                 # al bij het uploaden
    assert not list((restorejobs.StagedBackups(settings.data_dir).dir).glob("*"))    # niets blijft staan
    bad = b"-- SQL-Ledger Backup\n-- Dataset: x\n--\nCOPY a TO PROGRAM 'id';\n"
    r = post_backup(client, "/systeem/back-up", bad, name="Y", dbname=newdb)
    assert r.status_code == 200 and "Inlezen mislukt" in r.text and "Regel 4" in r.text   # bij het inlezen
    assert "Er is niets aangemaakt" in r.text and "Opnieuw proberen" in r.text
    assert not pg_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (newdb,)).fetchone()
    assert not db.execute("SELECT 1 FROM company WHERE name = 'Y'").fetchone()
    make_user(db, "bert", totp=False)
    client.post("/uitloggen", data={"csrf": csrf(client, client.get("/").text)})
    login(client, "bert")
    assert client.get("/systeem/back-up").status_code == 403


RMA_DUMPS = os.path.join(os.path.dirname(__file__), "..", "data", "rma")


def test_restore_run_my_accounts_backup(client, db, newdb, company):
    """RI7: een back-up van Run my Accounts / ledger123 wordt herkend (controlescherm), ingelezen en gecontroleerd;
    het rapport staat op het resultaatscherm en later bij Beheer > Run my Accounts-gegevens (alleen daar in het
    menu)."""
    secret = make_user(db, "anna", sysadmin=True, totp=True, memberships=[(company["id"], "admin")])
    login(client, "anna", secret)
    data = open(os.path.join(RMA_DUMPS, "rma_test_ch.sql.gz"), "rb").read()
    r = upload(client, data, filename="rma_test_ch.sql.gz")
    page = html.unescape(r.text)
    assert "back-up van Run my Accounts / ledger123" in page and "Run my Accounts / ledger123, versie 2.8.52" in page
    r = post_backup(client, "/systeem/back-up", data, name="RMA AG", dbname=newdb, add_me="1")
    page = html.unescape(r.text)
    assert r.status_code == 200 and "Back-up ingelezen" in page, page[:3000]
    assert "Controle Run my Accounts / ledger123 (versie 2.8.52)" in page and "Gecontroleerd: de proefbalans" in page
    assert "Bijgewerkt van versie 2.8.52 naar 3.2.4" in page and "Voorraad per magazijn is nog niet omgezet" in page
    assert "Koersverschillen: Run my Accounts had een aparte winst- (36005 Kursgewinn) en verliesrekening (39006 Kursverlust)" in page
    assert "Klanten met aanmaningsstop" in page and "rma.invoicetax" in page
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (newdb,)).fetchone()
    assert comp["rma"] is True
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'company_restored'").fetchone()
    assert log["detail"]["kind"] == "rma" and log["detail"]["version_from"] == "2.8.52"
    # in de administratie: menu-item en vaste pagina
    tok = csrf(client, client.get("/").text)
    client.post("/administratie", data={"csrf": tok, "company_id": comp["id"]})
    page = html.unescape(client.get("/beheer/rma").text)
    assert "Run my Accounts-gegevens" in page and "Controle Run my Accounts / ledger123 (versie 2.8.52)" in page
    assert 'href="/beheer/rma"' in client.get("/").text
    # §12 VP1/VP5: herkomst zonder streepjes, punt "Run my Accounts-rapport nalezen" in de overstaplijst
    page = html.unescape(client.get("/beheer/ingebruikname").text)
    assert "Run my Accounts / ledger123: Test Treuhand GmbH, versie 2.8.52" in page and "—, versie" not in page
    assert "Run my Accounts-rapport nalezen" in page and "btw-aangifte voor dit land nog niet beschikbaar" in page
    # een gewone administratie: geen menu-item, pagina 404
    client.post("/administratie", data={"csrf": tok, "company_id": company["id"]})
    assert 'href="/beheer/rma"' not in client.get("/").text
    assert client.get("/beheer/rma").status_code == 404


def test_check_screen_prefills_and_explains(client, db, newdb, settings):
    """PB1–PB3: controlescherm met soort, bedrijfsnaam, laatste boeking; naam en databasenaam vooringevuld; uitleg
    onder Databasenaam; Annuleren ruimt het bestand op."""
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = upload(client, dump_template(), filename="sl_template.sql.gz")
    assert r.status_code == 200 and "/systeem/back-up/controle/" in r.url.path
    page = html.unescape(r.text)
    assert "pg_dump (platte SQL)" in page and "Testbedrijf BV" in page and "Laatste boeking" in page
    assert "Naam van de database op de server: kleine letters, cijfers en _. Wordt niet getoond in de app." in page
    v = form_values(r.text)
    assert v["name"] == "Testbedrijf BV" and v["dbname"] == "testbedrijf_bv" and v["code"] == ""
    assert v["employee"] == "none"                          # app-gebruiker "Anna" staat niet in de back-up
    staged = list(restorejobs.StagedBackups(settings.data_dir).dir.glob("*.bin"))
    assert len(staged) == 1
    # een ander mag het klaargezette bestand niet zien
    token = r.url.path.rsplit("/", 1)[1]
    r2 = client.post(r.url.path, data={"csrf": csrf(client, r.text), "action": "cancel"})
    assert r2.url.path == "/systeem/back-up" and not list(restorejobs.StagedBackups(settings.data_dir).dir.glob("*"))
    assert client.get(f"/systeem/back-up/controle/{token}").status_code == 404


def test_staged_file_only_for_same_user(client, db, settings):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = upload(client, dump_template())
    path = r.url.path
    client.post("/uitloggen", data={"csrf": csrf(client, client.get("/").text)})
    secret = make_user(db, "carla", sysadmin=True, totp=True)
    login(client, "carla", secret)
    assert client.get(path).status_code == 404


def test_wizard_two_steps(client, db, newdb):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = post_backup(client, "/welkom/administratie/back-up", dump_template(), name="Wizard BV", dbname=newdb,
                    add_me="1")
    assert r.status_code == 200 and "Back-up ingelezen" in r.text
    cid = db.execute("SELECT id FROM company WHERE dbname = %s", (newdb,)).fetchone()["id"]
    assert f'href="/welkom/gebruikers?administratie={cid}"' in r.text


def test_preview_of_sl_backup_with_company_at_the_end():
    """SL-back-up: bedrijfsnaam staat helemaal achteraan (defaults); datums dd-mm-jjjj (DateStyle EUROPEAN);
    medewerkers met en zonder login; ook als .gz."""
    text = ("-- SQL-Ledger Backup\n-- Dataset: proef\n-- Version: 2.8.37\n-- Date: Thu Apr 16 2026\n--\n"
            "set DateStyle to 'POSTGRES, EUROPEAN';\n"
            "INSERT INTO employee (id,login,name,notes,enddate) VALUES ('1',NULL,'Jan Jansen','oud\nadres',"
            "'13-12-2012');\n"
            "INSERT INTO employee (id,login,name,notes,enddate) VALUES ('2','Jansen','Jan Jansen','',NULL);\n"
            "INSERT INTO acc_trans (trans_id,amount,transdate) VALUES ('5','1','14-04-2026');\n"
            "INSERT INTO acc_trans (trans_id,amount,transdate) VALUES ('6','1','30-03-2026');\n"
            + "-- vulling\n" * 1000 +
            "INSERT INTO defaults (fldname,fldvalue) VALUES ('company','Voorbeeld''s Holding BV');\n")
    for data in (text.encode(), gzip.compress(text.encode())):
        b = slbackup.preview(data)
        assert (b.kind, b.dataset, b.version) == ("sl", "proef", "2.8.37")
        assert b.company == "Voorbeeld's Holding BV" and str(b.last_date) == "2026-04-14"
        assert [(e["id"], e["login"], e["name"]) for e in b.employees] == [
            ("1", "", "Jan Jansen"), ("2", "Jansen", "Jan Jansen")]
    from sqlledger_web.routes_restore import match_employee
    assert match_employee(b.employees, {"name": "jan  jansen", "login": "jan"}) == "e:2"
    assert match_employee(b.employees, {"name": "Iemand", "login": "jansen"}) == "e:2"
    assert match_employee(b.employees, {"name": "Iemand", "login": "x"}) == "none"


def test_employee_choice_links_login(client, db, newdb, settings):
    """PB14: "Jij bent in deze administratie": bestaande medewerker (login wordt employee_login) of nieuw."""
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    db.execute("UPDATE app_user SET name = 'Jan Jansen' WHERE login = 'anna'")
    login(client, "anna", secret)
    r = upload(client, dump_template())
    assert form_values(r.text)["employee"] == "e:501"               # Jan Jansen (login jan) uit sl_template
    r = restore_backup(client, dump_template(), name="Med BV", dbname=newdb, add_me="1")
    assert "Back-up ingelezen" in r.text
    m = db.execute("SELECT m.employee_login FROM membership m JOIN company c ON c.id = m.company_id "
                   "WHERE c.dbname = %s", (newdb,)).fetchone()
    assert m["employee_login"] == "jan"
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'company_restored'").fetchone()["detail"]
    assert log["employee"] == "jan"


def test_employee_new(client, db, pg_admin, settings):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    name = f"wr_{uuid.uuid4().hex[:10]}"
    try:
        r = restore_backup(client, dump_template(), name="Nieuw BV", dbname=name, add_me="1", employee="new")
        assert "Back-up ingelezen" in r.text
        with psycopg.connect(**settings.company_conninfo(name)) as c:
            assert c.execute("SELECT name FROM employee WHERE login = 'anna'").fetchone()[0] == "Anna"
        assert db.execute("SELECT employee_login FROM membership WHERE user_id = (SELECT id FROM app_user "
                          "WHERE login = 'anna')").fetchone()["employee_login"] == "anna"
    finally:
        pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_progress_page_and_one_at_a_time(client, db, newdb, settings, monkeypatch):
    """PB4/PB5: taakpagina met voortgang (progressbar, aria-live) en status als JSON; een tweede start terwijl er
    één loopt wordt geweigerd."""
    import threading
    from sqlledger_web import routes_restore
    gate = threading.Event()
    real = slbackup.restore

    def slow(admin, connect, dbname, data, progress=None):
        progress("restore", 40, 100)
        gate.wait(30)
        return real(admin, connect, dbname, data, progress)
    monkeypatch.setattr(routes_restore.slbackup, "restore", slow)
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = upload(client, dump_template())
    check = r.url.path
    r = client.post(check, data={"csrf": csrf(client, r.text), "action": "restore", "name": "Traag BV",
                                 "dbname": newdb})
    try:
        assert "/systeem/back-up/taak/" in r.url.path
        job = r.url.path
        for _i in range(50):
            state = client.get(f"{job}/stand").json()
            if state["phase"] == "restore":
                break
            time.sleep(0.05)
        assert state == {"running": True, "failed": False, "phase": "restore", "percent": 40,
                         "text": "Terugzetten: 40%"}
        page = client.get(job).text
        assert '<progress class="progressbar" max="100" value="40"' in page and 'aria-live="polite"' in page
        assert "Terugzetten: 40%" in page
        # terugkomen op Back-up inlezen: de stand staat erbij
        assert "Bezig met inlezen van Traag BV" in html.unescape(client.get("/systeem/back-up").text)
        # tweede start: geweigerd
        r2 = upload(client, dump_template())
        r2 = client.post(r2.url.path, data={"csrf": csrf(client, r2.text), "action": "restore", "name": "Ander BV",
                                            "dbname": newdb + "x"})
        assert r2.status_code == 409 and "Er wordt al een back-up ingelezen" in r2.text
    finally:
        gate.set()
    for _i in range(600):
        if not client.get(f"{job}/stand").json()["running"]:
            break
        time.sleep(0.2)
    assert "Back-up ingelezen" in client.get(job).text


def test_wizard_tile_is_active():
    from pathlib import Path
    import sqlledger_web
    html = (Path(sqlledger_web.__file__).parent / "templates/start/company.html").read_text()
    assert 'href="/welkom/administratie/back-up"' in html and "komt in een volgende versie" not in html


@pytest.mark.parametrize("url", ["/systeem/back-up", "/welkom/administratie/back-up"])
@pytest.mark.parametrize("lang, texts", [
    ("nl", ["Hoe werkt het?", 'Kies die bij "Sjablonen en logo (.zip, optioneel)".',
            "Inlezen maakt altijd een nieuwe administratie en overschrijft nooit een bestaande."]),
    ("en", ["How does it work?", 'Choose it under "Templates and logo (.zip, optional)".',
            "Importing always creates a new company and never overwrites an existing one."]),
])
def test_help_block(client, db, url, lang, texts):
    """6d-1: uitklapbaar "Hoe werkt het?", ook in de eerste start, met de vertaalde veld- en menunamen."""
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    db.execute("UPDATE app_user SET language = %s WHERE login = 'anna'", (lang,))
    login(client, "anna", secret)
    r = client.get(url)
    assert r.status_code == 200
    page = html.unescape(r.text)
    assert '<details class="help">' in page
    for t in texts:
        assert t in page, t


def test_hard_stop_during_restore_is_cleaned_up(client, db, settings, pg_admin):
    """v1.0.1 keuze 17: een markering van een inlezen dat niet meer loopt (server gestopt) wordt bij de start of op
    Back-up inlezen opgeruimd: de database alleen als het inlezen hem aantoonbaar aanmaakte (``created``) en hij niet
    als administratie geregistreerd is; daarna een melding en een regel in het logboek."""
    import json
    from fastapi.testclient import TestClient
    from sqlledger_web.main import create_app
    runs = restorejobs._runs(settings.data_dir)
    runs.mkdir(parents=True, exist_ok=True)
    half, other, linked = (f"wr_{uuid.uuid4().hex[:10]}" for _i in range(3))
    for name in (half, other, linked):
        pg_admin.execute(f'CREATE DATABASE "{name}"')
    db.execute("INSERT INTO company (name, dbname, modules) VALUES ('Gekoppeld', %s, '{gl}')", (linked,))
    try:
        for job, name, created in (("a" * 32, half, True), ("b" * 32, other, False), ("c" * 32, linked, True)):
            (runs / f"{job}.json").write_text(json.dumps({"job": job, "dbname": name, "name": f"Adm {name}",
                                                          "user_id": 1, "login": "anna", "started": 0,
                                                          "created": created}))
        with TestClient(create_app(settings), base_url="https://testserver"):     # start = opruimen
            pass
        exists = lambda n: pg_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (n,)).fetchone()  # noqa
        assert not exists(half) and exists(other) and exists(linked)
        assert not [p for p in runs.glob("*.json") if not p.name.startswith("aborted-")]
        secret = make_user(db, "anna", sysadmin=True, totp=True)
        login(client, "anna", secret)
        page = html.unescape(client.get("/systeem/back-up").text)
        assert (f"Een inlezen van Adm {half} is afgebroken (server gestopt); de halve database is opgeruimd. "
                "Probeer het opnieuw.") in page
        assert f"Een inlezen van Adm {other} is afgebroken (server gestopt) voordat er iets was aangemaakt." in page
        assert f"Adm {linked}" not in page                                  # wel geregistreerd: geen melding
        logs = db.execute("SELECT detail FROM audit_log WHERE action = 'restore_aborted' ORDER BY id").fetchall()
        assert sorted((r["detail"]["dbname"], r["detail"]["dropped"]) for r in logs) == sorted(
            [(half, True), (other, False), (linked, False)])
    finally:
        for name in (half, other, linked):
            pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_marker_removed_after_restore(client, db, newdb, settings):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = restore_backup(client, dump_template(), name="Markering BV", dbname=newdb)
    assert "Back-up ingelezen" in r.text
    assert not list(restorejobs._runs(settings.data_dir).glob("*.json"))


def sl_backup(date="2026-04-16"):
    """De testadministratie als SQL-Ledger-back-up (kop met dataset en datum)."""
    head = f"-- SQL-Ledger Backup\n-- Dataset: proef\n-- Version: 3.2.4\n-- Date: {date}\n--\n"
    return gzip.compress(head.encode() + gzip.decompress(dump_template()))


def test_check_warns_when_backup_already_there(client, db, newdb):
    """K3: het controlescherm waarschuwt (blokkeert niet) als de back-up of de dataset al is ingelezen."""
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    page = upload(client, sl_backup()).text
    assert "al een administratie die hierop lijkt" not in page
    r = post_backup(client, "/systeem/back-up", sl_backup(), name="Proef BV", dbname=newdb, add_me="1")
    assert r.status_code == 200 and "Back-up ingelezen" in r.text
    page = html.unescape(upload(client, sl_backup()).text)
    assert "al een administratie die hierop lijkt" in page and "Deze back-up is al ingelezen als Proef BV." in page
    page = html.unescape(upload(client, sl_backup("2026-05-01")).text)
    assert "Er is al een administratie uit dataset proef: Proef BV (back-up van 2026-04-16)." in page
    assert 'name="dbname" value="proef"' in page                                  # vooringevuld; geen blokkade


def test_rma_check_difference_shows_report(client, db, newdb, pg_admin, monkeypatch):
    """RI6: verschil in de proefbalans na het inlezen → foutscherm met het verschil per rekening, niets aangemaakt."""
    from decimal import Decimal
    from sqlledger import rmaimport
    real, calls = rmaimport.snapshot, []

    def fake(conn, schema="public"):
        snap = real(conn, schema)
        calls.append(1)
        if len(calls) == 2:
            snap.balance[sorted(snap.balance)[0]] += Decimal("0.01")
        return snap

    monkeypatch.setattr(rmaimport, "snapshot", fake)
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    data = open(os.path.join(RMA_DUMPS, "rma_test_oud.sql.gz"), "rb").read()
    r = post_backup(client, "/systeem/back-up", data, name="RMA oud", dbname=newdb)
    page = html.unescape(r.text)
    assert "Inlezen mislukt" in page and "Na het inlezen klopt de proefbalans of het openstaande bedrag niet meer" in page
    assert "De proefbalans of het openstaande bedrag na het inlezen wijkt af van de back-up:" in page
    assert "Er is niets aangemaakt" in page
    assert not pg_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (newdb,)).fetchone()
