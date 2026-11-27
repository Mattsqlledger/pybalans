"""Betalingsspecificatie (fase 4 stap 4b, SB7/SB16): sjabloonvariabelen van het ORIGINEEL (cp.pl
``print_payment``/``print_payments``, Print op het betaalscherm vóór het boeken, Form::parse_template
onderschept) vergelijken met ``sqlledger.paymentspec`` op de betaling die Perl daarna boekt.

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/compare_payspec.py [<db>] [--numberformat 1.000,00] [--keep] [-v]

Standaard ``sl_template`` (testdata uit compare/arap_seed.sql) met scenario's: ontvangst op twee facturen,
deelbetaling met vooruitontvangst, betaling met vooruitbetaling, contante korting, vreemde valuta en het
scherm voor meerdere klanten. Per betaling vergeleken: relatie en adres, kenmerk, omschrijving, datum,
bedrag (ook in woorden), per factuur nummer, datum, betaald en korting; de vooruitbetaling als eigen regel.
Verklaarde verschillen (Perl-fout 25: print_payments geeft geen korting door) tellen niet als fout.
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
from sqlledger.paymentspec import PaymentSpecs, spec_vars  # noqa: E402
from sqlledger.printdata import PrintData  # noqa: E402

PARTY_KEYS = ("name", "address1", "address2", "city", "zipcode", "country")
HEAD_KEYS = ("source", "memo", "amount", "decimal", "integer_amount", "text_amount", "text_decimal")
ZEROS = ("", "0", "0,00", "0.00", None)

SETUP = """
UPDATE ar SET exchangerate = 1 WHERE exchangerate IS NULL;
UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL;
INSERT INTO chart (accno, description, charttype, category, link)
VALUES ('8590', 'Betalingskorting', 'A', 'E', 'AR_discount');
UPDATE ar SET cashdiscount = 0.02, discountterms = 10 WHERE id = 3001;
-- USD 500 tegen 1,10 (zoals tests/test_payments.py)
UPDATE ar SET exchangerate = 1.10, amount = 550, netamount = 550 WHERE id = 3003;
UPDATE acc_trans SET amount = amount * 1.1 WHERE trans_id = 3003;
INSERT INTO exchangerate (curr, transdate, exchangerate) VALUES ('USD', '2026-06-01', 1.10) ON CONFLICT DO NOTHING;
"""

D1, D2, D3 = "2026-07-01", "2026-07-02", "2026-07-03"

#: (soort, relatie, datum, kenmerk, argumenten voor de driver)
SCENARIOS = [
    ("customer", 900, D1, "R-1", {"rows": [{"invnumber": "F-3006", "paid": "100"}, {"invnumber": "F-3009"}],
                                  "memo": "deelbetaling"}),
    ("customer", 902, D1, "R-2", {"rows": [{"invnumber": "F-3007"}, {"invnumber": "F-3004", "paid": "40"}],
                                  "amount": "200"}),                      # rest wordt vooruitontvangst
    ("vendor", 905, D1, "C-1", {"rows": [{"invnumber": "I-4001"}], "amount": "1300"}),
    ("vendor", 901, D1, "C-2", {"rows": [{"invnumber": "I-4003", "paid": "21"}]}),
    ("customer", 900, D2, "K-1", {"rows": [{"invnumber": "F-3001", "paid": "118.58", "discount": "2.42"}],
                                  "discount_accno": "8590"}),
    ("customer", 903, D2, "U-1", {"rows": [{"invnumber": "F-3003"}], "currency": "USD", "exchangerate": "1.25"}),
]
#: scherm voor meerdere relaties (print_payments), elk op een eigen schone kopie: klanten 900 en 902 op D3
#: (900 precies F-3006, 902 oudste eerst met een deelbetaling), en met een overbetaling bij klant 900
MANY = [{"vc": "customer", "datepaid": D3, "payment_accno": "1100",
         "rows": [{"vc_id": 900, "paid": "263", "source": "M-1"}, {"vc_id": 902, "paid": "250", "source": "M-2"}]},
        {"vc": "customer", "datepaid": D3, "payment_accno": "1100",
         "rows": [{"vc_id": 900, "paid": "500", "source": "N-1"}, {"vc_id": 902, "paid": "100", "source": "N-2"}]}]


def norm(v):
    if v is None or v == "NULL":
        return ""
    return str(v).strip()


def amount_eq(a, b) -> bool:
    if norm(a) in ZEROS and norm(b) in ZEROS:
        return True
    return norm(a) == norm(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db", nargs="?", default="sl_template")
    ap.add_argument("--numberformat", default="1.000,00")
    ap.add_argument("--login", default="admin")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    copies = [f"ps_{a.db}_{os.getpid()}_{n}_orig" for n in range(1 + len(MANY))]
    with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
        for copy in copies:
            c.execute(f'DROP DATABASE IF EXISTS "{copy}"')
            c.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{a.db}"')
    conns = []
    try:
        for n, copy in enumerate(copies):
            with psycopg.connect(DSN + f" dbname={copy}", autocommit=True) as c:
                c.execute(SETUP)
                if n:       # meerdere relaties: zonder betalingskorting (zie Perl-fout 27 in README)
                    c.execute("UPDATE ar SET cashdiscount = 0, discountterms = 0 WHERE id = 3001")
        cfg = {"numberformat": a.numberformat, "login": a.login, "countrycode": "nl",
               "username": USERNAME, "useremail": USEREMAIL}
        ops = []
        for vc, vc_id, d, src, extra in SCENARIOS:
            ops.append({"op": "cp_post", "args": dict(vc=vc, vc_id=vc_id, datepaid=d, payment_accno="1100",
                                                    source=src, print=1, **extra)})
        perl = run_perl(copies[0], ops, cfg)
        perl_many = [run_perl(copy, [{"op": "cp_payments", "args": dict(m, print=1)}], cfg)[0]
                     for copy, m in zip(copies[1:], MANY)]
        config = UserConfig(login=a.login, numberformat=a.numberformat, dateformat="yyyy-mm-dd", countrycode="nl")
        conns = [connect(dbname=copy, host="127.0.0.1", port=55432, user="postgres") for copy in copies]
        sys.exit(report(a, conns, config, perl, perl_many))
    finally:
        for c in conns:
            c.close()
        if not a.keep:
            with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
                for copy in copies:
                    c.execute(f'DROP DATABASE IF EXISTS "{copy}" WITH (FORCE)')


def _words_fixed(perl: str, py: str) -> bool:
    """Perl locale/nl/Num2text: "honderdnul", "achtien", "twalf", geen trema; hier verbeterd."""
    fix = perl.replace("honderdnulen", "honderd").replace("honderdnul", "honderd").replace("achtien", "achttien").replace("twalf", "twaalf")
    for a, b in (("drieen", "drieën"), ("tweeen", "tweeën")):
        fix = fix.replace(a, b)
    return fix == py


def compare_one(diff, explained, label, p, v, *, many=False, head=True):
    for k in PARTY_KEYS:
        if norm(p.get(k)) != norm(v.get(k)):
            diff(f"relatie.{k}", label, p.get(k), v.get(k))
    for k in HEAD_KEYS if head else ():
        pv, yv = norm(p.get(k)), norm(v.get(k))
        if k == "memo" and many and not pv and not yv:
            continue
        if pv != yv:
            if k in ("text_amount", "text_decimal") and _words_fixed(pv, yv):
                explained["bedrag in woorden: Nederlandse spelling verbeterd (bewuste afwijking num2text)"] += 1
                continue
            diff(f"kop.{k}", label, pv, yv)
    if norm(p.get("datepaid")) != norm(v.get("datepaid")):
        diff("kop.datepaid", label, p.get("datepaid"), v.get("datepaid"))
    pinv = list(p.get("invnumber") or [])
    ppaid = list(p.get("paid") or [])
    pdisc = list(p.get("discount") or [])
    pdate = list(p.get("invdate") or [])
    if many:
        # print_payments zet elke aangevinkte post van de relatie op de specificatie, ook met 0 betaald
        keep = [i for i in range(len(pinv)) if norm(ppaid[i] if i < len(ppaid) else "") not in ZEROS]
        if len(keep) != len(pinv):
            explained["meerdere: Perl toont ook posten met 0 betaald"] += len(pinv) - len(keep)
        pinv, ppaid, pdate = [pinv[i] for i in keep], [ppaid[i] for i in keep], [pdate[i] if i < len(pdate) else "" for i in keep]
        pdisc = [pdisc[i] if i < len(pdisc) else "" for i in keep]
    lines = v["paylines"]
    prepay_perl = [i for i, n in enumerate(pinv) if n in ("Deposit", "Prepayment", "Vooruitbetaling", "Aanbetaling")]
    yinv = [("*" if l["prepayment"] else l["invnumber"]) for l in lines]
    pinv_n = [("*" if i in prepay_perl else n) for i, n in enumerate(pinv)]
    if sorted(pinv_n) != sorted(yinv):
        diff("facturen", label, pinv_n, yinv)
        return
    for i, n in enumerate(pinv_n):
        l = lines[yinv.index(n)]
        if not amount_eq(ppaid[i] if i < len(ppaid) else "", l["paid"]):
            diff("regel.betaald", label, n, ppaid[i] if i < len(ppaid) else None, l["paid"])
        pd_ = pdisc[i] if i < len(pdisc) else ""
        if not amount_eq(pd_, l["discount"]):
            if many and norm(pd_) in ZEROS:
                explained["meerdere relaties: Perl geeft geen korting door (Perl-fout 25)"] += 1
            else:
                diff("regel.korting", label, n, pd_, l["discount"])
        if n != "*" and norm(pdate[i] if i < len(pdate) else "") != norm(l["invdate"]):
            diff("regel.datum", label, n, pdate[i] if i < len(pdate) else None, l["invdate"])


def report(a, conns, config, perl, perl_many):
    pd = PrintData(conns[0], config, username=USERNAME, useremail=USEREMAIL)
    ps = PaymentSpecs(conns[0], config)
    diffs, explained, examples = Counter(), Counter(), {}

    def diff(key, *ex):
        diffs[key] += 1
        examples.setdefault(key, []).append(ex)

    n = 0
    for (vc, vc_id, d, src, _extra), res in zip(SCENARIOS, perl):
        label = f"{vc} {vc_id} {src}"
        if res.get("error"):
            diff("Perl-fout", label, res["error"][:300])
            continue
        spec = ps.find(vc, "1100", res.get("source") or src, date.fromisoformat(d), vc_id)
        if spec is None:
            diff("betaling niet gevonden", label)
            continue
        n += 1
        compare_one(diff, explained, label, res["print_vars"], spec_vars(pd, spec))
    for mn, (many, m, conn) in enumerate(zip(perl_many, MANY, conns[1:]), 1):
        n += compare_many(diff, explained, mn, many, m, conn, config)

    print(f"{a.db}: {n} betalingsspecificaties vergeleken ({len(SCENARIOS)} keer het scherm voor één relatie, "
          f"{len(MANY)} keer het scherm voor meerdere relaties)")
    for k, c in sorted(explained.items()):
        print(f"  verklaard: {k}: {c}")
    if not diffs:
        print("  0 onverklaarde verschillen")
    for k, c in sorted(diffs.items()):
        print(f"  VERSCHIL {k}: {c}")
        for ex in examples[k][: (10 if a.v else 3)]:
            print("     ", ex)
    return 1 if diffs else 0


def compare_many(diff, explained, mn, many, m, conn, config) -> int:
    """Scherm voor meerdere relaties (eigen kopie); geeft het aantal vergeleken specificaties."""
    if many.get("error"):
        diff("Perl-fout", f"meerdere {mn}", many["error"][:300])
        return 0
    pd = PrintData(conn, config, username=USERNAME, useremail=USEREMAIL)
    ps = PaymentSpecs(conn, config)
    with conn.cursor() as cur:
        cur.execute("""SELECT a.customer_id, ac.source, a.amount = 0 AND a.netamount = 0 FROM acc_trans ac
                       JOIN ar a ON (a.id = ac.trans_id) JOIN chart c ON (c.id = ac.chart_id)
                       WHERE c.accno = '1100' AND ac.transdate = %s AND NOT ac.fx_transaction""", (D3,))
        rows = [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]
    pvars = many.get("print_vars") or []
    if len(pvars) != len(m["rows"]):
        diff("meerdere: aantal documenten", mn, len(pvars), len(m["rows"]))
    n = 0
    for p in pvars:
        vc_id = int(p.get("customer_id") or 0)
        src = norm(p.get("source"))
        label = f"meerdere {mn} klant {vc_id} {src}"
        spec = ps.find("customer", "1100", src, date.fromisoformat(D3), vc_id)
        if spec is None:
            diff("meerdere: betaling niet gevonden", label)
            continue
        n += 1
        head = True
        elsewhere = [v2 for v2, s2, prepay in rows if prepay and s2 == src and v2 != vc_id]
        if elsewhere and not any(l.prepayment for l in spec.lines):
            # Perl (cp.pl post_payments) boekt de vooruitontvangst bij een andere klant; de afgedrukte
            # specificatie toont het ingevoerde bedrag. Vergelijk dan alleen de facturen.
            explained["meerdere: vooruitontvangst bij een andere klant geboekt (Perl-fout 26)"] += 1
            head = False
            keep = [i for i, x in enumerate(p.get("invnumber") or []) if x not in ("Deposit", "Prepayment")]
            for k in ("invnumber", "paid", "discount", "invdate"):
                p[k] = [(p.get(k) or [])[i] for i in keep if i < len(p.get(k) or [])]
        compare_one(diff, explained, label, p, spec_vars(pd, spec), many=True, head=head)
    return n


if __name__ == "__main__":
    main()
