"""Bijwerken van een oude SQL-Ledger-back-up: Perl (``User::dbupdate`` via upgrade_dataset.pl) vs Python
(``sqlledger.slupgrade``) op twee kopieën, daarna schema en gegevens vergelijken (FASE6_ANALYSE.md F3).

Terugzetten hier met psql (zoals een beheerder bij SL doet; de back-up is van de gebruiker zelf), zodat alleen het
bijwerken verschilt. Bedrijfsdata: alleen lokaal, de databases gaan na afloop weg (behalve met --keep).

gebruik: python compare/slupgrade_check.py --sldir $SRC <back-up.sql> [...] [--keep]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import slupgrade  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"
ENV = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": "55432", "PGUSER": "postgres"}


def restore(path: Path, db: str) -> None:
    subprocess.run(["createdb", "-E", "UTF8", "-T", "template0", db], check=True, env=ENV)
    subprocess.run(["psql", "-q", "-d", db, "-f", str(path)], env=ENV, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def schema(db: str) -> list:
    out = subprocess.run(["pg_dump", "-s", "-x", "-O", db], env=ENV, capture_output=True, text=True, check=True).stdout
    # onze eigen x_-tabellen (ensure_schema na het terugzetten) tellen niet mee; blokken per object
    blocks, cur = [], []
    for l in out.splitlines():
        if not l.strip() or l.startswith("--") or l.startswith("SET ") or "pg_catalog.set_config" in l:
            continue
        cur.append(l)
        if l.rstrip().endswith(";"):
            blocks.append("\n".join(cur))
            cur = []
    return sorted(b for b in blocks if not re.search(r"\bx_\w+", b))


def data(db: str) -> dict:
    out = {}
    with psycopg.connect(f"{DSN} dbname={db}") as c:
        tables = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                                           "AND tablename NOT LIKE 'x\\_%' ORDER BY 1")]
        for t in tables:
            rows = c.execute(f'SELECT "{t}"::text FROM "{t}"').fetchall()      # als tekst (administratie A: datum in jaar 20121)
            out[t] = sorted(r[0] for r in rows)
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sldir", required=True)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--restore-py", action="store_true", help="Python-kopie terugzetten met sqlledger.slbackup (F2)")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    bad = 0
    for f in a.files:
        path = Path(f)
        tag = uuid.uuid4().hex[:6]
        perl_db, py_db = f"slu_perl_{tag}", f"slu_py_{tag}"
        try:
            restore(path, perl_db)
            subprocess.run(["perl", str(HERE / "upgrade_dataset.pl"), a.sldir, perl_db], env=ENV, check=True,
                           stdout=subprocess.DEVNULL)
            if a.restore_py:
                from sqlledger import slbackup
                with psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as adm:
                    r = slbackup.restore(adm, lambda d: psycopg.connect(f"{DSN} dbname={d}", autocommit=True), py_db,
                                         path.read_bytes())
                res = r.upgrade
                print(f"   slbackup: {r.info.kind} {r.info.dataset} {r.info.version}, {r.info.statements} statements, "
                      f"{r.skipped} overgeslagen")
            else:
                restore(path, py_db)
                with psycopg.connect(f"{DSN} dbname={py_db}", autocommit=True) as c:
                    res = slupgrade.upgrade(c)
            s1, s2 = schema(perl_db), schema(py_db)
            d1, d2 = data(perl_db), data(py_db)
            sdiff = sorted(set(s1) ^ set(s2))
            ddiff = [t for t in sorted(set(d1) | set(d2)) if d1.get(t) != d2.get(t)]
            print(f"{path.name}: {res.from_version} -> {res.to_version}, {len(res.applied)} scripts, "
                  f"{len(res.errors)} overgeslagen statements; schema {'gelijk' if not sdiff else f'{len(sdiff)} regels verschil'}, "
                  f"gegevens {'gelijk' if not ddiff else 'verschil in ' + ', '.join(ddiff)}")
            for e in res.errors[:10]:
                print("   overgeslagen:", e)
            for l in sdiff[:10]:
                print("   schema:", ("Perl " if l in s1 else "Python ") + l[:120])
            bad += bool(sdiff or ddiff)
        finally:
            if not a.keep:
                for db in (perl_db, py_db):
                    subprocess.run(["dropdb", "--if-exists", db], env=ENV)
    print("ALLES GELIJK" if not bad else f"{bad} met verschillen")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
