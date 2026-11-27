"""Betalingsspecificatie op echte data (fase 4 stap 4b): elke betaling uit het betalingsregister
(``Payments.payment_register``, zonder gestorneerde) moet als specificatie te vinden zijn met hetzelfde totaal,
en de sjabloonvariabelen moeten te maken zijn. Alleen lezen.

gebruik: python compare/payspec_keeptest.py <db> [<db> ...] [-v]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import UserConfig, connect  # noqa: E402
from sqlledger.payments import Payments  # noqa: E402
from sqlledger.paymentspec import PaymentSpecs, spec_key, spec_vars  # noqa: E402
from sqlledger.printdata import PrintData  # noqa: E402


def run(db: str, verbose: bool) -> int:
    config = UserConfig(login="admin", numberformat="1.000,00", dateformat="dd-mm-yy", countrycode="nl")
    conn = connect(dbname=db, host="127.0.0.1", port=55432, user="postgres")
    bad = 0
    try:
        pm, ps, pd = Payments(conn, config), PaymentSpecs(conn, config), PrintData(conn, config)
        for vc in ("customer", "vendor"):
            rows = pm.payment_register(vc, option="exclude")
            stats, examples = Counter(), []
            for r in rows:
                spec = ps.find(vc, r["accno"], r["source"], r["datepaid"], r["vc_id"])
                if spec is None:
                    stats["niet gevonden"] += 1
                    examples.append(("niet gevonden", r["accno"], r["source"], r["datepaid"], r["vc_id"], r["amount"]))
                    continue
                if spec.total != r["amount"]:
                    stats["ander totaal"] += 1
                    examples.append(("totaal", spec.key, r["amount"], spec.total))
                    continue
                if spec.key != spec_key(r["accno"], r["source"], r["datepaid"], r["vc_id"]):
                    stats["andere sleutel"] += 1
                spec_vars(pd, spec)
                stats["gelijk"] += 1
                if spec.total_discount:
                    stats["met korting"] += 1
                if any(l.prepayment for l in spec.lines):
                    stats["met vooruitbetaling"] += 1
                if not r["source"]:
                    stats["zonder kenmerk"] += 1
            wrong = sum(v for k, v in stats.items() if k in ("niet gevonden", "ander totaal", "andere sleutel"))
            bad += wrong
            print(f"{db} {vc}: {len(rows)} betalingen; " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
            for ex in examples[: (20 if verbose else 5)]:
                print("    ", ex)
    finally:
        conn.close()
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dbs", nargs="+")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    bad = sum(run(db, a.v) for db in a.dbs)
    print("0 verschillen" if not bad else f"{bad} VERSCHILLEN")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
