"""Toets orders en offertes tegen het ORIGINEEL (fase 3, O6): kopie van <db> maken, ELKE order/offerte openen
en ongewijzigd opslaan, daarna vergelijken met <db>: kop (oe), regels (orderitems zonder id), afleveradres,
afdeling, status, referenties, vracht, magazijnbewegingen, voorraad, tellers en koersen.

Gesloten en gefactureerde orders kunnen in het scherm niet opgeslagen worden (O6, ON1); de toets slaat ze
toch op via ``save_order(_force=True)`` zodat de schrijfcode voor álle orders bewezen wordt, en telt hoeveel
het scherm zou weigeren.

gebruik (vanuit de repo):  python compare/oe_keeptest.py adm_c [--limit n] [--keep] [--web]

--web: openen en opslaan via de formuliercode van het webscherm (sqlledger_web.routes_oe).
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
from sqlledger.order import OrderLedger  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"


def by_trans(conn, sql):
    out = {}
    for r in conn.execute(sql).fetchall():
        out.setdefault(r[0], []).append(tuple(r[1:]))
    return out


def tables(conn):
    return {
        "oe": {r[0]: tuple(r[1:]) for r in conn.execute("SELECT * FROM oe").fetchall()},
        "orderitems": by_trans(conn, """SELECT trans_id, parts_id, description, qty, sellprice, discount, unit,
                                               project_id, reqdate, ship, serialnumber, itemnotes, lineitemdetail,
                                               ordernumber, ponumber, cost, vendor, vendor_id
                                        FROM orderitems ORDER BY id"""),
        "shipto": by_trans(conn, "SELECT s.* FROM shipto s JOIN oe o ON (o.id = s.trans_id)"),
        "dpt_trans": by_trans(conn, "SELECT d.* FROM dpt_trans d JOIN oe o ON (o.id = d.trans_id)"),
        "status": by_trans(conn, """SELECT s.trans_id, s.printed, s.emailed, s.spoolfile, s.formname
                                     FROM status s JOIN oe o ON (o.id = s.trans_id) ORDER BY s.formname"""),
        "reference": by_trans(conn, """SELECT r.trans_id, r.code, r.description, r.archive_id, r.login, r.formname,
                                              r.folder FROM reference r JOIN oe o ON (o.id = r.trans_id)
                                       ORDER BY r.description"""),
        "cargo": by_trans(conn, """SELECT c.trans_id, c.package, c.netweight, c.grossweight, c.volume
                                   FROM cargo c JOIN oe o ON (o.id = c.trans_id) ORDER BY c.id"""),
        "inventory": by_trans(conn, """SELECT i.trans_id, i.parts_id, i.qty, i.warehouse_id, i.shippingdate
                                       FROM inventory i JOIN oe o ON (o.id = i.trans_id)"""),
        "acc_trans": by_trans(conn, "SELECT a.trans_id, a.chart_id, a.amount FROM acc_trans a JOIN oe o ON (o.id = a.trans_id)"),
        "recurring": by_trans(conn, "SELECT r.id, r.* FROM recurring r JOIN oe o ON (o.id = r.id)"),
        "parts": {r[0]: tuple(r[1:]) for r in conn.execute("SELECT id, onhand, weight, lastcost FROM parts").fetchall()},
        "defaults": {r[0]: r[1] for r in conn.execute("SELECT fldname, fldvalue FROM defaults "
                                                      "WHERE fldname <> 'audittrail'").fetchall()},
        "exchangerate": {(r[0], r[1]): r[2] for r in conn.execute("SELECT curr, transdate, exchangerate FROM exchangerate")},
    }


def web_resave(conn, cfg, oe_id: int) -> None:
    """Openen zoals het orderscherm (form_from_order + recalc) en ongewijzigd opslaan (to_order + save)."""
    from sqlledger_web import routes_oe as ro
    from sqlledger_web.reportdefs import Lib
    from sqlledger._db import cursor, default_currency, get_precision
    lib = Lib(conn, cfg)
    with cursor(conn) as cur:
        prec, defcur = get_precision(cur), default_currency(cur)
    f = ro.form_from_order(lib, OrderLedger(conn, cfg).get_order(oe_id, lock=True), cfg.numberformat, prec)
    errors = ro.recalc(lib, f, cfg.numberformat, prec, defcur)
    if errors:
        raise ValueError(" ".join(errors))
    ro.do_save(lib, f, cfg.numberformat, _force=True)


def run(src: str, limit: int = 0, keep: bool = False, web: bool = False) -> None:
    admin = psycopg.connect(f"{DSN} dbname=postgres", autocommit=True)
    db = f"oekeep_{src}"
    admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
    admin.execute(f'CREATE DATABASE "{db}" TEMPLATE "{src}"')
    c = connect(f"{DSN} dbname={db}")
    c.execute("DELETE FROM semaphore")
    c.execute("UPDATE defaults SET fldvalue = '0' WHERE fldname = 'audittrail'")    # logregels niet meetellen
    cfg = UserConfig(login="admin")
    ol = OrderLedger(c, cfg)
    ids = [r[0] for r in c.execute("SELECT id FROM oe ORDER BY id").fetchall()]
    if limit:
        ids = ids[:limit]
    t0 = time.time()
    errs, refused, changed = {}, Counter(), {}
    for n, i in enumerate(ids, 1):
        try:
            o = ol.get_order(i)
            calc = ol.calculate(o)
            stored = c.execute("SELECT round(amount::numeric, 2), round(netamount::numeric, 2) FROM oe WHERE id = %s",
                               (i,)).fetchone()
            if (calc.amount, calc.netamount) != tuple(stored):
                changed[i] = (tuple(stored), (calc.amount, calc.netamount))
            if o.aa_id:
                refused["gefactureerd"] += 1
            elif o.closed:
                refused["gesloten"] += 1
            if web:
                web_resave(c, cfg, i)
            else:
                ol.save_order(o, _force=True)
        except (LedgerError, ValueError) as e:
            errs[i] = f"{type(e).__name__}: {e}"
        if n % 1000 == 0:
            print(f"  {src}: {n} ({round(time.time() - t0)} s)", flush=True)
    print(f"{src}: {len(ids)} orders/offertes opnieuw opgeslagen{' via het webscherm' if web else ''}, "
          f"{len(errs)} fouten, {round(time.time() - t0)} s; het scherm zou weigeren: {dict(refused)}")
    for i, e in list(errs.items())[:10]:
        print(f"   fout {i}: {e}")
    o = psycopg.connect(f"{DSN} dbname={src}", autocommit=True)
    A, B = tables(o), tables(c)
    total = 0
    for name in A:
        a, b = A[name], B[name]
        keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if name == "oe":
            expl = [k for k in keys if k in changed]
            keys = [k for k in keys if k not in changed]
            if expl:
                print(f"   oe: {len(expl)} orders verklaard: rekenen met de huidige btw-instellingen al anders uit dan "
                      f"opgeslagen, bv. {[(k, changed[k]) for k in expl[:3]]}")
        total += len(keys)
        print(f"{name}: {len(keys)} anders" + (f", bv. {keys[:8]}" if keys else ""))
        for k in keys[:3]:
            print(f"   {k} origineel: {a.get(k)}\n   {' ' * len(str(k))} opnieuw  : {b.get(k)}")
    # koerstabel: meerdere orders op dezelfde dag met een andere koers -> de laatst opgeslagen wint (zoals Perl)
    multi = {(r[0].strip(), r[1]) for r in o.execute(
        """SELECT curr, transdate FROM (SELECT curr, transdate, exchangerate FROM oe UNION ALL
                                        SELECT curr, transdate, exchangerate FROM ar UNION ALL
                                        SELECT curr, transdate, exchangerate FROM ap) x
           WHERE (curr, transdate) IN (SELECT curr, transdate FROM oe)
           GROUP BY 1, 2 HAVING count(DISTINCT exchangerate) > 1""").fetchall()}
    ex = [k for k in A["exchangerate"] if A["exchangerate"].get(k) != B["exchangerate"].get(k) and (k[0].strip(), k[1]) in multi]
    if ex:
        print(f"   koerstabel: {len(ex)} verklaard: orders/facturen op dezelfde dag met verschillende koersen, opslaan schrijft "
              f"de koers van de order naar de koerstabel (OE.pm update_exchangerate), de laatste wint: {ex[:3]}")
        total -= len(ex)
    print(f"totaal {total} onverklaarde verschillen")
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
