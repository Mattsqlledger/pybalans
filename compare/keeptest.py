"""Toets opnieuw boeken tegen het ORIGINEEL: kopie van <db> maken, alle verkoop-/inkoopboekingen
zonder artikelen met Python openen en opnieuw opslaan, en per boeking saldo per rekening,
factuurbedrag en betaald bedrag vergelijken met <db>.

gebruik (vanuit compare/):  python keeptest.py adm_a [--web]

--web: via de code van het webscherm (sqlledger_web.routes_arap): openen zoals het scherm
(form_from_trans + recalc), ongewijzigd opslaan (do_post); vergelijkt dan ook de kopvelden.
"""
import sys, psycopg, time
sys.path.insert(0, ".")
sys.path.insert(0, "..")
from sqlledger import connect, UserConfig, LedgerError
import run_compare as rc
src = sys.argv[1]
web = "--web" in sys.argv


def web_repost(conn, cfg, trans_id, vc):
    from sqlledger_web import routes_arap as ra
    from sqlledger_web.reportdefs import Lib
    from sqlledger._db import cursor, default_currency, get_precision
    lib = Lib(conn, cfg)
    with cursor(conn) as cur:
        prec, defcur = get_precision(cur), default_currency(cur)
    f = ra.form_from_trans(lib, lib.arap.get_transaction(trans_id, vc, lock=True), cfg.numberformat, prec)
    errors = ra.recalc(lib, f, cfg.numberformat, prec, defcur)
    if errors:
        raise ValueError(" ".join(errors))
    return ra.do_post(lib, f, cfg.numberformat)

admin=psycopg.connect("host=127.0.0.1 port=55432 user=postgres dbname=postgres",autocommit=True)
admin.execute('DROP DATABASE IF EXISTS "keeptest"'); admin.execute(f'CREATE DATABASE "keeptest" TEMPLATE "{src}"')
c=connect("host=127.0.0.1 port=55432 user=postgres dbname=keeptest")
c.execute("UPDATE defaults SET fldvalue = '' WHERE fldname = 'closedto'")
cfg=UserConfig(login="admin")
t0=time.time(); errs={}; n=0
for tb,vc in (("ar","customer"),("ap","vendor")):
    for (i,) in c.execute(f"SELECT id FROM {tb} WHERE NOT invoice ORDER BY id").fetchall():
        n+=1
        try: (web_repost if web else rc.arap_repost_py)(c,cfg,i,vc)
        except (LedgerError,ValueError) as e: errs[i]=str(e)
print(src, "herboekt", n, "fouten", len(errs), list(errs.items())[:3], round(time.time()-t0),"s")
o=psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={src}",autocommit=True)
for tb in ("ar","ap"):
    q=f"""select a.id, ac.chart_id, round(sum(ac.amount)::numeric,2) from {tb} a join acc_trans ac on ac.trans_id=a.id
          where not a.invoice group by 1,2 having round(sum(ac.amount)::numeric,2)<>0"""
    A=set(o.execute(q).fetchall()); B=set(c.execute(q).fetchall())
    ids=sorted({x[0] for x in A^B})
    qh=f"select id, round(amount::numeric,2), round(paid::numeric,2) from {tb} where not invoice"
    HA=dict((r[0],r[1:]) for r in o.execute(qh)); HB=dict((r[0],r[1:]) for r in c.execute(qh))
    hd=[i for i in HA if HA[i]!=HB.get(i)]
    print(tb, "ander saldo per rekening:", len(ids), ids[:10], "| ander bedrag/betaald:", len(hd), hd[:10])
    if web:
        cols = ("invnumber, transdate, duedate, terms, description, notes, ordnumber, ponumber, curr, "
                "round(exchangerate::numeric; 8), taxincluded, onhold, department_id, employee_id, dcn, "
                "bank_id, cashdiscount, discountterms, language_code, datepaid, approved, intnotes")
        qk = f"select id, {cols.replace(';', ',')} from {tb} where not invoice"
        KA = dict((r[0], r[1:]) for r in o.execute(qk)); KB = dict((r[0], r[1:]) for r in c.execute(qk))
        names = [x.strip() for x in cols.split(", ")]
        diff = {}
        for i in KA:
            for n, a, b in zip(names, KA[i], KB.get(i, ())):
                if (a if a != "" else None) != (b if b != "" else None):     # leeg = NULL
                    diff.setdefault(n, []).append((i, a, b))
        for n, rows in diff.items():
            print(f"   kop {tb}.{n}: {len(rows)} anders, bv. {rows[:3]}")
