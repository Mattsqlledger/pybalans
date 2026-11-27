"""Nieuwe administratie per standaardschema: kan SQL-Ledger (Perl) hem openen en rekent hij hetzelfde (RS10)?

Per schema in ``sqlledger/charts``: een administratie aanmaken met ``sqlledger.newcompany`` (de toets-template),
dan dezelfde boekingen (inbreng, verkoop met btw, kosten; bij een schema met verlegde btw ook een inkoop met het
btw-paar −x %/+x %) en de rapporten (rekeningen, proefbalans, W&V, balans)
door de Perl-driver en door Python, en alles vergelijken zoals ``run_compare.py``.

gebruik: python compare/newcompany_check.py --sldir /pad/naar/sql-ledger [--only leeg-nl]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import run_compare as rc  # noqa: E402
from sqlledger import chartfiles, newcompany  # noqa: E402

CFG = {"login": "admin", "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd", "countrycode": ""}


def line(accno, debit="", credit=""):
    return {"accno": accno, "debit": debit, "credit": credit}


def scenario(chart: chartfiles.ChartFile) -> dict:
    acc = chart.accounts
    first = lambda pred: next(r.accno for r in acc if pred(r))  # noqa: E731
    bank = first(lambda r: "AR_paid" in r.link)
    equity = first(lambda r: r.category == "Q")
    sales = chart.defaults["omzet"]
    expense = first(lambda r: "IC_expense" in r.link)
    vat = first(lambda r: "AR_tax" in r.link)
    rate = next(r.rate for r in acc if r.accno == vat)
    tax = f"{(100 * rate).quantize(rc.Decimal('0.01'))}"
    gross = f"{100 + float(tax):.2f}"
    setup, extra = [], []
    reverse = [r for r in acc if r.rate is not None and r.rate < 0]
    if reverse:
        # verlegde btw: inkoop bij een leverancier met het paar −x % (2A/4A/4B) en +x % (5B); netto geen btw
        neg = next((r for r in reverse if r.box == "4B"), reverse[0])
        posacc = next(r for r in acc if r.accno > neg.accno and r.rate == -neg.rate and r.box == "5B")
        payable = first(lambda r: "AP" in r.link)
        setup = ["INSERT INTO vendor (id, name, vendornumber) VALUES (9001, 'EU-leverancier', 'L-EU')",
                 "INSERT INTO address (id, trans_id, country) VALUES (9002, 9001, 'DE')"] + [
            f"INSERT INTO vendortax (vendor_id, chart_id) SELECT 9001, id FROM chart WHERE accno = '{a}'"
            for a in (neg.accno, posacc.accno)]
        extra = [
            {"op": "arap_post", "save_as": "p1", "args": {
                "vc": "vendor", "vc_id": 9001, "transdate": "2026-02-05", "arap_accno": payable,
                "description": "Verwerving EU", "lines": [{"accno": expense, "amount": "200.00"}]}},
            {"op": "account_tx", "args": {"accno": neg.accno}},
            {"op": "account_tx", "args": {"accno": posacc.accno}},
            {"op": "account_tx", "args": {"accno": payable}},
        ]
    return {
        "name": f"nieuwe administratie {chart.id}",
        "config": CFG,
        "setup": setup,
        "ops": extra + [
            {"op": "post", "save_as": "t1", "trans": {"transdate": "2026-01-02", "description": "Inbreng",
                                                      "lines": [line(bank, debit="1000.00"),
                                                                line(equity, credit="1000.00")]}},
            {"op": "post", "save_as": "t2", "trans": {"transdate": "2026-02-03", "description": "Verkoop",
                                                      "lines": [line(bank, debit=gross), line(sales, credit="100.00"),
                                                                line(vat, credit=tax)]}},
            {"op": "post", "save_as": "t3", "trans": {"transdate": "2026-02-04", "description": "Kosten",
                                                      "lines": [line(expense, debit="50.00"),
                                                                line(bank, credit="50.00")]}},
            {"op": "get", "id": "$t2"},
            {"op": "accounts"},
            {"op": "account_tx", "args": {"accno": bank}},
            {"op": "trial_balance", "args": {"fromdate": "2026-01-01", "todate": "2026-12-31"}},
            {"op": "income_statement", "args": {"fromdate": "2026-01-01", "todate": "2026-12-31", "decimalplaces": 2}},
            {"op": "balance_sheet", "args": {"todate": "2026-12-31", "decimalplaces": 2}},
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sldir", required=True)
    ap.add_argument("--dsn", default="host=127.0.0.1 port=55432 user=postgres")
    ap.add_argument("--only")
    args = ap.parse_args()
    admin = psycopg.connect(args.dsn + " dbname=postgres", autocommit=True)
    failures = 0
    charts = [c for c in chartfiles.available() if not args.only or c.id == args.only]
    for n, chart in enumerate(charts, 1):
        tpl = f"nc_tpl_{chart.id.replace('-', '_')}"
        admin.execute(f'DROP DATABASE IF EXISTS "{tpl}" WITH (FORCE)')
        newcompany.create(admin, lambda d: psycopg.connect(f"{args.dsn} dbname={d}"),
                          newcompany.NewCompany(dbname=tpl, company=f"Toets {chart.id}", chart=chart,
                                                employee_login="admin", employee_name="Beheerder"))
        runner = rc.Runner(argparse.Namespace(dsn=args.dsn, template=tpl, sldir=args.sldir))
        sc = scenario(chart)
        perl_conn = runner.fresh_db(f"nc_perl_{n}", sc)
        py_conn = runner.fresh_db(f"nc_py_{n}", sc)
        try:
            variants = [runner.run_perl(sc, f"nc_perl_{n}", 0)]
        except RuntimeError as exc:
            print(f"[{n}] Perl-driver faalde: {sc['name']}\n{exc}")
            failures += 1
            continue
        py_results = rc.run_python(py_conn, sc)
        problems = rc.diff_tables(rc.dump(perl_conn), rc.dump(py_conn))
        perl_conn.close()
        py_conn.close()
        bad = rc.mismatching_ops(variants, py_results, CFG["numberformat"])
        problems += [(f"op {i}", rc.describe(i, *rc.reconcile(variants[0][i - 1], py_results[i - 1],
                                                                CFG["numberformat"]))) for i in bad]
        errors = [(i + 1, a.get("error"), b.get("error")) for i, (a, b) in enumerate(zip(variants[0], py_results))
                  if a.get("error") or b.get("error")]
        status = "OK" if not problems and not errors else "VERSCHIL"
        failures += status != "OK"
        rows = sum(len(r.get("screen") or r.get("rows") or []) for r in variants[0])
        print(f"[{n}] {status:8} {sc['name']} ({len(chart.accounts)} rekeningen, {rows} rapportregels Perl)")
        for _, text in problems:
            print("     " + text.replace("\n", "\n     "))
        for i, e1, e2 in errors:
            print(f"     op {i}: fout Perl={e1!r} | Python={e2!r}")
        for name in (f"nc_perl_{n}", f"nc_py_{n}", tpl):
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    print(f"\n{len(charts)} schema's, {failures} met verschillen")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
