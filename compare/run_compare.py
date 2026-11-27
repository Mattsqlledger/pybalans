"""Vergelijk de Python-port met de originele SQL-Ledger Perl-code.

Per scenario:
  1. twee verse databases uit de template (één voor Perl, één voor Python)
  2. dezelfde operaties uitvoeren via perl_driver.pl resp. de sqlledger-bibliotheek
  3. de eindtoestand van alle grootboektabellen vergelijken
  4. de resultaten van de leesacties vergelijken

gebruik:
  python compare/run_compare.py --sldir <pad/naar/sql-ledger> [--template sl_template]
        [--dsn "host=127.0.0.1 port=55432 user=postgres"] [--only <tekst>] [--json out.json]
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import re
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Tuple

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import importlib  # noqa: E402

from sqlledger.amounts import round_amount as _ra  # noqa: E402
from sqlledger import (ChartOfAccounts, GeneralLedger, GLTransaction,  # noqa: E402
                       JournalLine, LedgerError, Recurring, Reference,
                       TransactionSearch, UserConfig, format_amount,
                       parse_amount, round_amount)
from sqlledger.dates import parse_date  # noqa: E402
from sqlledger.reports import Reports  # noqa: E402
from sqlledger.arap import ArapLedger, ArapLine, ArapPayment, ArapTransaction  # noqa: E402
from sqlledger.payments import Payment, PaymentLine, Payments  # noqa: E402
from datetime import timedelta  # noqa: E402
from compare_am import am_ui_py, extra_tables  # noqa: E402  (blok "am": beheer)
from compare_ic import IC_TABLES, ic_normalize, ic_stock_py, ic_ui_py  # noqa: E402  (blok "ic": artikelen)
from compare_im import im_ui_py  # noqa: E402  (blok "im": import, fase 4 stap 7)
from compare_jc import jc_ui_py, pe_so_py  # noqa: E402  (blok "jc": tijdkaarten, fase Uren)
from compare_is import IS_TABLES, explain_cogs, is_normalize, is_ui_py  # noqa: E402  (blok "is": facturen met artikelen)
from compare_oe import OE_TABLES, oe_normalize, oe_remap, oe_ui_py  # noqa: E402  (blok "oe": orders en offertes)

ARAP_OPS = ("arap_post", "arap_repost", "arap_get", "arap_delete", "arap_repost_all")
CP_OPS = ("cp_post", "cp_pay_all", "cp_void", "cp_reissue", "cp_payments")


def arap_ui_new(conn, cfg, a):
    """Nieuwe boeking zoals het scherm hem opbouwt (aa.pl add/create_links + update)."""
    lg = ArapLedger(conn, cfg)
    vc = a["vc"]
    td = parse_date(a["transdate"])
    d = lg.vc_defaults(vc, a["vc_id"], td)
    terms = int(a.get("terms", d["terms"]) or 0)
    currency = a.get("currency") or d["currency"]
    rate = Decimal(a["exchangerate"]) if a.get("exchangerate") else Decimal(1)
    if currency != d["currency"]:
        r = conn.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s",
                         (currency, td)).fetchone()
        rate = Decimal(repr(r[0])) if r and r[0] else None

    def pay(p, cur_changed):
        prate = Decimal(p["exchangerate"]) if p.get("exchangerate") else None
        if cur_changed:
            r = conn.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s",
                             (currency, parse_date(p["datepaid"]))).fetchone()
            if r and r[0]:
                prate = Decimal(repr(r[0]))
        return ArapPayment(accno=p["accno"], amount=Decimal(p["amount"]), datepaid=parse_date(p["datepaid"]),
                           source=p.get("source") or None, memo=p.get("memo") or None, exchangerate=prate)
    changed = currency != d["currency"]
    t = ArapTransaction(
        vc=vc, vc_id=a["vc_id"], transdate=td, arap_accno=a.get("arap_accno") or d["arap_accno"],
        lines=[ArapLine(accno=l["accno"], amount=Decimal(l["amount"]), description=l.get("description") or None,
                        project_id=l.get("project_id")) for l in a["lines"]],
        type=a.get("type", "transaction"), invnumber=a.get("invnumber"), terms=terms,
        duedate=td + timedelta(days=terms), ordnumber=a.get("ordnumber"), ponumber=a.get("ponumber"),
        description=a.get("description"), notes=a.get("notes"), currency=currency, exchangerate=rate,
        taxincluded=bool(a.get("taxincluded", d["taxincluded"])), department_id=a.get("department_id"),
        employee_id=d["employee_id"], language_code=d["language_code"],
        payments=[pay(p, changed) for p in a.get("payments", [])],
        discount=pay(a["discount"], changed) if a.get("discount") else None,
        cashdiscount=Decimal(str(a.get("cashdiscount", (d["cashdiscount"] or 0) * 100))),
        discountterms=int(a.get("discountterms", d["discountterms"] or 0) or 0),
        pending=bool(a.get("pending")), bank_accno=a.get("bank_accno") or d["payment_accno"],
        paymentmethod_id=d["paymentmethod_id"], taxrates=d["taxrates"])
    if rate is None:
        raise LedgerError("Exchange rate missing!")
    t.oldinvtotal = None
    t.taxes = lg.calculate_taxes(t)
    return lg, t


def arap_repost_py(conn, cfg, trans_id, vc):
    """Boeking openen en ongewijzigd opnieuw opslaan, zoals in het scherm."""
    lg = ArapLedger(conn, cfg)
    t = lg.get_transaction(trans_id, vc)
    if t.terms:
        # aa.pl update: 'oldterms' wordt niet geladen, dus de vervaldatum wordt
        # opnieuw transdate + termijn
        t.duedate = t.transdate + timedelta(days=int(t.terms))
    t.taxes = lg.calculate_taxes(t, only=t.calctax)
    t.pending = False   # de knop 'Post' in het scherm boekt definitief (keurt een concept goed)
    return lg.post_transaction(t)



def aa_ui_py(conn, cfg, a):
    """Verkoop-/inkoopscherm van de webapp (sqlledger_web.routes_arap) nagespeeld, zoals aa_ui in
    perl_driver.pl het aa.pl-scherm naspeelt: openen (met lock), relatie kiezen, velden invullen,
    doorrekenen, opslaan."""
    from sqlledger_web import routes_arap as ra
    from sqlledger_web.reportdefs import Lib
    from sqlledger._db import cursor as dbcursor, default_currency, get_precision
    lib = Lib(conn, cfg)
    nf = cfg.numberformat
    vc = a["vc"]
    with dbcursor(conn) as cur:
        prec = get_precision(cur)
        defcur = default_currency(cur)

    def fmt(v):     # zoals fmt() in perl_driver.pl
        v = "" if v in (None, "") else str(v)
        return v.replace(".", ",", 1) if nf in ("1.000,00", "1000,00") else v

    if a.get("id"):
        f = ra.form_from_trans(lib, lib.arap.get_transaction(a["id"], vc, lock=True), nf, prec)
        ra.recalc(lib, f, nf, prec, defcur)
    else:
        f = ra.blank_form(lib, vc, a.get("type") or "transaction", a.get("transdate") or "")
        ra.recalc(lib, f, nf, prec, defcur)
    mode = a.get("mode") or "post"
    if mode == "delete":
        lib.arap.delete_transaction(f.id, vc)
        return {}
    if a.get("transdate") is not None:
        f.transdate = a["transdate"]
    if a.get("vc_id"):
        row = conn.execute(f"SELECT name, {vc}number FROM {vc} WHERE id = %s", (a["vc_id"],)).fetchone()
        f.vc_text = f"{row[1] or ''} {row[0] or ''}".strip()
        errors = ra.recalc(lib, f, nf, prec, defcur)
        if errors:
            raise LedgerError(" ".join(errors))
    for k in ("invnumber", "description", "notes", "ordnumber", "ponumber", "dcn"):
        if a.get(k) is not None:
            setattr(f, k, a[k])
    if a.get("terms") is not None:
        f.terms = str(a["terms"])
    if a.get("duedate") is not None:
        f.duedate = a["duedate"]
    if "taxincluded" in a:
        f.taxincluded = bool(a["taxincluded"])
    if a.get("department_id") is not None:
        f.department_id = str(a["department_id"] or "")
    if a.get("currency"):
        f.currency = a["currency"]
    if a.get("exchangerate") is not None:
        f.exchangerate = fmt(a["exchangerate"])
    if a.get("arap_accno"):
        f.arap_accno = a["arap_accno"]
    if a.get("lines") is not None:
        old = f.lines
        f.lines = [ra.ALine(amount=fmt(ln.get("amount")), accno=ln["accno"], description=ln.get("description") or "",
                            project_id=str(ln.get("project_id") or ""),
                            cleared=old[i].cleared if i < len(old) else "")
                   for i, ln in enumerate(a["lines"])]
    for accno, amount in (a.get("taxes") or {}).items():
        for t in f.taxes:
            if t.accno == accno:
                t.amount, t.calc = fmt(amount), False
    if a.get("payments") is not None:
        f.pays = [ra.PayRow(datepaid=p["datepaid"], source=p.get("source") or "", memo=p.get("memo") or "",
                            amount=fmt(p["amount"]), exchangerate=fmt(p.get("exchangerate")), accno=p["accno"])
                  for p in a["payments"]]
    if a.get("discount"):
        d = a["discount"]
        f.discount = ra.PayRow(datepaid=d["datepaid"], source=d.get("source") or "", amount=fmt(d["amount"]),
                               accno=d["accno"])
    if a.get("cashdiscount") is not None:
        f.cashdiscount = fmt(a["cashdiscount"])
    if a.get("discountterms") is not None:
        f.discountterms = str(a["discountterms"])
    errors = ra.recalc(lib, f, nf, prec, defcur)
    if errors:
        raise LedgerError(" ".join(errors))
    if mode == "new_number":
        return {"invnumber": lib.arap.next_invnumber(vc, parse_date(f.transdate) if f.transdate else None,
                                                     f.currency or None, f.description or None)}
    if mode == "post_as_new":
        ra.for_new(lib, f)
    try:
        return {"id": ra.do_post(lib, f, nf)}
    except ValueError as exc:
        raise LedgerError(str(exc))


def gl_ui_py(conn, cfg, a):
    """Memoriaalscherm van de webapp (sqlledger_web.routes_gl) nagespeeld, zoals gl_ui in
    perl_driver.pl het gl.pl-scherm naspeelt: openen (met lock), velden invullen, opslaan."""
    from sqlledger_web import routes_gl as rg
    from sqlledger._db import cursor as dbcursor, default_currency, get_defaults, get_precision
    gl = GeneralLedger(conn, cfg)
    nf = cfg.numberformat
    with dbcursor(conn) as cur:
        prec = get_precision(cur)
        defcur = default_currency(cur)
        lock_glnumber = get_defaults(cur, "lock_glnumber").get("lock_glnumber") not in (None, "", "0")

    def fmt(v):     # zoals fmt() in perl_driver.pl
        v = "" if v in (None, "") else str(v)
        return v.replace(".", ",", 1) if nf in ("1.000,00", "1000,00") else v

    if a.get("id"):
        f = rg.form_from_view(gl.get_transaction(a["id"], lock=True), nf, prec)
    else:
        f = rg.GLForm(fxadj=bool(a.get("fxadj")), transdate=a.get("transdate") or "")
    mode = a.get("mode") or "post"
    if mode == "delete":
        gl.delete_transaction(f.id)
        return {}
    if "transdate" in a:
        f.transdate = a["transdate"] or ""
    for k in ("reference", "description", "notes"):
        if a.get(k) is not None:
            setattr(f, k, a[k])
    if a.get("department_id") is not None:
        f.department_id = str(a["department_id"] or "")
    if a.get("currency"):
        f.currency = a["currency"]
    if a.get("exchangerate") is not None:
        f.exchangerate = fmt(a["exchangerate"])
    old = f.lines
    f.lines = [rg.Line(accno=ln["accno"], debit=fmt(ln.get("debit")), credit=fmt(ln.get("credit")),
                       source=ln.get("source") or "", memo=ln.get("memo") or "",
                       project_id=str(ln.get("project_id") or ""), fx=bool(ln.get("fx")),
                       cleared=old[i].cleared if i < len(old) else "")      # afletterdatum hoort bij de rij
               for i, ln in enumerate(a.get("lines", []))]
    f.lines = [ln for ln in f.lines if rg._filled(ln)]
    if mode == "new_number":
        return {"reference": gl.next_reference(parse_date(f.transdate) if f.transdate else None,
                                               None if f.fxadj else (f.currency or None), f.description or None)}
    recurring = None
    if mode == "post_as_new":
        f.id = None
        if lock_glnumber:
            f.reference = ""
        for ln in f.lines:
            ln.cleared = ""
    elif f.id:
        recurring = gl.get_transaction(f.id).recurring
    trans = rg.to_transaction(f, nf, defcur)
    trans.recurring = recurring
    return {"id": gl.post_transaction(trans)}


def cp_ui_py(conn, cfg, a, resolve):
    """Betaalscherm (cp.pl payment/update_payment/post) nagespeeld met sqlledger.payments."""
    pm = Payments(conn, cfg)
    vc = a["vc"]
    lg = ArapLedger(conn, cfg)
    d = lg.vc_defaults(vc, a["vc_id"])
    datepaid = parse_date(a["datepaid"])
    currency = a.get("currency") or d["currency"]
    invs = pm.open_invoices(vc, a["vc_id"], currency=currency, datepaid=datepaid, lock=True)
    rate = None
    r = conn.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s",
                     (currency, datepaid)).fetchone()
    if r and r[0]:
        rate = Decimal(repr(r[0]))
    elif a.get("exchangerate"):
        rate = Decimal(a["exchangerate"])
    want = {}
    for w in a.get("rows", []):
        want[resolve(w["id"]) if w.get("id") else w["invnumber"]] = w
    nf = cfg.numberformat
    screen, lines = [], []
    for inv in invs:
        w = want.get(inv.id) or want.get(inv.invnumber)
        row = {"id": inv.id, "invnumber": inv.invnumber, "due": format_amount(inv.due, nf, 2),
               "paid": "", "discount": "", "checked": ""}
        if w:
            disc = Decimal(w["discount"]) if w.get("discount") is not None else inv.discount
            if w.get("paid") is not None:
                paid = Decimal(w["paid"])
            elif w.get("discount") is not None:
                paid = inv.due - disc
            else:
                paid = inv.paid_suggested
            row.update(paid=format_amount(paid, nf, 2), discount=format_amount(disc, nf, 2), checked="1")
            lines.append(PaymentLine(inv.id, paid, disc))
        screen.append(row)
    total = Decimal(a["amount"]) if a.get("amount") is not None else None
    p = Payment(vc=vc, vc_id=a["vc_id"], datepaid=datepaid, payment_accno=a["payment_accno"], lines=lines,
                amount=total, currency=currency, exchangerate=rate,
                # het scherm vult een leeg kenmerk aan met het volgende nummer, maar maakt het
                # leeg zolang er geen factuur is aangevinkt
                source=(a.get("source") or None) if lines else (a.get("source") or ""), memo=a.get("memo") or None,
                discount_accno=a.get("discount_accno"), arap_accno=a.get("arap_accno"),
                paymentmethod_id=a.get("paymentmethod_id"))
    res = {"screen": screen}
    if not a.get("nopost"):
        out = pm.post_payment(p)
        res["source"] = out.source
    return res


def cp_ui_web(conn, cfg, a, resolve):
    """Betaalscherm van de webapp (sqlledger_web.routes_cp, één relatie) nagespeeld zoals cp_ui in
    perl_driver.pl cp.pl naspeelt: relatie en datum, rekeningen/valuta, posten aanvinken, boeken."""
    from sqlledger_web import routes_cp as rc
    from sqlledger_web.reportdefs import Lib
    lib = Lib(conn, cfg)
    nf = cfg.numberformat
    vc = a["vc"]

    def fmt(v):
        return "" if v is None else format_amount(Decimal(str(v)), nf, 2)

    f = rc.PayForm(vc=vc, datepaid=a["datepaid"], olddatepaid=a["datepaid"], vc_id=a["vc_id"], all_vc=True,
                   payment_accno=a["payment_accno"], oldpayment_accno=a["payment_accno"],
                   discount_accno=a.get("discount_accno") or "", arap_accno=a.get("arap_accno") or "",
                   paymentmethod_id=str(a.get("paymentmethod_id") or ""))
    ch = rc._choices(lib, vc)
    errors = rc.recalc_payment(lib, f, ch, nf)
    if a.get("currency") and a["currency"] != f.currency:
        f.currency = a["currency"]
        errors = rc.recalc_payment(lib, f, ch, nf)
    if errors:
        raise LedgerError(" ".join(errors))
    if not f.exchangerate and a.get("exchangerate"):
        f.exchangerate = str(a["exchangerate"]).replace(".", "," if nf in ("1.000,00", "1000,00") else ".")
    want = {}
    for w in a.get("rows", []):
        want[resolve(w["id"]) if w.get("id") else w["invnumber"]] = w
    for r in f.rows:
        w = want.get(r.inv.id) or want.get(r.inv.invnumber)
        if w:
            f.inputs[r.inv.id] = rc.RowIn(checked=True, paid=fmt(w.get("paid")), discount=fmt(w.get("discount")),
                                          due=fmt(r.inv.due))
    if a.get("memo") is not None:
        f.memo = a["memo"]
    rc.recalc_payment(lib, f, ch, nf)
    if a.get("source") is not None:
        f.source = a["source"]
    if a.get("amount") is not None:
        f.amount = fmt(a["amount"])
    rc.recalc_payment(lib, f, ch, nf)        # zoals bij Boeken: de server rekent eerst door
    screen = [{"id": r.inv.id, "invnumber": r.inv.invnumber, "due": fmt(r.inv.due), "paid": r.paid,
               "discount": r.discount, "checked": "1" if r.checked else ""} for r in f.rows]
    res = {"screen": screen}
    if not a.get("nopost"):
        out = lib.payments.post_payment(rc.to_payment(f, nf))
        res["source"] = out.source
    return res


def cp_payments_web(conn, cfg, a):
    """Scherm Ontvangsten/Betalingen (meerdere relaties) van de webapp nagespeeld zoals
    cp_payments_ui in perl_driver.pl cp.pl payments naspeelt."""
    from sqlledger_web import routes_cp as rc
    from sqlledger_web.reportdefs import Lib
    lib = Lib(conn, cfg)
    nf = cfg.numberformat
    vc = a["vc"]

    def fmt(v):
        return "" if v is None else format_amount(Decimal(str(v)), nf, 2)

    f = rc.PaysForm(vc=vc, datepaid=a["datepaid"], olddatepaid=a["datepaid"], payment_accno=a["payment_accno"],
                    oldpayment_accno=a["payment_accno"], discount_accno=a.get("discount_accno") or "",
                    currency=a.get("currency") or "")
    ch = rc._choices(lib, vc)
    rc.recalc_payments(lib, f, ch, nf)
    for w in a.get("rows", []):
        r = next((x for x in f.rows if x.vc_id == w["vc_id"]), None)
        if r:
            f.inputs[r.vc_id] = rc.VcIn(checked=True, paid=fmt(w.get("paid")), memo=w.get("memo") or "",
                                        due=fmt(r.due))
    for r in f.rows:
        f.inputs.setdefault(r.vc_id, rc.VcIn(due=fmt(r.due)))
    rc.recalc_payments(lib, f, ch, nf)
    res = {"screen": [{"vc_id": r.vc_id, "due": fmt(r.due), "paid": r.paid, "source": r.source,
                       "checked": "1" if r.checked else ""} for r in f.rows]}
    if not a.get("nopost"):
        try:
            rc.book_many(lib, f, nf)
        except rc.BookError as exc:
            raise exc.cause
    return res


# ================================================================ rc (afletteren, fase 2) — begin
RC_OPS = ("rc_screen", "rc_ui")


def rc_html_screen(html: str) -> Dict[str, Any]:
    """rc.pl-scherm (get_payments/update/report) → beginsaldo, regels, totalen, afschriftsaldo, verschil."""
    def txt(c):
        c = re.sub(r"<br>", "|", c, flags=re.I)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ")).strip()
    report = "name=statementbalance" not in html
    out: Dict[str, Any] = {"begin": None, "rows": [], "total": None}
    for seg in re.split(r"<tr\b", html, flags=re.I)[1:]:
        m = re.match(r"[^>]*class=\"?(listrow|listtotal)", seg, flags=re.I)
        if not m:
            continue
        seg = re.split(r"</tr>|<table\b", seg, flags=re.I)[0]
        cells = re.findall(r"<t[hd]\b[^>]*>(.*?)(?=<t[hd]\b|</t[hd]>|$)", seg, flags=re.I | re.S)
        if m.group(1).lower() == "listtotal":
            t = [txt(c) for c in cells]
            out["total"] = [t[-2], t[-1]] if report else [t[4], t[5]]
            continue
        if report:
            d, src, name, deb, cred = (txt(c) for c in cells[:5])
            out["rows"].append([d, src, name.rstrip("|"), "", deb, cred, ""])
            continue
        d, src, name, clr, deb, cred, bal = cells[:7]
        if txt(name) == "Beginning Balance":
            out["begin"] = txt(bal)
            continue
        flag = "*" if txt(clr) == "*" else ("1" if re.search(r"\bchecked\b", clr) else "")
        out["rows"].append([txt(d), txt(src), txt(name).rstrip("|"), flag, txt(deb), txt(cred), txt(bal)])
    if not report:
        out["statementbalance"] = re.search(r"name=statementbalance[^>]*value=([^\s>]*)", html).group(1)
        out["difference"] = re.search(r"name=null[^>]*value=([^\s>]*)", html).group(1)
    return out


def rc_py_screen(view, nf: str) -> Dict[str, Any]:
    """Zelfde vorm uit het scherm van de webapp (routes_rc.View)."""
    st = view.st
    p = st.precision

    def f(v, dash):
        return format_amount(v, nf, p, dash).replace("&nbsp;", "").strip()
    out: Dict[str, Any] = {"begin": None if st.report else f(st.beginbalance, "0"), "rows": [],
                           "total": [f(st.total_debit, ""), f(st.total_credit, "")]}
    for ln in st.lines:
        on = ln.group in view.groups
        flag = "1" if on else ("~" if ln.partly else "")      # ~ = deels afgeletterd (Perl: status eerste regel)
        if ln.fx:
            row = ["", "", "", ("*" if on else "")]
        else:
            row = [ln.transdate.isoformat(), ln.source, "|".join(n or "" for n in ln.names), flag]
        if st.report:
            row[3] = ""
        row += [f(ln.debit, ""), f(ln.credit, ""), "" if st.report else f(ln.balance, "0")]
        out["rows"].append(row)
    if not st.report:
        out["statementbalance"] = view.balance_text if view.balance is None else f(view.balance, "0")
        out["difference"] = f(view.difference, "0") if view.difference is not None else None
    return out


def rc_sel(a: Dict[str, Any]):
    from sqlledger_web import routes_rc as wr
    return wr.Sel(rekening=a["accno"], van=a.get("fromdate") or "", tot=a.get("todate") or "",
                  weergave="samenvatting" if a.get("summary", 1) else "detail", koers=bool(a.get("fx", 1)))


def rc_screen_py(conn, cfg, a, variant: bool = True) -> Dict[str, Any]:
    if variant and rc_has_duplicates(conn, a["accno"]):
        with rc_perl_union():
            alt = rc_screen_py(conn, cfg, a, variant=False)["screen"]
        return dict(rc_screen_py(conn, cfg, a, variant=False), screen_perlunion=alt)
    from sqlledger.reconcile import Reconciliation
    from sqlledger_web import routes_rc as wr
    rc = Reconciliation(conn, cfg)
    s = rc_sel(a)
    van, tot = date.fromisoformat(s.van) if s.van else None, date.fromisoformat(s.tot) if s.tot else None
    if a.get("report"):
        st = rc.report(s.rekening, van, tot, summary=s.summary, fx=s.koers)
    else:
        st = rc.statement(s.rekening, van, tot, summary=s.summary, fx=s.koers)
    return {"screen": rc_py_screen(wr._view(st, cfg.numberformat), cfg.numberformat)}


def rc_ui_web(conn, cfg, a, variant: bool = True) -> Dict[str, Any]:
    """Afletterscherm van de webapp nagespeeld zoals rc_ui in perl_driver.pl rc.pl naspeelt:
    scherm tonen, vinkjes zetten (op kenmerk of "datum kenmerk"), Bijwerken, Opslaan."""
    if variant and rc_has_duplicates(conn, a["accno"]):
        with rc_perl_union():
            alt = rc_ui_web(conn, cfg, dict(a, nosave=1), variant=False)["screen"]
        return dict(rc_ui_web(conn, cfg, a, variant=False), screen_perlunion=alt)
    from sqlledger.reconcile import Reconciliation
    from sqlledger_web import routes_rc as wr
    rc = Reconciliation(conn, cfg)
    nf = cfg.numberformat
    s = rc_sel(a)
    van, tot = date.fromisoformat(s.van) if s.van else None, date.fromisoformat(s.tot) if s.tot else None
    st = rc.statement(s.rekening, van, tot, summary=s.summary, fx=s.koers)
    v0 = wr._view(st, nf)
    want, unwant = set(a.get("check", [])), set(a.get("uncheck", []))
    groups = set(v0.groups)
    for ln in st.lines:
        if ln.fx:
            continue
        ids = {ln.source, f"{ln.transdate.isoformat()} {ln.source}"}
        if ids & want:
            groups.add(ln.group)
        if ids & unwant:
            groups.discard(ln.group)
    tokens = [ln.tokens for ln in st.lines if ln.group in groups]
    sb = a.get("statementbalance")
    bal = v0.balance_text if sb in (None, "auto") else (str(sb).replace(".", ",") if nf in ("1.000,00", "1000,00") else str(sb))
    action = "select_all" if a.get("select_all") else "update"
    out = wr.process(rc, s, action, tokens, bal, nf)
    if out.error:
        raise LedgerError(out.error)
    v = out.view
    tokens = [ln.tokens for ln in v.st.lines if ln.group in v.groups]
    if sb == "auto":
        bal = format_amount(v.cleared_balance, nf, v.st.precision, "0")
        v = wr.process(rc, s, "update", tokens, bal, nf).view
    res: Dict[str, Any] = {"screen": rc_py_screen(v, nf)}
    if not a.get("nosave"):
        out = wr.process(rc, s, "save", tokens, bal, nf)
        if out.error:
            res["save_error"] = out.error
    return res
def rc_blocks(screen: Dict[str, Any], nf: str):
    """Afletterscherm per blok (datum, kenmerk): som debet, som credit, namen (verzameling), vinkjes
    (verzameling) en saldo aan het eind van het blok. Ongevoelig voor de volgorde van regels met
    dezelfde datum en hetzelfde kenmerk (Perl: ORDER BY 1,2,3, daarbinnen willekeurig); omdat een
    samengevoegde regel alleen regels met hetzelfde teken bevat, zijn de debet-/creditsommen per blok
    dan toch gelijk."""
    out: List[Any] = []
    key = None
    for d, src, names, flag, deb, cred, bal in screen.get("rows") or []:
        if d or src or key is None:          # koersverschilregel: geen datum/kenmerk, hoort bij het vorige blok
            k = (d, src)
            if k != key:
                out.append([k, Decimal(0), Decimal(0), set(), set(), ""])
                key = k
        blk = out[-1]
        blk[1] += screen_amount(deb, nf) if deb else Decimal(0)
        blk[2] += screen_amount(cred, nf) if cred else Decimal(0)
        blk[3].update(n for n in names.split("|") if n)
        blk[4].add(flag)
        blk[5] = bal
    rest = {k: v for k, v in screen.items() if k != "rows"}
    return rest, [[b[0], str(b[1]), str(b[2]), sorted(b[3]), sorted(b[4]), b[5]] for b in out]


RC_ORDER = ("afletterscherm: volgorde van regels met dezelfde datum en hetzelfde kenmerk "
            "(Perl willekeurig; per blok gelijke bedragen, namen, vinkjes en saldo)")
RC_PARTIAL = ("afletterscherm: deels afgeletterde samengevoegde regel; Perl neemt de status van de eerste "
              "regel (vinkje en verschil), de webapp 'deels' (README 103)")
RC_UNION = ("afletterscherm: identieke acc_trans-regels; Perl's UNION toont er één (README 100), "
            "gelijk aan de webapp met die regels samengevoegd")


def rc_explain(a: Dict[str, Any], b: Dict[str, Any], nf: str):
    """None als de schermen echt verschillen, anders de lijst verklaringen (leeg = gelijk)."""
    if a == b:
        return []
    (ra, ba), (rb, bb) = rc_blocks(a, nf), rc_blocks(b, nf)
    if (ra, ba) == (rb, bb):
        return [RC_ORDER]
    wild = [i for i, x in enumerate(bb) if "~" in x[4]]
    if wild and len(ba) == len(bb):
        for i in wild:
            ba[i][4] = bb[i][4]
        ra.pop("difference", None)
        rb.pop("difference", None)
        if (ra, ba) == (rb, bb):
            return [RC_ORDER, RC_PARTIAL]
    return None


@contextmanager
def rc_perl_union():
    """Bootst Perl's UNION na: identieke regels (datum, kenmerk, fx, bedrag, afletterdatum, boeking,
    naam) tellen één keer. Alleen voor de vergelijking."""
    import sqlledger.reconcile as m
    orig = m._merge

    def merge(raw, summary):
        seen, out = set(), []
        for r in raw:
            k = (r["transdate"], r["source"], r["fx"], r["amount"], r["cleared"], r["trans_id"], r["description"])
            if k not in seen:
                seen.add(k)
                out.append(r)
        return orig(out, summary)
    m._merge = merge
    try:
        yield
    finally:
        m._merge = orig


def rc_has_duplicates(conn, accno: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM acc_trans ac JOIN chart c ON c.id = ac.chart_id WHERE c.accno = %s "
        "GROUP BY ac.trans_id, ac.transdate, ac.source, ac.fx_transaction, ac.amount, ac.cleared "
        "HAVING count(*) > 1 LIMIT 1", (accno,)).fetchone())


# ================================================================ rc (afletteren, fase 2) — einde


REPORTS = ("trial_balance", "income_statement", "balance_sheet")
ARAP_REPORTS = ("aging", "reminder", "tax_report", "payments", "arap_list")
# bedragen staan altijd met decimalen op het scherm (ids, kassanummers niet)
AMOUNT_RE = re.compile(r"^\(?-?[0-9][0-9.,']*[.,][0-9]{2}\)?( DR| CR)?$")


def html_class_rows(html: str) -> List[Tuple[str, List[str]]]:
    """(soort, bedragen) per tabelregel met class listrow/listsubtotal/listtotal."""
    out = []
    html = re.sub(r"<select\b.*?</select>", "", html, flags=re.I | re.S)
    for seg in re.split(r"<tr\b", html, flags=re.I)[1:]:
        m = re.match(r"[^>]*class=\"?(list(?:row|subtotal|total))", seg, flags=re.I)
        if not m:
            continue
        kind = {"listrow": "row", "listsubtotal": "subtotal", "listtotal": "total"}[m.group(1).lower()]
        seg = re.split(r"</tr>|<table\b", seg, flags=re.I)[0]
        # (tag-attributen, inhoud): bedragen staan in rp.pl/aa.pl altijd rechts uitgelijnd;
        # een tekstkolom (omschrijving, kenmerk, factuurnummer) die toevallig op een
        # bedrag lijkt (bv. omschrijving "1010,83") staat links en telt niet mee.
        cells = re.findall(r"<t[hd]\b([^>]*)>(.*?)(?=<t[hd]\b|</t[hd]>|$)", seg, flags=re.I | re.S)
        txt = [(attrs, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ")).strip())
               for attrs, c in cells]
        out.append((kind, [t for attrs, t in txt if AMOUNT_RE.match(t)
                           and not (kind == "row" and not re.search(r"align=\"?right", attrs, flags=re.I))]))
    return out


# ------------------------------------------------------------------ rapport-HTML

def html_rows(html: str) -> List[List[str]]:
    """Tabelregels uit het rp.pl-scherm als lijst celteksten."""
    out = []
    for seg in re.split(r"<tr\b", html, flags=re.I)[1:]:
        seg = re.split(r"</tr>|<table\b", seg, flags=re.I)[0]
        cells = re.findall(r"<t[hd]\b[^>]*>(.*?)(?=<t[hd]\b|</t[hd]>|$)", seg, flags=re.I | re.S)
        txt = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ")).strip()
               for c in cells]
        if any(txt):
            out.append(txt)
    return out


def report_rows_perl(op: str, html: str) -> List[List[str]]:
    rows = html_rows(html)
    start = {"trial_balance": "Account", "income_statement": "Income",
             "balance_sheet": "Assets"}[op]
    for i, r in enumerate(rows):
        if r[0] == start or (op == "trial_balance" and r[0] == "GIFI"):
            rows = rows[i + (1 if op == "trial_balance" else 0):]
            break
    else:
        return []
    end = {"trial_balance": None, "income_statement": "Income / (Loss)",
           "balance_sheet": "Total Equity"}[op]
    if op == "trial_balance":
        for i, r in enumerate(rows):
            if len(r) == 6 and not r[0] and not r[1] and not r[2]:   # totaalregel
                return rows[:i + 1]
    for i, r in enumerate(rows):
        if r[0] == end:
            return rows[:i + 1]
    return rows


SECTION_LABELS = {"Income", "Expenses", "Assets", "Liabilities", "Equity"}


def drop_stale_subtotals(rows: List[List[str]]) -> Tuple[List[List[str]], int]:
    """Verwijder Perl's 'verouderde' subtotaalregels (rp.pl section_display).

    Een sectie zonder eigen kop krijgt in Perl een subtotaalregel met de naam
    van de laatste kop uit een vorige sectie (en op de balans daarin het
    resultaat lopend boekjaar). Python laat die regel bewust weg; hier wordt hij
    uit de Perl-uitvoer gefilterd zodat de rest streng vergeleken kan worden.
    """
    out, dropped = [], 0
    headings: set = set()          # kopnamen uit eerdere secties
    current: set = set()           # namen in de huidige sectie
    current_headings: set = set()
    for r in rows:
        if len(r) == 1 and r[0] in SECTION_LABELS:
            headings |= current_headings
            current, current_headings = set(), set()
            out.append(r)
            continue
        label = r[0]
        if label in headings and label not in current:
            dropped += 1
            continue
        if all(v == "-" for v in r[1:]):     # kopregel: bedragen op 0 gezet
            current_headings.add(label)
        current.add(label)
        out.append(r)
    return out, dropped


def report_rows_python(op: str, rep, nf: str) -> List[List[str]]:
    def f(v, places, dash):
        if v is None:
            return ""
        return format_amount(v, nf, places, dash).replace("&nbsp;", "").strip()

    rows = []
    if op == "trial_balance":
        p = int(rep.info.get("precision") or 2)
        for r in rep.display:
            beg, dr, cr, end = r.values
            if r.kind == "account":
                rows.append([r.accno, r.description.strip()] + [f(x, p, "&nbsp;") for x in (beg, dr, cr, end)])
            elif r.kind == "heading":
                rows.append(["", r.description, "", "", "", ""])
            elif r.kind == "subtotal":
                rows.append(["", r.description] + [f(x, p, "&nbsp;") for x in (beg, dr, cr, end)])
            elif r.kind == "total":
                rows.append(["", "", "", f(dr, p, "&nbsp;"), f(cr, p, "&nbsp;"), ""])
        return [r for r in rows if any(r)]
    dp = rep.info["decimalplaces"]
    for r in rep.display:
        if r.kind == "section":
            rows.append([r.description])
        elif r.kind in ("account", "heading", "current_earnings"):
            rows.append([r.description] + [f(v, dp, "-") for v in r.values])
        else:
            rows.append([r.description] + [f(v, dp, "") for v in r.values])
    return [[(c or "").strip() for c in r] for r in rows if any(r)]


def account_rows_python(out, nf: str) -> List[List[Any]]:
    """Het ca.pl-scherm (list_transactions) uit AccountTransactions.display:
    (soort, bedragen) per regel, zoals html_class_rows het Perl-scherm leest."""
    p = int(out.info.get("precision") or 2)
    screen = []
    for d in out.display:
        dr, cr, bal = d.values
        cells = [format_amount(dr, nf, p, "&nbsp;"), format_amount(cr, nf, p, "&nbsp;")]
        if bal is not None:
            cells.append(format_amount(bal, nf, p, "0"))
        cells = [c.replace("&nbsp;", "").strip() for c in cells]
        screen.append(["row" if d.kind == "balance" else d.kind, [c for c in cells if c]])
    return screen


def gl_html_rows(html: str) -> List[List[Any]]:
    """gl.pl transactions: (soort, bedragen) per regel; de kop per rekening bij
    splitledger (regel zonder class met colspan) wordt 'account'."""
    out = []
    html = re.sub(r"<select\b.*?</select>", "", html, flags=re.I | re.S)
    for seg in re.split(r"<tr\b", html, flags=re.I)[1:]:
        m = re.match(r"[^>]*class=\"?(list(?:row|subtotal|total))", seg, flags=re.I)
        seg = re.split(r"</tr>|<table\b", seg, flags=re.I)[0]
        if m:
            kind = {"listrow": "row", "listsubtotal": "subtotal", "listtotal": "total"}[m.group(1).lower()]
        elif re.search(r"<td colspan=", seg, flags=re.I):
            kind = "account"
        else:
            continue
        cells = re.findall(r"<t[hd]\b[^>]*>(.*?)(?=<t[hd]\b|</t[hd]>|$)", seg, flags=re.I | re.S)
        txt = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ")).strip() for c in cells]
        out.append([kind, [t for t in txt if AMOUNT_RE.match(t)]])
    return out


def gl_rows_python(out, nf: str) -> List[List[Any]]:
    """Het gl.pl-scherm uit TransactionList.display, zoals gl_html_rows het leest."""
    p = int(out.info.get("precision") or 2)
    kinds = {"balance": "row", "account_subtotal": "subtotal"}
    screen = []
    for d in out.display:
        dr, cr, bal = d.values
        cells = [format_amount(dr, nf, p, "&nbsp;"), format_amount(cr, nf, p, "&nbsp;")]
        if bal is not None:
            cells.append(format_amount(bal, nf, p, "0" if d.kind != "total" else "&nbsp;"))
        cells = [c.replace("&nbsp;", "").strip() for c in cells]
        screen.append([kinds.get(d.kind, d.kind), [c for c in cells if AMOUNT_RE.match(c)]])
    return screen


def arap_rows_python(rep, nf: str, prec: int = 2) -> List[List[Any]]:
    """rp.pl/aa.pl-schermen (ouderdom, btw, betalingen, overzicht): (soort, bedragen) per regel."""
    def fmt(v):
        return format_amount(v, nf, prec, "&nbsp;").replace("&nbsp;", "").strip()
    return [[d.kind, [x for x in (fmt(v) for v in d.values) if x]] for d in rep.display]


TABLES = {
    # tabel: (kolommen die genegeerd worden, reden)
    "gl": (),
    "ar": (),
    "ap": (),
    "payment": (),
    "acc_trans": (),
    "dpt_trans": (),
    "exchangerate": (),
    "defaults": (),
    "audittrail": ("transdate",),        # tijdstempel van het moment van uitvoeren
    "recurring": (),
    "recurringemail": (),
    "recurringprint": (),
    "br": (),
    "vr": (),
    "reference": (),
    "archive": (),
    "semaphore": ("expires",),           # tijdstempel
    "yearend": (),
    "pay_trans": (),
    "status": (),
}


# ===== ct (relaties, fase 2) ==============================================================
TABLES.update({t: () for t in ("customer", "vendor", "address", "contact", "bank", "shipto",
                               "customertax", "vendortax")})
TABLES.update(IC_TABLES)
TABLES["jcitems"] = ()                 # tijdkaarten (fase Uren)
TABLES.update(IS_TABLES)
TABLES.update(OE_TABLES)
CT_IDS = {"business_id", "employee_id", "pricegroup_id", "paymentmethod_id"}
CT_NUMS = {"terms", "discount", "creditlimit", "cashdiscount", "discountterms", "threshold"}


def ct_normalize(conn, table: str, cols: List[str], rows: List[tuple]) -> List[tuple]:
    """Relatietabellen vergelijkbaar maken waar Perl en Python hetzelfde bedoelen:
    koppeling 0 = NULL, leeg getal = 0, leeg vinkje = uit, lege valuta = standaardvaluta
    (Perl kiest bij een lege valuta de eerste uit de lijst), en bank-naam/-adres zonder
    onderscheid in hoofdletters (Perl zet ze in hoofdletters; README 61)."""
    if table not in ("customer", "vendor", "address", "bank"):
        return rows
    defcur = None
    if table in ("customer", "vendor"):
        r = conn.execute("SELECT curr FROM curr ORDER BY rn LIMIT 1").fetchone()
        defcur = r[0].strip() if r else None
    out = []
    for row in rows:
        d = dict(zip(cols, row))
        if table in ("customer", "vendor"):
            for c in CT_IDS:
                d[c] = None if d.get(c) in (None, "0") else d[c]
            for c in CT_NUMS:
                d[c] = "0" if d.get(c) is None else d[c]
            for c in ("taxincluded", "remittancevoucher"):
                d[c] = "0" if d.get(c) is None else d[c]
            d["curr"] = (d.get("curr") or "").strip() or defcur
        elif table == "bank" or (table == "address" and d.get("id") == d.get("trans_id")):
            for c in ("name", "address1", "address2", "city", "state", "zipcode", "country"):
                if isinstance(d.get(c), str):
                    d[c] = d[c].upper()
        out.append(tuple(d[c] for c in cols))
    return sorted(out, key=repr)


def ct_ui_py(conn, cfg, a):
    """Klant-/leveranciersscherm van de webapp (sqlledger_web.routes_ct) nagespeeld, zoals ct_ui in
    perl_driver.pl ct.pl naspeelt: openen, velden invullen, opslaan / als nieuw / verwijderen / nieuw nummer."""
    from sqlledger_web import routes_ct as rc
    from sqlledger.contacts import Contacts
    nf = cfg.numberformat
    lib = Contacts(conn, cfg)
    vc = a["vc"]
    prec = lib.choices(vc)["precision"]

    def fmt(v):     # zoals fmt() in perl_driver.pl
        v = "" if v in (None, "") else str(v)
        return v.replace(".", ",", 1) if nf in ("1.000,00", "1000,00") else v

    f = rc.form_from_relation(lib.get(vc, a["id"]) if a.get("id") else lib.new(vc), nf, prec)
    mode = a.get("mode") or "save"
    if mode == "delete":
        lib.delete(vc, f.id)
        return {}
    for k, x in (a.get("fields") or {}).items():
        if k == "taxes":
            f.taxaccounts = list(x)
        elif k in ("taxincluded", "remittancevoucher"):
            setattr(f, k, bool(x))
        elif k in ("discount", "cashdiscount", "creditlimit", "threshold"):
            setattr(f, k, fmt(x))
        else:
            setattr(f, k, "" if x is None else str(x))
    if mode == "new_number":
        return {"number": lib.next_number(vc, rc.to_relation(f, rc.base_relation(lib, f), nf), peek=True)}
    try:
        return {"id": rc.do_save(lib, f, nf, as_new=mode == "save_as_new")}
    except ValueError as exc:
        raise LedgerError(str(exc))
# ===== einde ct ==========================================================================


# ------------------------------------------------------------------ normaliseren

def norm(v: Any) -> Any:
    """Maak waarden uit Perl (strings, 1/0) en Python (Decimal, date, bool)
    vergelijkbaar."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    if isinstance(v, list):
        return [norm(x) for x in v]
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items()}
    if isinstance(v, (int, float, Decimal)) or (isinstance(v, str) and _numeric(v)):
        try:
            d = Decimal(str(v))
            # bedragen in de database zijn float; Perl telt als float op, Python exact.
            # Vergelijken op 6 decimalen (ver onder een cent).
            if abs(d) < Decimal("1e15") and d != d.to_integral_value():
                d = d.quantize(Decimal("0.000001"))
            if d == 0:
                return "0"
            if d == d.to_integral_value():
                return str(d.quantize(Decimal(1)))
            return format(d.normalize(), "f")
        except InvalidOperation:
            return v
    if isinstance(v, str):
        return v.rstrip() if v.endswith(" ") and not v.strip() == "" else v
    return v


def _numeric(s: str) -> bool:
    s = s.strip()
    if not s or s[0] == "0" and len(s) > 1 and s[1] != ".":  # '0042', '0010' zijn codes
        return False
    try:
        Decimal(s)
        return any(c.isdigit() for c in s) and "-" not in s[1:] and not s.isalpha()
    except InvalidOperation:
        return False


def isodate(v):
    if isinstance(v, str) and re.fullmatch(r"\d{8}", v):
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return norm(v)


KEYS = {
    "get_header": ["id", "reference", "description", "notes", "transdate", "department_id",
                   "department", "curr", "exchangerate", "approved", "batchid",
                   "batchdescription", "employee_id"],
    "get_line": ["accno", "description", "amount", "source", "memo", "project_id",
                 "projectnumber", "cleared", "fx_transaction", "transdate", "approved"],
    "list": ["id", "type", "reference", "description", "transdate", "source", "amount",
             "accno", "account_description", "category", "gifi_accno", "notes", "link",
             "cleared", "department", "memo", "module", "debit", "credit", "address",
             "contra", "gifi_contra", "balance", "name", "vcnumber", "lineitem"],
    "accounts": ["accno", "description", "charttype", "category", "link", "gifi_accno",
                 "gifi_description", "amount", "debit", "credit", "contra", "closed"],
    "account_tx": ["id", "reference", "description", "transdate", "amount", "module",
                   "cleared", "source", "db", "vc_id", "debit", "credit", "accno"],
}


def pick(row: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    out = {}
    for k in keys:
        v = norm(row.get(k))
        if k in ("amount", "debit", "credit", "balance") and v == "0":
            v = None      # 0 en leeg zijn op het scherm gelijk
        if k in ("contra", "gifi_contra", "address") and isinstance(v, str):
            v = v.strip() or None
        out[k] = v
    return out


def normalize_arap_get(res: Dict[str, Any]) -> Dict[str, Any]:
    """Vergelijkbare weergave van een geladen boeking: Perl-formuliervelden of
    Python ArapTransaction."""
    if "trans" in res:
        t = res["trans"]
        return {
            "invnumber": t.invnumber, "transdate": norm(t.transdate), "duedate": norm(t.duedate),
            "terms": norm(t.terms), "taxincluded": norm(bool(t.taxincluded)), "type": t.type,
            "currency": t.currency, "exchangerate": norm(t.exchangerate), "arap": t.arap_accno,
            "lines": [[l.accno, norm(l.amount), l.description or None, norm(l.project_id)] for l in t.lines],
            "taxes": {a: norm(v) for a, v in t.taxes.items() if v},
            "payments": [[p.accno, norm(p.amount), norm(p.datepaid), p.source or None, p.memo or None,
                          norm(p.exchangerate)] for p in t.payments],
            "discount": norm(t.discount.amount) if t.discount else None,
            "calctax": sorted(t.calctax), "oldinvtotal": norm(t.oldinvtotal),
        }
    f = res.get("form", {})
    ARAP = f.get("ARAP", "AR")
    acc = lambda v: (v or "").split("--")[0] or None  # noqa: E731
    lines = []
    for i in range(1, int(f.get("rowcount") or 0) + 1):
        if f.get(f"amount_{i}") not in (None, "", 0, "0"):
            pid = (f.get(f"projectnumber_{i}") or "").split("--")
            lines.append([acc(f.get(f"{ARAP}_amount_{i}")), norm(f.get(f"amount_{i}")),
                          f.get(f"description_{i}") or None, norm(pid[1] if len(pid) > 1 and pid[1] else None)])
    taxes = {k[4:]: norm(v) for k, v in f.items() if k.startswith("tax_") and v not in (None, "", 0, "0")}
    pays = []
    for i in range(1, int(f.get("paidaccounts") or 0) + 1):
        if f.get(f"paid_{i}") not in (None, "", 0, "0"):
            pays.append([acc(f.get(f"{ARAP}_paid_{i}")), norm(f.get(f"paid_{i}")), norm(f.get(f"datepaid_{i}")),
                         f.get(f"source_{i}") or None, f.get(f"memo_{i}") or None, norm(f.get(f"exchangerate_{i}"))])
    return {
        "invnumber": f.get("invnumber"), "transdate": norm(f.get("transdate")), "duedate": norm(f.get("duedate")),
        "terms": norm(f.get("terms") or 0), "taxincluded": norm(bool(f.get("taxincluded"))), "type": f.get("type"),
        "currency": f.get("currency"), "exchangerate": norm(f.get("exchangerate")), "arap": acc(f.get(ARAP)),
        "lines": lines, "taxes": taxes, "payments": pays,
        "discount": norm(f.get("discount_paid")) if f.get("discount_paid") else None,
        "calctax": sorted(k[8:] for k, v in f.items() if k.startswith("calctax_") and v),
        "oldinvtotal": norm(f.get("oldinvtotal")),
    }


def normalize_result(res: Dict[str, Any]) -> Dict[str, Any]:
    op = res.get("op")
    if "error" in res:
        return {"op": op, "error": True}
    if op == "post":
        return {"op": op, "id": norm(res.get("id"))}
    if op == "get":
        lines = [pick(r, KEYS["get_line"]) for r in res.get("lines", [])]
        refs = sorted((norm(r.get("description")), norm(r.get("code")), norm(r.get("archive_id")),
                       norm(r.get("folder")), norm(r.get("filename")), norm(bool(r.get("confidential"))))
                      for r in res.get("references", []))
        return {"op": op, "header": pick(res.get("header", {}), KEYS["get_header"]),
                "fxadj": norm(bool(res.get("fxadj"))),
                "lines": sorted(lines, key=json.dumps),
                "line_order": [ln["accno"] for ln in lines],
                "recurring": norm(res.get("recurring")),
                "references": refs, "readonly": norm(bool(res.get("readonly")))}
    if op in ("list", "accounts", "account_tx"):
        rows = [pick(r, KEYS[op]) for r in res.get("rows", [])]
        if op == "account_tx":
            for r in rows:
                if isinstance(r.get("accno"), list):
                    r["accno"] = sorted(r["accno"])
        out = {"op": op, "rows": sorted(rows, key=json.dumps)}
        order_keys = {"list": ("id", "transdate", "reference"), "accounts": ("accno",),
                      "account_tx": ("transdate", "reference")}[op]
        out["order"] = [[r[k] for k in order_keys] for r in rows]
        if op != "accounts":
            bal = res.get("balance")
            # Perl telt floats op (bv. -15736.7099999837); afronden op 6 decimalen
            bal = round(Decimal(str(bal)), 6) if bal not in (None, "") else None
            out["balance"] = norm(bal) if bal else "0"
        if op == "list":
            out["datefrom"] = isodate(res.get("datefrom"))
            out["dateto"] = isodate(res.get("dateto"))
        return out
    if op in REPORTS:
        out = {"op": op, "screen": res.get("screen")}
        if op == "trial_balance":
            rows = [pick(r, ["accno", "description", "category", "contra", "charttype", "amount",
                             "balance", "debit", "credit"]) for r in res.get("rows", [])]
            for r in rows:
                for k in ("amount", "balance", "debit", "credit"):
                    if r[k] is not None:
                        # Perl telt floats op (restjes als -1e-12); afronden op 6 decimalen
                        r[k] = norm(round(Decimal(r[k]), 6))
                    if r[k] in ("0", "-0"):
                        r[k] = None
            out["rows"] = rows
        return out
    if op in ARAP_OPS:
        out = {"op": op}
        if op == "arap_repost_all":
            out["count"] = res.get("count")
            out["errors"] = sorted(res.get("errors", {}))
        elif op == "arap_get":
            out["trans"] = normalize_arap_get(res)
        elif op in ("arap_post", "arap_repost"):
            out["id"] = norm(res.get("id"))
        return out
    if op == "recurring_ui":            # recurring
        return {"op": op, "screen": sorted([{k: norm(r.get(k)) for k in ("id", "checked", "nextdate")}
                                            for r in res.get("screen") or []], key=json.dumps)}
    if op == "names":
        rows = [pick(r, ["id", "name", "city", "zipcode", "curr"]) for r in res.get("rows", [])]
        # sorteersleutel (naam of nummer); bij gelijke sleutel is de Perl-volgorde willekeurig
        by = res.get("by") or "name"
        order = [norm(r.get(by)) for r in res.get("rows", [])]
        return {"op": op, "rows": sorted(rows, key=json.dumps), "order": order}
    if op in ("am_ui", "ic_ui", "ic_stock", "is_ui", "oe_ui", "im_ui", "jc_ui", "pe_so"):   # blok "am"/"ic": alleen fout of niet; de rest zit in de tabellen
        return {"op": op}
    if op == "gl_ui":
        return {"op": op, "id": norm(res.get("id")), "reference": res.get("reference")}
    if op == "aa_ui":
        return {"op": op, "id": norm(res.get("id")), "invnumber": res.get("invnumber")}
    if op == "ct_ui":
        return {"op": op, "id": norm(res.get("id")), "number": res.get("number")}
    if op in ("account_screen", "gl_screen"):
        return {"op": op, "screen": res.get("screen")}
    if op in RC_OPS:            # rc (afletteren, fase 2)
        return {"op": op, "screen": res.get("screen"), "save_error": bool(res.get("save_error"))}
    if op in ("cp_void", "cp_reissue"):
        return {"op": op}
    if op == "cp_pay_all":
        return {"op": op, "count": res.get("count"), "errors": sorted(res.get("errors", {}))}
    if op == "cp_payments":
        screen = [{k: norm(r.get(k)) for k in ("vc_id", "due", "paid", "source")} for r in res.get("screen", [])
                  if r.get("checked")]
        return {"op": op, "screen": sorted(screen, key=json.dumps)}
    if op in CP_OPS:
        screen = [{k: norm(r.get(k)) for k in ("id", "invnumber", "due", "paid", "discount", "checked")}
                  for r in res.get("screen", [])]
        for r in screen:
            r["checked"] = "1" if r["checked"] else None
            if r["checked"] is None:
                r["paid"] = r["discount"] = None
            elif r["discount"] == "0":
                r["discount"] = None
        return {"op": op, "screen": sorted(screen, key=json.dumps)}
    if op in ARAP_REPORTS or op in ("yearend", "save_level"):
        out = {"op": op, "screen": res.get("screen")}
        if op == "aging":
            rows = []
            for r in res.get("rows", []):
                rate = Decimal(str(r.get("exchangerate") or 1)) or Decimal(1)
                row = pick(r, ["vc_id", "invnumber", "transdate", "duedate", "curr", "module", "id"])
                row["curr"] = (row["curr"] or "").strip() or None
                for b in ("c0", "c15", "c30", "c45", "c60", "c75", "c90"):
                    row[b] = norm(_ra(Decimal(str(r.get(b) or 0)) / rate, 2)) \
                        if not r.get("_python") else norm(r.get(b))
                rows.append(row)
            out["rows"] = sorted(rows, key=json.dumps)
        elif op == "arap_list":
            out["rows"] = sorted((pick(r, ["id", "invnumber", "transdate", "duedate", "amount", "paid", "tax",
                                            "netamount", "curr", "name", "vc_id", "datepaid", "accno", "source",
                                            "memo", "debit", "credit"]) for r in res.get("rows", [])),
                                 key=json.dumps)
            for r in out["rows"]:
                for k in ("tax", "paid", "netamount", "debit", "credit"):
                    if r[k] == "0":
                        r[k] = None        # Perl: NULL (geen regels) of 0
                if res.get("summary"):
                    # samenvatting: regelvelden van 'de eerste regel' hangen af van de
                    # regelvolgorde in de database en staan niet op het scherm
                    for k in ("accno", "source", "debit", "credit"):
                        r[k] = None
                    if r["memo"]:
                        r["memo"] = "\n".join(sorted(str(r["memo"]).split("\n")))
            out["rows"] = sorted(out["rows"], key=json.dumps)
            # Perl sorteert op (sorteerkolom,) datum, nummer, naam; bij gelijke sleutels is de
            # volgorde die van de database (willekeurig) -> vergelijk de reeks sleutels
            sk = res.get("sort")
            out["order"] = [[norm(r.get(sk)) if sk else None, isodate(r.get("transdate")),
                             norm(r.get("invnumber")), norm(r.get("name"))] for r in res.get("rows", [])]
        elif op == "reminder":
            out["rows"] = sorted((pick(r, ["id", "invnumber", "transdate", "duedate", "due", "level",
                                            "module", "iban", "bic", "vc_id"]) for r in res.get("rows", [])),
                                 key=json.dumps)
        elif op == "tax_report":
            out["rows"] = sorted((pick(r, ["id", "invoice", "transdate", "invnumber", "name", "netamount",
                                            "tax", "total", "accno", "address", "description"])
                                  for r in res.get("rows", [])), key=json.dumps)
            out["order"] = [(str(r.get("accno")), str(r.get("transdate"))[:10]) for r in res.get("rows", [])]
        elif op == "payments":
            accs = []
            for a in res.get("accounts", []):
                pays = sorted((pick(x, ["transdate", "paid", "source", "memo", "reference", "name",
                                        "trans_id", "employee"]) for x in a.get("payments", [])),
                              key=json.dumps)
                accs.append({"accno": a.get("accno"), "payments": pays})
            out["accounts"] = sorted(accs, key=lambda x: str(x["accno"]))
        elif op == "yearend":
            out["ok"] = "id" in res
        return out
    if op == "amounts":
        return {"op": op, "parse": norm(res.get("parse")), "format": res.get("format"),
                "round": norm(res.get("round"))}
    return {"op": op}


# ------------------------------------------------------------------ Python-driver

def recurring_string(r: Recurring) -> str:
    """Zelfde tekstvorm als Perl's $form->{recurring} na get_recurring."""
    email = ":".join(f"{a}:{b}" for a, b in r.email) if r.email else ":"
    prn = ":".join(f"{a}:{b}:{c or ''}" for a, b, c in r.print) if r.print else "::"
    def esc(x):  # Form::escape
        return re.sub(r"[^a-zA-Z0-9_.-]", lambda m: "%%%02x" % ord(m.group(0)), x or "")
    vals = [esc(r.reference), esc(r.description), r.startdate.isoformat(), str(r.repeat),
            r.unit, str(r.howmany), "1" if r.payment else "0", prn, email, esc(r.message)]
    return ",".join(vals)


# ======================================================================= recurring (begin)
def recurring_ui_py(conn, cfg, a):
    """Herhaalschema en verwerken via de webcode (sqlledger_web.routes_recurring), zoals
    recurring_ui in perl_driver.pl arap.pl schedule/save_schedule en am.pl
    recurring_transactions/process_transactions als browser naspeelt."""
    from starlette.datastructures import FormData
    from sqlledger_web import routes_recurring as rr
    from sqlledger.recurring import RecurringTransactions
    rt = RecurringTransactions(conn, cfg)
    mode = a.get("mode") or "schedule"
    if mode == "process":
        rows = [r for r in rt.list() if r.processable]
        screen = [{"id": r.id, "checked": 1 if r.due else 0,
                   "nextdate": r.nextdate.isoformat() if r.nextdate else ""} for r in rows]
        want = {int(i) for i in a.get("ids") or []}
        nextdates = {int(k): v for k, v in (a.get("nextdates") or {}).items()}
        form = []
        for r in rows:
            form.append((f"old_{r.id}", r.nextdate.isoformat() if r.nextdate else ""))
            form.append((f"next_{r.id}", nextdates.get(r.id) or (r.nextdate.isoformat() if r.nextdate else "")))
            if r.id in want:
                form.append(("sel", str(r.id)))
        results = rt.process(rr.items_from_post(FormData(form)))
        return {"screen": screen, "results": [(r.id, r.ok, r.number, r.reason) for r in results]}
    tid = int(a["id"])
    kind = rt.kind(tid)
    # zoals de browser: eerst de boeking openen (met lock), dan Herhalen
    if kind["module"] == "gl":
        GeneralLedger(conn, cfg).get_transaction(tid, lock=True)
    elif kind["module"] == "oe":
        from sqlledger.order import OrderLedger
        OrderLedger(conn, cfg).get_order(tid, lock=True)
    elif kind["invoice"]:
        from sqlledger.invoice import InvoiceLedger
        InvoiceLedger(conn, cfg).get_invoice(tid, kind["vc"], lock=True)
    else:
        ArapLedger(conn, cfg).get_transaction(tid, kind["vc"], lock=True)
    if mode == "delete":
        rt.delete(tid)
        return {}
    f = rr.form_from_schedule(rt.get(tid), kind["transdate"])
    for k in ("reference", "description", "startdate", "repeat", "unit", "howmany", "nextdate"):
        if a.get(k) is not None:
            setattr(f, k, str(a[k]))
    f.payment = bool(a.get("payment"))
    rt.save(tid, rr.to_schedule(f, kind["module"] in ("ar", "ap")))
    return {}
# ======================================================================= recurring (einde)


def run_python(conn: psycopg.Connection, scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    cfg = UserConfig(**scenario["config"])
    gl = GeneralLedger(conn, cfg)
    ca = ChartOfAccounts(conn, cfg)
    saved: Dict[str, Any] = {}
    results = []

    def resolve(v):
        return saved.get(v[1:]) if isinstance(v, str) and v.startswith("$") else v

    def dec(v):
        return Decimal(v) if v not in (None, "") else Decimal(0)

    for op in scenario["ops"]:
        res: Dict[str, Any] = {"op": op["op"]}
        try:
            if op["op"] == "post":
                t = op["trans"]
                rec = None
                if t.get("recurring"):
                    r = t["recurring"]
                    rec = Recurring(
                        startdate=parse_date(r["startdate"]), repeat=r["repeat"], unit=r["unit"],
                        howmany=r["howmany"], reference=r.get("reference"),
                        description=r.get("description"), payment=r.get("payment", False),
                        nextdate=parse_date(r.get("nextdate")),
                        email=[tuple(x) for x in r.get("email", [])],
                        print=[tuple(x) for x in r.get("print", [])], message=r.get("message"))
                trans = GLTransaction(
                    id=resolve(t.get("id")),
                    transdate=parse_date(t.get("transdate")),
                    reference=t.get("reference"), description=t.get("description"),
                    notes=t.get("notes"), department_id=t.get("department_id"),
                    currency=t.get("currency"),
                    exchangerate=dec(t.get("exchangerate") or "1"),
                    pending=bool(t.get("pending")), batch_id=t.get("batch_id"),
                    recurring=rec,
                    references=[Reference(**r) for r in t.get("references", [])],
                    lines=[JournalLine(
                        accno=ln["accno"], debit=dec(ln.get("debit")), credit=dec(ln.get("credit")),
                        source=ln.get("source") or None, memo=ln.get("memo") or None,
                        project_id=ln.get("project_id"), cleared=parse_date(ln.get("cleared")),
                        fx_transaction=bool(ln.get("fx_transaction")))
                        for ln in t["lines"]],
                )
                tid = gl.post_transaction(trans)
                if op.get("save_as"):
                    saved[op["save_as"]] = tid
                res["id"] = tid
            elif op["op"] == "delete":
                gl.delete_transaction(resolve(op["id"]), batch_id=op.get("batch_id"))
            elif op["op"] == "get":
                view = gl.get_transaction(resolve(op["id"]), lock=True)
                h = dict(view.header)
                res["header"] = h
                res["fxadj"] = view.fxadj
                res["lines"] = view.lines
                res["recurring"] = recurring_string(view.recurring) if view.recurring else None
                res["references"] = view.references
                res["readonly"] = view.readonly
            elif op["op"] == "list":
                s = dict(op.get("search", {}))
                for k in ("datefrom", "dateto"):
                    if k in s:
                        s[k] = parse_date(s[k])
                for k in ("amountfrom", "amountto"):
                    if k in s:
                        s[k] = Decimal(s[k])
                for k in ("month", "year", "interval"):
                    if k in s:
                        s[k] = int(s[k])
                out = gl.list_transactions(TransactionSearch(**s))
                res["rows"] = out.rows
                res["balance"] = out.balance
                res["datefrom"], res["dateto"] = out.datefrom, out.dateto
            elif op["op"] == "gl_screen":
                s = dict(op.get("search", {}))
                for k in ("datefrom", "dateto"):
                    if k in s:
                        s[k] = parse_date(s[k])
                for k in ("amountfrom", "amountto"):
                    if k in s:
                        s[k] = Decimal(s[k])
                for k in ("month", "year", "interval"):
                    if k in s:
                        s[k] = int(s[k])
                s["l_subtotal"] = bool(s.get("l_subtotal"))
                s.setdefault("sort", "transdate")      # gl.pl transactions: sort ||= transdate
                out = gl.list_transactions(TransactionSearch(**s))
                res["screen"] = gl_rows_python(out, cfg.numberformat)
            elif op["op"] == "accounts":
                res["rows"] = ca.all_accounts()
            elif op["op"] == "account_tx":
                a = dict(op.get("args", {}))
                for k in ("month", "year"):
                    if k in a:
                        a[k] = int(a[k])
                accno = a.pop("accno", None)
                out = ca.account_transactions(accno, **a)
                res["rows"] = out.rows
                res["balance"] = out.balance
            elif op["op"] == "account_screen":
                a = dict(op.get("args", {}))
                for k in ("month", "year"):
                    if k in a:
                        a[k] = int(a[k])
                a["l_subtotal"] = bool(a.get("l_subtotal"))
                accno = a.pop("accno", None)
                out = ca.account_transactions(accno, **a)
                res["screen"] = account_rows_python(out, cfg.numberformat)
            elif op["op"] == "sql":
                conn.execute(op["sql"])
            elif op["op"] in REPORTS:
                a = dict(op.get("args", {}))
                for k in ("all_accounts", "previousyear", "reversedisplay", "l_heading", "l_subtotal"):
                    if k in a:
                        a[k] = bool(a[k])
                for k in ("year", "month", "interval", "fromyear", "frommonth", "toyear", "tomonth",
                          "decimalplaces"):
                    if k in a:
                        a[k] = int(a[k])
                rep = getattr(Reports(conn, cfg), op["op"])(**a)
                res["screen"] = report_rows_python(op["op"], rep, cfg.numberformat)
                if op["op"] == "trial_balance":
                    res["rows"] = rep.rows
            elif op["op"] in ARAP_REPORTS:
                a = dict(op.get("args", {}))
                rp = Reports(conn, cfg)
                nf = cfg.numberformat
                prec = 2

                def fmt(v):
                    return format_amount(v, nf, prec, "&nbsp;").replace("&nbsp;", "").strip()
                for k in ("summary", "overdue", "nontaxable", "l_subtotal", "fx_transaction"):
                    if k in a:
                        a[k] = bool(a[k])
                if op["op"] == "aging":
                    rep = rp.aging(a.pop("arap", "ar"), **a)
                    res["rows"] = [dict(r, _python=True) for r in rep.rows]
                    res["screen"] = [[d.kind, [x for x in (fmt(v) for v in d.values) if x]]
                                     for d in rep.display]
                elif op["op"] == "arap_list":
                    from sqlledger.arap_list import ArapSearch
                    vc = a.pop("vc")
                    for k in ("transdatefrom", "transdateto"):
                        if a.get(k):
                            a[k] = parse_date(a[k])
                    for k in ("summary", "outstanding", "open", "closed", "paidlate", "paidearly", "onhold",
                              "l_memo", "l_curr", "l_subtotal"):
                        if k in a:
                            a[k] = bool(a[k])
                    a.setdefault("summary", False)
                    rep = ArapLedger(conn, cfg).list_transactions(vc, ArapSearch(**a))
                    res["rows"] = rep.rows
                    res["summary"] = 1 if (rep.search.summary or rep.search.outstanding) else 0
                    res["sort"] = rep.search.sort
                    res["screen"] = [[d.kind, [x for x in (fmt(v) for v in d.values) if x]]
                                     for d in rep.display]
                    # zelfde scherm met de typefout van aa.pl subtotal ({mount}): openstaand per
                    # valuta in een subtotaal = -betaald (README 36)
                    alt = []
                    for d in rep.display:
                        vals = list(d.values)
                        if d.kind == "subtotal":
                            for j, c in enumerate(rep.columns):
                                if c.endswith("_due"):
                                    paid = vals[rep.columns.index(c[:-4] + "_paid")]
                                    vals[j] = -(paid or 0)
                        alt.append([d.kind, [x for x in (fmt(v) for v in vals) if x]])
                    if alt != res["screen"]:
                        res["screen_perltypo"] = alt
                elif op["op"] == "reminder":
                    rep = rp.reminders(**{k: v for k, v in a.items()})
                    res["rows"] = rep.rows
                    res["screen"] = [["row", [x for x in [format_amount(r["due"] / r["exchangerate"], nf, prec)]
                                              if x]] for r in rep.rows]
                elif op["op"] == "tax_report":
                    rep = rp.tax_report(a.pop("db", "ar"), **a)
                    res["rows"] = rep.rows
                    res["screen"] = [[d.kind, [x for x in (fmt(v) for v in d.values) if x]]
                                     for d in rep.display]
                else:
                    rep = rp.payments(a.pop("db", "ar"), **a)
                    res["accounts"] = rep.accounts
                    res["screen"] = [[d.kind, [x for x in (fmt(v) for v in d.values) if x]]
                                     for d in rep.display]
            elif op["op"] == "yearend":
                a = dict(op["args"])
                tid = Reports(conn, cfg).post_yearend(a.pop("todate"), a.pop("accno"), **a)
                res["id"] = tid
            elif op["op"] == "save_level":
                Reports(conn, cfg).save_reminder_levels({int(resolve(k)): v for k, v in op["levels"].items()})
            elif op["op"] == "arap_post":
                lg, t = arap_ui_new(conn, cfg, op["args"])
                tid = lg.post_transaction(t)
                res["id"] = tid
                if op.get("save_as"):
                    saved[op["save_as"]] = tid
            elif op["op"] == "arap_repost":
                a = op["args"]
                res["id"] = arap_repost_py(conn, cfg, resolve(a["id"]), a["vc"])
            elif op["op"] == "cp_post":
                res.update(cp_ui_web(conn, cfg, op["args"], resolve))
            elif op["op"] == "cp_payments":
                res.update(cp_payments_web(conn, cfg, op["args"]))
            elif op["op"] == "aa_ui":
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                res.update(aa_ui_py(conn, cfg, a))
                if op.get("save_as") and res.get("id"):
                    saved[op["save_as"]] = res["id"]
            elif op["op"] == "pe_so":      # pe (verkooporders uit project, fase Uren U-2)
                res.update(pe_so_py(conn, cfg, op["args"]))
            elif op["op"] == "jc_ui":      # jc (tijdkaarten, fase Uren)
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                res.update(jc_ui_py(conn, cfg, a))
                if op.get("save_as") and res.get("id"):
                    saved[op["save_as"]] = res["id"]
            elif op["op"] == "im_ui":      # im (import, fase 4 stap 7)
                r = im_ui_py(conn, cfg, op["args"])
                if r.get("error"):
                    raise LedgerError(r["error"])
                res.update(r)
            elif op["op"] == "ic_ui":      # ic (artikelen, fase 3)
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                res.update(ic_ui_py(conn, cfg, a))
                if op.get("save_as") and res.get("id"):
                    saved[op["save_as"]] = res["id"]
            elif op["op"] == "is_ui":      # is/ir (facturen met artikelen, fase 3)
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                res.update(is_ui_py(conn, cfg, a))
                if op.get("save_as") and res.get("id"):
                    saved[op["save_as"]] = res["id"]
            elif op["op"] == "oe_ui":      # oe (orders en offertes, fase 3)
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                res.update(oe_ui_py(conn, cfg, a))
                if op.get("save_as") and res.get("id"):
                    saved[op["save_as"]] = res["id"]
            elif op["op"] == "ic_stock":   # ic: assembleren (fase 3)
                res.update(ic_stock_py(conn, cfg, {**op["args"], "id": resolve(op["args"]["id"])}))
            elif op["op"] == "ct_ui":      # ct (relaties, fase 2)
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                res.update(ct_ui_py(conn, cfg, a))
                if op.get("save_as") and res.get("id"):
                    saved[op["save_as"]] = res["id"]
            elif op["op"] == "am_ui":          # blok "am": beheer
                res.update(am_ui_py(conn, cfg, dict(op["args"])))
            elif op["op"] == "gl_ui":
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                res.update(gl_ui_py(conn, cfg, a))
                if op.get("save_as") and res.get("id"):
                    saved[op["save_as"]] = res["id"]
            # ------------------------------------------------------------ recurring (begin)
            elif op["op"] == "recurring_ui":
                a = dict(op["args"])
                if a.get("id"):
                    a["id"] = resolve(a["id"])
                a["ids"] = [resolve(i) for i in a.get("ids") or []]
                res.update(recurring_ui_py(conn, cfg, a))
            # ------------------------------------------------------------ recurring (einde)
            elif op["op"] == "names":
                a = dict(op["args"])
                lg = ArapLedger(conn, cfg)
                td = parse_date(a["transdate"]) if a.get("transdate") else None
                if a.get("list"):
                    res["rows"] = lg.all_names(a["vc"], transdate=td, open_invoices=bool(a.get("open_invoices")),
                                               include_id=a.get("include_id"))
                else:
                    res["rows"] = lg.find_names(a["vc"], name=a.get("name"), number=a.get("number"), transdate=td)
                    nbn = conn.execute("SELECT fldvalue FROM defaults WHERE fldname = 'namesbynumber'").fetchone()
                    res["by"] = f"{a['vc']}number" if (a.get("number") or (nbn and nbn[0])) else "name"
            elif op["op"] in ("cp_void", "cp_reissue"):
                # via de webcode (scherm Betalingen corrigeren): betaling zoeken op rekening en kenmerk,
                # aanvinken, storneren / opnieuw uitgeven (Perl: op de datum van vandaag)
                from sqlledger_web import routes_cp as rc
                from sqlledger_web.reportdefs import Lib
                a = op["args"]
                lib = Lib(conn, cfg)
                rows = [r for r in rc._register_rows(lib, a["vc"], {
                    "rekening": a["accno"], "relatie": "", "kenmerk_van": a["source"], "kenmerk_tot": a["source"],
                    "van": "", "tot": "", "weergave": "exclude"}) if r["can"] and r["source"] == a["source"]]
                if not rows:
                    raise LedgerError(f"Payment {a['source']} not found")
                src = a.get("new_source")
                for r in rows:
                    if op["op"] == "cp_void":
                        lib.payments.void_payment(a["vc"], r["accno"], r["source"], trans_ids=r["trans_ids"],
                                                  datepaid=r["datepaid"], voiddate=date.today())
                    else:
                        lib.payments.reissue_payment(a["vc"], r["accno"], r["source"], src, new_date=date.today(),
                                                     trans_ids=r["trans_ids"], datepaid=r["datepaid"])
            elif op["op"] == "cp_pay_all":
                a = op["args"]
                errs = {}
                groups = conn.execute(a["sql"]).fetchall()
                for vc_id, curr, ids in groups:
                    args = {**a, "vc_id": vc_id, "currency": curr,
                            "exchangerate": (a.get("exchangerates") or {}).get(curr),
                            "rows": [{"id": int(i)} for i in ids.split(",")]}
                    try:
                        cp_ui_web(conn, cfg, args, resolve)
                    except (LedgerError, ValueError) as exc:
                        errs[f"{vc_id}/{curr}"] = f"{type(exc).__name__}: {exc}"
                res["count"] = len(groups)
                res["errors"] = errs
            elif op["op"] == "arap_get":
                a = op["args"]
                res["trans"] = ArapLedger(conn, cfg).get_transaction(resolve(a["id"]), a["vc"])
            elif op["op"] == "arap_delete":
                ArapLedger(conn, cfg).delete_transaction(resolve(op["id"]), op["vc"])
            elif op["op"] == "arap_repost_all":
                a = op["args"]
                ids = [r[0] for r in conn.execute(a["sql"]).fetchall()]
                errs = {}
                for i in ids:
                    try:
                        arap_repost_py(conn, cfg, i, a["vc"])
                    except (LedgerError, ValueError) as exc:
                        errs[str(i)] = f"{type(exc).__name__}: {exc}"
                res["count"] = len(ids)
                res["errors"] = errs
            elif op["op"] == "rc_screen":           # rc (afletteren, fase 2)
                res.update(rc_screen_py(conn, cfg, op["args"]))
            elif op["op"] == "rc_ui":
                res.update(rc_ui_web(conn, cfg, op["args"]))
            elif op["op"] == "amounts":
                nf = cfg.numberformat
                res["parse"] = [parse_amount(x, nf) for x in op.get("parse", [])]
                res["format"] = [format_amount(Decimal(a), nf, p, d) for a, p, d in op.get("format", [])]
                res["round"] = [round_amount(Decimal(a), p) for a, p in op.get("round", [])]
        except (LedgerError, ValueError) as exc:
            res["error"] = f"{type(exc).__name__}: {exc}"
        results.append(res)
    return results


# ------------------------------------------------------------------ database

class _SafeDateLoader(psycopg.adapt.Loader):
    """Datum, of de tekst als Python hem niet kan bevatten (administratie A: leverdatum jaar 20121)."""

    def load(self, data):
        text = bytes(data).decode()
        try:
            return date.fromisoformat(text)
        except ValueError:
            return text


def dump(conn: psycopg.Connection) -> Dict[str, List[tuple]]:
    out = {}
    for table, skip in TABLES.items():
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s "
            "ORDER BY ordinal_position", (table,)).fetchall() if r[0] not in skip]
        cur = conn.cursor()
        cur.adapters.register_loader("date", _SafeDateLoader)
        rows = cur.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
        out[table] = (cols, oe_normalize(table, cols, is_normalize(conn, table, cols, ic_normalize(table, cols, ct_normalize(
            conn, table, cols, sorted((tuple(norm(v) for v in r) for r in rows), key=repr))))))
    return out


def _skey(row) -> tuple:
    """Sorteersleutel die lege waarden (None) naast tekst verdraagt."""
    return tuple((v is not None, "" if v is None else str(v)) for v in row)


def _fx_rounding_only(ra, rb) -> bool:
    """Regels gelijk op de nummering en de verdeling van afrondingscenten over de
    koersverschilregels na: die verdeling volgt de regelvolgorde (lopend
    afrondingsverschil), en die is bij oude regels zonder nummer willekeurig.
    Opgeteld moeten de koersverschillen gelijk zijn en per regel hooguit een
    cent afwijken (de cent kan naar een andere kostenregel van dezelfde boeking
    schuiven)."""
    def split(rs):
        base = sorted((r[:9] for r in rs if r[5] != "1"), key=_skey)
        fx = sorted((r[:9] for r in rs if r[5] == "1"), key=lambda r: _skey(r[:1] + r[2:]))
        return base, fx
    (ba, fa), (bb, fb) = split(ra), split(rb)
    if ba != bb or len(fa) != len(fb):
        return False
    total = Decimal(0)
    for x, y in zip(fa, fb):
        if x[:1] + x[2:] != y[:1] + y[2:] or abs(Decimal(x[1]) - Decimal(y[1])) > Decimal("0.01"):
            return False
        total += Decimal(x[1]) - Decimal(y[1])
    return total == 0


def arap_table_explanations(perl_conn, py_conn, orig_conn=None) -> Dict[str, Any]:
    """Verklaar verschillen na het opnieuw boeken van AR/AP-boekingen.

    1. Perl laat bij opnieuw opslaan btw-regels weg op rekeningen die niet (meer)
       aan de relatie gekoppeld zijn (of gesloten/verlopen tarief); Python behoudt
       ze. Verklaard als het verschil per boeking alleen uit zulke btw-regels en
       de debiteuren-/crediteurenregel bestaat.
    2. Regelnummers (acc_trans.id) bij gelijke datum: volgorde uit de database.
    """
    explained: Dict[str, set] = {"taxdrop": set(), "linenr": set(), "clearedcopy": set(), "equal": set(),
                                 "arapnull": set(), "keeporig": set(), "dcnkeep": set()}

    def raw(conn, tid):
        return [list(norm(v) for v in r) for r in conn.execute(
            "SELECT c.accno, a.amount, a.transdate, a.source, a.approved, a.fx_transaction, a.project_id, "
            "a.memo, a.cleared, a.id, c.link FROM acc_trans a LEFT JOIN chart c ON c.id = a.chart_id "
            "WHERE a.trans_id = %s", (tid,)).fetchall()]

    def pair(tid):
        """Regels van Perl en Python, met de Perl-bug 'cleared_$i' geneutraliseerd:
        create_links gebruikt dezelfde veldnaam voor betaling i en regel i, dus bij
        opnieuw opslaan krijgt regel i in Perl de afletterdatum van betaling i."""
        ra, rb = raw(perl_conn, tid), raw(py_conn, tid)
        # Perl boekt regels op een niet als AR/AP gekoppelde debiteuren-/crediteurenrekening
        # zonder rekening (chart_id NULL); Python behoudt de rekening (README 25a)
        nulls = [r for r in ra if r[0] is None]
        cand = {r[0] for r in rb if r[0] is not None and not r[10]} - {r[0] for r in ra}
        if nulls and len(cand) == 1:
            explained["arapnull"].add(tid)
            for r in nulls:
                r[0] = next(iter(cand))
                r[10] = ""
        paid = {r[9]: r[8] for r in ra if "_paid" in (r[10] or "")}
        # (ook als de datums toevallig gelijk zijn, bv. betaling 1 en een andere regel
        # op dezelfde dag afgeletterd: dan is alleen de regel met de datum anders)
        perl_amt = [r for r in ra if "_amount" in (r[10] or "")]
        if any(r[8] for r in perl_amt) and \
                sorted((tuple(r[:9]) for r in ra), key=_skey) != sorted((tuple(r[:9]) for r in rb), key=_skey) and \
                all(r[8] is None or paid.get(r[9]) == r[8] for r in perl_amt):
            # Perl: regel i krijgt de afletterdatum van betaling i, eigen datum gaat verloren
            explained["clearedcopy"].add(tid)
            for rs in (ra, rb):
                for r in rs:
                    if "_amount" in (r[10] or ""):
                        r[8] = None
        return sorted((tuple(r[:10]) for r in ra), key=_skey), sorted((tuple(r[:10]) for r in rb), key=_skey)

    for table, vc, ARAP in (("ar", "customer", "AR"), ("ap", "vendor", "AP")):
        diff_ids = set()
        for conn_a, conn_b in ((perl_conn, py_conn), (py_conn, perl_conn)):
            pass
        q = f"SELECT trans_id, string_agg(md5(ROW(chart_id, amount, transdate, source, approved, fx_transaction, project_id, memo, id, cleared)::text), ',' ORDER BY md5(ROW(chart_id, amount, transdate, source, approved, fx_transaction, project_id, memo, id, cleared)::text)) FROM acc_trans WHERE trans_id IN (SELECT id FROM {table}) GROUP BY trans_id"
        pa = dict(perl_conn.execute(q).fetchall())
        pb = dict(py_conn.execute(q).fetchall())
        diff_ids = {t for t in set(pa) | set(pb) if pa.get(t) != pb.get(t)}
        ha = {r[0]: r for r in perl_conn.execute(f"SELECT * FROM {table}").fetchall()}
        hb = {r[0]: r for r in py_conn.execute(f"SELECT * FROM {table}").fetchall()}
        diff_ids |= {t for t in set(ha) | set(hb) if ha.get(t) != hb.get(t)}
        if not diff_ids:
            continue
        # README 48: bestaand betalingskenmerk (dcn) zonder dcn-sjabloon maakt Perl bij opnieuw
        # opslaan leeg; Python behoudt het. Verklaard als de kop alleen in dcn verschilt, Perl
        # leeg heeft en Python precies het origineel.
        cols = [d.name for d in perl_conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
        dcn_i = cols.index("dcn") if "dcn" in cols else None
        if dcn_i is not None and orig_conn is not None:
            orig_dcn = dict(orig_conn.execute(f"SELECT id, dcn FROM {table} WHERE id = ANY(%s)",
                                              (sorted(diff_ids),)).fetchall())
            for tid in diff_ids:
                a, b = ha.get(tid), hb.get(tid)
                if a and b and a != b and a[dcn_i] in (None, "") and b[dcn_i] == orig_dcn.get(tid) \
                        and a[:dcn_i] + a[dcn_i + 1:] == b[:dcn_i] + b[dcn_i + 1:]:
                    ha[tid] = b
                    explained["dcnkeep"].add(tid)
        unlinked = {r[0]: set(r[1]) for r in py_conn.execute(f"""
            SELECT a.id, array_agg(DISTINCT c.accno) FROM {table} a
            JOIN acc_trans ac ON ac.trans_id = a.id JOIN chart c ON c.id = ac.chart_id
            WHERE c.link LIKE '%%{ARAP}_tax%%' AND a.id = ANY(%s) AND (c.closed OR NOT EXISTS (
                SELECT 1 FROM {vc}tax x JOIN tax t ON (t.chart_id = x.chart_id)
                WHERE x.{vc}_id = a.{vc}_id AND x.chart_id = c.id
                  AND (t.validto >= a.transdate OR t.validto IS NULL)))
            GROUP BY a.id""", (sorted(diff_ids),)).fetchall()}
        arap_accnos = {r[0] for r in py_conn.execute(
            "SELECT accno FROM chart WHERE link = %s", (ARAP,)).fetchall()}
        for tid in diff_ids:
            ra, rb = pair(tid)
            if sorted((r[:-1] for r in ra), key=_skey) == sorted((r[:-1] for r in rb), key=_skey) \
                    and ha.get(tid) == hb.get(tid):
                explained["linenr" if ra != rb else "equal"].add(tid)
                continue
            if ha.get(tid) == hb.get(tid) and _fx_rounding_only(ra, rb):
                explained["linenr"].add(tid)
                continue
            if tid in unlinked:
                extra = [r for r in rb if r[:-1] not in [x[:-1] for x in ra]]
                missing = [r for r in ra if r[:-1] not in [x[:-1] for x in rb]]
                if all(r[0] in unlinked[tid] or r[0] in arap_accnos for r in extra) and \
                        all(r[0] in arap_accnos for r in missing):
                    explained["taxdrop"].add(tid)
                    continue
            if orig_conn is not None and _keeps_original(orig_conn, py_conn, table, ARAP, tid):
                explained["keeporig"].add(tid)
    return explained


def _keeps_original(orig_conn, py_conn, table: str, ARAP: str, tid: int) -> bool:
    """README 25b/25c: Perl laat regels op een rekening zonder AR/AP-koppeling vallen
    (betalingen en kosten verdwijnen) of telt een kostenregel op een betaalrekening
    als negatieve betaling. Verklaard als het origineel zo'n regel heeft en Python
    na opnieuw opslaan per rekening hetzelfde saldo en hetzelfde factuurbedrag en
    betaalde bedrag heeft als het origineel."""
    special = orig_conn.execute(f"""
        SELECT 1 FROM acc_trans ac JOIN {table} a ON a.id = ac.trans_id LEFT JOIN chart c ON c.id = ac.chart_id
        WHERE ac.trans_id = %s AND NOT ac.fx_transaction
          AND (coalesce(c.link, '') !~ %s
               OR (c.link ~ %s AND ac.transdate = a.transdate AND ac.source IS NULL AND ac.id IS NULL))
        LIMIT 1""", (tid, ARAP, f"{ARAP}_paid")).fetchone()
    if not special:
        return False
    q = """SELECT chart_id, round(sum(amount)::numeric, 2) FROM acc_trans WHERE trans_id = %s
           GROUP BY 1 HAVING round(sum(amount)::numeric, 2) <> 0"""
    h = f"SELECT round(amount::numeric, 2), round(paid::numeric, 2) FROM {table} WHERE id = %s"
    return (set(orig_conn.execute(q, (tid,)).fetchall()) == set(py_conn.execute(q, (tid,)).fetchall())
            and orig_conn.execute(h, (tid,)).fetchone() == py_conn.execute(h, (tid,)).fetchone())


def diff_tables(a, b, skip_ids=frozenset()) -> List[Tuple[str, str]]:
    problems = []
    for table in TABLES:
        cols, ra = a[table]
        _, rb = b[table]
        if skip_ids and table in ("ar", "ap", "acc_trans", "payment"):
            key = cols.index("trans_id") if "trans_id" in cols else cols.index("id")
            ra = [r for r in ra if r[key] not in skip_ids]
            rb = [r for r in rb if r[key] not in skip_ids]
        if ra != rb:
            only_a = [r for r in ra if r not in rb]
            only_b = [r for r in rb if r not in ra]
            msg = [f"tabel {table}: kolommen {cols}"]
            msg += [f"    alleen Perl  : {r}" for r in only_a[:6]]
            msg += [f"    alleen Python: {r}" for r in only_b[:6]]
            problems.append((f"tabel {table}", "\n".join(msg)))
    return problems


def describe(i: int, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    na, nb = normalize_result(a), normalize_result(b)
    detail = []
    for k in sorted(set(na) | set(nb)):
        if na.get(k) == nb.get(k):
            continue
        va, vb = na.get(k), nb.get(k)
        if k in ("rows", "lines") and isinstance(va, list) and isinstance(vb, list) and len(va) == len(vb):
            for ra, rb in zip(va, vb):
                fields = {f: (ra.get(f), rb.get(f)) for f in ra if ra.get(f) != rb.get(f)}
                if fields:
                    ident = {f: ra.get(f) for f in ("id", "accno", "transdate", "amount")}
                    detail.append(f"    {k} {ident}: " + ", ".join(
                        f"{f} Perl={x!r} Python={y!r}" for f, (x, y) in fields.items()))
        elif isinstance(va, list) and isinstance(vb, list):
            oa = [x for x in va if x not in vb][:4]
            ob = [x for x in vb if x not in va][:4]
            detail.append(f"    {k}: alleen Perl {oa}\n    {k}: alleen Python {ob}"
                          if (oa or ob) else f"    {k}: zelfde inhoud, andere volgorde")
        else:
            detail.append(f"    {k}: Perl={va!r} Python={vb!r}")
    err = ""
    if "error" in a or "error" in b:
        err = f"\n    fout Perl={a.get('error')!r} Python={b.get('error')!r}"
    return f"op {i} ({na.get('op')}):\n" + "\n".join(detail) + err


EXPLAINED: Dict[str, int] = {}


def _explain(kind: str) -> None:
    EXPLAINED[kind] = EXPLAINED.get(kind, 0) + 1


def screen_amount(text: str, nf: str) -> Decimal:
    """Bedrag van het scherm terug naar Decimal: '-' en '' = 0, (x) = -x."""
    text = (text or "").strip()
    if text in ("", "-"):
        return Decimal(0)
    if text.startswith("(") and text.endswith(")"):
        return -parse_amount(text[1:-1], nf)
    return parse_amount(text, nf)


def reconcile(perl: Dict[str, Any], py: Dict[str, Any], nf: str) -> Dict[str, Any]:
    """Vervang in een kopie van het Perl-resultaat alleen die verschillen die
    aantoonbaar door een bekende Perl-bug komen; elk geval wordt geteld."""
    import copy
    perl = copy.deepcopy(perl)
    op = perl.get("op")

    # ca.pl: beginsaldo dat exact 0 is maar als float-som een restje heeft (bv. -8.6e-12)
    # is in Perl 'waar' -> regel '0,00'. Python telt exact (numeric) en laat hem weg (README 1).
    if op == "account_screen" and perl.get("screen") and py.get("screen") is not None:
        first = perl["screen"][0]
        if (first[0] == "row" and len(first[1]) == 1 and screen_amount(first[1][0], nf) == 0
                and perl["screen"][1:] == py["screen"]):
            perl["screen"] = perl["screen"][1:]
            _explain("mutaties: beginsaldo 0 met float-restje toont in Perl een regel 0,00")

    # rc.pl (afletteren, fase 2): volgorde binnen dezelfde datum + kenmerk is in Perl willekeurig
    if op in RC_OPS and perl.get("screen") and py.get("screen") and perl["screen"] != py["screen"]:
        for cand, why in ((py["screen"], ""), (py.get("screen_perlunion"), "union")):
            reasons = rc_explain(perl["screen"], cand, nf) if cand else None
            if reasons is not None:
                perl["screen"] = py["screen"]
                for r in reasons + ([RC_UNION] if why else []):
                    _explain(r)
                break

    # gl.pl: regels met gelijke sorteersleutel (zelfde boeking, rekening en datum) komen uit
    # PostgreSQL in willekeurige volgorde; het lopend saldo halverwege verschilt dan. Verklaard
    # als alle (sub)totalen en koppen (met het saldo aan het eind van elk blok) exact gelijk zijn
    # en binnen elk blok dezelfde bedragen staan (alleen de volgorde, dus het tussensaldo, anders).
    if op == "gl_screen" and perl.get("screen") and py.get("screen") and perl["screen"] != py["screen"]:
        a, b = perl["screen"], py["screen"]

        def blocks(sc):
            out, cur = [], []
            for kind, vals in sc:
                if kind == "row":
                    cur.append(vals)
                else:
                    out.append((cur, (kind, vals)))
                    cur = []
            out.append((cur, None))
            return out

        ba, bb = blocks(a), blocks(b)
        ok = len(a) == len(b) and len(ba) == len(bb)
        # saldokolom aanwezig: laatste bedrag van een regel is het saldo (regel zonder bedrag: alleen saldo)
        bal = any(kind == "row" and len(vals) >= 2 for kind, vals in a + b)
        key = (lambda r: tuple(r[:-1])) if bal else tuple
        for (ra, ea), (rb, eb) in zip(ba, bb) if ok else ():
            if ea != eb or sorted(map(key, ra)) != sorted(map(key, rb)):
                ok = False
                break
        if ok:
            perl["screen"] = py["screen"]
            _explain("journaal: regels met gelijke sorteersleutel in andere volgorde (tussensaldo anders, "
                     "blokken en totalen gelijk)")

    # bug 9: globale %seen in GL::transactions -> Perl laat tegenrekeningen weg die
    # al bij een andere boeking op dezelfde dag voorkwamen. Verklaard als de
    # Perl-rekeningen een echte deelverzameling zijn van die van Python.
    if op == "list" and len(perl.get("rows", [])) == len(py.get("rows", [])):
        # regels met gelijke sorteersleutel komen uit PostgreSQL in willekeurige
        # volgorde: Python-regels op inhoud koppelen in plaats van op positie
        def rkey(r):
            return (str(r.get("id")), str(r.get("accno")), str(r.get("transdate"))[:10],
                    round(Decimal(str(r.get("amount") or 0)), 6), r.get("source") or "", r.get("memo") or "")
        pool: Dict[Any, List[Dict[str, Any]]] = {}
        for b in py["rows"]:
            pool.setdefault(rkey(b), []).append(b)
        paired = [pool[rkey(a)].pop(0) if pool.get(rkey(a)) else {} for a in perl["rows"]]
        py = dict(py, rows=paired)

        # bug 9d: memoriaalboeking met meerdere regels van precies het tegenbedrag:
        # Perl kiest de tegenrekening via de hash-volgorde. Verklaard als de
        # Perl-keuze ook zo'n regel is.
        ids: Dict[str, List[Dict[str, Any]]] = {}
        for a in perl["rows"]:
            ids.setdefault(str(a.get("id")), []).append(a)
        for a, b in zip(perl["rows"], paired):
            if a.get("type") == "gl" and b and a.get("contra") != b.get("contra"):
                amt = abs(Decimal(str(a.get("amount") or 0)))
                neg = Decimal(str(a.get("amount") or 0)) < 0
                options = {str(o.get("accno")) for o in ids[str(a.get("id"))]
                           if abs(Decimal(str(o.get("amount") or 0))) == amt
                           and (Decimal(str(o.get("amount") or 0)) < 0) != neg}
                if (a.get("contra") or "").strip() in options and (b.get("contra") or "").strip() in options:
                    a["contra"], a["gifi_contra"] = b.get("contra"), b.get("gifi_contra")
                    _explain("memoriaal: meerdere passende tegenrekeningen, keuze via hash-volgorde")
        # bug 9b: bij gelijke bedragen (volledige betaling) hangt in Perl van de
        # hash-volgorde af welke AR/AP-regel de bank als tegenrekening krijgt.
        # Verklaard als na verwisselen van de twee regels de deelverzameling-regel geldt.
        by_id: Dict[str, List[int]] = {}
        for k, (a, b) in enumerate(zip(perl["rows"], py["rows"])):
            if b and a.get("type") in ("ar", "ap") and (a.get("link") or "") in ("AR", "AP"):
                by_id.setdefault(str(a.get("id")), []).append(k)

        def ok(pk: int, yk: int) -> bool:
            pa = set((perl["rows"][pk].get("contra") or "").split())
            pb = set((py["rows"][yk].get("contra") or "").split())
            return pa <= pb

        import itertools
        for idx in by_id.values():
            groups: Dict[Decimal, List[int]] = {}
            for k in idx:
                groups.setdefault(abs(Decimal(str(perl["rows"][k]["amount"]))), []).append(k)
            for grp in groups.values():
                if len(grp) < 2 or len(grp) > 6 or all(ok(k, k) for k in grp):
                    continue
                for perm in itertools.permutations(grp):
                    if all(ok(pk, yk) for pk, yk in zip(perm, grp)):
                        for k in grp:
                            a, b = perl["rows"][k], py["rows"][k]
                            a["contra"], a["gifi_contra"] = b["contra"], b.get("gifi_contra")
                        _explain("bank als tegenrekening bij factuur i.p.v. betaling, gelijke bedragen (hash-volgorde)")
                        break

        # volgorde van tegenrekeningen bij AR/AP volgt in Perl de hash-volgorde
        for a, b in zip(perl["rows"], py["rows"]):
            if a.get("type") in ("ar", "ap") and a.get("contra") != b.get("contra") \
                    and sorted((a.get("contra") or "").split()) == sorted((b.get("contra") or "").split()):
                a["contra"] = b["contra"]
                _explain("zelfde tegenrekeningen, andere volgorde (hash-volgorde)")
            if a.get("type") in ("ar", "ap") and a.get("gifi_contra") != b.get("gifi_contra") \
                    and sorted((a.get("gifi_contra") or "").split()) == sorted((b.get("gifi_contra") or "").split()):
                a["gifi_contra"] = b.get("gifi_contra")

        # bug 9c: zonder AR/AP-hoofdregel in de selectie zet Perl de bankrekening als
        # tegenrekening op $form->{GL}[undef] = de eerste regel van het overzicht.
        if perl["rows"]:
            a, b = perl["rows"][0], py["rows"][0]
            paid = {str(r.get("accno")) for r in perl["rows"] if "_paid" in (r.get("link") or "")}
            if a.get("contra") and not (b.get("contra") or "").strip() \
                    and (a.get("contra") or "").strip() in paid:
                a["contra"], a["gifi_contra"] = b.get("contra"), b.get("gifi_contra")
                _explain("tegenrekening op eerste regel via $form->{GL}[undef] (GL.pm)")
        for a, b in zip(perl["rows"], py["rows"]):
            if a.get("type") in ("ar", "ap") and str(a.get("id")) == str(b.get("id")) \
                    and str(a.get("accno")) == str(b.get("accno")):
                pa = set((a.get("contra") or "").split())
                pb = set((b.get("contra") or "").split())
                if pa != pb and pa < pb:
                    a["contra"] = b["contra"]
                    _explain("tegenrekening weggelaten door globale %seen (GL.pm)")
                ga = set((a.get("gifi_contra") or "").split())
                gb = set((b.get("gifi_contra") or "").split())
                if ga != gb and ga < gb:
                    a["gifi_contra"] = b["gifi_contra"]

    # regels met gelijke sorteerwaarde komen uit PostgreSQL in willekeurige volgorde:
    # binnen een blok (tot het volgende subtotaal/totaal) mag de volgorde verschillen
    # Een subtotaal van (bijna) nul staat als lege regel op het scherm; of die er
    # staat hangt af van float-restjes in de opgeslagen bedragen. Lege subtotaal-
    # regels dragen geen informatie en worden aan beide kanten genegeerd.
    if op in ARAP_REPORTS and perl.get("screen") is not None and py.get("screen") is not None \
            and perl["screen"] != py["screen"]:
        cleaned = [x for x in perl["screen"] if not (x[0] == "subtotal" and not x[1])]
        pycleaned = [x for x in py["screen"] if not (x[0] == "subtotal" and not x[1])]
        if len(cleaned) != len(perl["screen"]) or len(pycleaned) != len(py["screen"]):
            _explain("lege subtotaalregel (float-restje)")
            perl["screen"] = cleaned
            py = dict(py, screen=pycleaned)
    if op in ARAP_REPORTS and perl.get("screen") and py.get("screen") and perl["screen"] != py["screen"]:
        def blocks(screen):
            out, cur = [], []
            for kind, vals in screen:
                if kind == "row":
                    cur.append(json.dumps(vals))
                else:
                    out.append((sorted(cur), kind, vals))
                    cur = []
            out.append((sorted(cur), None, None))
            return out
        if blocks([list(x) for x in perl["screen"]]) == blocks([list(x) for x in py["screen"]]):
            perl["screen"] = py["screen"]
            _explain("zelfde regels binnen subtotaalblok, andere volgorde bij gelijke sorteerwaarde")
        elif op == "payments":
            # bug: RP::payments roept per betaalrekening $form->sort_order aan, die bij
            # gelijke sorteerkolom steeds de richting omdraait -> rekeningen afwisselend
            # oplopend/aflopend. Verklaard als per rekening dezelfde groepen (regels +
            # subtotaal) en hetzelfde rekeningtotaal voorkomen.
            def segments(screen):
                # per rekening: alle regels, alle subtotaalbedragen en het rekeningtotaal
                # (groepen met som 0 krijgen geen subtotaal en schuiven bij de buurgroep,
                # welke buur hangt van de sorteerrichting af)
                segs, rows, subs = [], [], []
                for kind, vals in screen:
                    if kind == "total":
                        segs.append((sorted(rows), sorted(subs), json.dumps(vals)))
                        rows, subs = [], []
                    elif kind == "row":
                        rows.append(json.dumps(vals))
                    else:
                        subs.append(json.dumps(vals))
                return segs
            if segments([list(x) for x in perl["screen"]]) == segments([list(x) for x in py["screen"]]):
                perl["screen"] = py["screen"]
                _explain("betalingen: sorteerrichting wisselt per rekening (sort_order togglet in RP::payments)")

    # bug 13 (rp.pl regel 1442): op de balans met vorig jaar telt Perl het resultaat
    # van DIT jaar op bij het laatste subtotaal van het eigen vermogen in de kolom
    # VORIG jaar. Verklaard als Perl == Python - resultaat(vorig) + resultaat(dit).
    if op == "balance_sheet" and perl.get("screen") and py.get("screen"):
        ps, ys = perl["screen"], py["screen"]
        ce = next((r for r in ys if r[0] == "Current Earnings"), None)
        if ce and len(ps) == len(ys):
            for k, (a, b) in enumerate(zip(ps, ys)):
                if a != b and a[0] == b[0] and len(a) == len(b) == len(ce):
                    fixed = list(a)
                    ok = True
                    for j in range(1, len(a)):
                        if a[j] == b[j]:
                            continue
                        if j % 2 == 0:       # kolommen: dit, vorig, dit, vorig, ...
                            expect = (screen_amount(b[j], nf) - screen_amount(ce[j], nf)
                                      + screen_amount(ce[j - 1], nf))
                            if format_amount(expect, nf, 2, "") == a[j]:
                                fixed[j] = b[j]
                                continue
                        ok = False
                    if ok:
                        ps[k] = fixed
                        _explain("subtotaal eigen vermogen vorig jaar met resultaat dit jaar (rp.pl r.1442)")
    return perl, py


def mismatching_ops(perl_variants: List[List[Dict]], py: List[Dict], nf: str = "1,000.00") -> List[int]:
    """Operaties (1-based) waarvan Python met géén van de Perl-varianten overeenkomt."""
    bad = []
    for i, b in enumerate(py):
        nb = normalize_result(b)
        ok = False
        for v in perl_variants:
            if i < len(v):
                rp, rb = reconcile(v[i], b, nf)
                if normalize_result(rp) == normalize_result(rb):
                    ok = True
                    break
                if b.get("screen_perltypo"):
                    saved = dict(EXPLAINED)
                    rp2, rb2 = reconcile(v[i], dict(b, screen=b["screen_perltypo"]), nf)
                    if normalize_result(rp2) != normalize_result(rb2):
                        EXPLAINED.clear()
                        EXPLAINED.update(saved)
                    else:
                        _explain("aa.pl subtotal: openstaand per valuta = -betaald (typefout {mount})")
                        ok = True
                        break
        if not ok:
            bad.append(i + 1)
    return bad


class Runner:
    def __init__(self, args):
        self.args = args
        self.admin = psycopg.connect(args.dsn + " dbname=postgres", autocommit=True)

    def fresh_db(self, name: str, sc: Dict[str, Any]) -> psycopg.Connection:
        self.admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        self.admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{self.args.template}"')
        conn = psycopg.connect(f"{self.args.dsn} dbname={name}", autocommit=True)
        for sql in sc.get("setup", []):
            conn.execute(sql)
        return conn

    def run_perl(self, sc: Dict[str, Any], db: str, seed: int) -> List[Dict]:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(sc, fh)
        # macOS: fork() na libpq crasht door GSSAPI/objc-initialisatie; beide uitzetten
        env = dict(os.environ, PERL_HASH_SEED=str(seed), PERL_PERTURB_KEYS="0",
                   OBJC_DISABLE_INITIALIZE_FORK_SAFETY="YES", PGGSSENCMODE="disable")
        proc = subprocess.run(["perl", str(HERE / "perl_driver.pl"), self.args.sldir, db, fh.name],
                              capture_output=True, text=True, env=env)
        os.unlink(fh.name)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        results = json.loads(proc.stdout)
        for r in results:
            if r.get("op") in ARAP_REPORTS and "html" in r:
                rows = html_class_rows(r.pop("html"))
                if r["op"] == "reminder":
                    rows = [x for x in rows if x[0] == "row"]
                r["screen"] = [list(x) for x in rows]
            if r.get("op") == "account_screen" and "html" in r:
                r["screen"] = [list(x) for x in html_class_rows(r.pop("html"))]
            if r.get("op") in RC_OPS and "html" in r:      # rc (afletteren, fase 2)
                r["screen"] = rc_html_screen(r.pop("html"))
            if r.get("op") == "gl_screen" and "html" in r:
                r["screen"] = gl_html_rows(r.pop("html"))
            if r.get("op") in REPORTS and "html" in r:
                r["screen"] = report_rows_perl(r["op"], r.pop("html"))
                if r["op"] != "trial_balance":
                    r["screen"], r["stale_subtotals"] = drop_stale_subtotals(r["screen"])
        return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sldir", required=True)
    ap.add_argument("--template", default="sl_template")
    ap.add_argument("--dsn", default="host=127.0.0.1 port=55432 user=postgres")
    ap.add_argument("--only")
    ap.add_argument("--seeds", type=int, default=8,
                    help="aantal Perl-hashseeds voor niet-deterministische Perl-uitkomsten")
    ap.add_argument("--json")
    ap.add_argument("--dbprefix", default="cmp", help="voorvoegsel van de testdatabases (parallelle runs)")
    ap.add_argument("--scenarios", default="", help="'' = standaard, 'real' = scenarios_real.py")
    args = ap.parse_args()
    SCENARIOS = importlib.import_module({"real": "scenarios_real", "c": "scenarios_c", "am": "scenarios_am", "ic": "scenarios_ic", "im": "scenarios_im", "jc": "scenarios_jc", "is": "scenarios_is", "oe": "scenarios_oe", "rh": "scenarios_rh"}.get(args.scenarios, "scenarios")).SCENARIOS

    runner = Runner(args)
    summary = []
    failures = 0
    for n, sc in enumerate(SCENARIOS, 1):
        if args.only and args.only.lower() not in sc["name"].lower():
            continue
        perl_conn = runner.fresh_db(f"{args.dbprefix}_perl_{n}", sc)
        py_conn = runner.fresh_db(f"{args.dbprefix}_py_{n}", sc)
        try:
            variants = [runner.run_perl(sc, f"{args.dbprefix}_perl_{n}", 0)]
        except RuntimeError as exc:
            print(f"[{n:2}] Perl-driver faalde: {sc['name']}\n{exc}")
            failures += 1
            continue
        py_results = run_python(py_conn, sc)
        skip, notes = frozenset(), []
        if any(o["op"] == "arap_repost_all" for o in sc["ops"]):
            # het origineel (de template) erbij: voor Perl-bugs waar Python het origineel behoudt
            orig_conn = psycopg.connect(f"{args.dsn} dbname={args.template}", autocommit=True)
            try:
                ex = arap_table_explanations(perl_conn, py_conn, orig_conn)
            finally:
                orig_conn.close()     # anders kan de template niet meer gekopieerd worden
            # alleen boekingen die na de verklaringen volledig kloppen worden overgeslagen
            skip = frozenset(str(t) for k in ("taxdrop", "linenr", "equal", "keeporig") for t in ex[k])
            if ex["dcnkeep"]:
                notes.append(f"{len(ex['dcnkeep'])}x verklaard: Perl maakt een bestaand betalingskenmerk (dcn) "
                             "zonder sjabloon leeg; Python behoudt het origineel (README 48)")
            if ex["keeporig"]:
                notes.append(f"{len(ex['keeporig'])}x verklaard: Perl laat regels op een rekening zonder "
                             "AR/AP-koppeling vallen of telt een kostenregel op een betaalrekening als betaling; "
                             "Python = origineel (README 25b/25c)")
            if ex["taxdrop"]:
                notes.append(f"{len(ex['taxdrop'])}x verklaard: Perl laat btw weg op rekening die niet aan "
                             "de relatie gekoppeld is (Python behoudt)")
            if ex["clearedcopy"]:
                notes.append(f"{len(ex['clearedcopy'])}x verklaard: Perl zet afletterdatum van betaling i op "
                             "regel i (veldnaam cleared_i gedeeld)")
            if ex["arapnull"]:
                notes.append(f"{len(ex['arapnull'])}x verklaard: Perl boekt regels op niet-gekoppelde "
                             "debiteuren-/crediteurenrekening zonder rekening (Python behoudt de rekening)")
            if ex["linenr"]:
                notes.append(f"{len(ex['linenr'])}x verklaard: regelnummers bij gelijke datum (volgorde database; "
                             "bij valuta schuift daardoor soms een afrondingscent tussen koersverschilregels)")
        with extra_tables(sc, TABLES):        # blok "am": beheertabellen erbij
            da, db = dump(perl_conn), dump(py_conn)
            n_swap = oe_remap(da, db)              # blok "oe": nabestelling en factuur in andere volgorde (O4)
            if n_swap:
                notes.append(f"{n_swap} id's omgenummerd: Perl maakt de nabestelling vóór de factuur, Python pas bij "
                             "het boeken van de factuur (O4); inhoud verder vergeleken")
            n_cogs = explain_cogs(da, db)          # blok "is": kostprijs op een andere datum (F3)
            if n_cogs:
                notes.append(f"{n_cogs}x verklaard: kostprijs per boeking en rekening gelijk, Perl boekt bij opnieuw "
                             "opslaan/latere inkoop terug en opnieuw op een andere datum (README fase 3 F3)")
            problems = diff_tables(da, db, skip)
        perl_conn.close()
        py_conn.close()

        nf = sc["config"].get("numberformat", "1,000.00")
        EXPLAINED.clear()
        bad = mismatching_ops(variants, py_results, nf)
        nondeterministic = []
        if bad:
            for seed in range(1, args.seeds):
                conn = runner.fresh_db(f"{args.dbprefix}_perl_{n}_s", sc)
                conn.close()
                variants.append(runner.run_perl(sc, f"{args.dbprefix}_perl_{n}_s", seed))
            EXPLAINED.clear()
            still_bad = mismatching_ops(variants, py_results, nf)
            nondeterministic = [i for i in bad if i not in still_bad]
            bad = still_bad
        problems += [(f"op {i}", describe(i, *reconcile(variants[0][i - 1], py_results[i - 1], nf)))
                     for i in bad]

        expected = sc.get("expect_diff", {})
        unexpected = [p for p in problems if p[0] not in expected and "*" not in expected]
        if not problems:
            status = "OK"
        elif unexpected:
            status = "VERSCHIL"
            failures += 1
        else:
            status = "VERWACHT VERSCHIL"
        print(f"[{n:2}] {status:17} {sc['name']}")
        for key, text in problems:
            reason = expected.get(key) or expected.get("*")
            print("     " + text.replace("\n", "\n     "))
            if reason:
                print(f"     -> verklaring: {reason}")
        for n_ in notes:
            print(f"     ({n_})")
        for kind, cnt in EXPLAINED.items():
            print(f"     ({cnt}x verklaard: {kind})")
        stale = sum(r.get("stale_subtotals", 0) for r in variants[0])
        if stale:
            print(f"     ({stale} verouderde Perl-subtotaalregel(s) gefilterd, zie drop_stale_subtotals)")
        if nondeterministic:
            print(f"     (op {', '.join(map(str, nondeterministic))}: Perl-uitkomst hangt af van "
                  f"hash-volgorde; Python komt overeen met een van de {len(variants)} Perl-runs)")
        errs = [(i + 1, r.get("error"), p.get("error")) for i, (r, p) in
                enumerate(zip(variants[0], py_results)) if r.get("error") or p.get("error")]
        for i, e1, e2 in errs:
            print(f"     op {i}: fout Perl={e1!r} | Python={e2!r}")
        summary.append({"scenario": sc["name"], "status": status,
                        "problems": [t for _, t in problems], "nondeterministic_ops": nondeterministic})

    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n{len(summary)} scenario's, {failures} met onverwachte verschillen")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
