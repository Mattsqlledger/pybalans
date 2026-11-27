"""Toets relaties tegen het ORIGINEEL via de webapp: kopie van <db> maken, ELKE klant en
leverancier openen in het bewerkscherm (/klanten/<id>, /leveranciers/<id>) en ongewijzigd
opslaan zoals een browser (velden uit de HTML, ook verborgen velden, gekozen opties,
aangevinkte vakjes en keuzerondjes), daarna de relatietabellen vergelijken met <db>.

gebruik (vanuit de repo):  python compare/ct_keeptest.py adm_a adm_c [--keep] [--touch]

--touch: bij elke relatie alleen de notities aanvullen en opslaan; daarna mag in alle
relatietabellen niets anders veranderd zijn dan die notities (gericht bijwerken).

Maakt tijdelijk een kopie ``kt_keep_<db>`` en een app-database ``kt_keep_app``; beide
worden na afloop verwijderd (tenzij --keep).
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
TABLES = {"customer": "id", "vendor": "id", "address": "id", "contact": "id", "bank": "id",
          "shipto": None, "customertax": None, "vendortax": None, "reference": "id", "defaults": "fldname",
          "audittrail": None, "partscustomer": None, "partsvendor": None}
PASSWORD = "keeptest-wachtwoord-123"


def browser_fields(page: str) -> list:
    """Wat een browser bij 'Opslaan' meestuurt: invoervelden (niet uitgeschakeld; vinkjes en
    rondjes alleen aangevinkt; regeleinden uit tekstvelden weg), gekozen optie per keuzelijst
    (anders de eerste), tekstvakken (eerste regeleinde weg, regeleinden als CRLF)."""
    out = []

    def attr(tag, name):
        m = re.search(rf'\b{name}="([^"]*)"', tag)
        return htmlmod.unescape(m.group(1)) if m else None
    form = re.search(r'<form method="post" action="/(klanten|leveranciers)" class="ctform">(.*?)</form>', page, re.S)
    body = form.group(2)
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
    for t, key in TABLES.items():
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


WRITES = {"n": 0, "fields": {}}


def _count_writes():
    """Tel hoe vaak opslaan iets wijzigde (en welke velden): moet 0 zijn."""
    from sqlledger import contacts
    orig = contacts.Contacts._update

    def update(self, cur, new, old, raw, changed):
        WRITES["n"] += 1
        for k in changed:
            WRITES["fields"][k] = WRITES["fields"].get(k, 0) + 1
        return orig(self, cur, new, old, raw, changed)
    contacts.Contacts._update = update


def run(src: str, keep: bool, touch: bool = False) -> int:
    WRITES.update(n=0, fields={})
    admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True)
    copy, app = f"kt_keep_{src}", "kt_keep_app"
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
            before = snapshot(orig)
            with TestClient(create_app(settings), base_url="https://testserver") as client:
                page = client.get("/login").text
                token = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
                client.post("/login", data={"csrf": token, "login": "keeptest", "password": PASSWORD})
                errors, n = [], 0
                for vc, prefix in (("customer", "klanten"), ("vendor", "leveranciers")):
                    for vid in [r["id"] for r in cdb.execute(f"SELECT id FROM {vc} ORDER BY id").fetchall()]:
                        n += 1
                        page = client.get(f"/{prefix}/{vid}")
                        if page.status_code != 200:
                            errors.append((vc, vid, f"openen {page.status_code}"))
                            continue
                        data: dict = {}
                        for k, v in browser_fields(page.text) + [("action", "save")]:
                            data.setdefault(k, []).append(v)
                        if touch:       # alleen de notities wijzigen: verder mag niets veranderen
                            data["notes"] = [(data.get("notes") or [""])[0] + " [kt]"]
                        r = client.post(f"/{prefix}", data=data, follow_redirects=False)
                        if r.status_code != 303:
                            msg = re.search(r'<p class="error" role="alert">(.*?)</p>', r.text, re.S)
                            text = re.sub(r"<[^>]+>|\s+", " ", r.text)[-300:]
                            errors.append((vc, vid, msg.group(1).strip() if msg else f"{r.status_code} {text}"))
            print(f"{src}: {n} relaties geopend en ongewijzigd opgeslagen in {round(time.time() - t0)} s, "
                  f"{len(errors)} fouten {errors[:5]}; als gewijzigd opgeslagen: {WRITES['n']} {WRITES['fields']}")
            skip = ("customer.notes", "vendor.notes") if touch else ()
            if touch:
                before = snapshot(orig, skip)
                tagged = sum(cdb.execute(f"SELECT count(*) AS n FROM {t} WHERE notes LIKE %s", ("%[kt]",))
                             .fetchone()["n"] for t in ("customer", "vendor"))
                print(f"{src}: notities gewijzigd bij {tagged} van {n} relaties")
                diff = (n - tagged) + compare(before, snapshot(cdb, skip))
                WRITES["n"] = 0
            else:
                diff = compare(before, snapshot(cdb))
            print(f"{src}: {diff} verschillende rijen t.o.v. het origineel")
            return len(errors) + diff + WRITES["n"]
    finally:
        if not keep:
            admin.execute(f'DROP DATABASE IF EXISTS "{copy}" WITH (FORCE)')
            admin.execute(f'DROP DATABASE IF EXISTS "{app}" WITH (FORCE)')
        admin.close()


if __name__ == "__main__":
    _count_writes()
    dbs = [a for a in sys.argv[1:] if not a.startswith("--")] or ["adm_a", "adm_c"]
    total = sum(run(db, "--keep" in sys.argv, "--touch" in sys.argv) for db in dbs)
    sys.exit(1 if total else 0)
