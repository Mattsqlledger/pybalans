"""Echte-datatoets bankafschrift inlezen (fase 2, I1–I10).

Op een KOPIE van <db>:
1. uit alle boekingen op bankrekening <accno> in de periode wordt een synthetisch
   CAMT.053-afschrift gemaakt: één regel per betaling (kenmerk + datum + relatie,
   zoals de bank één overboeking toont) of per overige boeking; met het kenmerk (dcn)
   als gestructureerd betalingskenmerk als de factuur er een heeft, anders de
   factuurnummers in de omschrijving, en de IBAN van de relatie als die bekend is;
2. de ontvangsten/betalingen in de periode worden op de kopie verwijderd alsof ze
   nooit geboekt zijn (dezelfde regels die ``void_payment`` herkent, maar weggehaald
   i.p.v. tegengeboekt; werkt ook zonder kenmerk, wat ``void_payment`` niet kan).
   Betalingen met kenmerk NULL of met een vooruitbetaling blijven staan; die regels
   moeten via "bestaande boeking" (d) gekoppeld worden;
3. het afschrift wordt ingelezen (rekening gekozen, IBAN opgeslagen), alle voorstellen
   worden geboekt/gekoppeld;
4. controle: ar/ap amount/paid/datepaid en saldo per rekening gelijk aan het origineel,
   en per regel of het voorstel de oorspronkelijke boeking was.

gebruik (vanuit de repo-root):
    python compare/bank_realdata.py --db adm_a --accno 1000 --from 2024-10-01 --to 2024-12-31
    python compare/bank_realdata.py --db adm_c --accno 1100 --from 2015-10-01 --to 2015-10-31
De kopie (bi_<db>_xxxx) wordt na afloop verwijderd (behalve met --keep).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from bankfiles import Entry, camt, make_iban  # noqa: E402
from sqlledger import LedgerError, UserConfig, connect  # noqa: E402
from sqlledger._db import cursor, default_currency  # noqa: E402
from sqlledger.amounts import round_amount, to_decimal  # noqa: E402
from sqlledger.bankimport import BankImport, MatchAction  # noqa: E402
from sqlledger.payments import Payments  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"
ZERO = Decimal(0)


def snapshot(conn):
    with cursor(conn) as cur:
        arap = {}
        for t in ("ar", "ap"):
            cur.execute(f"SELECT id, amount, paid, datepaid FROM {t}")
            for r in cur.fetchall():
                arap[(t, r["id"])] = (round_amount(to_decimal(r["amount"]), 2),
                                      round_amount(to_decimal(r["paid"]), 2), r["datepaid"])
        cur.execute("SELECT chart_id, round(sum(amount)::numeric, 2) AS s FROM acc_trans GROUP BY chart_id")
        bal = {r["chart_id"]: to_decimal(r["s"]) for r in cur.fetchall()}
    return arap, bal


def build_lines(conn, chart_id, dfrom, dto):
    """Regels van het synthetische afschrift + wat er oorspronkelijk geboekt was."""
    with cursor(conn) as cur:
        cur.execute("""SELECT ac.trans_id, ac.transdate, ac.source, sum(ac.amount)::numeric AS amt,
                              sum(CASE WHEN ac.fx_transaction THEN 0 ELSE ac.amount END)::numeric AS amt_nofx,
                              max(ar.customer_id) AS cid, max(ap.vendor_id) AS vid,
                              max(coalesce(ar.invnumber, ap.invnumber)) AS invnumber,
                              max(coalesce(ar.dcn, ap.dcn)) AS dcn, max(gl.description) AS gldesc,
                              max(coalesce(ar.amount, ap.amount)) AS invamount
                       FROM acc_trans ac
                       LEFT JOIN ar ON (ar.id = ac.trans_id) LEFT JOIN ap ON (ap.id = ac.trans_id)
                       LEFT JOIN gl ON (gl.id = ac.trans_id)
                       WHERE ac.chart_id = %s AND ac.transdate BETWEEN %s AND %s AND ac.approved
                       GROUP BY ac.trans_id, ac.transdate, ac.source
                       ORDER BY ac.transdate, ac.trans_id""", (chart_id, dfrom, dto))
        rows = cur.fetchall()
        cur.execute("SELECT id, name FROM customer")
        cnames = {r["id"]: r["name"] for r in cur.fetchall()}
        cur.execute("SELECT id, name FROM vendor")
        vnames = {r["id"]: r["name"] for r in cur.fetchall()}
        cur.execute("SELECT id, iban FROM bank WHERE coalesce(iban, '') <> ''")
        ibans = {r["id"]: r["iban"].replace(" ", "").upper() for r in cur.fetchall()}
    lines = {}
    for r in rows:
        amt = round_amount(-to_decimal(r["amt"]), 2)
        if r["cid"] or r["vid"]:
            vc = "customer" if r["cid"] else "vendor"
            vc_id = r["cid"] or r["vid"]
            key = (vc, vc_id, r["source"], r["transdate"]) if r["source"] is not None else ("x", r["trans_id"], r["transdate"], None)
        else:
            vc, vc_id = None, None
            key = ("x", r["trans_id"], r["transdate"], r["source"])
        ln = lines.setdefault(key, dict(key=key, date=r["transdate"], amount=ZERO, vc=vc, vc_id=vc_id,
                                        source=r["source"], invoices=[], groups=[], gldesc=r["gldesc"],
                                        deposit=False))
        ln["amount"] += amt
        ln["groups"].append((r["trans_id"], r["transdate"], r["source"]))
        if vc:
            paid = -to_decimal(r["amt_nofx"]) if vc == "customer" else to_decimal(r["amt_nofx"])
            ln["invoices"].append(dict(trans_id=r["trans_id"], invnumber=r["invnumber"], dcn=r["dcn"],
                                       paid=round_amount(paid, 2)))
            if not to_decimal(r["invamount"]):
                ln["deposit"] = True
    out = []
    for ln in lines.values():
        if not ln["amount"]:
            continue
        ln["name"] = (cnames if ln["vc"] == "customer" else vnames).get(ln["vc_id"]) if ln["vc"] else None
        ln["iban"] = ibans.get(ln["vc_id"]) if ln["vc"] else None
        out.append(ln)
    return sorted(out, key=lambda l: (l["date"], str(l["key"])))


def remove_payment(cur, vc, chart_id, tid, datepaid, source):
    """Eén betaling van een factuur weghalen alsof hij nooit geboekt is: dezelfde regels
    als ``void_payment`` terugdraait (bank, koersverschil, debiteuren/crediteuren,
    koerswinst/-verlies, korting), maar dan verwijderd; paid/amount/datepaid terug."""
    table, ARAP = ("ar", "AR") if vc == "customer" else ("ap", "AP")
    cur.execute("SELECT fldvalue FROM defaults WHERE fldname = 'fxgainloss_accno_id'")
    r = cur.fetchone()
    pl = Payments._payment_lines(cur, table, ARAP, tid, chart_id, source, datepaid, 2, r["fldvalue"] if r else None)
    for row in pl["rows"]:
        cur.execute("DELETE FROM acc_trans WHERE ctid = %s::tid", (row["ctid"],))
    cur.execute(f"SELECT amount, paid FROM {table} WHERE id = %s", (tid,))
    h = cur.fetchone()
    cur.execute(f"UPDATE {table} SET paid = %s, amount = %s, datepaid = %s WHERE id = %s",
                (round_amount(to_decimal(h["paid"]) - pl["paid_base"], 2),
                 round_amount(to_decimal(h["amount"]) + pl["cdt_base"], 2),
                 Payments._last_datepaid(cur, tid, ARAP), tid))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--accno", required=True)
    ap.add_argument("--from", dest="dfrom", required=True, type=date.fromisoformat)
    ap.add_argument("--to", dest="dto", required=True, type=date.fromisoformat)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True)
    name = f"bi_{args.db}_{uuid.uuid4().hex[:6]}"
    for attempt in range(30):
        try:
            admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{args.db}"')
            break
        except psycopg.errors.ObjectInUse:
            time.sleep(10)
    else:
        sys.exit(f"kan geen kopie van {args.db} maken (in gebruik)")
    t0 = time.time()
    try:
        conn = connect(f"{DSN} dbname={name}")
        conn.execute("UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'")
        cfg = UserConfig(login="bi_test")
        with cursor(conn) as cur:
            cur.execute("SELECT id FROM chart WHERE accno = %s", (args.accno,))
            chart_id = cur.fetchone()["id"]
            curr = default_currency(cur)
            cur.execute("SELECT round(sum(amount)::numeric, 2) AS s FROM acc_trans WHERE chart_id = %s "
                        "AND transdate < %s AND approved", (chart_id, args.dfrom))
            opening = -to_decimal(cur.fetchone()["s"])
        arap0, bal0 = snapshot(conn)
        lines = build_lines(conn, chart_id, args.dfrom, args.dto)

        # 2. betalingen verwijderen (kopie "van vóór de betaling")
        voided, void_fail = set(), Counter()
        for ln in lines:
            if not ln["vc"] or ln["source"] is None:
                continue
            if ln["deposit"]:
                void_fail["vooruitbetaling"] += 1
                continue
            try:
                with conn.transaction(), cursor(conn) as cur:
                    for inv in ln["invoices"]:
                        if inv["paid"]:
                            remove_payment(cur, ln["vc"], chart_id, inv["trans_id"], ln["date"], ln["source"])
                voided.add(ln["key"])
            except LedgerError as exc:
                void_fail[str(exc)[:60]] += 1

        # 1. afschrift
        own = make_iban("NL", "TEST0000000001")
        entries = []
        for i, ln in enumerate(lines, 1):
            ref = desc = None
            if ln["vc"]:
                dcns = [x["dcn"] for x in ln["invoices"] if x["dcn"]]
                if len(ln["invoices"]) == 1 and dcns:
                    ref = dcns[0]
                else:
                    desc = "Factuur " + " ".join(x["invnumber"] or "" for x in ln["invoices"])
            else:
                desc = ln["gldesc"] or "Boeking"
            ln["bank_ref"] = f"RD{i:05d}"
            entries.append(Entry(ln["amount"], ln["date"], ln["name"], ln["iban"], desc, ref, bank_ref=ln["bank_ref"]))
        data = camt(own, entries, opening, ccy=curr, stmt_id=f"{args.db}-{args.dfrom}")

        bi = BankImport(conn, cfg)
        res = bi.import_file(data, "synthetisch.xml", chart_accno=args.accno)
        st = res.statements[0]
        print(f"afschrift: {st.lines_new} regels, waarschuwingen: {st.warnings or 'geen'}")
        by_ref = {l["bank_ref"]: l for l in lines}

        # 3. voorstellen boeken (bij twijfel kiest de "gebruiker" de bestaande boeking)
        props = bi.proposals(args.accno)
        cats, correct, wrong, errors, doubts = Counter(), Counter(), [], Counter(), 0
        for p in props:
            ln = by_ref[p.line["bank_ref"]]
            cats[p.category or "-"] += 1
            if ln["key"] in voided:
                expect = {x["trans_id"]: x["paid"] for x in ln["invoices"] if x["paid"]}
                got = {a.trans_id: a.paid for a in p.allocations}
                ok = p.kind == "payment" and p.vc_id == ln["vc_id"] and got == expect and not p.prepayment
            else:
                ok = p.kind == "match" and (p.match["trans_id"], p.match["transdate"], p.match["source"]) in \
                    set(ln["groups"])
            ok = ok and not p.doubt
            correct["goed" if ok else "fout/geen"] += 1
            if p.sure:
                correct["zeker"] += 1
                correct["zeker en goed"] += ok
            if not ok:
                wrong.append((p.category, ln["key"][0], ln["amount"], ("TWIJFEL " if p.doubt else "") + p.reason[:90]))
            action = p.action()
            if p.doubt:
                doubts += 1
                g = p.alternative
                action = MatchAction(g["trans_id"], g["transdate"], g["source"])
            if action:
                try:
                    bi.process(p.line["id"], action)
                except LedgerError as exc:
                    errors[str(exc)[:80]] += 1

        # 4. controle
        arap1, bal1 = snapshot(conn)
        diffs = Counter()
        diff_ex = []
        for k, v0 in arap0.items():
            v1 = arap1.get(k)
            for idx, what in enumerate(("amount", "paid", "datepaid")):
                if v1[idx] != v0[idx]:
                    diffs[what] += 1
                    if len(diff_ex) < 8:
                        diff_ex.append((k, what, v0[idx], v1[idx]))
        new_arap = len(set(arap1) - set(arap0))
        bal_diff = {c: (bal0.get(c, ZERO), bal1.get(c, ZERO)) for c in set(bal0) | set(bal1)
                    if bal0.get(c, ZERO) != bal1.get(c, ZERO)}
        with cursor(conn) as cur:
            cur.execute("""SELECT count(*) AS n FROM x_bank_line WHERE chart_id = %s AND status = 'open'""",
                        (chart_id,))
            still_open = cur.fetchone()["n"]
            cur.execute("""SELECT count(*) AS n FROM (SELECT 1 FROM acc_trans WHERE chart_id = %s AND transdate
                           BETWEEN %s AND %s AND cleared IS NULL GROUP BY trans_id, transdate, source
                           HAVING round(sum(amount)::numeric, 2) <> 0) x""", (chart_id, args.dfrom, args.dto))
            uncleared = cur.fetchone()["n"]

        n = len(props)
        print(f"{args.db} {args.accno} {args.dfrom}..{args.dto}: {n} bankregels, {len(voided)} betalingen "
              f"teruggezet (niet: {dict(void_fail)})")
        print(f"  voorstellen per categorie: {dict(sorted(cats.items()))}")
        print(f"  automatisch goed gekoppeld: {correct['goed']}/{n} = {100 * correct['goed'] / max(n, 1):.1f}%; zeker (bulk): {correct['zeker']}, daarvan goed {correct['zeker en goed']}; twijfel (bestaande boeking gekozen): {doubts}")
        for w in wrong[:15]:
            print("   niet goed:", w)
        print(f"  verwerkfouten: {dict(errors) or 'geen'}; nog open regels: {still_open}; "
              f"niet-afgeletterde boekingen in periode: {uncleared}")
        print(f"  ar/ap verschillen t.o.v. origineel: {dict(diffs) or 'geen'}; nieuwe ar/ap: {new_arap}")
        for d in diff_ex:
            print("   ", d)
        print(f"  saldo per rekening verschillend: {len(bal_diff)} {list(bal_diff.items())[:5]}")
        print(f"  tijd {time.time() - t0:.0f}s")
        conn.close()
    finally:
        if not args.keep:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


if __name__ == "__main__":
    main()
