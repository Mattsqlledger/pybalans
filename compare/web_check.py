"""Webschermen tegen de bibliotheek op een echte administratie (R8, stap 3).

De bibliotheek is tegen Perl vergeleken (run_compare.py); hier wordt getoond dat
de webapp precies die bedragen laat zien, in dezelfde volgorde. Alleen lezen:
de administratie wordt niet gewijzigd, de app-database is tijdelijk.

    python compare/web_check.py --db adm_a --years 2023 2024 2025
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import uuid
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tests" / "web"))

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import sqlledger  # noqa: E402
from sqlledger import (ArapLedger, ArapSearch, ChartOfAccounts, GeneralLedger, Reports,  # noqa: E402
                       TransactionSearch, UserConfig)
from sqlledger_web import appdb, routes_reports  # noqa: E402
from sqlledger_web.main import create_app  # noqa: E402
from sqlledger_web.settings import Settings  # noqa: E402
from run_compare import account_rows_python, arap_rows_python, gl_rows_python, report_rows_python  # noqa: E402
from webhelpers import login, make_user  # noqa: E402

ROW_RE = re.compile(r'<tr class="k-(\w+)">(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<td class="([^"]*)">(.*?)</td>', re.S)
NF = "1.000,00"


def web_amounts(page: str):
    out = []
    for _, body in ROW_RE.findall(page):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", t)).strip() for cls, t in CELL_RE.findall(body) if "num" in cls]
        if any(cells):
            out.append([c for c in cells if c])
    return out


def nonempty(rows):
    return [r for r in rows if r]


def statement_expected(rep):
    rows = report_rows_python("income_statement", rep, NF)
    shown = [d for d in rep.display if any([d.description] + [v for v in d.values if v is not None])]
    assert len(rows) == len(shown)
    return nonempty([[c for c in r[1:] if c] for r, d in zip(rows, shown) if d.kind != "heading"])


def cases(conn, cfg, years, accounts):
    rp, ca, al = Reports(conn, cfg), ChartOfAccounts(conn, cfg), ArapLedger(conn, cfg)
    gl = GeneralLedger(conn, cfg)
    for y in years:
        f, t = date(y, 1, 1), date(y, 12, 31)
        yr = {"run": "1", "period": "year", "year": str(y)}
        hs = {"l_heading": "1", "l_subtotal": "1"}
        yield (f"proefbalans {y}", "proefbalans", {**yr, **hs},
               lambda: nonempty([[c for c in r[2:] if c] for r in report_rows_python(
                   "trial_balance", rp.trial_balance(fromdate=f, todate=t), NF)]))
        yield (f"W&V {y} kwartalen + vorig jaar", "winst-en-verlies",
               {**yr, **hs, "includeperiod": "quarter", "previousyear": "1", "decimalplaces": "2"},
               lambda: statement_expected(rp.income_statement(fromdate=f, todate=t, includeperiod="quarter",
                                                              previousyear=True, decimalplaces=2)))
        yield (f"W&V {y} kasstelsel", "winst-en-verlies", {**yr, **hs, "method": "cash", "decimalplaces": "2"},
               lambda: statement_expected(rp.income_statement(fromdate=f, todate=t, method="cash", decimalplaces=2)))
        yield (f"balans {y} maanden", "balans",
               {"run": "1", "period": "", "todate": t.isoformat(), **hs, "includeperiod": "month",
                "decimalplaces": "2"},
               lambda: statement_expected(rp.balance_sheet(todate=t, includeperiod="month", decimalplaces=2)))
        for arap in ("ar", "ap"):
            key = "verkoop" if arap == "ar" else "inkoop"
            yield (f"ouderdom {arap} {y}", f"ouderdom-{key}",
                   {"run": "1", "period": "", "todate": t.isoformat(), "summary": "0"},
                   lambda arap=arap: nonempty([a for _, a in arap_rows_python(rp.aging(
                       arap, todate=t, buckets=["c0", "c30", "c60", "c90"], summary=False), NF)]))
            yield (f"btw {arap} {y}", f"btw-{key}", {**yr, "l_subtotal": "1"},
                   lambda arap=arap: nonempty([a for _, a in arap_rows_python(rp.tax_report(
                       arap, accounts=[a["accno"] for a in rp.tax_accounts(arap)["taxaccounts"]],
                       fromdate=f, todate=t, sort="transdate", l_subtotal=True), NF)]))
            yield (f"betalingen {arap} {y}", "ontvangsten" if arap == "ar" else "betalingen", yr,
                   lambda arap=arap: nonempty([a for _, a in arap_rows_python(rp.payments(
                       arap, accounts=[a["accno"] for a in rp.payment_accounts(arap)],
                       fromdate=f, todate=t, sort="transdate"), NF)]))
            vc = "customer" if arap == "ar" else "vendor"
            yield (f"overzicht {arap} {y}", f"overzicht-{key}", {**yr, "page": "1"},
                   lambda vc=vc: nonempty([a for _, a in arap_rows_python(al.list_transactions(
                       vc, ArapSearch(transdatefrom=f, transdateto=t, sort="transdate")), NF)]))
        for extra, search in (({}, {}), ({"splitledger": "1"}, {"splitledger": True}),
                              ({"splitledger": "1", "l_subtotal": "1"}, {"splitledger": True, "l_subtotal": True})):
            yield (f"journaal {y} {' '.join(extra) or 'alles'}", "journaal", {**yr, **extra},
                   lambda search=search: nonempty([x for _, x in gl_rows_python(gl.list_transactions(
                       TransactionSearch(datefrom=f, dateto=t, sort="transdate", **search)), NF)]))
        for a in accounts:
            yield (f"journaal {a} {y}", "journaal", {**yr, "accno": a},
                   lambda a=a: nonempty([x for _, x in gl_rows_python(gl.list_transactions(
                       TransactionSearch(accno=a, datefrom=f, dateto=t, sort="transdate")), NF)]))
            yield (f"mutaties {a} {y}", "rekeningmutaties",
                   {"run": "1", "period": "", "fromdate": f.isoformat(), "todate": t.isoformat(), "accno": a},
                   lambda a=a: nonempty([x for _, x in account_rows_python(
                       ca.account_transactions(a, fromdate=f, todate=t, sort="transdate"), NF)]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--accounts", nargs="*", default=[])
    ap.add_argument("--dsn", default="host=127.0.0.1 port=55432 user=postgres")
    args = ap.parse_args()

    appname = f"wa_check_{uuid.uuid4().hex[:8]}"
    s = Settings(app_dsn=f"{args.dsn} dbname={appname}", db_host="127.0.0.1", db_port=55432, db_user="postgres")
    appdb.create_database(s.app_dsn)
    routes_reports.PAGE_SIZE = 10 ** 9          # alles op één pagina
    bad = 0
    try:
        with psycopg.connect(s.app_dsn, autocommit=True, row_factory=psycopg.rows.dict_row) as db:
            appdb.migrate(db)
            company = db.execute("INSERT INTO company (name, dbname, modules) VALUES ('Echt', %s, %s) RETURNING id",
                                 (args.db, list(appdb.MODULES))).fetchone()
            make_user(db, "lees", memberships=[(company["id"], "readonly")])
            db.execute("UPDATE app_user SET numberformat = %s", (NF,))
        conn = sqlledger.connect(host="127.0.0.1", port=55432, user="postgres", dbname=args.db)
        cfg = UserConfig(login="lees", numberformat=NF, dateformat="dd-mm-yy", countrycode="nl")
        with TestClient(create_app(s), base_url="https://testserver") as client:
            login(client, "lees")
            for name, key, params, expected in cases(conn, cfg, args.years, args.accounts):
                page = client.get(f"/rapporten/{key}", params=params).text
                web, exp = web_amounts(page), expected()
                status = "OK " if web == exp else "VERSCHIL"
                if web != exp:
                    bad += 1
                print(f"{status} {name}: {len(exp)} regels")
                if web != exp:
                    for i, (a, b) in enumerate(zip(web, exp)):
                        if a != b:
                            print(f"     regel {i}: web {a} / bibliotheek {b}")
                            break
                    if len(web) != len(exp):
                        print(f"     aantal regels: web {len(web)} / bibliotheek {len(exp)}")
        conn.close()
    finally:
        with psycopg.connect(f"{args.dsn} dbname=postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE "{appname}" WITH (FORCE)')
    print(f"\n{bad} met verschillen")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
