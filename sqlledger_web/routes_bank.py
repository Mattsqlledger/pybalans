"""Bankafschrift inlezen, koppelen en boeken (fase 2, I1–I10; nieuw t.o.v. SQL-Ledger).

Schermen (menu Kas en bank, boekhouder+ en module cash):

* ``/bank/inlezen`` — CAMT.053/MT940 uploaden (max. 10 MB); bij een onbekend
  rekeningnummer kiest de gebruiker de betaalrekening en de IBAN wordt opgeslagen.
  Uitslag: saldocontrole, waarschuwingen en overgeslagen (al bekende) regels.
* ``/bank/regels`` — open regels per bankrekening met het voorstel; per regel
  uitklappen: voorstel boeken, memoriaal tegen een rekening (optioneel btw en als
  vaste regel opslaan) of negeren. "Alle zekere voorstellen boeken" (kenmerk of
  factuurnummer met precies het bedrag) na bevestiging.
* ``/bank/regel/<id>`` — één regel: facturen kiezen/aanpassen (relatie, bedragen,
  vooruitbetaling), memoriaal, koppelen aan een bestaande boeking, negeren.
* ``/bank/vaste-regels`` en ``/bank/afschriften`` — beheer en overzicht.

Alles wat geboekt wordt, gaat via de bibliotheek (``sqlledger.bankimport``); de
server rekent, het scherm werkt zonder JavaScript. Een voorstel wordt alleen
geboekt als het nog precies zo is als getoond (handtekening per regel).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.datastructures import FormData, UploadFile

from sqlledger import LedgerError
from sqlledger._db import cursor
from sqlledger.amounts import format_amount
from sqlledger.bankimport import (BankImport, GLAction, IgnoreAction, MatchAction, PayAction, Proposal, Rule,
                                  UnknownAccountError)
from sqlledger.payments import PaymentLine

from .i18n import N_, gettext as _
from .libmsg import lib_message
from .routes_arap import KIND, _amount, _lib, _vc_label, exact_label_vc
from .routes_cp import message, require_cash
from .tables import Col, Row, Table, to_csv, to_xlsx
from .reportpdf import pdf_response
from .web import Ctx, Redirect, csrf_form, ctx_company, render

router = APIRouter()

MAX_UPLOAD = 10 * 1024 * 1024
ZERO = Decimal(0)

CATEGORIES = {"a": N_("kenmerk"), "b": N_("factuurnummer"), "c": N_("rekeningnummer relatie"),
              "d": N_("bestaande boeking"), "e": N_("vaste regel"), "f": N_("naam en bedrag"),
              "g": N_("zoals eerder")}
STATUS = {"open": N_("open"), "booked": N_("geboekt"), "matched": N_("gekoppeld"), "ignored": N_("genegeerd")}
VC = {"customer": N_("Klant"), "vendor": N_("Leverancier")}
#: modulenamen uit de SQL in line_page (getoond met _(b.module))
MODULE_NAMES = (N_("verkoop"), N_("inkoop"), N_("memoriaal"))


async def upload_form(request: Request) -> FormData:
    """Formulier met bestand: eerst de grootte controleren, dan CSRF."""
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > MAX_UPLOAD + 64 * 1024:
        raise HTTPException(413, _("Het bestand is te groot (maximaal 10 MB)."))
    return await csrf_form(request)


def _bi(ctx: Ctx):
    require_cash(ctx)
    conn, lib = _lib(ctx)
    return conn, lib, BankImport(conn, lib.cfg)


def _fmt(ctx: Ctx, places: int = 2):
    nf = ctx.user["numberformat"]
    return lambda v, dash="0": "" if v is None else \
        format_amount(v, nf, places, dash).replace("&nbsp;", "").strip()


def _int(text) -> Optional[int]:
    text = str(text or "").strip()
    return int(text) if text.isdigit() else None


def signature(p: Proposal) -> str:
    """Wat er geboekt wordt als de gebruiker het voorstel accepteert."""
    parts = [p.category or "", p.kind or "", str(p.vc_id or "")]
    parts += [f"{a.trans_id}:{a.paid}" for a in p.allocations]
    for g in (p.matches or ([p.match] if p.match else [])):
        parts.append(f"{g['trans_id']}:{g['transdate']}:{g['source'] or ''}")
    parts += [f"l{l['id']}" for l in p.together]
    if p.rule:
        parts.append(f"r{p.rule.get('id')}:{p.rule['accno']}:{p.rule.get('tax_accno') or ''}")
    return "|".join(parts)


def new_invoice_url(c: Dict[str, Any]) -> str:
    """VZ1: "voer eerst de factuur in" bij een relatie uit de historie zonder open post."""
    return f"/{KIND[c['vc']]['prefix']}/nieuw?" + urlencode({"relatie": c["vc_id"]})


def _tax_accounts(lib) -> List[Dict[str, Any]]:
    with cursor(lib.conn) as cur:
        cur.execute("""SELECT DISTINCT c.accno, c.description FROM chart c JOIN tax t ON (t.chart_id = c.id)
                       WHERE c.closed = '0' ORDER BY c.accno""")
        return cur.fetchall()


def _choices(lib) -> Dict[str, Any]:
    return dict(accounts=lib.gl.postable_accounts(), taxes=_tax_accounts(lib), departments=lib.gl.departments())


# ------------------------------------------------------------------ inlezen

#: soort rekening → volgorde in "Alle rekeningen" zonder historie (PB26): resultaatrekeningen eerst
_RESULT_FIRST = {"I": 0, "E": 0}


def contra_accounts(lib, bi: BankImport, bank_accno: Optional[str]) -> List[Dict[str, Any]]:
    """Keuzes voor het veld Tegenrekening (PB26): eerst "Eerder gebruikt" (de tegenrekeningen die het vaakst/laatst
    in memoriaalboekingen op deze bankrekening staan), dan alle rekeningen; zonder historie de resultaatrekeningen
    (opbrengsten en kosten) vóór de balansrekeningen. ``group`` is de kop in de zoeklijst (static/combo.js)."""
    accounts = lib.gl.postable_accounts()
    recent = [a for a in bi.recent_contra_accounts(bank_accno) if a != bank_accno]
    by_no = {a["accno"]: a for a in accounts}
    out = [dict(by_no[a], group=_("Eerder gebruikt")) for a in recent if a in by_no]
    rest = [a for a in accounts if a["accno"] != bank_accno]
    if not out:
        cats = bi.account_categories()
        rest.sort(key=lambda a: (_RESULT_FIRST.get(cats.get(a["accno"], ""), 1), a["accno"]))
    out += [dict(a, group=_("Alle rekeningen")) for a in rest]
    return out


def import_page(ctx: Ctx, bi: BankImport, *, status=200, result=None, error=None, choose=False, chosen=""):
    return render(ctx, "bank/import.html", status, accounts=bi.payment_accounts(), result=result, error=error,
                  choose=choose, chosen=chosen, amount=_fmt(ctx), max_mb=MAX_UPLOAD // (1024 * 1024))


@router.get("/bank/inlezen", name="bank_import")
def bank_import(ctx: Ctx = Depends(ctx_company)):
    conn, lib, bi = _bi(ctx)
    with conn:
        return import_page(ctx, bi)


@router.post("/bank/inlezen", name="bank_import_post")
def bank_import_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    conn, lib, bi = _bi(ctx)
    chart = str(form.get("rekening", "")).strip()
    upload = form.get("bestand")
    with conn:
        if not isinstance(upload, UploadFile) or not upload.filename:
            return import_page(ctx, bi, status=400, error=_("Kies een bestand."), chosen=chart)
        data = upload.file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            return import_page(ctx, bi, status=413, error=_("Het bestand is te groot (maximaal 10 MB)."), chosen=chart)
        filename = re.sub(r"[^\w .\-]", "_", upload.filename)[:200]
        try:
            result = bi.import_file(data, filename, chart_accno=chart or None)
        except UnknownAccountError as exc:
            return import_page(ctx, bi, status=400, choose=True, chosen=chart,
                               error=_("{error} Kies hieronder de bankrekening en kies het bestand opnieuw.").format(
                                   error=lib_message(exc)))
        except LedgerError as exc:
            return import_page(ctx, bi, status=400, error=lib_message(exc), chosen=chart)
        ctx.log("bank_imported", filename=filename, statements=[s.id for s in result.statements],
                new=result.lines_new, skipped=result.lines_skipped)
        return import_page(ctx, bi, result=result)


# ------------------------------------------------------------------ regels verwerken

def _account_or_default(bi: BankImport, accno: Optional[str]) -> Optional[str]:
    accounts = {a["accno"] for a in bi.payment_accounts()}
    if accno in accounts:
        return accno
    counts = bi.open_counts()
    return next(iter(counts), None)


def lines_page(ctx: Ctx, lib, bi: BankImport, accno: Optional[str], *, status=200, error=None, confirm=None,
               notice=None, values=None):
    props = bi.proposals(accno) if accno else []
    sure = [p for p in props if p.sure]
    # PB27: na het boeken "Voortaan zo boeken?" (vaste regel) of het rekeningnummer bij de relatie opslaan
    after = _int(ctx.request.query_params.get("regel")) if status == 200 else None
    suggestion = bi.rule_suggestion(after) if after else None
    iban_offer = bi.vc_iban_offer(after) if after and not suggestion else None
    ch = _choices(lib)
    if suggestion:
        names = {a["accno"]: a["description"] for a in ch["accounts"]}
        suggestion["accno_label"] = f"{suggestion['accno']} {names.get(suggestion['accno']) or ''}".strip()
    return render(ctx, "bank/lines.html", status, accno=accno, accounts=bi.payment_accounts(),
                  counts=bi.open_counts(), props=props, sure=sure, sig=signature, error=error, confirm=confirm,
                  cats=CATEGORIES, vcs=VC, amount=_fmt(ctx), values=values or {}, ch=ch,
                  contra=contra_accounts(lib, bi, accno) if props else [], suggestion=suggestion,
                  iban_offer=iban_offer, rule_props=bi.rule_proposals(accno) if accno else [],
                  new_invoice_url=new_invoice_url, **({"notice": notice} if notice else {}))


@router.get("/bank/regels", name="bank_lines")
def bank_lines(ctx: Ctx = Depends(ctx_company)):
    conn, lib, bi = _bi(ctx)
    q = ctx.request.query_params
    with conn:
        accno = _account_or_default(bi, q.get("rekening"))
        notice = None
        if q.get("ok") == "bulk":
            n = _int(q.get("aantal")) or 0
            notice = _("{n} zekere voorstel(len) verwerkt.").format(n=n)
        elif q.get("ok") == "line":
            notice = _("Bankregel verwerkt.")
        elif q.get("ok") == "rule":
            n = _int(q.get("aantal")) or 0
            notice = (_("Vaste regel opgeslagen. Geen andere open regel krijgt dit voorstel.") if not n else
                      _("Vaste regel opgeslagen: 1 open regel heeft nu dit voorstel.") if n == 1 else
                      _("Vaste regel opgeslagen: {n} open regels hebben nu dit voorstel.").format(n=n))
        elif q.get("ok") == "iban":
            notice = _("Rekeningnummer opgeslagen bij de relatie; volgende regels van dit rekeningnummer krijgen een voorstel.")
        return lines_page(ctx, lib, bi, accno, notice=notice)


def _gl_action(ctx: Ctx, form, suffix: str) -> GLAction:
    g = lambda k: str(form.get(k + suffix, "")).strip()  # noqa: E731
    accno = g("gl_accno").split(" ", 1)[0]
    if not accno:
        raise ValueError(_("Kies de tegenrekening."))
    return GLAction(accno=accno, tax_accno=g("gl_tax") or None, description=g("gl_desc")[:250] or None,
                    department_id=_int(g("gl_dept")))


def _save_rule_from(bi: BankImport, form, suffix: str, line_id: int, a: GLAction) -> Optional[int]:
    g = lambda k: str(form.get(k + suffix, "")).strip()  # noqa: E731
    if g("gl_rule") != "1":
        return None
    keyword = g("gl_keyword") or None
    return bi.rule_from_line(line_id, a.accno, tax_accno=a.tax_accno, keyword=keyword,
                             use_iban=g("gl_by") != "keyword", description=a.description)


@router.post("/bank/regels", name="bank_lines_post")
def bank_lines_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    conn, lib, bi = _bi(ctx)
    action = str(form.get("action", ""))
    with conn:
        accno = _account_or_default(bi, str(form.get("rekening", "")).strip())
        what, __, arg = action.partition(":")
        line_id = _int(arg)
        back = "/bank/regels?" + urlencode({"rekening": accno or ""})
        if what == "bulk":
            return lines_page(ctx, lib, bi, accno, confirm="bulk")
        if what == "bulk_confirmed":
            shown = {}
            for item in form.getlist("bulk"):
                lid, __, sig = str(item).partition("=")
                if _int(lid):
                    shown[int(lid)] = sig
            done, errors = 0, []
            for p in bi.proposals(accno, list(shown)) if shown else []:
                if not p.sure or signature(p) != shown.get(p.line["id"]):
                    errors.append(_("{date} {amount}: voorstel is gewijzigd, niet geboekt.").format(
                        date=p.line["bookdate"], amount=p.line["amount"]))
                    continue
                try:
                    res = bi.process(p.line["id"], p.action())
                    done += 1
                    ctx.log("bank_line_processed", line_id=p.line["id"], kind=res.kind, trans_ids=res.trans_ids,
                            source=res.source, bulk=True)
                except LedgerError as exc:
                    errors.append(f"{p.line['bookdate']} {p.line['amount']}: {_(message(exc))}")
            if errors:
                return lines_page(ctx, lib, bi, accno, status=409, notice=_("{n} zekere voorstel(len) verwerkt.").format(n=done),
                                  error=" ".join(errors))
            raise Redirect(back + "&" + urlencode({"ok": "bulk", "aantal": done}))
        if what not in ("accept", "gl", "ignore") or not line_id:
            raise HTTPException(400, _("Onbekende actie."))
        try:
            if what == "accept":
                p = bi.proposal(line_id)
                if not p.kind or signature(p) != str(form.get(f"sig_{line_id}", "")):
                    return lines_page(ctx, lib, bi, accno, status=409,
                                      error=_("Het voorstel is intussen gewijzigd; controleer het en boek opnieuw."))
                res = bi.process(line_id, p.action())
            elif what == "gl":
                a = _gl_action(ctx, form, f"_{line_id}")
                with conn.transaction():
                    res = bi.process(line_id, a)
                    rule = _save_rule_from(bi, form, f"_{line_id}", line_id, a)
                    if rule:
                        conn.execute("UPDATE x_bank_line SET rule_id = %s WHERE id = %s", (rule, line_id))
                        ctx.log("bank_rule_saved", rule_id=rule)
            else:
                res = bi.process(line_id, IgnoreAction(str(form.get(f"note_{line_id}", "")).strip() or None))
        except ValueError as exc:
            return lines_page(ctx, lib, bi, accno, status=400, error=lib_message(exc), values=dict(form))
        except LedgerError as exc:
            return lines_page(ctx, lib, bi, accno, status=400, error=message(exc), values=dict(form))
        ctx.log("bank_line_processed", line_id=line_id, kind=res.kind, trans_ids=res.trans_ids, source=res.source)
        raise Redirect(back + "&" + urlencode({"ok": "line", "regel": line_id}))


# ------------------------------------------------------------------ één regel

@dataclass
class PayForm:
    vc: str = ""
    vc_text: str = ""
    vc_id: Optional[int] = None
    prepay: bool = False
    checked: Dict[int, bool] = field(default_factory=dict)
    paid: Dict[int, str] = field(default_factory=dict)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    total: Decimal = ZERO          # som van de bedragen bij de posten
    bank: Decimal = ZERO           # te verdelen (teken als betaalscherm)
    rest: Decimal = ZERO


def _resolve_vc(lib, f: PayForm, line) -> Optional[str]:
    text = f.vc_text.strip()
    if f.vc_id and f.vc in VC:
        return None
    if not text or f.vc not in VC:
        f.vc_id = None
        return None
    m = re.fullmatch(r"#(\d+)", text)               # keuze uit de kandidaten (id)
    if m:
        f.vc_id = int(m.group(1))
        info = lib.arap.vc_info(f.vc, f.vc_id)
        f.vc_text = _vc_label(info)
        return None
    first = text.split(" ", 1)[0]
    hits = [r for r in lib.arap.find_names(f.vc, number=f'"{first}"') if (r["number"] or "") == first]
    if len(hits) != 1:
        hits = exact_label_vc(lib, f.vc, text) or hits
    if len(hits) != 1:
        hits = lib.arap.find_names(f.vc, name=text)
        exact = [r for r in hits if (r["name"] or "").lower() == text.lower() or _vc_label(r).lower() == text.lower()]
        hits = exact if len(exact) == 1 else hits
    if len(hits) != 1:
        f.vc_id = None
        if f.vc == "customer":
            msg = (_("Geen klant gevonden voor “{text}”.") if not hits
                   else _("Meer dan één klant gevonden voor “{text}”; typ het nummer."))
        else:
            msg = (_("Geen leverancier gevonden voor “{text}”.") if not hits
                   else _("Meer dan één leverancier gevonden voor “{text}”; typ het nummer."))
        return msg.format(text=text)
    f.vc_id = hits[0]["id"]
    f.vc_text = _vc_label(hits[0])
    return None


def recalc_pay(ctx: Ctx, lib, bi: BankImport, f: PayForm, line, errors: List[str]) -> None:
    nf = ctx.user["numberformat"]
    err = _resolve_vc(lib, f, line)
    if err:
        errors.append(err)
    f.rows = []
    f.bank = line["amount"] if f.vc == "customer" else -line["amount"]
    if not f.vc_id:
        return
    fmt = _fmt(ctx)
    for inv in bi.open_invoices(line, f.vc, f.vc_id):
        checked = f.checked.get(inv.id, False)
        paid = f.paid.get(inv.id, "")
        if checked and not paid.strip():
            paid = fmt(inv.due)
        try:
            value = _amount(paid, nf) if checked else ZERO
        except ValueError as exc:
            errors.append(lib_message(exc))
            value = ZERO
        f.rows.append(dict(inv=inv, checked=checked, paid=paid if checked else "", value=value))
    f.total = sum((r["value"] for r in f.rows), ZERO)
    f.rest = f.bank - f.total


def payform_from(form) -> PayForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    same = g("vc_text") == g("vc_shown") and g("vc") == g("vc_old")
    f = PayForm(vc=g("vc"), vc_text=g("vc_text"), vc_id=_int(g("vc_id")) if same else None,
                prepay=g("prepay") == "1")
    for k in form.keys():
        m = re.fullmatch(r"due_(\d+)", k)
        if m:
            i = int(m.group(1))
            f.checked[i] = g(f"inv_{i}") == "1"
            f.paid[i] = g(f"paid_{i}")
    return f


def payform_from_proposal(ctx: Ctx, lib, p: Proposal) -> PayForm:
    fmt = _fmt(ctx)
    f = PayForm()
    if p.kind == "payment":
        f.vc, f.vc_id = p.vc, p.vc_id
        f.vc_text = _vc_label(lib.arap.vc_info(p.vc, p.vc_id))
        f.prepay = p.prepayment > 0
        for a in p.allocations:
            f.checked[a.trans_id] = True
            f.paid[a.trans_id] = fmt(a.paid)
    elif p.candidates:
        c = p.candidates[0]
        f.vc, f.vc_id = c["vc"], c["vc_id"]
        f.vc_text = _vc_label(lib.arap.vc_info(c["vc"], c["vc_id"]))
    else:
        f.vc = "customer" if p.line["amount"] > 0 else "vendor"
    return f


def line_page(ctx: Ctx, lib, bi: BankImport, line_id: int, *, status=200, error=None, f: Optional[PayForm] = None,
              values=None, notice=None):
    lines = bi.lines(status=None, line_ids=[line_id])
    if not lines:
        raise HTTPException(404, _("Bankregel niet gevonden."))
    line = lines[0]
    p = bi.proposal(line_id) if line["status"] == "open" else None
    matches = bi.match_candidates(line_id) if line["status"] == "open" else []
    near = bi.open_bookings(line_id) if line["status"] == "open" else []
    if p and p.kind == "gl" and not values:
        # voorstel uit een vaste regel of de historie (VZ1) alvast in het memoriaal
        names = {a["accno"]: a["description"] for a in lib.gl.postable_accounts()}
        values = {"gl_accno": f"{p.rule['accno']} {names.get(p.rule['accno']) or ''}".strip(),
                  "gl_tax": p.rule.get("tax_accno") or "", "gl_desc": p.rule.get("description") or ""}
    errors: List[str] = []
    if line["status"] == "open":
        if f is None:
            f = payform_from_proposal(ctx, lib, p)
        recalc_pay(ctx, lib, bi, f, line, errors)
    if errors and not error:
        error = " ".join(errors)
    booked = []
    if line["trans_ids"]:
        with cursor(lib.conn) as cur:
            cur.execute("""SELECT id, invnumber AS ref, 'verkoop' AS module FROM ar WHERE id = ANY(%(i)s)
                           UNION ALL SELECT id, invnumber, 'inkoop' FROM ap WHERE id = ANY(%(i)s)
                           UNION ALL SELECT id, reference, 'memoriaal' FROM gl WHERE id = ANY(%(i)s)""",
                        {"i": line["trans_ids"]})
            booked = cur.fetchall()
    return render(ctx, "bank/line.html", status, line=line, p=p, f=f, matches=matches, near=near, booked=booked,
                  error=error, cats=CATEGORIES, vcs=VC, statuses=STATUS, amount=_fmt(ctx), ch=_choices(lib),
                  contra=contra_accounts(lib, bi, line["accno"]) if line["status"] == "open" else [],
                  values=values or {}, sig=signature, new_invoice_url=new_invoice_url,
                  **({"notice": notice} if notice else {}))


@router.get("/bank/regel/{line_id}", name="bank_line")
def bank_line(line_id: int, ctx: Ctx = Depends(ctx_company)):
    conn, lib, bi = _bi(ctx)
    with conn:
        return line_page(ctx, lib, bi, line_id)


@router.post("/bank/regel/{line_id}", name="bank_line_post")
def bank_line_post(line_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    conn, lib, bi = _bi(ctx)
    action = str(form.get("action", ""))
    nf = ctx.user["numberformat"]
    with conn:
        lines = bi.lines(status=None, line_ids=[line_id])
        if not lines:
            raise HTTPException(404, _("Bankregel niet gevonden."))
        line = lines[0]
        f = payform_from(form)
        if action == "update":
            return line_page(ctx, lib, bi, line_id, f=f, values=dict(form))
        try:
            if action == "pay":
                errors: List[str] = []
                recalc_pay(ctx, lib, bi, f, line, errors)
                if errors:
                    raise ValueError(" ".join(errors))
                if not f.vc_id:
                    raise ValueError(_("Kies een klant of leverancier."))
                pl = [PaymentLine(r["inv"].id, r["value"], expected_due=r["inv"].due) for r in f.rows
                      if r["checked"] and r["value"]]
                if f.rest and not (f.prepay and f.rest > 0):
                    raise ValueError(_("Het totaal bij de posten ({total}) sluit niet aan op het bankbedrag "
                                       "({bank}).").format(total=format_amount(f.total, nf, 2, '0'),
                                                           bank=format_amount(f.bank, nf, 2, '0')))
                res = bi.process(line_id, PayAction(f.vc, f.vc_id, pl, allow_prepayment=f.prepay))
            elif action == "gl":
                a = _gl_action(ctx, form, "")
                with conn.transaction():
                    res = bi.process(line_id, a)
                    rule = _save_rule_from(bi, form, "", line_id, a)
                    if rule:
                        conn.execute("UPDATE x_bank_line SET rule_id = %s WHERE id = %s", (rule, line_id))
                        ctx.log("bank_rule_saved", rule_id=rule)
            elif action == "accept":
                p = bi.proposal(line_id)
                if not p.kind or signature(p) != str(form.get("sig", "")):
                    raise ValueError(_("Het voorstel is intussen gewijzigd; controleer het en boek opnieuw."))
                res = bi.process(line_id, p.action())
            elif action == "match_multi":
                keys = {str(k) for k in form.getlist("multi")}
                chosen = [g for g in bi.open_bookings(line_id)
                          if f"{g['trans_id']}|{g['transdate']}|{g['source'] or ''}" in keys]
                if len(chosen) < 1:
                    raise ValueError(_("Kies de boekingen om samen af te letteren."))
                res = bi.process(line_id, MatchAction(chosen[0]["trans_id"], chosen[0]["transdate"], chosen[0]["source"],
                                                      more=[(g["trans_id"], g["transdate"], g["source"])
                                                            for g in chosen[1:]]))
            elif action == "match":
                key = str(form.get("match", ""))
                m = next((g for g in bi.match_candidates(line_id)
                          if f"{g['trans_id']}|{g['transdate']}|{g['source'] or ''}" == key), None)
                if not m:
                    raise ValueError(_("Kies de boeking om aan te koppelen."))
                res = bi.process(line_id, MatchAction(m["trans_id"], m["transdate"], m["source"]))
            elif action == "ignore":
                res = bi.process(line_id, IgnoreAction(str(form.get("note", "")).strip() or None))
            elif action == "reopen":
                bi.reopen(line_id)
                ctx.log("bank_line_reopened", line_id=line_id)
                raise Redirect(f"/bank/regel/{line_id}")
            else:
                raise HTTPException(400, _("Onbekende actie."))
        except ValueError as exc:
            return line_page(ctx, lib, bi, line_id, status=400, error=lib_message(exc), f=f, values=dict(form))
        except LedgerError as exc:
            return line_page(ctx, lib, bi, line_id, status=400, error=message(exc), f=f, values=dict(form))
        ctx.log("bank_line_processed", line_id=line_id, kind=res.kind, trans_ids=res.trans_ids, source=res.source)
        raise Redirect("/bank/regels?" + urlencode({"rekening": line["accno"], "ok": "line", "regel": line_id}))


# ------------------------------------------------------------------ vaste regels

def rules_page(ctx: Ctx, lib, bi: BankImport, *, status=200, error=None, values=None, notice=None):
    return render(ctx, "bank/rules.html", status, rules=bi.rules(), accounts=bi.payment_accounts(),
                  ch=_choices(lib), error=error, values=values or {}, **({"notice": notice} if notice else {}))


@router.get("/bank/vaste-regels", name="bank_rules")
def bank_rules(ctx: Ctx = Depends(ctx_company)):
    conn, lib, bi = _bi(ctx)
    with conn:
        return rules_page(ctx, lib, bi, notice=_("Opgeslagen.") if ctx.request.query_params.get("ok") == "saved"
                          else None)


@router.post("/bank/vaste-regels", name="bank_rules_post")
def bank_rules_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    conn, lib, bi = _bi(ctx)
    action = str(form.get("action", ""))
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    with conn:
        try:
            if action.startswith("delete:") and _int(action[7:]):
                bi.delete_rule(int(action[7:]))
                ctx.log("bank_rule_deleted", rule_id=int(action[7:]))
            elif action.startswith("from_line:") and _int(action[10:]):
                # PB27 "Voortaan zo boeken?": regel uit de geboekte bankregel, daarna terug naar de open regels
                line_id = int(action[10:])
                by = g("by") if g("by") in ("iban", "keyword", "both") else "keyword"
                rid = bi.rule_from_line(line_id, g("accno").split(" ", 1)[0], tax_accno=g("tax_accno") or None,
                                        keyword=g("keyword") if by != "iban" else None,
                                        use_iban=by != "keyword", description=g("description") or None)
                ctx.log("bank_rule_saved", rule_id=rid, from_line=line_id)
                back = {"rekening": g("rekening"), "ok": "rule", "aantal": bi.matching_open_lines(rid)}
                raise Redirect("/bank/regels?" + urlencode(back))
            elif action.startswith("iban_from_line:") and _int(action[15:]):
                line_id = int(action[15:])
                if bi.save_vc_iban(line_id, replace=g("replace") == "1"):
                    ctx.log("bank_vc_iban_saved", line_id=line_id, replace=g("replace") == "1")
                raise Redirect("/bank/regels?" + urlencode({"rekening": g("rekening"), "ok": "iban"}))
            elif action.startswith("suggested:") and _int(action[10:]):
                # BT3: voorgestelde vaste regel (uit de geboekte/afgeletterde regels) met één klik
                rid = bi.save_rule(Rule(accno=g("accno"), iban=g("iban") or None, direction=g("direction") or None,
                                        chart_accno=g("rekening") or None, tax_accno=g("tax_accno") or None))
                ctx.log("bank_rule_saved", rule_id=rid, suggested=True)
                back = {"rekening": g("rekening"), "ok": "rule", "aantal": bi.matching_open_lines(rid)}
                raise Redirect("/bank/regels?" + urlencode(back))
            elif action == "add":
                rid = bi.save_rule(Rule(accno=g("accno").split(" ", 1)[0], iban=g("iban") or None,
                                        keyword=g("keyword") or None, direction=g("direction") or None,
                                        chart_accno=g("chart_accno") or None, tax_accno=g("tax_accno") or None,
                                        description=g("description") or None,
                                        department_id=_int(g("department_id"))))
                ctx.log("bank_rule_saved", rule_id=rid)
            else:
                raise HTTPException(400, _("Onbekende actie."))
        except LedgerError as exc:
            if action.startswith(("from_line:", "iban_from_line:", "suggested:")):
                accno = _account_or_default(bi, g("rekening"))
                return lines_page(ctx, lib, bi, accno, status=400, error=lib_message(exc))
            return rules_page(ctx, lib, bi, status=400, error=lib_message(exc), values=dict(form))
    raise Redirect("/bank/vaste-regels?ok=saved")


# ------------------------------------------------------------------ afschriften

@router.get("/bank/afschriften", name="bank_statements")
def bank_statements(ctx: Ctx = Depends(ctx_company)):
    conn, lib, bi = _bi(ctx)
    with conn:
        return render(ctx, "bank/statements.html", statements=bi.statements(), amount=_fmt(ctx))


def line_columns() -> List[Col]:
    return [Col("bookdate", _("Boekdatum"), "date"), Col("amount", _("Bedrag"), "amount", dash="0"),
            Col("name", _("Tegenpartij")), Col("iban", _("Rekeningnummer")), Col("description", _("Omschrijving")),
            Col("reference", _("Kenmerk")), Col("status", _("Status")), Col("source", _("Geboekt met kenmerk"))]


@router.get("/bank/afschriften/{statement_id}", name="bank_statement")
def bank_statement(statement_id: int, ctx: Ctx = Depends(ctx_company)):
    conn, lib, bi = _bi(ctx)
    export = ctx.request.query_params.get("export")
    with conn:
        st = next((s for s in bi.statements() if s["id"] == statement_id), None)
        if not st:
            raise HTTPException(404, _("Afschrift niet gevonden."))
        lines = bi.lines(status=None, statement_id=statement_id)
        table = Table(line_columns(),
                      [Row("row", dict(l, status=_(STATUS.get(l["status"], l["status"])))) for l in lines],
                      _("Afschrift {number} rekening {accno}").format(
                          number=st['sequence'] or st['statement_ref'] or st['id'], accno=st['accno']))
        nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
        if export == "csv":
            return Response(to_csv([table], nf, df), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="afschrift-{statement_id}.csv"'})
        if export == "pdf":
            return pdf_response(ctx, [Table(table.columns, table.rows)], table.title, [], f"afschrift-{statement_id}")
        if export == "xlsx":
            return Response(to_xlsx([table], table.title, [], nf, df),
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="afschrift-{statement_id}.xlsx"'})
        return render(ctx, "bank/statement.html", st=st, lines=lines, statuses=STATUS, amount=_fmt(ctx))
