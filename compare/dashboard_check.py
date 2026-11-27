"""Dashboard tegen de rapporten (fase 4 stap 5b, DB7): snelle open posten = ouderdomsanalyse, per factuur.

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/dashboard_check.py <db> [<db> ...] [--peildatum 2026-10-11]

Per verkoop/inkoop: dezelfde facturen als ``Reports.aging`` (alle kolommen, per factuur) en per factuur hetzelfde
bedrag (de ouderdomsanalyse toont het in de factuurvaluta: open / koers, afgerond). Ook de tijd van beide.
Alleen lezen.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import Reports, UserConfig, connect  # noqa: E402
from sqlledger.amounts import round_amount  # noqa: E402
from sqlledger.dashboard import Dashboard  # noqa: E402
from sqlledger.reports_arap import BUCKETS  # noqa: E402


def check(conn, cfg, arap: str, today: date) -> int:
    t0 = time.time()
    dash = Dashboard(conn, cfg).open_items(arap, today, rows=True)
    t1 = time.time()
    aging = Reports(conn, cfg).aging(arap, todate=today, buckets=BUCKETS)
    t2 = time.time()
    precision = int(aging.info.get("precision") or 2)
    want = {r["id"]: sum(r[b] for b in BUCKETS) for r in aging.rows}
    have = {}
    for r in dash.rows:
        rate = next((a["exchangerate"] for a in aging.rows if a["id"] == r["id"]), 1)
        have[r["id"]] = round_amount(r["open"] / rate, precision)
    diff = sorted(set(want) ^ set(have)) + sorted(i for i in set(want) & set(have) if want[i] != have[i])
    print(f"  {arap}: dashboard {len(have)} facturen {dash.total} ({t1 - t0:.2f} s), ouderdomsanalyse "
          f"{len(want)} ({t2 - t1:.2f} s): {len(diff)} verschillen")
    for i in diff[:10]:
        print(f"    id {i}: dashboard {have.get(i)} ouderdom {want.get(i)}")
    return len(diff)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dbs", nargs="+")
    ap.add_argument("--peildatum", default=None)
    a = ap.parse_args()
    today = date.fromisoformat(a.peildatum) if a.peildatum else date.today()
    cfg = UserConfig(login="admin", numberformat="1.000,00", dateformat="dd-mm-yy", countrycode="nl")
    bad = 0
    for db in a.dbs:
        print(db, today.isoformat())
        with connect(dbname=db, host="127.0.0.1", port=55432, user="postgres") as conn:
            for arap in ("ar", "ap"):
                bad += check(conn, cfg, arap, today)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
