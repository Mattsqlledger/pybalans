"""VAT Return (VK) op echte data (BTW_LANDEN_ANALYSE.md UK6).

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``; op een KOPIE, het script legt land en koppelingen vast):
    python compare/vat_check_gb.py <db> --afdracht 1520 [--omschrijving "vat return"]

1. Per kwartaal met een eigen aangifteboeking (memoriaal op de afdrachtrekening, omschrijving bevat de tekst, datum =
   kwartaaleinde): box 5 exact tegen wat de administratie zelf tegenboekte, en per btw-rekening het verschil.
2. Bewijs dat elke btw-mutatie precies één keer telt: de som van de verschillen over alle kwartalen = het saldo dat
   na de laatste eigen aangifte nog op de btw-rekeningen staat (begin: saldo na de aangifte ervoor). Verschillen per
   kwartaal zijn dan verschuivingen (boekingen die na de eigen aangifte zijn ingevoerd, gewijzigd of van datum
   veranderd), zoals bij administratie A (``compare/vat_check.py``).
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import UserConfig, connect  # noqa: E402
from sqlledger.vatreturn import VatReturns, period_of  # noqa: E402

D = Decimal


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--afdracht", required=True)
    ap.add_argument("--omschrijving", default="vat return")
    a = ap.parse_args()
    conn = connect(dbname=a.db)
    vr = VatReturns(conn, UserConfig(login="vatcheck"), country="GB")
    vr.save_country("GB", 0, "vatcheck")
    accs = vr.tax_accounts()
    vr.save_settings({x["id"]: x["box"] or x["suggested"] for x in accs}, a.afdracht, "", "vatcheck")
    conn.commit()
    accs = vr.tax_accounts()
    print("koppelingen:", ", ".join(f"{x['accno']}={x['box']}" for x in accs))
    vat_ids = [x["id"] for x in accs if x["box"] != "none"]
    cur = conn.cursor()
    cur.execute("""SELECT g.id, g.transdate FROM gl g
                   WHERE EXISTS (SELECT 1 FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                                 WHERE ac.trans_id = g.id AND c.accno = %s)
                     AND EXISTS (SELECT 1 FROM acc_trans ac WHERE ac.trans_id = g.id AND ac.chart_id = ANY(%s))
                     AND g.description ILIKE %s ORDER BY g.transdate""",
                (a.afdracht, vat_ids, f"%{a.omschrijving}%"))
    own = [(gid, d) for gid, d in cur.fetchall() if period_of("quarter", d).todate == d]
    total_diff = D(0)
    equal = 0
    for gid, d in own:
        p = period_of("quarter", d)
        cur.execute("""SELECT ac.chart_id, sum(ac.amount::numeric) FROM acc_trans ac
                       WHERE ac.trans_id = %s AND ac.chart_id = ANY(%s) GROUP BY 1""", (gid, vat_ids))
        theirs = {cid: -amt for cid, amt in cur.fetchall()}
        rep = vr.report(p)
        ours = {x.chart_id: x.amount for x in rep.accounts}
        diff = rep.total_exact - sum(theirs.values(), D(0))
        total_diff += diff
        equal += abs(diff) < D("0.005")
        per = " ".join(f"{x['accno']}:{ours.get(x['id'], 0) - theirs.get(x['id'], 0):+}" for x in accs
                       if ours.get(x["id"], 0) != theirs.get(x["id"], 0))
        print(f"{p.fromdate}..{p.todate}  eigen {sum(theirs.values(), D(0)):>11}  box 5 {rep.totals['5']:>11}  "
              f"verschil {diff:>9}  {per}")
    if not own:
        print("geen eigen aangifteboekingen gevonden")
        return 1
    start, last = period_of("quarter", own[0][1]).fromdate, own[-1][1]
    own_ids = [gid for gid, _d in own]
    cur.execute("""SELECT coalesce(sum(ac.amount::numeric) FILTER (WHERE ac.transdate < %(s)s), 0),
                          coalesce(sum(ac.amount::numeric) FILTER (WHERE ac.transdate <= %(e)s), 0),
                          coalesce(sum(ac.amount::numeric) FILTER (
                              WHERE ac.transdate BETWEEN %(s)s AND %(e)s AND NOT (ac.trans_id = ANY(%(own)s))
                                AND EXISTS (SELECT 1 FROM acc_trans x JOIN chart c ON c.id = x.chart_id
                                            WHERE x.trans_id = ac.trans_id AND c.accno = %(settle)s)), 0)
                   FROM acc_trans ac WHERE ac.chart_id = ANY(%(vat)s)""",
                {"s": start, "e": last, "own": own_ids, "settle": a.afdracht, "vat": vat_ids})
    begin, end, other = cur.fetchone()
    expect = (end - begin) - other
    print(f"{equal}/{len(own)} kwartalen gelijk; som verschillen {total_diff}; saldo btw-rekeningen (credit) begin "
          f"{begin}, eind {end}; andere boekingen op de afdrachtrekening {other}; verwacht {expect}")
    ok = abs(total_diff - expect) < D("0.005")
    print("ELKE BTW-MUTATIE TELT PRECIES EEN KEER" if ok else "NIET SLUITEND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
