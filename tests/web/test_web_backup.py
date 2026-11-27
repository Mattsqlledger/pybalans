"""Back-up maken (FASE6_ANALYSE.md 6d-1, I7): Systeem > Back-up maken, inlezen met sjablonen, herinnering op het
dashboard en ``sqlledger-web backup``."""

import argparse
import html
import gzip
import io
import re
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sqlledger_web import backups, cli
from webhelpers import csrf, login, make_user, nohelp, restore_backup

TEMPLATE_OK = "<p>{{ company.name }} – eigen factuur</p>\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


@pytest.fixture
def newdb(pg_admin):
    names = []

    def make():
        names.append(f"wb_{uuid.uuid4().hex[:10]}")
        return names[-1]
    yield make
    for name in names:
        pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def sysadmin(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    return client


def own_templates(settings, company_id):
    d = backups.templates_dir(settings, company_id)
    (d / "brief").mkdir(parents=True)
    (d / "invoice.html").write_text(TEMPLATE_OK)
    (d / "invoice.html.20261001-120000").write_text("<p>oud</p>")       # vorige versie: gaat mee, niet terug
    (d / "brief" / "invoice.html").write_text(TEMPLATE_OK)
    (d / "logo.png").write_bytes(PNG)


def download(client, url):
    page = client.get("/systeem/back-up-maken").text
    return client.post(url, data={"csrf": csrf(client, page)})


def test_page_download_log_and_restore(client, db, company, settings, newdb):
    c = sysadmin(client, db)
    home = c.get("/").text
    assert 'href="/systeem/back-up-maken"' in home and 'href="/systeem/back-up"' in home   # menu Systeem (K1)
    page = c.get("/systeem/back-up-maken").text
    assert "Testbedrijf" in page and "nog nooit" in page and "pybalans backup --dir" in page
    assert "/sjablonen" not in page                                                 # nog geen eigen sjablonen
    r = download(c, f"/systeem/back-up-maken/{company['id']}")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    name = re.search(r'filename="([^"]+)"', r.headers["content-disposition"]).group(1)
    assert re.fullmatch(rf"{company['dbname']}-\d{{8}}-\d{{4}}\.sql\.gz", name)
    text = gzip.decompress(r.content).decode()
    assert text.startswith("-- PyBalans back-up") and "-- App-Version: 3.2.4" in text
    row = db.execute("SELECT last_backup FROM company WHERE id = %s", (company["id"],)).fetchone()
    assert datetime.now(timezone.utc) - row["last_backup"] < timedelta(minutes=1)
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'backup_made'").fetchone()["detail"]
    assert log["kind"] == "database" and log["filename"] == name and log["via"] == "web"
    assert "nog nooit" not in nohelp(c.get("/systeem/back-up-maken").text)
    # het bestand weer inlezen als nieuwe administratie
    target = newdb()
    r = restore_backup(c, r.content, filename=name, name="Kopie", dbname=target)
    assert r.status_code == 200 and "Back-up ingelezen" in r.text
    assert db.execute("SELECT detail FROM audit_log WHERE action = 'company_restored'").fetchone()["detail"]["kind"] == "app"


def test_templates_zip_and_restore_with_templates(client, db, company, settings, newdb):
    c = sysadmin(client, db)
    own_templates(settings, company["id"])
    assert f"/systeem/back-up-maken/{company['id']}/sjablonen" in c.get("/systeem/back-up-maken").text
    r = download(c, f"/systeem/back-up-maken/{company['id']}/sjablonen")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == ["brief/invoice.html", "invoice.html", "invoice.html.20261001-120000",
                                        "logo.png"]
    db_dump = download(c, f"/systeem/back-up-maken/{company['id']}").content
    target = newdb()
    resp = restore_backup(c, db_dump, templates=r.content, name="Met sjablonen", dbname=target)
    assert resp.status_code == 200 and "3 bestanden overgenomen" in resp.text and "1 bestanden overgeslagen" in resp.text
    cid = db.execute("SELECT id FROM company WHERE dbname = %s", (target,)).fetchone()["id"]
    d = backups.templates_dir(settings, cid)
    assert (d / "invoice.html").read_text() == TEMPLATE_OK and (d / "brief" / "invoice.html").is_file()
    assert (d / "logo.png").read_bytes() == PNG and not (d / "invoice.html.20261001-120000").exists()


def test_bad_templates_zip_creates_nothing(client, db, company, pg_admin, newdb):
    c = sysadmin(client, db)
    db_dump = download(c, f"/systeem/back-up-maken/{company['id']}").content
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("invoice.html", "<p>{% if %}</p>")
    for data, message in ((buf.getvalue(), "Sjabloon invoice.html: Fout in regel 1"),
                          (b"geen zip", "geen zip-bestand")):
        target = newdb()
        r = restore_backup(c, db_dump, templates=data, name="Fout", dbname=target)
        assert r.status_code == 400 and message in r.text, r.text[-800:]           # al bij het uploaden
        assert not pg_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,)).fetchone()
    assert not db.execute("SELECT 1 FROM company WHERE name = 'Fout'").fetchone()


def test_failure_is_shown_and_logged(client, db, company, settings):
    c = sysadmin(client, db)
    settings.pg_dump = "/bestaat/niet/pg_dump"
    r = download(c, f"/systeem/back-up-maken/{company['id']}")
    assert r.status_code == 500 and "Back-up van Testbedrijf mislukt" in r.text and "niet gevonden" in r.text
    assert db.execute("SELECT 1 FROM audit_log WHERE action = 'backup_failed'").fetchone()
    assert db.execute("SELECT last_backup FROM company WHERE id = %s", (company["id"],)).fetchone()["last_backup"] is None


def test_only_sysadmin(client, db, company):
    secret = make_user(db, "baas", memberships=[(company["id"], "admin")], totp=True)
    login(client, "baas", secret)
    assert client.get("/systeem/back-up-maken").status_code == 403
    r = client.post(f"/systeem/back-up-maken/{company['id']}", data={"csrf": csrf(client, client.get("/").text)})
    assert r.status_code == 403


def todo(client):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get("/dashboard/blok/tedoen").text))


def test_dashboard_reminder(client, db, company):
    secret = make_user(db, "baas", memberships=[(company["id"], "admin")], totp=True)
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper")])
    login(client, "baas", secret)
    assert "Nog geen back-up gemaakt met deze app" in todo(client)
    assert 'href="/systeem/back-up-maken"' not in client.get("/dashboard/blok/tedoen").text   # geen sysadmin
    db.execute("UPDATE company SET last_backup = now() - interval '8 days' WHERE id = %s", (company["id"],))
    assert "Laatste back-up is van" in todo(client)
    db.execute("UPDATE company SET last_backup = now() - interval '6 days' WHERE id = %s", (company["id"],))
    assert "back-up" not in todo(client).lower()
    db.execute("UPDATE company SET last_backup = NULL WHERE id = %s", (company["id"],))
    client.cookies.clear()
    login(client, "boek")
    assert "back-up" not in todo(client).lower()                                   # boekhouder: niet


def run_cli(settings, folder, **kw):
    args = argparse.Namespace(dir=str(folder), company=kw.get("company"), inactive=False, keep=kw.get("keep", 0),
                              no_app_db=kw.get("no_app_db", False))
    return cli.cmd_backup(settings, args)


def test_cli_backup_all_keep_and_failure(db, company, settings, tmp_path, capsys):
    own_templates(settings, company["id"])
    other = db.execute("INSERT INTO company (name, dbname, modules) VALUES ('Weg', 'bestaat_niet_xyz', '{}') "
                       "RETURNING id").fetchone()
    # klok per aanroep: 1 Testbedrijf, 2 Weg (mislukt), 3 app-db | 4 Testbedrijf, 5 app-db | 6 Testbedrijf
    stamps = iter([datetime(2026, 12, d, 2, 0) for d in range(1, 10)])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(stamps)
    import sqlledger_web.backups as b
    orig = b.datetime
    b.datetime = Clock
    try:
        assert run_cli(settings, tmp_path, keep=2) == 1                       # "Weg" mislukt, de rest gaat door
        assert run_cli(settings, tmp_path, keep=2, company=[company["dbname"]]) == 0
        assert run_cli(settings, tmp_path, keep=2, company=["Testbedrijf"], no_app_db=True) == 0
    finally:
        b.datetime = orig
    files = sorted(p.name for p in tmp_path.iterdir())
    dbname, appname = company["dbname"], settings.app_dsn.split("dbname=")[1]
    assert [f for f in files if f.startswith(dbname) and f.endswith(".sql.gz")] == \
        [f"{dbname}-20261204-0200.sql.gz", f"{dbname}-20261206-0200.sql.gz"]          # --keep 2
    assert [f for f in files if f.endswith("-sjablonen.zip")] == \
        [f"{dbname}-20261204-0200-sjablonen.zip", f"{dbname}-20261206-0200-sjablonen.zip"]
    assert [f for f in files if f.startswith(appname)] == [f"{appname}-20261203-0200.sql.gz",
                                                          f"{appname}-20261205-0200.sql.gz"]
    assert gzip.decompress((tmp_path / f"{appname}-20261205-0200.sql.gz").read_bytes()).count(b"app_user") > 0
    err = capsys.readouterr().err
    assert "Weg: MISLUKT" in err
    rows = db.execute("SELECT action, company_id, detail->>'via' AS via FROM audit_log "
                      "WHERE action LIKE 'backup%%' ORDER BY id").fetchall()
    assert [(r["action"], r["company_id"]) for r in rows] == [("backup_made", company["id"]), ("backup_failed", other["id"]),
                                                              ("backup_made", company["id"]), ("backup_made", company["id"])]
    assert {r["via"] for r in rows} == {"cli"}
    assert run_cli(settings, tmp_path, company=["onbekend"], no_app_db=True) == 1


@pytest.mark.parametrize("lang, texts", [
    ("nl", ["Hoe werkt het?", "Per administratie een volledige back-up downloaden: alle gegevens en de bijlagen.",
            'knop "Sjablonen en logo (.zip)"?', "Automatisch elke nacht (Mac)"]),
    ("en", ["How does it work?", "Download a full backup for each company: all data and the attachments.",
            '"Templates and logo (.zip)" button?', "Automatically every night (Mac)"]),
])
def test_help_block(client, db, company, lang, texts):
    """6d-1 (golf 3: help/sys_backup.html): uitklapbaar "Hoe werkt het?" met de vertaalde knopnamen, launchd- en
    cron-voorbeeld onder een eigen kopje (V5a)."""
    c = sysadmin(client, db)
    db.execute("UPDATE app_user SET language = %s WHERE login = 'anna'", (lang,))
    page = html.unescape(c.get("/systeem/back-up-maken").text)
    assert '<details class="help">' in page
    for t in texts:
        assert t in page, t
    assert "launchctl load ~/Library/LaunchAgents/nl.sqlledger.backup.plist" in page
    assert "<string>nl.sqlledger.backup</string>" in page and "0 3 * * * pybalans --config" in page


def test_busy_message_while_making(client, db, company):
    """v1.0.1 PB6: knoppen downloaden via backup.js (uit + "Bezig met maken…", daarna weer actief); zonder
    JavaScript blijft het een gewone formulierpost."""
    c = sysadmin(client, db)
    page = html.unescape(c.get("/systeem/back-up-maken").text)
    assert '<script src="/static/backup.js" defer></script>' in page
    assert f'action="/systeem/back-up-maken/{company["id"]}" class="inline" data-download>' in page
    assert 'data-busy="Bezig met maken…"' in page and 'aria-live="polite"' in page
    js = (Path(__file__).parents[2] / "sqlledger_web/static/backup.js").read_text()
    assert "disabled = true" in js and "disabled = false" in js and "createObjectURL" in js
