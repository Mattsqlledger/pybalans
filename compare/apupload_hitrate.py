"""Voorstel van Inkoopfactuur uploaden (fase 4 stap 4e, IU3/IU4) terugrekenen op echte data.

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/apupload_hitrate.py [adm_a adm_c ...] [--limit N]

Per inkoopboeking zonder artikelen (bedrag > 0) het voorstel zoals het scherm het op dat moment
had gedaan (``PurchaseUpload.suggest`` met alleen de boekingen ervóór, zonder vaste
kostenrekening) en vergelijken met wat er werkelijk geboekt is:

- kostenrekening: alleen boekingen met één kostenrekening (zoals in de analyse); raak, mis of
  geen voorstel (eerste boeking van de leverancier);
- btw: boekingen met hoogstens één btw-rekening; raak als het voorstel dezelfde rekening is (of
  "geen btw" bij een boeking zonder btw), "kiezen" als het scherm laat kiezen.

Draait op een kopie van elke database (de controle maakt de eigen tabellen aan).
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import ArapLedger, UserConfig, connect  # noqa: E402
from sqlledger.amounts import to_decimal  # noqa: E402
from sqlledger.apupload import PurchaseUpload  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"


def check(db: str, limit: int = 0) -> Counter:
    copy = f"hit_{db}_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{db}"')
    c = Counter()
    try:
        conn = connect(f"{DSN} dbname={copy}")
        cfg = UserConfig(login="admin")
        pu, al = PurchaseUpload(conn, cfg), ArapLedger(conn, cfg)
        rows = conn.execute("""SELECT id, vendor_id, transdate FROM ap
                               WHERE invoice IS NOT TRUE AND amount > 0 AND vendor_id > 0
                               ORDER BY transdate, id""").fetchall()
        if limit:
            rows = rows[:limit]
        start = time.time()
        for tid, vendor, td in rows:
            c["boekingen"] += 1
            t = al.get_transaction(tid, "vendor")
            s = pu.suggest(vendor, td, before=(td, tid))
            accounts = {ln.accno for ln in t.lines}
            if len(accounts) == 1:
                c["kosten: één rekening"] += 1
                if s.expense_accno is None:
                    c["kosten: geen voorstel"] += 1
                elif s.expense_accno == next(iter(accounts)):
                    c["kosten: raak"] += 1
                else:
                    c["kosten: mis"] += 1
            taxes = {a for a, v in t.taxes.items() if to_decimal(v)}
            if len(taxes) <= 1:
                c["btw: hoogstens één rekening"] += 1
                want = next(iter(taxes)) if taxes else ""
                if s.tax_accno is None:
                    c["btw: kiezen"] += 1
                elif s.tax_accno == want:
                    c["btw: raak"] += 1
                else:
                    c["btw: mis"] += 1
            else:
                c["btw: meer rekeningen (splitsen)"] += 1
        c["seconden"] = int(time.time() - start)
        conn.close()
    finally:
        with psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{copy}" WITH (FORCE)')
    return c


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dbs", nargs="*", default=["adm_a", "adm_c"])
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    for db in a.dbs:
        c = check(db, a.limit)
        known = c["kosten: raak"] + c["kosten: mis"]
        print(f"== {db}: {c['boekingen']} inkoopboekingen zonder artikelen ({c['seconden']} s)")
        print(f"   kostenrekening (één rekening: {c['kosten: één rekening']}): raak {c['kosten: raak']} van {known} "
              f"met voorstel ({pct(c['kosten: raak'], known)}), mis {c['kosten: mis']}, "
              f"geen voorstel {c['kosten: geen voorstel']}")
        tk = c["btw: raak"] + c["btw: mis"]
        print(f"   btw (hoogstens één rekening: {c['btw: hoogstens één rekening']}): raak {c['btw: raak']} van {tk} "
              f"({pct(c['btw: raak'], tk)}), mis {c['btw: mis']}, kiezen {c['btw: kiezen']}; "
              f"meer rekeningen {c['btw: meer rekeningen (splitsen)']}")


if __name__ == "__main__":
    main()
