"""Toets export → import op echte administraties (FASE4_ANALYSE.md stap 7, keuze 11 en 12).

Per database een kopie; per soort de export als CSV én xlsx (in de notatie 1.000,00 / dd-mm-yyyy), terug inlezen met
``read_file`` en de proefrun draaien: elke rij moet "ongewijzigd" zijn. Daarna alles werkelijk importeren (alle
rijen gekozen) en de tabellen vergelijken met het origineel: 0 verschillen.

gebruik (vanuit de repo):  python compare/im_keeptest.py adm_a adm_c [--keep]
"""
from __future__ import annotations

import sys
import uuid
from collections import Counter
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import UserConfig  # noqa: E402
from sqlledger.importer import Importer, export_file, read_file  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"
KINDS = ("partsgroup", "coa", "customer", "vendor", "part", "service")
TABLES = ("customer", "vendor", "address", "contact", "bank", "customertax", "vendortax", "parts", "partstax",
          "partsgroup", "chart", "tax")
NF, DF = "1.000,00", "dd-mm-yy"


def dump(conn):
    out = {}
    for t in TABLES:
        rows = conn.execute(f"SELECT * FROM {t}").fetchall()
        out[t] = sorted((tuple(sorted((k, repr(v)) for k, v in r.items())) for r in rows))
    return out


def main(argv):
    keep = "--keep" in argv
    dbs = [a for a in argv if not a.startswith("--")]
    admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True)
    failures = 0
    for db in dbs:
        copy = f"imk_{db}_{uuid.uuid4().hex[:6]}"
        admin.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{db}"')
        try:
            conn = psycopg.connect(f"{DSN} dbname={copy}", autocommit=True, row_factory=dict_row)
            cfg = UserConfig(login="admin", numberformat=NF, dateformat=DF)
            before = dump(conn)
            for kind in KINDS:
                for fmt in ("csv", "xlsx"):
                    data = export_file(conn, cfg, kind, fmt, NF, DF)
                    header, rows = read_file(data, f"x.{fmt}")
                    imp = Importer(conn, cfg, kind, NF, DF)
                    res = imp.preview(header, rows)
                    count = Counter(i.status for i in res.items)
                    dup = [i for i in res.items if "meer dan eens voor in de administratie" in i.error
                           or (i.error.startswith("Komt ook voor in rij"))]
                    bad = [i for i in res.items if i.status != "same" and i not in dup]
                    if dup:
                        print(f"           {len(dup)} rijen met een nummer dat in de administratie dubbel staat "
                              "(verklaard: niet te kiezen welke bedoeld is)")
                    print(f"{db:8} {kind:10} {fmt:4} {len(rows):6} rijen  {dict(count)}")
                    for i in bad[:8]:
                        print(f"           rij {i.rows[0]} {i.key!r}: {i.status} {i.changes} {i.error}")
                    failures += bool(bad)
                    if fmt == "xlsx":
                        imp.run(header, rows, chosen=[i.rows[0] for i in res.items if i.status != "error"])
            after = dump(conn)
            diff = [t for t in TABLES if before[t] != after[t]]
            print(f"{db:8} na importeren: {'geen verschillen' if not diff else 'VERSCHIL in ' + ', '.join(diff)}")
            failures += bool(diff)
            conn.close()
        finally:
            if not keep:
                admin.execute(f'DROP DATABASE IF EXISTS "{copy}" WITH (FORCE)')
            else:
                print("kopie bewaard:", copy)
    print("\n" + ("ALLES ONGEWIJZIGD" if not failures else f"{failures} met verschillen"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
