"""Documenten (fase 4, D21): sjabloonvariabelen van het ORIGINEEL (is.pl/ir.pl "Printen", vlak voor
Form::parse_template) vergelijken met ``sqlledger.printdata``.

gebruik (vanuit de repo, na ``source ~/sqlledger-test/env.sh``):
    python compare/compare_print.py <db> [--ids 1,2,3] [--sample n] [--vc customer|vendor]
           [--formname invoice] [--numberformat 1.000,00] [--countrycode nl] [--keep] [-v]

Er wordt een kopie van <db> gemaakt (Perl schrijft bij printen ar.dcn/bank_id weg). Vergeleken worden
alle variabelen die Python levert en die Perl ook kent; verschillen worden per variabele geteld.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlledger import UserConfig, connect  # noqa: E402
from sqlledger.printdata import PrintData  # noqa: E402

DSN = "host=127.0.0.1 port=55432 user=postgres"
USERNAME, USEREMAIL = "Test", "test@example.com"
#: interne of schermvelden die geen sjabloonvariabele zijn
SKIP = {"vc", "formname", "taxincluded", "precision", "cashdiscount", "transdate", "payment_accno"}


def norm(v):
    """Perl en Python gelijk trekken: NULL-tekst, undef, getallen als tekst."""
    if isinstance(v, list):
        return [norm(x) for x in v]
    if v is None or v == "NULL":
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


#: bewuste afwijkingen (README fase 4) die hier niet als verschil tellen
PAYKEYS = {"payment", "paymentaccount", "paymentdate", "paymentsource", "paymentmemo", "paymentmethod",
           "paid", "total", "out_decimal", "text_out_decimal", "text_out_amount", "integer_out_amount"}


TAXKEYS = {"linetax", "taxrates", "totaltax", "invtotal", "taxdescription", "taxnumber", "taxrate", "taxbase",
           "taxbaseinclusive", "tax", "total", "cd_tax", "cd_taxbase", "cd_invtotal", "cd_amount", "cd_subtotal",
           "cd_available", "integer_amount", "text_amount", "decimal", "text_decimal", "out_decimal",
           "text_out_decimal", "text_out_amount", "integer_out_amount", "integer_cd_invtotal", "text_cd_invtotal",
           "cd_decimal", "text_cd_decimal", "paid", "subtotal"}


def num(t: str) -> float:
    """Opgemaakt getal (1.000,00 of 1,000.00) of kaal getal naar float."""
    t = (t or "0").strip()
    if NF[0] == "1.000,00" and "e" not in t:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "") if "e" not in t else t.replace(",", ".")
    return float(t)


NF = ["1.000,00"]


def nl_words(t: str) -> str:
    return t.replace("ë", "e").replace("twaalf", "twalf").replace("achttien", "achtien")


def explained(key, perl, py, ctx) -> bool:
    bom = ctx.get("bom")
    if bom and isinstance(perl, list) and isinstance(py, list):
        rest = [x for n, x in enumerate(py) if n not in bom]
        if len(perl) == len(rest) and perl == rest:
            return True         # Perl: lijst zonder de onderdeelregels (verschoven kolommen)
        if len(perl) == len(py) and [x for n, x in enumerate(perl) if n not in bom] == rest:
            return True         # Perl: onderdeelregel met lege waarde waar Python er geen heeft
    if isinstance(perl, str) and isinstance(py, str) and perl != py:
        try:
            if perl.encode("latin-1").decode("utf-8") == py:
                return True     # Perl: UTF-8 dubbel gecodeerd (€ -> â\x82¬)
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    if key in ("part", "service"):
        return perl == py                       # NULL al genormaliseerd
    if key.endswith("_tax") and not key.startswith("cd_"):
        return True             # Perl: btw na contantkorting (meestal 0), Python: btw van de rekening
    if (key.startswith("cd_") and key.endswith("_tax") or key == "cd_available") and isinstance(perl, str):
        # Perl: kaal (drijvende-komma)getal; Python: opgemaakt bedrag
        try:
            from decimal import ROUND_HALF_UP, Decimal
            q = Decimal(str(perl or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)
            return float(q) == round(num(py), 2)
        except ValueError:
            return False
    if key in ("totalnetweight", "totalgrossweight"):
        return True             # Perl telt kale gewichten ("4.14") in de notatie 1.000,00 als 414
    if key in ("netweight", "grossweight", "volume"):
        pl = perl if isinstance(perl, list) else [perl]
        yl = py if isinstance(py, list) else [py]

        def same(a, b):
            if (a or "0") in ("0", "") and (b or "0") in ("0", ""):
                return True
            try:
                return abs(num(b) - float(a.replace(",", "."))) < 1e-6 or abs(num(a) - num(b)) < 1e-6
            except ValueError:
                return False
        return len(pl) == len(yl) and all(same(a, b) for a, b in zip(pl, yl))
    if key == "cc" and perl.startswith('"'):
        return True             # Perl: cc met aanhalingstekens afgebroken in het verborgen veld
    if key in ("employee", "workphone", "workfax", "workmobile", "shiptocontact") and perl == "":
        return True             # Perl: medewerker niet (meer) in de keuzelijst -> leeg
    if key == "dcn" and perl == "":
        return True             # Perl: geen rekening in de lege betaalregel -> geen kenmerk
    if perl == "" and py == []:
        return True
    if ctx.get("tax_extra") and key in TAXKEYS:
        return True             # btw-rekening op de factuur die de klant niet meer heeft (fase 3, F2)
    if key in ("discount", "projectnumber") and not isinstance(perl, list):
        return True             # Perl: lijst verloren (symbolische array / overschreven)
    if key == "intnotes" and ctx.get("arap") and perl.strip() == ctx.get("vc_notes", "").strip():
        return True             # Perl: interne notities overschreven door de notities van de relatie (AA::get_name)
    if ctx.get("arap") and key in ("accno", "account", "description", "projectnumber", "amount") and \
            isinstance(perl, list) and sorted(perl) == sorted(py) and ctx.get("unnumbered"):
        return True             # regels zonder regelnummer op dezelfde datum: volgorde ook in Perl willekeurig
    if ctx.get("arap") and ctx.get("discount_paid") and key.startswith("cd_"):
        return True             # Perl leest de betaalde korting (kaal getal) in de getalnotatie: 52.42 -> 5242
    if ctx.get("arap") and (key == "taxrate" or key.endswith("_taxrate")):
        return str(perl).replace(".", ",") == str(py).replace(".", ",")   # Perl: getalnotatie vergeten
    if ctx.get("arap") and key in PAYKEYS - {"paid", "total"} and isinstance(perl, list) and \
            sorted(perl) == sorted(py) and len(set(ctx.get("paydates", []))) <= 1:
        return True             # Perl: betalingen op dezelfde datum in willekeurige volgorde
    if key == "listprice" and ctx.get("order") and isinstance(perl, list):
        return all(x == "" for x in perl)       # Perl laadt de lijstprijs niet bij een opgeslagen order
    if key == "discountrate" and isinstance(perl, list) and len(perl) == len(py) and \
            all(a == b or a.replace(",.", ",") == b for a, b in zip(perl, py)):
        return True             # Perl: korting met 3 decimalen opgemaakt als "25,.185"
    if key == "lineitemdetail":
        return [("1" if x == "on" else x) for x in perl] == py
    if key.startswith(("fdy", "ldy")):
        return True             # Perl: maand van de factuurdatum i.p.v. 1 januari / 31 december
    if key in PAYKEYS and ctx.get("unlinked_payment"):
        return True             # betaling op rekening zonder AR_paid-koppeling (README 25b/c)
    if key.startswith("text_"):
        # Perl NL: "nul" midden in een getal (60 -> nulenzestig, 1050 -> eenduizendnulenvijftig, honderdnul)
        p = re.sub(r"nul(en)?(?=[a-z*])|(?<=honderd)nul", "", perl) if perl.strip("*") != "nul" else perl
        if p == nl_words(py):
            return True
    if key in ("decimal", "out_decimal", "cd_decimal", "text_decimal", "text_out_decimal", "text_cd_decimal",
               "integer_amount", "integer_out_amount", "integer_cd_invtotal", "text_amount", "text_out_amount",
               "text_cd_invtotal"):
        # Perl kapt de drijvende komma af (12,36 -> 35 cent; -0,00000001 -> "-0"/"****")
        return ctx.get("float_trunc", False) or perl in ("-0", "****")
    if isinstance(perl, list) and isinstance(py, list) and len(perl) == len(py):
        return all(a == b or ("e-" in a) for a, b in zip(perl, py))
    if isinstance(perl, str) and "e-" in perl:
        return True             # gewicht in e-notatie
    return False


#: interne formuliervelden van Perl (geen sjabloonvariabelen) voor --missing
PERL_INTERNAL = re.compile(r"^(select|old|bank|a_|AR|AP|IN$|OUT$|path|script|login|format|media|copies|pre$|title|"
                           r"helpref|fileid|version|dbversion|templates|rowcount|paidaccounts|reference_rows|"
                           r"referenceurl|closedto|defaultcurrency|audittrail|queued|printed|emailed|subject|"
                           r"message|readonly|type$|id$|.*_id$|.*_rate$|sortby|groupsubtotaldescription|"
                           r"\d+_|mm$|dd$|yy|f[dl][dmy]|recurring|creditlimit|creditremaining|tradediscount|"
                           r"pricegroup|business|department|warehouse|closed|backorder|currencies|taxaccounts|"
                           r"locked|batch|remittancevoucher|roundchange|discount_accno|temp|checktax|cashovershort|"
                           r"sort$|.*_printed$|.*_emailed$|stagger$|reqdate$)")


def compare_vars(pv, yv, ctx, i, diffs, examples, missing=None):
    # onderdeelregels van samengestelde artikelen: Perl (assembly_details) slaat ze in een deel van de
    # lijsten over, waardoor die lijsten verschuiven; zonder die regels moet de rest gelijk zijn
    bom = [n for n, x in enumerate(yv.get("bom") or []) if x == "1"]
    ctx = dict(ctx, bom=bom)
    for k, val in yv.items():
        if k in SKIP or k not in pv:
            continue
        p, y = norm(pv[k]), norm(val)
        if p != y and not explained(k, p, y, ctx):
            diffs[k] += 1
            examples.setdefault(k, []).append((i, p, y))
    if missing is not None:
        for k, val in pv.items():
            if k not in yv and k not in SKIP and k != "dcn" and not PERL_INTERNAL.match(k) and not re.search(r"_\d+$", k) and val not in ("", None, []):
                missing[k] += 1


def run_perl(db, ops, cfg):
    scen = {"config": cfg, "ops": ops}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(scen, fh)
        path = fh.name
    env = dict(os.environ, PGGSSENCMODE="disable", OBJC_DISABLE_INITIALIZE_FORK_SAFETY="YES")
    out = subprocess.run(["perl", str(HERE / "perl_driver.pl"), os.environ["SRC"], db, path],
                         capture_output=True, text=True, env=env)
    os.unlink(path)
    if out.returncode:
        raise SystemExit(out.stderr[-2000:])
    return json.loads(out.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--ids")
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--vc", default="customer")
    ap.add_argument("--formname")
    ap.add_argument("--numberformat", default="1.000,00")
    ap.add_argument("--countrycode", default="nl")
    ap.add_argument("--login", default="admin")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--sortby", default="runningnumber", choices=["runningnumber", "partnumber", "description", "bin"])
    ap.add_argument("--group", default="", help="project, group of project,group")
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--oe", action="store_true", help="orders/offertes (oe.pl) i.p.v. facturen")
    ap.add_argument("--arap", action="store_true", help="verkoop/inkoop zonder artikelen (ar.pl/ap.pl)")
    ap.add_argument("--missing", action="store_true", help="variabelen die Perl kent en Python niet")
    a = ap.parse_args()
    NF[0] = a.numberformat
    copy = f"prt_{a.db}_{os.getpid()}_orig"   # _orig: de opruimstap van de nachtrun laat hem staan
    with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{copy}"')
        c.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{a.db}"')
    tb = "ar" if a.vc == "customer" else "ap"
    try:
        with psycopg.connect(DSN + f" dbname={copy}") as c:
            if a.oe:
                ids = [int(x) for x in a.ids.split(",")] if a.ids else [r[0] for r in c.execute(
                    "SELECT id FROM oe WHERE " + ("customer_id > 0" if a.vc == "customer" else "vendor_id > 0")
                    + " ORDER BY random() LIMIT %s", (a.sample,)).fetchall()]
                oetypes = {r[0]: r[1] for r in c.execute(
                    "SELECT id, quotation FROM oe WHERE id = ANY(%s)", (ids,))}
            elif a.arap:
                ids = [int(x) for x in a.ids.split(",")] if a.ids else [r[0] for r in c.execute(
                    f"SELECT id FROM {tb} WHERE NOT invoice ORDER BY random() LIMIT %s", (a.sample,)).fetchall()]
            elif a.ids:
                ids = [int(x) for x in a.ids.split(",")]
            else:
                ids = [r[0] for r in c.execute(
                    f"SELECT id FROM {tb} WHERE invoice AND till IS NULL ORDER BY random() LIMIT %s"
                    if tb == "ar" else f"SELECT id FROM {tb} WHERE invoice ORDER BY random() LIMIT %s",
                    (a.sample,)).fetchall()]
            types = {} if a.oe else {r[0]: r[1] for r in c.execute(
                f"SELECT id, amount < 0 FROM {tb} WHERE id = ANY(%s)", (ids,))}
        cfg = {"numberformat": a.numberformat, "login": a.login, "countrycode": a.countrycode,
               "username": USERNAME, "useremail": USEREMAIL}
        ops = []
        grp = {"groupprojectnumber": "on" if "project" in a.group else "",
               "grouppartsgroup": "on" if "group" in a.group.replace("project", "") else ""}
        for i in ids if a.oe else []:
            quo = oetypes.get(i)
            otype = {("customer", False): "sales_order", ("customer", True): "sales_quotation",
                     ("vendor", False): "purchase_order", ("vendor", True): "request_quotation"}[(a.vc, bool(quo))]
            forms = [a.formname] if a.formname else (
                [otype, "packing_list", "pick_list"] if otype == "sales_order" else [otype])
            for fn in forms:
                ops.append({"op": "print_ui", "args": {"script": "oe.pl", "vc": a.vc, "id": i, "type": otype,
                                                        "formname": fn, "sortby": a.sortby, **grp}})
        for i in ids if a.arap else []:
            ttype = ("credit_note" if a.vc == "customer" else "debit_note") if types.get(i) else "transaction"
            ops.append({"op": "print_ui", "args": {"script": "ar.pl" if a.vc == "customer" else "ap.pl",
                                                    "vc": a.vc, "id": i, "type": ttype,
                                                    "formname": a.formname or ttype}})
        for i in [] if (a.oe or a.arap) else ids:
            credit = types.get(i)
            itype = ("credit_invoice" if a.vc == "customer" else "debit_invoice") if credit else "invoice"
            formname = a.formname or ({"customer": itype}.get(a.vc) if a.vc == "customer"
                                      else ("debit_invoice" if credit else "vendor_invoice"))
            ops.append({"op": "print_ui", "args": {"script": "is.pl" if a.vc == "customer" else "ir.pl",
                                                    "vc": a.vc, "id": i, "type": itype, "formname": formname,
                                                    "sortby": a.sortby,
                                                    "groupprojectnumber": "on" if "project" in a.group else "",
                                                    "grouppartsgroup": "on" if "group" in a.group.replace("project", "") else ""}})
        perl = run_perl(copy, ops, cfg)
        config = UserConfig(login=a.login, numberformat=a.numberformat, countrycode=a.countrycode)
        diffs = Counter()
        examples = {}
        failed = 0
        missing = Counter()
        with connect(dbname=copy, host="127.0.0.1", port=55432, user="postgres") as conn:
            pd = PrintData(conn, config, username=USERNAME, useremail=USEREMAIL)
            for op, res in zip(ops, perl):
                i = op["args"]["id"]
                if res.get("error") and a.oe and not conn.execute(
                        "SELECT 1 FROM orderitems WHERE trans_id = %s LIMIT 1", (i,)).fetchone():
                    continue            # order zonder regels: Perl print niets, Python een leeg document
                if res.get("error"):
                    failed += 1
                    print(f"{i}: Perl-fout {res['error'][:200]}")
                    continue
                pv = res["vars"]
                if a.oe:
                    yv = pd.order_vars(i, op["args"]["formname"], sortby=a.sortby,
                                       groupprojectnumber="project" in a.group,
                                       grouppartsgroup="group" in a.group.replace("project", ""))
                    ctx = {"order": True}
                    compare_vars(pv, yv, ctx, i, diffs, examples, missing if a.missing else None)
                    continue
                if a.arap:
                    yv = pd.transaction_vars(i, a.vc, op["args"]["formname"])
                    from sqlledger.arap import ArapLedger
                    t = ArapLedger(conn, config).get_transaction(i, a.vc)
                    base = ArapLedger(conn, config).vc_defaults(a.vc, t.vc_id, t.transdate)["taxrates"]
                    vcn = conn.execute(f"SELECT notes FROM {a.vc} WHERE id = %s", (t.vc_id,)).fetchone()
                    unnumbered = bool(conn.execute(
                        "SELECT 1 FROM acc_trans WHERE trans_id = %s AND id IS NULL LIMIT 1", (i,)).fetchone())
                    unlinked = bool(conn.execute(
                        """SELECT 1 FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                           WHERE ac.trans_id = %s AND ac.id IS NOT NULL AND ac.amount <> 0
                           AND COALESCE(c.link, '') NOT LIKE %s""",
                        (i, f"%{'AR' if a.vc == 'customer' else 'AP'}_paid%")).fetchone())
                    ctx = {"arap": True, "unlinked_payment": unlinked,
                           "discount_paid": bool(t.discount and t.discount.amount and NF[0] == "1.000,00"), "unnumbered": unnumbered, "vc_notes": (vcn[0] if vcn else "") or "",
                           "paydates": [str(p.datepaid) for p in t.payments if p.amount], "tax_extra": bool({k for k, x in t.taxes.items() if x} - set(base)),
                           "float_trunc": all(norm(pv.get(k)) == norm(yv.get(k)) for k in ("invtotal", "total")
                                              if k in pv)}
                    compare_vars(pv, yv, ctx, i, diffs, examples, missing if a.missing else None)
                    continue
                yv = pd.invoice_vars(i, a.vc, op["args"]["formname"], sortby=a.sortby,
                                     groupprojectnumber="project" in a.group,
                                     grouppartsgroup="group" in a.group.replace("project", ""))
                ctx = {"unlinked_payment": bool(conn.execute(
                    """SELECT 1 FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                       WHERE ac.trans_id = %s AND ac.id IS NOT NULL AND ac.amount <> 0
                       AND COALESCE(c.link, '') NOT LIKE %s""",
                    (i, f"%{'AR' if a.vc == 'customer' else 'AP'}_paid%")).fetchone())}
                from sqlledger.invoice import InvoiceLedger
                il = InvoiceLedger(conn, config)
                inv = il.get_invoice(i, a.vc)
                base = il.arap.vc_defaults(a.vc, inv.vc_id, inv.transdate)["taxrates"]
                ctx["tax_extra"] = bool(set(il.tax_rates(inv)) - set(base))
                # één cent verschil in de decimalen/woorden = Perl kapte af (alleen samen met gelijke totalen)
                ctx["float_trunc"] = all(norm(pv.get(k)) == norm(yv.get(k)) for k in ("invtotal", "total")
                                         if k in pv) or ctx["unlinked_payment"]
                compare_vars(pv, yv, ctx, i, diffs, examples, missing if a.missing else None)
        soort = "orders/offertes" if a.oe else ("boekingen zonder artikelen" if a.arap else "facturen")
        print(f"{a.db}: {len(ids)} {soort} ({a.vc}), {len(ops)} documenten, Perl-fouten {failed}, "
              f"variabelen met verschil: {len(diffs)}")
        if a.missing:
            print("  alleen in Perl:", " ".join(sorted(missing)))
        for k, n in diffs.most_common():
            print(f"  {k}: {n}")
            for i, p, y in examples[k][:3 if a.v else 1]:
                print(f"      {i}: perl={p!r:.200} py={y!r:.200}")
    finally:
        if not a.keep:
            with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
                c.execute(f'DROP DATABASE IF EXISTS "{copy}"')


if __name__ == "__main__":
    main()
