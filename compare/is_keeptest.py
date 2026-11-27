"""Toets facturen met artikelen tegen het ORIGINEEL (fase 3, F9): kopie van <db> maken, ELKE factuur
(ar/ap met invoice = true) openen en ongewijzigd opslaan, daarna vergelijken met <db>:
grootboekregels per boeking (zonder acc_trans.id), factuurregels (zonder id), kop, voorraad,
laatste/gemiddelde inkoopprijs en ``invoice.allocated``.

gebruik (vanuit de repo):  python compare/is_keeptest.py adm_c [adm_a] [--limit n] [--keep] [--web]

--web: via de formuliercode van het webscherm (sqlledger_web.routes_is) in plaats van de bibliotheek.
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import LedgerError, UserConfig, connect  # noqa: E402
from sqlledger.invoice import InvoiceLedger  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"
HDR = ("invnumber, transdate, duedate, terms, description, notes, intnotes, ordnumber, quonumber, ponumber, curr, "
       "round(exchangerate::numeric, 8), taxincluded, onhold, department_id, employee_id, dcn, bank_id, cashdiscount, "
       "discountterms, language_code, datepaid, round(amount::numeric, 2), round(netamount::numeric, 2), "
       "round(paid::numeric, 2), shippingpoint, shipvia, waybill, paymentmethod_id, warehouse_id")


def acc_rows(conn, tb):
    # kenmerk/memo: leeg = NULL (oude Perl-versies schreven '' bij de btw-correctie van contante korting)
    rows = conn.execute(f"""SELECT ac.trans_id, ac.chart_id, round(ac.amount::numeric, 2), ac.transdate,
                                   NULLIF(ac.source, ''), NULLIF(ac.memo, ''), ac.cleared, ac.project_id,
                                   ac.fx_transaction, ac.approved
                            FROM acc_trans ac JOIN {tb} a ON (a.id = ac.trans_id) WHERE a.invoice""").fetchall()
    out = {}
    for r in rows:
        if r[2] != 0:
            out.setdefault(r[0], Counter())[r[1:]] += 1
    return out


def inv_rows(conn, tb):
    rows = conn.execute(f"""SELECT i.trans_id, i.parts_id, i.qty, i.allocated, i.sellprice, i.fxsellprice, i.discount,
                                   NULLIF(i.description, ''), COALESCE(i.assemblyitem, false), NULLIF(i.unit, ''),
                                   i.project_id, i.deliverydate::text, NULLIF(i.serialnumber, ''), NULLIF(i.itemnotes, ''),
                                   NULLIF(i.ordernumber, ''), NULLIF(i.ponumber, ''), i.cost, NULLIF(i.vendor, ''), i.vendor_id,
                                   COALESCE(i.lineitemdetail, false), COALESCE(i.kititem, false)
                            FROM invoice i JOIN {tb} a ON (a.id = i.trans_id) WHERE a.invoice ORDER BY i.id""").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r[0], []).append(r[1:])
    return out


def web_repost(conn, cfg, trans_id: int, vc: str) -> int:
    """Openen zoals het factuurscherm (form_from_invoice + recalc) en ongewijzigd opslaan (do_post)."""
    from sqlledger_web import routes_is as ri
    from sqlledger_web.reportdefs import Lib
    from sqlledger._db import cursor, default_currency, get_precision
    lib = Lib(conn, cfg)
    with cursor(conn) as cur:
        prec, defcur = get_precision(cur), default_currency(cur)
    f = ri.form_from_invoice(lib, InvoiceLedger(conn, cfg).get_invoice(trans_id, vc, lock=True), cfg.numberformat, prec)
    errors = ri.recalc(lib, f, cfg.numberformat, prec, defcur)
    if errors:
        raise ValueError(" ".join(errors))
    return ri.do_post(lib, f, cfg.numberformat)


def run(src: str, limit: int = 0, keep: bool = False, web: bool = False) -> None:
    admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True)
    db = f"iskeep_{src}"
    admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
    admin.execute(f'CREATE DATABASE "{db}" TEMPLATE "{src}"')
    c = connect(f"{DSN} dbname={db}")
    c.execute("UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'")
    c.execute("DELETE FROM semaphore")
    cfg = UserConfig(login="admin")
    lib = InvoiceLedger(c, cfg)
    t0 = time.time()
    errs, n = {}, 0
    changed = {}            # factuur -> (geboekt, berekend met de huidige instellingen)
    for tb, vc in (("ap", "vendor"), ("ar", "customer")):
        ids = [r[0] for r in c.execute(f"SELECT id FROM {tb} WHERE invoice ORDER BY id").fetchall()]
        if limit:
            ids = ids[:limit]
        for i in ids:
            n += 1
            try:
                # vóór het opslaan: rekent de factuur met de huidige btw-instellingen (artikel, relatie)
                # nog op hetzelfde bedrag uit als geboekt? Zo niet: instellingen na het boeken gewijzigd
                inv = lib.get_invoice(i, vc)
                calc = lib.calculate(inv, lib.tax_rates(inv))
                stored = c.execute(f"SELECT round(amount::numeric, 2), round(netamount::numeric, 2) FROM {tb} "
                                   "WHERE id = %s", (i,)).fetchone()
                if (calc.invamount, calc.invnetamount) != tuple(stored):
                    changed[i] = (tuple(stored), (calc.invamount, calc.invnetamount))
                if web:
                    web_repost(c, cfg, i, vc)
                else:
                    lib.post_invoice(lib.get_invoice(i, vc))
            except (LedgerError, ValueError) as e:
                errs[i] = f"{type(e).__name__}: {e}"
            if n % 500 == 0:
                print(f"  {src}: {n} ({round(time.time() - t0)} s)", flush=True)
    print(f"{src}: {n} facturen opnieuw opgeslagen, {len(errs)} fouten, {round(time.time() - t0)} s")
    explained = set()
    for i, e in list(errs.items())[:10]:
        print(f"   fout {i}: {e}")
    o = psycopg.connect(f"{DSN} dbname={src}", autocommit=True)
    for tb in ("ar", "ap"):
        A, B = acc_rows(o, tb), acc_rows(c, tb)
        diff_ids = {t for t in set(A) | set(B) if A.get(t) != B.get(t)}
        explained |= diff_ids & set(changed)
        bad = sorted(diff_ids - set(changed))
        print(f"{tb}: grootboek anders bij {len(bad)} facturen {bad[:10]}")
        for t in bad[:3]:
            print(f"   {t} alleen origineel: {sorted((A.get(t, Counter()) - B.get(t, Counter())).elements())[:6]}")
            print(f"   {t} alleen opnieuw  : {sorted((B.get(t, Counter()) - A.get(t, Counter())).elements())[:6]}")
        IA, IB = inv_rows(o, tb), inv_rows(c, tb)
        bad = sorted(t for t in set(IA) | set(IB) if IA.get(t) != IB.get(t))
        print(f"{tb}: factuurregels anders bij {len(bad)} facturen {bad[:10]}")
        for t in bad[:3]:
            for x, y in zip(IA.get(t, []), IB.get(t, [])):
                if x != y:
                    print(f"   {t}: {x}\n   {' ' * len(str(t))}  {y}")
        q = f"SELECT id, {HDR} FROM {tb} WHERE invoice"
        HA = {r[0]: r[1:] for r in o.execute(q)}
        HB = {r[0]: r[1:] for r in c.execute(q)}
        names = [x.strip() for x in HDR.replace("::numeric, 8)", "").replace("::numeric, 2)", "").split(",")]
        diff = {}
        for i in HA:
            if i in explained:
                continue
            for name, a, b in zip(names, HA[i], HB.get(i, ())):
                if (a if a != "" else None) != (b if b != "" else None):
                    diff.setdefault(name, []).append((i, a, b))
        for name, rows in diff.items():
            print(f"   kop {tb}.{name}: {len(rows)} anders, bv. {rows[:3]}")
    print(f"verklaard: {len(explained)} facturen met een ander grootboek rekenen vóór het opslaan al anders uit "
          f"dan geboekt (btw-instellingen van artikel/relatie na het boeken gewijzigd; opnieuw opslaan rekent zoals "
          f"Perl met de huidige instellingen): {sorted(explained)[:10]}")
    q = "SELECT id, onhand, lastcost, avgcost, obsolete, bin, weight FROM parts"
    PA = {r[0]: r[1:] for r in o.execute(q)}
    PB = {r[0]: r[1:] for r in c.execute(q)}
    pd = [(i, PA[i], PB.get(i)) for i in PA if PA[i] != PB.get(i)]
    print(f"artikelen anders: {len(pd)} {pd[:5]}")
    c.close()
    o.close()
    if not keep:
        admin.execute(f'DROP DATABASE IF EXISTS "{db}"')


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    if limit:
        args = [a for a in args if a != str(limit)]
    for db in args:
        run(db, limit, "--keep" in sys.argv, "--web" in sys.argv)
