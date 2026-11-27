"""Aanmaningen en rekeningoverzichten (fase 4 stap 4a, SB16): sjabloonvariabelen van het ORIGINEEL
(rp.pl generate_reminder / generate_ar_aging -> print, Form::parse_template onderschept) vergelijken met
``sqlledger.dunning``.

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/compare_dunning.py <db> [--todate 2026-06-30] [--buckets c0,c30,c60,c90] [--overdue]
           [--synthetic] [--numberformat 1.000,00] [--keep] [-v]

Perl maakt een aanmaning per factuur en gebruikt vandaag als peildatum; Python een brief per klant en
valuta (SB1). Vergeleken wordt daarom per factuur: welke facturen, het voorgestelde niveau en de
gegevens van factuur en klant. Rekeningoverzichten per (klant, valuta): regels, kolommen en totalen.
``--synthetic`` voegt eerst (in de kopie) een creditnota, een klant met twee valuta en een factuur op
niveau 3 toe. Verklaarde verschillen (bewuste afwijkingen, Perl-fouten 17/19/21) tellen niet als fout.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from compare_print import DSN, USEREMAIL, USERNAME, run_perl  # noqa: E402
from sqlledger import UserConfig, connect  # noqa: E402
from sqlledger.dunning import Dunning, letter_vars, statement_vars  # noqa: E402
from sqlledger.printdata import PrintData  # noqa: E402
from sqlledger.reports import Reports  # noqa: E402

ITEM_KEYS = ("invnumber", "invdate", "duedate", "due", "ordnumber", "ponumber", "invdescription", "dcn")
PARTY_KEYS = ("name", "address1", "address2", "city", "zipcode", "country", "customernumber", "currency")
ZEROS = ("", "0", "0,00", "0.00", None)


def norm(v):
    if v is None or v == "NULL":
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def amount_eq(a, b) -> bool:
    if norm(a) in ZEROS and norm(b) in ZEROS:
        return True
    return norm(a) == norm(b)


SYNTHETIC = """
-- creditnota klant 900 (negatief open), klant 902 ook in USD, F-3002 op niveau 3 (cap), klant met alleen tegoed
INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2026-06-01', 1.25) ON CONFLICT DO NOTHING;
INSERT INTO ar (id, invnumber, transdate, duedate, customer_id, netamount, amount, paid, invoice, curr, approved)
VALUES (3100, 'C-3100', '2026-06-28', '2026-06-28', 900, -23, -23, 0, false, 'EUR', true),
       (3200, 'F-3200', '2026-06-01', '2026-06-15', 902, 125, 125, 0, false, 'USD', true),
       (3300, 'C-3300', '2026-05-01', '2026-05-01', 903, -900, -900, 0, false, 'USD', true);
INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
SELECT x.id, (SELECT id FROM chart WHERE accno = '1200'), x.a, x.d, true
FROM (VALUES (3100, 23, date '2026-06-28'), (3200, -125, date '2026-06-01'), (3300, 900, date '2026-05-01')) x(id, a, d);
DELETE FROM status WHERE trans_id = 3002 AND formname LIKE 'reminder_';
INSERT INTO status (trans_id, formname) VALUES (3002, 'reminder3');
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--todate", help="peildatum rekeningoverzicht (aanmaningen: altijd vandaag, zoals rp.pl)")
    ap.add_argument("--buckets", default="c0,c30,c60,c90")
    ap.add_argument("--overdue", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--numberformat", default="1.000,00")
    ap.add_argument("--login", default="admin")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    today = date.today()
    todate = date.fromisoformat(a.todate) if a.todate else today
    buckets = [b for b in a.buckets.split(",") if b]
    copy = f"dn_{a.db}_{os.getpid()}_orig"
    with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{copy}"')
        c.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{a.db}"')
    try:
        if a.synthetic:
            with psycopg.connect(DSN + f" dbname={copy}", autocommit=True) as c:
                c.execute(SYNTHETIC)
        cfg = {"numberformat": a.numberformat, "login": a.login, "countrycode": "nl",
               "username": USERNAME, "useremail": USEREMAIL}
        perl = run_perl(copy, [{"op": "rp_print_ui", "args": {"type": "reminder"}},
                               {"op": "rp_print_ui", "args": {"type": "statement", "todate": todate.isoformat(),
                                                              "buckets": buckets, "overdue": a.overdue}}], cfg)
        config = UserConfig(login=a.login, numberformat=a.numberformat, dateformat="yyyy-mm-dd", countrycode="nl")
        conn = connect(dbname=copy, host="127.0.0.1", port=55432, user="postgres")
        try:
            report(a, conn, config, perl, today, todate, buckets)
        finally:
            conn.close()
    finally:
        if not a.keep:
            with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{copy}" WITH (FORCE)')


def report(a, conn, config, perl, today, todate, buckets):
    pd = PrintData(conn, config, username=USERNAME, useremail=USEREMAIL)
    d = Dunning(conn, config)
    diffs, explained, examples = Counter(), Counter(), {}

    def diff(key, *ex):
        diffs[key] += 1
        examples.setdefault(key, []).append(ex)

    # ---------------------------------------------------------------- aanmaningen
    rem = perl[0]
    perl_rows = {}
    for r in rem["rows"]:
        if r["id"] in perl_rows:
            explained["Perl: factuur dubbel bij twee contactpersonen (README 17)"] += 1
            continue
        perl_rows[r["id"]] = r
    raw = {str(r["id"]) for r in Reports(conn, config).reminders(asof=today).rows}
    if set(perl_rows) != raw:
        diff("aanmaningen: andere facturen", sorted(set(perl_rows) ^ raw))
    letters = d.letters(today)
    in_letter = {}
    for l in letters:
        v = letter_vars(pd, l, today)
        for n, (item, iv) in enumerate(zip(l.items, v["items"])):
            in_letter[str(item.id)] = (l, item, iv, v, n)
        for item, iv in zip(l.credits, v["credits"]):
            if item.duedate and item.duedate <= today:
                in_letter[str(item.id)] = (l, item, iv, v, -1)
    letter_keys = {(str(l.vc_id), l.curr) for l in letters}
    for tid in sorted(set(perl_rows) - set(in_letter)):
        p = perl_rows[tid]["vars"]
        if (norm(p.get("customer_id")), norm(p.get("currency"))) not in letter_keys:
            explained["klant met totaal ≤ 0: geen aanmaning (SB2)"] += 1
        else:
            diff("aanmaning ontbreekt", tid)
    for tid, (l, item, iv, v, n) in in_letter.items():
        if tid not in perl_rows:
            continue
        p = perl_rows[tid]["vars"]
        if n >= 0:
            plevel = int(perl_rows[tid]["level"] or 1)
            if plevel != item.next_level:
                # Perl-fout 21: na niveau 3 rekent rp.pl 4, de keuzelijst kent alleen 1–3 en toont 1
                if item.level == 3 and item.next_level == 3 and plevel in (1, 4):
                    explained["niveau na 3 blijft 3 (Perl-fout 21: 4)"] += 1
                else:
                    diff("niveau", tid, plevel, item.next_level)
        for k in ITEM_KEYS:
            pv, yv = norm(p.get(k)), norm(iv.get(k))
            ok = amount_eq(pv, yv) if k == "due" else pv == yv
            if not ok:
                diff(f"factuur.{k}", tid, pv, yv)
        for k in PARTY_KEYS:
            if norm(p.get(k)) != norm(v.get(k)):
                diff(f"klant.{k}", tid, p.get(k), v.get(k))
        if n == 0:
            for k in ("iban", "bic"):
                if norm(p.get(k)) != norm(v.get(k)):
                    diff(f"bank.{k}", tid, p.get(k), v.get(k))

    # ---------------------------------------------------------------- rekeningoverzichten
    st = perl[1]
    py = {s.key: s for s in d.statements(todate, buckets=buckets, overdue=a.overdue)}
    # facturen met een betaling ná de peildatum: Perl rekent met wat nu betaald is, wij per peildatum (SB5)
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ac.trans_id FROM acc_trans ac JOIN chart ch ON (ch.id = ac.chart_id)
                       JOIN ar a ON (a.id = ac.trans_id)
                       WHERE ac.transdate > %s AND 'AR' = ANY(string_to_array(ch.link, ':'))""", (todate,))
        late = {r[0] for r in cur.fetchall()}
    perl_st = {r["key"]: r["vars"] for r in st["rows"]}
    for key in sorted(set(py) | set(perl_st)):
        if key not in perl_st:
            if key in py and all(r["id"] in late for r in py[key].rows):
                explained["open op de peildatum, later betaald: Perl rekent met nu (SB5)"] += 1
            elif key in st["keys"]:
                explained["Perl: tweede valuta van een klant geen overzicht (Perl-fout 19)"] += 1
            else:
                diff("overzicht ontbreekt in Perl", key)
            continue
        if key not in py:
            diff("overzicht ontbreekt in Python", key)
            continue
        p, v = perl_st[key], statement_vars(pd, py[key], todate)
        prow = sorted(zip(p.get("invnumber") or [], *[p.get(b) or [] for b in buckets]))
        yrow = sorted((r["invnumber"], *[r[b] for b in buckets]) for r in v["rows"])
        if prow and prow[::2] == prow[1::2] and len(prow) == 2 * len(yrow):
            # Perl joint op contact: bij twee contactpersonen elke factuur twee keer, totalen dubbel (README 17)
            explained["Perl: overzicht met elke factuur dubbel (twee contactpersonen, README 17)"] += 1
            prow = prow[::2]
            if [x[0] for x in prow] == [x[0] for x in yrow] and all(
                    amount_eq(x, y) for pr, yr in zip(prow, yrow) for x, y in zip(pr[1:], yr[1:])):
                continue
        if len(prow) != len(yrow):
            diff("overzicht: aantal regels", key, len(prow), len(yrow))
        else:
            for pr, yr in zip(prow, yrow):
                if pr[0] != yr[0] or not all(amount_eq(x, y) for x, y in zip(pr[1:], yr[1:])):
                    diff("overzicht: regel", key, pr, yr)
        for b in buckets:
            if not amount_eq(p.get(f"{b}total"), v["totals"][b]):
                diff(f"overzicht: totaal {b}", key, p.get(f"{b}total"), v["totals"][b])
        if not amount_eq(p.get("total"), v["total"]):
            diff("overzicht: eindtotaal", key, p.get("total"), v["total"])
        for k in PARTY_KEYS:
            if norm(p.get(k)) != norm(v.get(k)):
                diff(f"overzicht: klant.{k}", key, p.get(k), v.get(k))

    print(f"{a.db}: aanmaningen Perl {len(perl_rows)} facturen, Python {len(letters)} brieven "
          f"({sum(len(l.items) for l in letters)} facturen); overzichten Perl {len(perl_st)}, Python {len(py)} "
          f"(peildatum {todate}, kolommen {','.join(buckets)}{', vanaf vervaldatum' if a.overdue else ''})")
    for k, n in sorted(explained.items()):
        print(f"  verklaard: {k}: {n}")
    if not diffs:
        print("  0 onverklaarde verschillen")
    for k, n in sorted(diffs.items()):
        print(f"  VERSCHIL {k}: {n}")
        for ex in examples[k][: (10 if a.v else 3)]:
            print("     ", ex)
    sys.exit(1 if diffs else 0)


if __name__ == "__main__":
    main()
