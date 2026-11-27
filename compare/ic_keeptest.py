"""Toets artikelen tegen het ORIGINEEL via de webapp: kopie van <db> maken, ELK artikel openen
in het bewerkscherm (/artikelen/<id>) en ongewijzigd opslaan zoals een browser, daarna de
artikeltabellen vergelijken met <db> (AT2: velden die het scherm niet kent blijven staan).

gebruik (vanuit de repo):  python compare/ic_keeptest.py adm_a adm_c [--keep] [--touch]

--touch: bij elk artikel alleen de notities aanvullen; daarna mag niets anders veranderd zijn
dan die notities (en de audittrail).
Artikelen die hier niet bewerkt kunnen worden (loon, samengesteld, kit, project) worden alleen
geopend.
"""
from __future__ import annotations

import html as htmlmod
import re
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from fastapi.testclient import TestClient  # noqa: E402

from sqlledger_web import accounts, appdb, security  # noqa: E402
from sqlledger_web.main import create_app  # noqa: E402
from sqlledger_web.settings import Settings  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"
TABLES = ("parts", "partstax", "partsvendor", "partscustomer", "makemodel", "translation", "partsgroup",
          "assembly", "defaults", "audittrail")
PASSWORD = "keeptest-wachtwoord-123"


def browser_fields(page: str) -> list:
    """Wat een browser bij 'Opslaan' meestuurt: invoervelden (niet uitgeschakeld; vinkjes en
    rondjes alleen aangevinkt; regeleinden uit tekstvelden weg), gekozen optie per keuzelijst
    (anders de eerste), tekstvakken (eerste regeleinde weg, regeleinden als CRLF)."""
    out = []

    def attr(tag, name):
        m = re.search(rf'\b{name}="([^"]*)"', tag)
        return htmlmod.unescape(m.group(1)) if m else None
    form = re.search(r'<form method="post" action="/artikelen" class="icform">(.*?)</form>', page, re.S)
    body = form.group(1)
    for m in re.finditer(r"<input\b[^>]*>", body):
        tag = m.group(0)
        name = attr(tag, "name")
        if not name or re.search(r"\sdisabled\b", tag):
            continue
        typ = (attr(tag, "type") or "text").lower()
        if typ in ("checkbox", "radio") and not re.search(r"\schecked\b", tag):
            continue
        value = attr(tag, "value")
        if value is None:
            value = "on" if typ in ("checkbox", "radio") else ""
        if typ not in ("checkbox", "radio", "hidden"):
            value = value.replace("\r", "").replace("\n", "")
        out.append((name, value))
    for m in re.finditer(r"<select\b([^>]*)>(.*?)</select>", body, re.S):
        name = attr(m.group(1), "name")
        if not name or re.search(r"\sdisabled\b", m.group(1)):
            continue
        opts = [(attr(o.group(1), "value") if attr(o.group(1), "value") is not None
                 else htmlmod.unescape(o.group(2)).strip(), "selected" in o.group(1))
                for o in re.finditer(r"<option\b([^>]*)>([^<]*)", m.group(2))]
        sel = [v for v, s in opts if s]
        out.append((name, sel[-1] if sel else (opts[0][0] if opts else "")))
    for m in re.finditer(r"<textarea\b([^>]*)>(.*?)</textarea>", body, re.S):
        name = attr(m.group(1), "name")
        v = htmlmod.unescape(m.group(2))
        if v.startswith("\r\n"):
            v = v[2:]
        elif v.startswith("\n"):
            v = v[1:]
        out.append((name, v.replace("\r\n", "\n").replace("\n", "\r\n")))
    return out


def snapshot(conn, skip=()) -> dict:
    """skip: kolommen (tabel.kolom) die niet meetellen."""
    out = {}
    for t in TABLES:
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        out[t] = sorted((tuple((k, v) for k, v in r.items() if f"{t}.{k}" not in skip) for r in rows), key=repr)
    return out


def compare(a: dict, b: dict) -> int:
    n = 0
    for t in TABLES:
        sa, sb = set(a[t]), set(b[t])
        only_a, only_b = sorted(sa - sb, key=repr), sorted(sb - sa, key=repr)
        if only_a or only_b:
            n += len(only_a) + len(only_b)
            print(f"   tabel {t}: {len(only_a)} alleen origineel, {len(only_b)} alleen na opslaan")
            for r in only_a[:3]:
                print("      origineel:", dict(r))
            for r in only_b[:3]:
                print("      na opslaan:", dict(r))
    return n


def createdb(admin, name: str, template: str) -> None:
    admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    for _ in range(30):
        try:
            admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
            return
        except psycopg.errors.ObjectInUse:
            time.sleep(10)          # een ander gebruikt de template even
    raise RuntimeError(f"template {template} blijft in gebruik")



def run(src: str, keep: bool, touch: bool = False) -> int:
    admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True)
    copy, app = f"kt_ic_{src}", "kt_ic_app"
    createdb(admin, copy, src)
    admin.execute(f'DROP DATABASE IF EXISTS "{app}"')
    settings = Settings(app_dsn=f"{DSN} dbname={app}", db_host="127.0.0.1", db_port=55432, db_user="postgres")
    appdb.create_database(settings.app_dsn)
    t0 = time.time()
    try:
        with psycopg.connect(settings.app_dsn, autocommit=True, row_factory=dict_row) as adb:
            appdb.migrate(adb)
            cid = adb.execute("INSERT INTO company (name, dbname, modules) VALUES (%s, %s, %s) RETURNING id",
                              (src, copy, list(appdb.MODULES))).fetchone()["id"]
            uid = accounts.create_user(adb, "keeptest", "Keeptest")
            adb.execute("UPDATE app_user SET password_hash = %s WHERE id = %s",
                        (security.hash_password(PASSWORD), uid))
            adb.execute("INSERT INTO membership VALUES (%s, %s, %s, %s)", (uid, cid, "bookkeeper", "admin"))
        with psycopg.connect(f"{DSN} dbname={src}", row_factory=dict_row, autocommit=True) as orig, \
                psycopg.connect(f"{DSN} dbname={copy}", row_factory=dict_row, autocommit=True) as cdb:
            skip = ("parts.notes", "audittrail.transdate") if touch else ()
            before = snapshot(orig, skip)
            with TestClient(create_app(settings), base_url="https://testserver") as client:
                page = client.get("/login").text
                token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
                client.post("/login", data={"csrf": token, "login": "keeptest", "password": PASSWORD})
                errors, n, saved, readonly = [], 0, 0, 0
                for pid in [r["id"] for r in cdb.execute("SELECT id FROM parts ORDER BY id").fetchall()]:
                    n += 1
                    page = client.get(f"/artikelen/{pid}")
                    if page.status_code != 200:
                        errors.append((pid, f"openen {page.status_code}"))
                        continue
                    if 'value="save"' not in page.text:
                        readonly += 1
                        continue
                    data: dict = {}
                    for k, v in browser_fields(page.text) + [("action", "save")]:
                        data.setdefault(k, []).append(v)
                    if touch:
                        data["notes"] = [(data.get("notes") or [""])[0] + " [kt]"]
                    r = client.post("/artikelen", data=data, follow_redirects=False)
                    if r.status_code != 303:
                        msg = re.search(r'<p class="error" role="alert">(.*?)</p>', r.text, re.S)
                        text = re.sub(r"<[^>]+>|\s+", " ", r.text)[-300:]
                        errors.append((pid, msg.group(1).strip() if msg else f"{r.status_code} {text}"))
                    else:
                        saved += 1
            print(f"{src}: {n} artikelen geopend, {saved} opgeslagen, {readonly} alleen-lezen, "
                  f"in {round(time.time() - t0)} s; {len(errors)} fouten {errors[:5]}")
            after = snapshot(cdb, skip)
            if touch:
                after["audittrail"] = before["audittrail"]
            diff = compare(before, after)
            print(f"{src}: {diff} verschillende rijen t.o.v. het origineel")
            return len(errors) + diff
    finally:
        if not keep:
            admin.execute(f'DROP DATABASE IF EXISTS "{copy}" WITH (FORCE)')
            admin.execute(f'DROP DATABASE IF EXISTS "{app}" WITH (FORCE)')
        admin.close()


if __name__ == "__main__":
    dbs = [a for a in sys.argv[1:] if not a.startswith("--")] or ["adm_a", "adm_c"]
    total = sum(run(db, "--keep" in sys.argv, "--touch" in sys.argv) for db in dbs)
    sys.exit(1 if total else 0)
