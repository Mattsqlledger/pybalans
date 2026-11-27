"""Inkoop > Inkoopfactuur uploaden (fase 4 stap 4e, beslispunten IU1–IU8, alles A).

Eén factuur per keer: PDF (of foto) slepen, dan staat hij naast het formulier (op de telefoon
eronder). De gebruiker kiest leverancier, factuurdatum, factuurnummer en het totaalbedrag
inclusief btw; kostenrekening, btw, vervaldatum, crediteurenrekening, valuta en afdeling stelt
``sqlledger.apupload`` voor (zichtbaar, aan te passen). "Boeken" boekt via dezelfde weg als het
gewone scherm (``routes_arap``: dezelfde controles, waarschuwing bij een dubbel factuurnummer) met
de PDF als bijlage; "Volledig scherm" opent het gewone inkoopscherm met alles ingevuld.

Btw en netto rekent de server (``split_total``); het scherm vraagt ze op bij een wijziging
(``action=calc``), zoals het gewone boekingsscherm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import LedgerError
from sqlledger._db import cursor
from sqlledger.amounts import format_amount
from sqlledger.apupload import PurchaseUpload, UploadSuggestion, split_total
from sqlledger.attachments import duplicates
from sqlledger.errors import ValidationError

from . import routes_arap as arap
from . import routes_attach as attach
from .i18n import gettext as _
from .libmsg import lib_message
from .reportdefs import Lib
from .routes_gl import RefRow, _accno
from .uploads import read_upload
from .web import Ctx, Redirect, ctx_company, render, require_role, upload_form

router = APIRouter()

NO_TAX = "geen"


@dataclass
class UploadForm:
    token: str = ""
    filename: str = ""
    kind: str = ""
    vc_text: str = ""
    vc_shown: str = ""
    vc_id: Optional[int] = None
    transdate: str = ""
    oldtransdate: str = ""
    invnumber: str = ""
    total: str = ""
    expense: str = ""
    tax_accno: str = ""          # rekening, NO_TAX of "" (nog kiezen)
    tax: str = ""
    tax_auto: str = ""           # laatst berekende btw: wijkt het veld daarvan af, dan is het zelf ingevuld
    terms: str = "0"
    duedate: str = ""
    currency: str = ""
    exchangerate: str = ""
    arap_accno: str = ""
    department_id: str = ""
    description: str = ""
    dup_ok: bool = False
    file_ok: bool = False        # "toch toevoegen": hetzelfde bestand hangt al elders (stap 6b, RB5)
    # berekend
    net: Decimal = Decimal(0)
    invtotal: Decimal = Decimal(0)

    @property
    def is_image(self) -> bool:
        return self.kind in ("png", "jpg")


def form_from_post(form: FormData) -> UploadForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    vid = g("vc_id")
    return UploadForm(token=g("token"), vc_text=g("vc_text"), vc_shown=g("vc_shown"),
                      vc_id=int(vid) if vid.isdigit() else None, transdate=g("transdate"),
                      oldtransdate=g("oldtransdate"), invnumber=g("invnumber")[:100], total=g("total"),
                      expense=g("expense"), tax_accno=g("tax_accno"), tax=g("tax"), tax_auto=g("tax_auto"),
                      terms=g("terms") or "0", duedate=g("duedate"), currency=g("currency"),
                      exchangerate=g("exchangerate"), arap_accno=g("arap_accno"),
                      department_id=g("department_id"), description=str(form.get("description", ""))[:2000],
                      dup_ok=g("dup_ok") == "1", file_ok=bool(g("token")) and g(f"dup_ok_{g('token')}") == "1")


def _fmt(v: Decimal, nf: str, precision: int) -> str:
    return format_amount(v, nf, precision).replace("&nbsp;", "").strip()


def refresh(ctx: Ctx, lib: Lib, u: UploadForm) -> Tuple[List[str], Optional[UploadSuggestion]]:
    """Leverancier bepalen, bij een nieuwe leverancier (of datum) de voorstellen invullen en
    netto/btw uitrekenen. Geeft invoerfouten en het voorstel terug."""
    nf = ctx.user["numberformat"]
    precision = lib.precision()
    errors: List[str] = []
    af = arap.ArapForm(vc="vendor", vc_text=u.vc_text, vc_shown=u.vc_shown, vc_id=u.vc_id, transdate=u.transdate)
    before = u.vc_id
    err = arap.resolve_vc(lib, af)
    if err:
        errors.append(err)
    u.vc_id, u.vc_text = af.vc_id, af.vc_text
    td = arap._date(u.transdate)
    if u.transdate and not td:
        errors.append(_("Ongeldige datum: {value}").format(value=u.transdate))
    new_vendor = bool(u.vc_id) and (u.vc_id != before or not u.vc_shown)
    if new_vendor:
        u.vc_text = arap._vc_label(lib.arap.vc_info("vendor", u.vc_id))
    u.vc_shown = u.vc_text if u.vc_id else ""
    s = PurchaseUpload(lib.conn, lib.cfg).suggest(u.vc_id, td) if u.vc_id and td else None
    defcur = arap._defcur(lib)
    if s and new_vendor:
        names = arap._account_names(lib, "vendor")
        u.expense = f"{s.expense_accno} {names.get(s.expense_accno, '')}".strip() if s.expense_accno else ""
        u.tax_accno = NO_TAX if s.tax_accno == "" else (s.tax_accno or "")
        u.tax = u.tax_auto = ""
        u.terms = str(s.terms)
        u.duedate = s.duedate.isoformat() if s.duedate else ""
        u.currency = s.currency if s.currency != defcur else ""
        u.arap_accno = s.arap_accno or ""
        u.department_id = str(s.department_id or "")
        u.exchangerate = ""
    elif s and u.transdate != u.oldtransdate:
        try:
            u.duedate = (td + timedelta(days=int(u.terms or 0))).isoformat()
        except ValueError:
            pass
        u.exchangerate = ""
    if u.currency and u.currency != defcur and td and not u.exchangerate:
        rate = lib.gl.exchange_rate(u.currency, td)
        u.exchangerate = arap._rate_text(rate, nf) if rate else ""
    if not u.arap_accno:
        acc = lib.arap.accounts("vendor")["AP"]
        u.arap_accno = acc[0]["accno"] if acc else ""
    u.oldtransdate = u.transdate

    def num(text):
        try:
            return arap._amount(text, nf)
        except ValueError as exc:
            errors.append(lib_message(exc))
            return Decimal(0)
    total = num(u.total)
    rates = s.rates if s else {}
    rate = rates.get(u.tax_accno) if u.tax_accno not in ("", NO_TAX) else None
    auto_net, auto_tax = split_total(total, rate, precision)
    manual = rate is not None and u.tax and u.tax != u.tax_auto
    if manual:
        u.net, tax = split_total(total, rate, precision, tax=num(u.tax))
    else:
        u.net, tax = auto_net, auto_tax
    u.tax_auto = _fmt(auto_tax, nf, precision) if rate is not None else ""
    u.tax = _fmt(tax, nf, precision) if rate is not None else ""
    u.invtotal = total
    return list(dict.fromkeys(errors)), s


def to_arap_form(lib: Lib, u: UploadForm, nf: str, s: Optional[UploadSuggestion]) -> "arap.ArapForm":
    """Het gewone inkoopformulier met alles ingevuld: één kostenregel (netto), btw als vast bedrag op
    de gekozen rekening, de PDF als nog niet opgeslagen bijlage."""
    precision = lib.precision()
    f = arap.ArapForm(vc="vendor", vc_id=u.vc_id, transdate=u.transdate)
    if u.vc_id:
        arap.apply_vc_defaults(lib, f, nf)
    f.vc_text = f.vc_shown = u.vc_text
    f.oldtransdate = u.transdate
    f.invnumber, f.description, f.dup_ok = u.invnumber, u.description, u.dup_ok
    f.terms = f.oldterms = u.terms or "0"
    f.duedate = f.oldduedate = u.duedate
    f.currency = f.oldcurrency = u.currency or f.currency
    f.exchangerate = u.exchangerate
    f.department_id = u.department_id
    f.arap_accno = u.arap_accno or f.arap_accno
    f.taxincluded = f.checktax = False
    names = arap._account_names(lib, "vendor")
    acc = _accno(u.expense)
    f.lines = [arap.ALine(amount=_fmt(u.net, nf, precision) if u.net else "",
                          accno=f"{acc} {names.get(acc, '')}".strip() if acc else "")]
    chosen = u.tax_accno if u.tax_accno not in ("", NO_TAX) else None
    accounts = sorted(set(s.rates if s else {}) | ({chosen} if chosen else set()))
    f.taxes = [arap.TaxRow(accno=a, amount=u.tax if a == chosen else "", calc=False) for a in accounts]
    f.refs = [RefRow(description=u.filename, token=u.token, filename=u.filename)] if u.token else []
    return f


def _required(u: UploadForm, s: Optional[UploadSuggestion], nf: str) -> List[str]:
    out = []
    if not u.token:
        out.append(_("Sleep eerst de factuur (PDF of foto) in het vak."))
    if not u.vc_id:
        out.append(_("Kies een leverancier."))
    if not u.transdate:
        out.append(_("Factuurdatum ontbreekt."))
    if not u.invnumber:
        out.append(_("Vul het factuurnummer in."))
    try:
        if not arap._amount(u.total, nf):
            out.append(_("Vul het totaalbedrag in."))
    except ValueError:
        pass                                # staat al bij de invoerfouten
    if not _accno(u.expense):
        out.append(_("Kies een kostenrekening."))
    if u.tax_accno == "" and s and s.tax_choice:
        out.append(_("Kies de btw-rekening (of geen btw)."))
    return out


def _choices(lib: Lib, u: UploadForm, s: Optional[UploadSuggestion]) -> Dict[str, Any]:
    acc = lib.arap.accounts("vendor")
    names = arap._account_names(lib, "vendor")
    arap_accounts = list(acc["AP"])
    if u.arap_accno and u.arap_accno not in {a["accno"] for a in arap_accounts}:
        arap_accounts.append({"accno": u.arap_accno, "description": names.get(u.arap_accno, "")})
    taxes = []
    for a in sorted(set(s.rates if s else {}) | ({u.tax_accno} if u.tax_accno not in ("", NO_TAX) else set())):
        r = s.rates.get(a) if s else None
        pct = "" if r is None else format((r * 100).normalize(), "f").replace(".", ",") + "%"
        taxes.append({"accno": a, "description": names.get(a, ""), "pct": pct})
    return dict(expense_accounts=acc["AP_amount"], arap_accounts=arap_accounts, taxes=taxes,
                departments=lib.gl.departments("vendor"), defaultcurrency=arap._defcur(lib))


def _page(ctx: Ctx, lib: Lib, u: UploadForm, s: Optional[UploadSuggestion], *, status: int = 200,
          error: Optional[str] = None, **kw):
    pf = attach.store(ctx).get(u.token, ctx.company["id"], attach.owner(ctx)) if u.token else None
    dups = None
    if pf:
        u.filename, u.kind = pf.filename, pf.kind
        with cursor(lib.conn) as cur:          # hetzelfde bestand al elders (stap 6b, RB5)
            dups = duplicates(cur, ctx.user_config().short_login, pf.sha256) or None
    else:
        u.token = u.filename = u.kind = ""
    return render(ctx, "arap/upload.html", status, u=u, s=s, ch=_choices(lib, u, s), error=error, dups=dups,
                  NO_TAX=NO_TAX, expense_accno=_accno(u.expense),
                  amount=lambda v: _fmt(v, ctx.user["numberformat"], lib.precision()), **kw)


def _posted(lib: Lib, trans_id: str) -> Optional[Dict[str, Any]]:
    if not trans_id.isdigit():
        return None
    with cursor(lib.conn) as cur:
        cur.execute("""SELECT a.id, a.invnumber, a.amount, a.curr, v.name FROM ap a JOIN vendor v ON v.id = a.vendor_id
                       WHERE a.id = %s""", (int(trans_id),))
        return cur.fetchone()


def _require(ctx: Ctx) -> None:
    arap.require_module(ctx, "vendor")
    require_role(ctx, "bookkeeper")


@router.get("/inkoop/uploaden")
def upload_page(ctx: Ctx = Depends(ctx_company)):
    _require(ctx)
    conn, lib = arap._lib(ctx)
    with conn:
        u = UploadForm(transdate=date.today().isoformat(), oldtransdate=date.today().isoformat())
        q = ctx.request.query_params
        posted = _posted(lib, q.get("geboekt", "")) if q.get("ok") == "upload_posted" else None
        return _page(ctx, lib, u, None, posted=posted)


@router.post("/inkoop/uploaden")
def upload_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    _require(ctx)
    u = form_from_post(form)
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    files = attach.store(ctx)
    upload_error = None
    if action == "upload":
        limit = attach.upload_limit(ctx)
        up = read_upload(form, "bestand", limit)
        if not up:
            upload_error = _("Kies een bestand.")
        else:
            try:
                pf = files.save(ctx.company["id"], attach.owner(ctx), *up, limit=limit)
            except ValidationError as exc:
                upload_error = lib_message(exc)
            else:
                files.discard(u.token)             # vervangt een eerder bestand
                u.token = pf.token
    elif action == "discard":
        files.discard(u.token)
        u.token = ""
    pf = files.get(u.token, ctx.company["id"], attach.owner(ctx)) if u.token else None
    u.token, u.filename, u.kind = (pf.token, pf.filename, pf.kind) if pf else ("", "", "")

    conn, lib = arap._lib(ctx)
    with conn:
        errors, s = refresh(ctx, lib, u)
        if action == "calc":
            return _page(ctx, lib, u, s)
        if upload_error:
            return _page(ctx, lib, u, s, status=400, error=upload_error)
        if action in ("upload", "discard", "update"):
            return _page(ctx, lib, u, s, error=" ".join(errors) or None)
        if action == "full":
            f = to_arap_form(lib, u, nf, s)
            attach.check_pending(ctx, f.refs, form)
            attach.mark_duplicates(ctx, lib.conn, f.refs)
            calc_errors = arap.recalc(lib, f, nf, lib.precision(), arap._defcur(lib))
            return arap._page(ctx, lib, f, error=" ".join(errors + calc_errors) or None)
        if action != "post":
            raise HTTPException(400, _("Onbekende actie."))
        problems = errors + _required(u, s, nf)
        if problems:
            return _page(ctx, lib, u, s, status=400, error=" ".join(dict.fromkeys(problems)))
        if not u.dup_ok:
            dups = lib.arap.same_invnumber("vendor", u.vc_id, u.invnumber)
            if dups:
                return _page(ctx, lib, u, s, confirm="duplicate", duplicates=dups)
        f = to_arap_form(lib, u, nf, s)
        attach.check_pending(ctx, f.refs, form)
        attach.mark_duplicates(ctx, lib.conn, f.refs)
        calc_errors = arap.recalc(lib, f, nf, lib.precision(), arap._defcur(lib))
        if calc_errors:
            return _page(ctx, lib, u, s, status=400, error=" ".join(calc_errors))
        missing = attach.resolve_pending(ctx, f.refs)
        if missing:
            return _page(ctx, lib, u, s, status=400, error=missing)
        try:
            trans_id = arap.do_post(lib, f, nf)
        except ValueError as exc:
            return _page(ctx, lib, u, s, status=400, error=lib_message(exc))
        except LedgerError as exc:
            return _page(ctx, lib, u, s, status=400, error=arap.message(exc))
    attach.discard_pending(ctx, f.refs)
    ctx.log("arap_posted", id=trans_id, module="ap", invnumber=u.invnumber, new=True, type="transaction",
            upload=True)
    raise Redirect(f"/inkoop/uploaden?ok=upload_posted&geboekt={trans_id}")

