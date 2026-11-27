"""Beheer op echte data: alles openen en ongewijzigd opslaan via de formuliercode van de webapp.

Op een kopie van ``<db>``: elke rekening, het btw-scherm, de instellingen, afsluiten,
elke valuta (precisie), elke koersdatum en elke afdeling/project/taal/branche/
betaalmethode wordt geopend (``*_fields``) en ongewijzigd opgeslagen
(``*_from_form`` + bibliotheek). Daarna moeten de tabellen gelijk zijn aan het
origineel (het audittrail krijgt de 'saved'-regels; die worden alleen geteld).

gebruik: python compare/am_keeptest.py <db> [--dsn ...] [--keep]
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlledger import UserConfig  # noqa: E402
from sqlledger.admin import Admin  # noqa: E402
from sqlledger.masterdata import MasterData  # noqa: E402
from sqlledger_web import routes_am as ra  # noqa: E402

TABLES = ("chart", "tax", "defaults", "curr", "exchangerate", "department", "project", "language",
          "business", "paymentmethod", "partstax", "customertax", "vendortax", "translation", "bank",
          "address", "customer", "vendor", "semaphore")


def dump(conn, table):
    cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns "
                                       "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
                                       (table,)).fetchall()]
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    return cols, sorted(rows, key=repr)


def copy_db(admin, src, name):
    admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    for _ in range(30):
        try:
            admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{src}"')
            return
        except psycopg.errors.ObjectInUse:
            time.sleep(10)          # een ander gebruikt de template even
    raise RuntimeError(f"{src} blijft in gebruik")


def save_everything(conn, nf="1.000,00"):
    cfg = UserConfig(login="admin", numberformat=nf)
    adm, md = Admin(conn, cfg), MasterData(conn, cfg)
    count = {}
    for r in adm.accounts():
        f = ra.account_fields(adm.get_account(r["id"]))
        adm.save_account(ra.account_from_form(f))
        count["rekeningen"] = count.get("rekeningen", 0) + 1
    rows, closed = ra.taxes_from_form(ra.tax_fields(adm.taxes(), nf), nf)
    adm.save_taxes(rows, closed)
    count["btw-regels"] = len(rows)
    values, shown = ra.settings_from_form(ra.settings_fields(adm.settings()))
    count["instellingen gewijzigd"] = len(adm.save_settings(values, shown))
    c = adm.closing()
    adm.save_closing(c["closedto"], c["revtrans"], c["audittrail"])
    for cur in md.currencies():
        if cur["prec"] is not None:
            md.set_precision(cur["curr"], cur["prec"])
        count["valuta"] = count.get("valuta", 0) + 1
    dates = sorted({r["transdate"] for r in md.exchangerates()})
    for d in dates:
        f = ra.rate_fields(d, md.rates_on(d), nf)
        transdate, rates, olddate = ra.rates_from_form(f, nf)
        md.save_exchangerates(transdate, rates, olddate)
    count["koersdatums"] = len(dates)
    for slug, kind in ra._kinds().items():
        for row in kind.rows(md):
            f = ra.simple_fields(kind, row, nf)
            kind.save(md, ra.simple_values(kind, f, nf), f["key"])
            count[slug] = count.get(slug, 0) + 1
    return count


def save_everything_web(conn, company_db, dsn):
    """Hetzelfde, maar als browser via de webapp (TestClient): HTML-formulier lezen en versturen."""
    import uuid
    from fastapi.testclient import TestClient
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "web"))
    from test_web_am import submit                    # noqa: E402
    from webhelpers import login, make_user          # noqa: E402
    from sqlledger_web import appdb
    from sqlledger_web.main import create_app
    from sqlledger_web.settings import Settings

    params = conninfo_to_dict(dsn)
    appname = f"am_app_{uuid.uuid4().hex[:8]}"
    s = Settings(app_dsn=f"{dsn} dbname={appname}", db_host=params.get("host", "127.0.0.1"),
                 db_port=int(params.get("port", 5432)), db_user=params.get("user", ""))
    appdb.create_database(s.app_dsn)
    count = {}
    try:
        with psycopg.connect(s.app_dsn, autocommit=True, row_factory=dict_row) as db:
            appdb.migrate(db)
            cid = db.execute("INSERT INTO company (name, dbname, modules) VALUES ('Kopie', %s, %s) RETURNING id",
                             (company_db, list(appdb.MODULES))).fetchone()["id"]
            secret = make_user(db, "baas", totp=True, memberships=[(cid, "admin", "admin")])
            with TestClient(create_app(s), base_url="https://testserver") as client:
                login(client, "baas", secret)

                def save(url, action_url, **changes):
                    r = submit(client, url, action_url, {"action": "save", **changes})
                    if r.status_code != 303:
                        raise RuntimeError(f"{url}: {r.status_code} {r.text[:500]}")
                    count[action_url] = count.get(action_url, 0) + 1

                for (chart_id,) in conn.execute("SELECT id FROM chart ORDER BY id").fetchall():
                    save(f"/beheer/rekeningschema/{chart_id}", "/beheer/rekeningschema")
                save("/beheer/btw", "/beheer/btw")
                save("/beheer/instellingen", "/beheer/instellingen")
                save("/beheer/afsluiten", "/beheer/afsluiten")
                for (d,) in conn.execute("SELECT DISTINCT transdate FROM exchangerate ORDER BY 1").fetchall():
                    save(f"/beheer/koersen/datum?datum={d.isoformat()}", "/beheer/koersen/datum")
                for slug, kind in ra._kinds().items():
                    html = client.get(f"/beheer/{slug}").text
                    keys = re.findall(rf'href="/beheer/{slug}/([^"/]+)"', html)
                    for key in keys:
                        if key != "nieuw":
                            save(f"/beheer/{slug}/{key}", f"/beheer/{slug}")
                html = client.get("/beheer/valuta").text
                for curr in re.findall(r'name="prec_([A-Z]{3})"', html):
                    m = re.search(rf'name="prec_{curr}" value="([^"]*)"', html)
                    r = client.post("/beheer/valuta", data={"csrf": re.search(r'name="csrf" value="([^"]+)"', html).group(1),
                                                            "curr": curr, f"prec_{curr}": m.group(1), "action": "prec"},
                                    follow_redirects=False)
                    if r.status_code != 303:
                        raise RuntimeError(f"valuta {curr}: {r.status_code}")
                    count["/beheer/valuta"] = count.get("/beheer/valuta", 0) + 1
    finally:
        with psycopg.connect(f"{dsn} dbname=postgres", autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{appname}" WITH (FORCE)')
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--dsn", default="host=127.0.0.1 port=55432 user=postgres")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--web", action="store_true", help="als browser via de webapp (HTML-formulieren)")
    args = ap.parse_args()
    name = f"am_keep_{args.db}" + ("_web" if args.web else "")
    admin = psycopg.connect(f"{args.dsn} dbname=postgres", autocommit=True)
    copy_db(admin, args.db, name)
    bad = 0
    try:
        with psycopg.connect(f"{args.dsn} dbname={name}", autocommit=True) as conn:
            audit_before = conn.execute("SELECT count(*) FROM audittrail").fetchone()[0]
            count = save_everything_web(conn, name, args.dsn) if args.web else save_everything(conn)
            audit_after = conn.execute("SELECT count(*) FROM audittrail").fetchone()[0]
            with psycopg.connect(f"{args.dsn} dbname={args.db}", autocommit=True) as orig:
                for t in TABLES:
                    a, b = dump(orig, t), dump(conn, t)
                    if a != b:
                        bad += 1
                        only_a = [r for r in a[1] if r not in b[1]]
                        only_b = [r for r in b[1] if r not in a[1]]
                        print(f"VERSCHIL {t}: {len(only_a)} alleen origineel, {len(only_b)} alleen na opslaan")
                        for r in only_a[:5]:
                            print("   origineel:", r)
                        for r in only_b[:5]:
                            print("   opgeslagen:", r)
        print(f"{args.db}: opgeslagen {count}; audittrail +{audit_after - audit_before} regels")
        print(f"{args.db}: {len(TABLES)} tabellen vergeleken, {bad} met verschil")
    finally:
        if not args.keep:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
