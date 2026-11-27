"""Inkoopfactuur uploaden en bijlagen (fase 4 stap 4e, IU1–IU8) tegen het ORIGINEEL vergelijken.

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/compare_attach.py [--keep] [-v]

Op een kopie van ``sl_template``:

1. Boeking: per geval rekent het uploadscherm (``routes_apupload``: voorstel, splitsen, het gewone
   inkoopformulier, ``do_post``) een boeking; dezelfde factuur gaat in ap.pl (aa.pl, als een
   browser: leverancier, regel met het netto op de kostenrekening, btw-bedrag) → grootboekregels en
   kop (bedrag, netto, vervaldatum, valuta) moeten gelijk zijn.
2. Bijlagen heen en terug: een bijlage die Python opslaat opent SQL-Ledger (Form::get_reference,
   zoals rd.pl) byte voor byte, met bestandsnaam en contenttype; een bijlage die SQL-Ledger opslaat
   (Form::save_reference met tmpfile, zoals aa.pl/rd.pl) opent Python; opnieuw boeken in ap.pl laat
   een Python-bijlage staan en opnieuw boeken in Python een SL-bijlage; vertrouwelijk werkt in beide.
3. Stap 6a (RB1/RB2): bijlagen die Python bij facturen met artikelen, orders/offertes, relaties en artikelen
   zet, ziet SQL-Ledger met dezelfde formname en opent ze; opnieuw opslaan in is.pl/ir.pl, oe.pl, ct.pl en
   ic.pl laat ze staan; een bijlage die SQL-Ledger bij een order zet ziet Python en blijft bij opnieuw
   opslaan in Python; omzetten (offerte -> order) in Python deelt het archiefstuk en SQL-Ledger opent het
   bij de nieuwe order.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from compare_print import DSN, run_perl  # noqa: E402
from sqlledger import ArapLedger, UserConfig, connect  # noqa: E402
from sqlledger.amounts import format_amount  # noqa: E402
from sqlledger.attachments import Attachments  # noqa: E402
from sqlledger_web import routes_apupload as up  # noqa: E402
from sqlledger_web import routes_arap as arap  # noqa: E402
from sqlledger_web.reportdefs import Lib  # noqa: E402

NF = "1.000,00"
LOGIN = "admin"
CFG = {"numberformat": NF, "login": LOGIN, "countrycode": "nl"}

SETUP = """
UPDATE tax SET validto = '2025-12-31' WHERE taxnumber IS NULL;
UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL;
INSERT INTO vendortax VALUES (901, (SELECT id FROM chart WHERE accno = '1801'));
INSERT INTO tax (chart_id, rate, taxnumber) SELECT id, 0.09, 'NL-LAAG-V' FROM chart WHERE accno = '1801';
"""

#: leverancier, datum, totaal, btw-rekening ("" = voorstel, "geen"), eigen btw-bedrag, kostenrekening
CASES = [
    (905, "2026-07-01", "121.00", "", None, "8100"),
    (905, "2026-07-02", "100.00", "", None, "8100"),          # netto 82,64 / btw 17,36
    (905, "2026-07-03", "100.00", "", "17.35", "8010"),       # btw van de factuur (afronding)
    (901, "2026-07-05", "109.00", "1801", None, "8020"),      # laag tarief (9%) bij twee gekoppelde
    (901, "2026-07-06", "10.00", "1801", None, "8020"),       # 9,17 / 0,83
    (905, "2026-07-07", "-60.50", "", None, "8100"),          # negatief totaal
    (905, "2026-07-08", "1234567.89", "", None, "8100"),
    (905, "2026-07-09", "50.00", "geen", None, "8100"),       # zonder btw
    (905, "2026-07-10", "75.00", "", None, "8100"),           # vorige boeking zonder btw: voorstel geen btw
]

PDF = b"%PDF-1.4\n" + bytes(range(256)) * 9 + b"\n%%EOF\n"          # 2.322 bytes, alle bytewaarden
JPG = b"\xff\xd8\xff\xe0" + os.urandom(1300)
PNG = b"\x89PNG\r\n\x1a\n" + os.urandom(511)                      # precies één blok van 512


def ledger(conn, tid):
    with conn.cursor() as cur:
        cur.execute("""SELECT c.accno, ac.amount::numeric, ac.transdate FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id
                       WHERE ac.trans_id = %s ORDER BY 1, 2""", (tid,))
        lines = [(r[0], Decimal(r[1]), str(r[2])) for r in cur.fetchall()]
        cur.execute("""SELECT amount::numeric, netamount::numeric, duedate, curr, vendor_id, transdate,
                              COALESCE(department_id, 0), taxincluded FROM ap WHERE id = %s""", (tid,))
        head = tuple(str(x) for x in cur.fetchone())
    return lines, head


def python_post(conn, config, case, n):
    vendor, td, total, tax_accno, tax, expense = case
    lib = Lib(conn, config)
    ctx = SimpleNamespace(user={"numberformat": NF})
    label = arap._vc_label(lib.arap.vc_info("vendor", vendor))
    u = up.UploadForm(vc_id=vendor, vc_text=label, vc_shown="", transdate=td, oldtransdate=td,
                      invnumber=f"PY-{n}", total=format_amount(Decimal(total), NF, 2))
    up.refresh(ctx, lib, u)                      # nieuwe leverancier: voorstellen
    errors, s = up.refresh(ctx, lib, u)
    if tax_accno:
        u.tax_accno = tax_accno
    u.expense = expense
    if tax is not None:
        u.tax = format_amount(Decimal(tax), NF, 2)
    errors, s = up.refresh(ctx, lib, u)
    assert not errors, errors
    f = up.to_arap_form(lib, u, NF, s)
    assert not arap.recalc(lib, f, NF, lib.precision(), arap._defcur(lib))
    with conn.transaction():
        tid = arap.do_post(lib, f, NF)
    return tid, u, s


def stap6a(conn, config, att, same):
    from scenarios_is import IS_SETUP
    from sqlledger.order import OrderLedger
    for sql in IS_SETUP:
        conn.execute(sql)
    # sl_template heeft geen rekening met IC_income: ic.pl kan dan geen dienst openen ("Income account does
    # not exist"); testdata, geen bijlagenzaak
    conn.execute("UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'")
    conn.commit()
    lines = [{"partnumber": "A-1", "qty": "2", "sellprice": "2"}]
    made = run_perl(conn.info.dbname, [
        {"op": "is_ui", "args": {"vc": "vendor", "transdate": "2026-02-01", "vc_id": 905, "lines": lines}},
        {"op": "is_ui", "args": {"vc": "customer", "transdate": "2026-03-01", "vc_id": 900,
                                 "lines": [{"partnumber": "A-1", "qty": "1", "sellprice": "5"}]}},
        {"op": "oe_ui", "args": {"type": "sales_order", "transdate": "2026-03-01", "vc_id": 900, "lines": lines}},
        {"op": "oe_ui", "args": {"type": "sales_quotation", "transdate": "2026-03-01", "vc_id": 902,
                                 "lines": lines}},
    ], CFG)
    for r in made:
        if r.get("error"):
            same("6a: records in SL", False, r["error"])
            return
    ap_id, ar_id, so_id, sq_id = (r["id"] for r in made)
    targets = [("ap_invoice", ap_id, {"op": "is_ui", "args": {"vc": "vendor", "id": ap_id}}),
               ("ar_invoice", ar_id, {"op": "is_ui", "args": {"vc": "customer", "id": ar_id}}),
               ("sales_order", so_id, {"op": "oe_ui", "args": {"type": "sales_order", "id": so_id}}),
               ("sales_quotation", sq_id, {"op": "oe_ui", "args": {"type": "sales_quotation", "id": sq_id}}),
               ("customer", 902, {"op": "ct_ui", "args": {"vc": "customer", "id": 902}}),
               ("vendor", 905, {"op": "ct_ui", "args": {"vc": "vendor", "id": 905}}),
               ("part", 2001, {"op": "ic_ui", "args": {"id": 2001}}),
               ("service", 1000, {"op": "ic_ui", "args": {"id": 1000}})]
    added = {}
    for formname, tid, _ in targets:
        added[formname] = att.add(tid, formname, f"{formname}.pdf", PDF + formname.encode())
    conn.commit()
    gets = [{"op": "ref_get", "args": {"archive_id": added[f].archive_id, "trans_id": tid}} for f, tid, _ in targets]
    res = run_perl(conn.info.dbname, gets + [op for _, _, op in targets] + gets, CFG)
    n = len(targets)
    for i, (formname, tid, _) in enumerate(targets):
        a = added[formname]
        r = res[i]
        same(f"6a SL opent {formname}", bytes.fromhex(r["hex"]) == PDF + formname.encode()
             and r["filename"] == f"{formname}.pdf"
             and [(x["archive_id"], x["formname"]) for x in r["references"]] == [(a.archive_id, formname)],
             {k: v for k, v in r.items() if k != "hex"})
        saved = res[n + i]
        after = res[2 * n + i]
        same(f"6a opnieuw opslaan in SL laat {formname} staan", not saved.get("error")
             and [x.archive_id for x in att.list(tid)] == [a.archive_id]
             and [x["archive_id"] for x in after["references"]] == [a.archive_id],
             saved.get("error") or att.list(tid))
    # SL zet een bijlage bij de order; Python ziet hem en houdt hem bij opnieuw opslaan
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "o.pdf").write_bytes(PDF + b"order")
        r = run_perl(conn.info.dbname, [{"op": "ref_save", "args": {
            "trans_id": so_id, "dir": tmp, "tmpfile": "o.pdf", "filename": "uit-sl.pdf", "description": "SL",
            "formname": "sales_order"}}], CFG)[0]
    ol = OrderLedger(conn, config)
    with conn.transaction():
        ol.save_order(ol.get_order(so_id))
    conn.commit()
    lst = att.list(so_id)
    same("6a SL-bijlage bij order in Python, blijft bij opslaan",
         sorted(x.archive_id for x in lst) == sorted([added["sales_order"].archive_id, r["archive_id"]])
         and att.get(r["archive_id"])[1] == PDF + b"order", lst)
    # omzetten offerte -> order in Python: zelfde archiefstuk, SL opent het bij de nieuwe order
    with conn.transaction():
        new = ol.save_order(ol.convert(sq_id, "sales_order"))
    conn.commit()
    r = run_perl(conn.info.dbname, [{"op": "ref_get", "args": {"archive_id": added["sales_quotation"].archive_id,
                                                               "trans_id": new}}], CFG)[0]
    same("6a omzetten deelt het archiefstuk",
         [x["archive_id"] for x in r["references"]] == [added["sales_quotation"].archive_id]
         and bytes.fromhex(r["hex"]) == PDF + b"sales_quotation"
         and [x.archive_id for x in att.list(sq_id)] == [added["sales_quotation"].archive_id], r["references"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    db = f"cmp_attach_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{db}" TEMPLATE sl_template')
    problems, checks = [], 0
    try:
        conn = connect(f"{DSN} dbname={db}")
        conn.execute(SETUP)
        conn.commit()
        config = UserConfig(login=LOGIN, numberformat=NF, dateformat="yyyy-mm-dd", countrycode="nl")

        # ---------------------------------------------------------------- 1. boeking
        py_ids, pl_ops = [], []
        for n, case in enumerate(CASES, 1):
            tid, u, s = python_post(conn, config, case, n)
            conn.commit()
            py_ids.append(tid)
            lines, _ = ledger(conn, tid)
            net = next(-amt for acc, amt, _ in lines if acc == case[5])
            taxes = {acc: str(-amt) for acc, amt, _ in lines if acc in (s.rates or {})}
            for acc in s.rates:
                taxes.setdefault(acc, "0")
            pl_ops.append({"op": "aa_ui", "save_as": f"p{n}", "args": {
                "vc": "vendor", "vc_id": case[0], "transdate": case[1], "invnumber": f"PL-{n}",
                "duedate": u.duedate, "lines": [{"amount": str(net), "accno": case[5]}], "taxes": taxes}})
        res = run_perl(db, pl_ops, CFG)
        for n, (case, r, py) in enumerate(zip(CASES, res, py_ids), 1):
            checks += 1
            if r.get("error"):
                problems.append(f"geval {n}: Perl-fout {r['error']}")
                continue
            lp, hp = ledger(conn, py)
            ll, hl = ledger(conn, r["id"])
            if (lp, hp) != (ll, hl):
                problems.append(f"geval {n} {case}: Python {lp} {hp} ≠ Perl {ll} {hl}")
            elif a.v:
                print(f"geval {n}: gelijk {lp}")

        # ---------------------------------------------------------------- 2. bijlagen
        att = Attachments(conn, config)
        py_tid, pl_tid = py_ids[0], res[0]["id"]
        x = att.add(py_tid, "ap_transaction", "factuur juli.pdf", PDF)
        y = att.add(py_tid, "ap_transaction", "bon.jpg", JPG, description="Kassabon")
        z = att.add(py_tid, "ap_transaction", "geheim.png", PNG, confidential=True)
        conn.commit()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "sl1.pdf").write_bytes(PDF + b"SL")
            ops = [
                {"op": "ref_get", "args": {"archive_id": x.archive_id, "trans_id": py_tid}},
                {"op": "ref_get", "args": {"archive_id": y.archive_id}},
                {"op": "ref_get", "args": {"archive_id": z.archive_id, "trans_id": py_tid, "login": "iemand"}},
                {"op": "ref_save", "args": {"trans_id": pl_tid, "dir": tmp, "tmpfile": "sl1.pdf",
                                            "filename": "van-sl.pdf", "description": "Uit SQL-Ledger",
                                            "formname": "ap_transaction"}},
                # opnieuw boeken in ap.pl (bewerken + opslaan) van de boeking met Python-bijlagen
                {"op": "aa_ui", "args": {"vc": "vendor", "id": py_tid}},
                {"op": "ref_get", "args": {"archive_id": x.archive_id, "trans_id": py_tid}},
            ]
            r = run_perl(db, ops, CFG)
        conn.rollback()

        def same(label, ok, detail=""):
            nonlocal checks
            checks += 1
            if not ok:
                problems.append(f"{label}: {detail}")
            elif a.v:
                print(f"{label}: gelijk")
        same("Python-pdf in SL", bytes.fromhex(r[0]["hex"]) == PDF and r[0]["filename"] == "factuur juli.pdf"
             and r[0]["contenttype"] == "application/pdf", r[0])
        same("Python-jpg in SL", bytes.fromhex(r[1]["hex"]) == JPG and r[1]["contenttype"] == "image/jpeg"
             and r[1]["description"] == "Kassabon", {k: v for k, v in r[1].items() if k != "hex"})
        refs = {x["archive_id"]: x for x in r[0]["references"]}
        same("SL ziet de Python-bijlagen (all_references)", set(refs) == {x.archive_id, y.archive_id, z.archive_id}
             and refs[z.archive_id]["confidential"] == 1 and refs[x.archive_id]["formname"] == "ap_transaction", refs)
        same("vertrouwelijk verborgen voor een ander in SL",
             {x_["archive_id"] for x_ in r[2]["references"]} == {x.archive_id, y.archive_id}, r[2]["references"])
        sl_archive = r[3].get("archive_id")
        data = att.get(sl_archive)
        same("SL-pdf in Python", data[1] == PDF + b"SL" and data[0].filename == "van-sl.pdf"
             and data[0].description == "Uit SQL-Ledger", data[0])
        same("ap.pl opnieuw boeken: Python-bijlagen blijven", not r[4].get("error")
             and {a_.archive_id for a_ in att.list(py_tid)} == {x.archive_id, y.archive_id, z.archive_id}
             and bytes.fromhex(r[5]["hex"]) == PDF, r[4].get("error"))
        al = ArapLedger(conn, config)
        with conn.transaction():
            al.post_transaction(al.get_transaction(pl_tid, "vendor"))
        conn.commit()
        same("Python opnieuw boeken: SL-bijlage blijft", [a_.archive_id for a_ in att.list(pl_tid)] == [sl_archive]
             and att.get(sl_archive)[1] == PDF + b"SL")

        # ---------------------------------------------------------------- 3. stap 6a: andere schermen
        stap6a(conn, config, att, same)
        conn.close()
    finally:
        if not a.keep:
            with psycopg.connect(f"{DSN} dbname=postgres", autocommit=True) as admin:
                admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        else:
            print(f"database bewaard: {db}")
    print(f"{checks} vergelijkingen, {len(problems)} verschil(len)")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
