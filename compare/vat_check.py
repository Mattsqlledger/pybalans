"""Btw-aangifte op echte data (BTW_ANALYSE.md, toetsen bij A).

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``; op een KOPIE, het script legt rubrieken vast):
    python compare/vat_check.py <db> --afdracht 1840 [--van 2011 --tot 2026]

1. Per jaar en per btw-rekening: de mutaties uit verkoop/inkoop in de aangifte = het bestaande btw-rapport
   (``Reports.tax_report``, verkoop min inkoop).
2. Per maand met een eigen afdrachtboeking (memoriaal met de afdrachtrekening én btw-rekeningen): het exacte totaal
   van de aangifte tegen wat de administratie zelf tegenboekte. Verschillen worden ingedeeld:
   - ``afronding``: hoogstens 1 euro per btw-rekening (de administratie boekte per rekening hele euro's);
   - ``verschoven``: het verschil heft (binnen afronding) een verschil in een aangrenzende maand op (boeking later
     van datum veranderd);
   - ``rest``: lijst met maand en bedrag.
3. Grondslag tegen btw per rubriek met één tarief (btw ÷ grondslag ≈ tarief) en het aantal meldingen "btw past niet".
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import UserConfig, connect  # noqa: E402
from sqlledger import Reports  # noqa: E402
from sqlledger.vatreturn import VatReturns, free_period, period_of  # noqa: E402

D = Decimal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--afdracht", required=True)
    ap.add_argument("--afronding", default="")
    ap.add_argument("--van", type=int, default=2011)
    ap.add_argument("--tot", type=int, default=date.today().year)
    a = ap.parse_args()
    conn = connect(dbname=a.db)
    cfg = UserConfig(login="vatcheck")
    vr = VatReturns(conn, cfg)
    accs = vr.tax_accounts()
    vr.save_settings({x["id"]: x["box"] or x["suggested"] for x in accs}, a.afdracht, a.afronding, "vatcheck")
    print("rubrieken:", ", ".join(f"{x['accno']}={x['box'] or x['suggested']}" for x in accs))
    tax_ids = [x["id"] for x in accs]
    accno = {x["id"]: x["accno"] for x in accs}
    bad = 0

    # 1. per jaar en rekening tegen het btw-rapport (alleen verkoop/inkoop)
    rp = Reports(conn, cfg)
    t0 = time.time()
    for year in range(a.van, a.tot + 1):
        p = free_period(date(year, 1, 1), date(year, 12, 31))
        rep = vr.report(p)
        ours = {x.accno: x.amount for x in rep.accounts}
        with conn.cursor() as cur:
            cur.execute("""SELECT ac.chart_id, sum(ac.amount::numeric) FROM acc_trans ac JOIN gl g ON (g.id = ac.trans_id)
                           WHERE ac.chart_id = ANY(%s) AND ac.transdate BETWEEN %s AND %s AND g.approved
                             AND NOT EXISTS (SELECT 1 FROM acc_trans s JOIN chart c ON (c.id = s.chart_id)
                                             WHERE s.trans_id = g.id AND c.accno = %s)
                           GROUP BY ac.chart_id""", (tax_ids, p.fromdate, p.todate, a.afdracht))
            for cid, amt in cur.fetchall():
                ours[accno[cid]] = ours.get(accno[cid], D(0)) - amt
        report: dict = defaultdict(lambda: D(0))
        for db, sign in (("ar", 1), ("ap", -1)):
            r = rp.tax_report(db, accounts=[x["accno"] for x in accs], fromdate=p.fromdate, todate=p.todate)
            for row in r.rows:
                report[row["accno"]] += sign * row["tax"]
        diffs = {k: (ours.get(k, D(0)), report.get(k, D(0))) for k in set(ours) | set(report)
                 if ours.get(k, D(0)) != report.get(k, D(0))}
        if diffs:
            bad += 1
            print(f"  {year}: VERSCHIL met btw-rapport {diffs}")
    print(f"1. per jaar tegen het btw-rapport: {a.tot - a.van + 1 - bad} jaren gelijk, {bad} met verschil "
          f"({time.time() - t0:.1f} s)")

    # 2. per maand tegen de eigen afdrachtboekingen
    with conn.cursor() as cur:
        cur.execute("""SELECT g.transdate, g.reference,
                              sum(CASE WHEN ac.chart_id = ANY(%(tax)s) THEN ac.amount::numeric ELSE 0 END),
                              count(DISTINCT ac.chart_id) FILTER (WHERE ac.chart_id = ANY(%(tax)s))
                       FROM gl g JOIN acc_trans ac ON (ac.trans_id = g.id) JOIN chart c ON (c.id = ac.chart_id)
                       GROUP BY g.id, g.transdate, g.reference
                       HAVING bool_or(c.accno = %(settle)s) AND bool_or(ac.chart_id = ANY(%(tax)s))
                       ORDER BY g.transdate""", {"tax": tax_ids, "settle": a.afdracht})
        own = cur.fetchall()
    months = {}
    for d, ref, taxsum, n in own:
        if not (a.van <= d.year <= a.tot):
            continue
        p = period_of("month", d)
        if p.todate != d:
            continue                    # geen maandaangifte (kwartaal, correctie, jaarrekening)
        m = months.setdefault(p.key, {"period": p, "theirs": D(0), "n": 0, "refs": []})
        m["theirs"] -= taxsum
        m["n"] += n
        m["refs"].append(ref)
    t0 = time.time()
    keys = sorted(months)
    for k in keys:
        rep = vr.report(months[k]["period"])
        months[k]["ours"] = rep.total_exact
        months[k]["diff"] = rep.total_exact - months[k]["theirs"]
        months[k]["warn"] = len(rep.split_warnings)
    cls = defaultdict(list)
    done = set()
    for i, k in enumerate(keys):
        m = months[k]
        if k in done:
            continue
        if abs(m["diff"]) <= max(m["n"], 1):
            cls["afronding"].append(k)
            continue
        nxt = keys[i + 1] if i + 1 < len(keys) else None
        if nxt and abs(m["diff"] + months[nxt]["diff"]) <= m["n"] + months[nxt]["n"]:
            cls["verschoven"] += [k, nxt]
            done.add(nxt)
            continue
        cls["rest"].append(k)
    print(f"2. {len(keys)} maanden met eigen afdrachtboeking ({time.time() - t0:.1f} s): "
          + ", ".join(f"{c} {len(v)}" for c, v in cls.items()))
    for k in cls["rest"]:
        m = months[k]
        print(f"  {k[:7]} {','.join(m['refs'])}: eigen {m['theirs']}, aangifte {m['ours']}, verschil {m['diff']}"
              + (f", {m['warn']} meldingen 'btw past niet'" if m["warn"] else ""))

    # 3. grondslag tegen btw
    rates = {x["box"] or x["suggested"]: x["rate"] for x in accs if x["rate"]}
    off = 0
    warns = 0
    for year in range(a.van, a.tot + 1):
        rep = vr.report(free_period(date(year, 1, 1), date(year, 12, 31)))
        warns += len(rep.split_warnings)
        for code, b in rep.boxes.items():
            r = rates.get(code)
            if year >= 2019 and code in ("1A", "1B") and r and b.base_exact and abs(b.base_exact * r - b.tax_exact) > max(5, b.tax_exact * D("0.01")):
                off += 1
                print(f"  {year} {code}: grondslag {b.base_exact} × {r} = {b.base_exact * r:.2f}, btw {b.tax_exact}")
    print(f"3. grondslag × tarief ≈ btw (1A/1B, per jaar vanaf 2019 = huidige tarieven, ≤ 1% of € 5): {off} afwijkingen; meldingen 'btw past niet': {warns}")
    conn.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
