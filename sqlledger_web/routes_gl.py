"""Memoriaal (stap 4): boeking invoeren, wijzigen, verwijderen, koersverschilboeking
en de jaarafsluiting.

Het scherm volgt gl.pl: kopgegevens, regels met rekening/debet/credit/kenmerk/memo
(en project, FX), knoppen Opslaan, Opslaan als nieuw, Nieuw nummer, Verwijderen.
Rekenen en controleren gebeurt in de bibliotheek; hier wordt alleen ingelezen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from starlette.datastructures import FormData

from sqlledger import GLTransaction, JournalLine, LedgerError, Reference
from sqlledger._db import currencies, cursor, default_currency, get_defaults, get_yearend
from sqlledger.amounts import format_amount, parse_amount
from sqlledger.dates import format_date

from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .reportdefs import Lib
from . import routes_attach as attach
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role, upload_form

router = APIRouter()

#: meldingen uit de bibliotheek (Perl-teksten) in het Nederlands
MESSAGES = {
    "Transaction Date missing!": N_("Boekdatum ontbreekt."),
    "Cannot post transaction for a closed period!": N_("Deze datum valt in een afgesloten periode."),
    "Cannot change a transaction in a closed period!":
        N_("Een boeking in een afgesloten periode kan niet gewijzigd worden."),
    "Cannot delete a transaction in a closed period!":
        N_("Een boeking in een afgesloten periode kan niet verwijderd worden."),
    "Transaction is locked (revtrans)": N_("Boekingen kunnen niet gewijzigd worden (instelling: alleen tegenboeken)."),
    "Transaction has no lines": N_("De boeking heeft geen regels."),
    "Cannot post transaction with a debit and credit entry for the same account!":
        N_("Een regel kan niet zowel debet als credit hebben."),
    "Debit and credit amounts must not be negative": N_("Debet en credit mogen niet negatief zijn."),
    "Out of balance transaction!": N_("Debet en credit zijn niet gelijk."),
    "Exchange rate missing!": N_("Vul de koers in."),
    "Nothing to do!": N_("Er is niets af te sluiten: geen opbrengsten of kosten."),
    "Yearend date missing!": N_("Datum ontbreekt."),
}

def message(exc: Exception) -> str:
    text = str(exc)
    if text in MESSAGES:
        return _(MESSAGES[text])
    return lib_message(exc)


def require_gl(ctx: Ctx, fx: bool = False) -> None:
    modules = ctx.company["modules"] or []
    if "gl" not in modules and not (fx and "cash" in modules):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


# ------------------------------------------------------------------ formuliermodel

@dataclass
class Line:
    accno: str = ""
    debit: str = ""
    credit: str = ""
    source: str = ""
    memo: str = ""
    project_id: str = ""
    fx: bool = False
    cleared: str = ""


@dataclass
class RefRow:
    description: str = ""
    code: str = ""
    archive_id: str = ""
    folder: str = ""
    confidential: bool = False
    filename: str = ""
    token: str = ""             # bijlage die nog niet opgeslagen is (stap 4e, routes_attach)
    data: Optional[bytes] = field(default=None, repr=False)
    dups: Any = None            # attachments.Duplicates: hetzelfde bestand hangt al elders (stap 6b, RB5)
    dup_ok: bool = False        # "toch toevoegen" aangevinkt

    @property
    def is_file(self) -> bool:
        return bool(self.archive_id or self.token)


@dataclass
class GLForm:
    id: Optional[int] = None
    fxadj: bool = False
    reference: str = ""
    transdate: str = ""
    department_id: str = ""
    currency: str = ""
    exchangerate: str = ""
    description: str = ""
    notes: str = ""
    lines: List[Line] = field(default_factory=list)
    refs: List[RefRow] = field(default_factory=list)
    vat_period: str = ""        # btw-boeking van dit tijdvak (routes_vat, BA6): na opslaan vastleggen


EMPTY_ROWS = {False: 4, True: 3}


def _pad(f: GLForm) -> GLForm:
    """Altijd minstens één lege regel onderaan (M2)."""
    while not f.lines or _filled(f.lines[-1]) or len(f.lines) < EMPTY_ROWS[f.fxadj]:
        f.lines.append(Line())
        if len(f.lines) >= 200:
            break
    if not f.refs or f.refs[-1].description or f.refs[-1].is_file:
        f.refs.append(RefRow())
    return f


def _filled(ln: Line) -> bool:
    return bool(ln.debit.strip() or ln.credit.strip() or ln.source.strip() or ln.memo.strip() or ln.project_id)


def _accno(text: str) -> str:
    """Rekeningnummer uit 'nummer omschrijving' (zoekveld, M5)."""
    return (text or "").strip().split(" ", 1)[0].split("--", 1)[0]


def form_from_post(form: FormData) -> GLForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    f = GLForm(id=int(g("id")) if g("id").isdigit() else None, fxadj=g("fxadj") == "1",
               reference=g("reference")[:100], transdate=g("transdate"), department_id=g("department_id"),
               currency=g("currency"), exchangerate=g("exchangerate"), description=str(form.get("description", "")),
               notes=str(form.get("notes", "")), vat_period=g("vat_period")[:21])
    n = int(g("rowcount")) if g("rowcount").isdigit() else 0
    for i in range(1, min(n, 500) + 1):
        f.lines.append(Line(accno=g(f"accno_{i}"), debit=g(f"debit_{i}"), credit=g(f"credit_{i}"),
                            source=g(f"source_{i}"), memo=g(f"memo_{i}"), project_id=g(f"project_{i}"),
                            fx=g(f"fx_{i}") == "1", cleared=g(f"cleared_{i}")))
    f.lines = [ln for ln in f.lines if _filled(ln)]
    f.refs = refs_from_post(form)
    return f


def refs_from_post(form: FormData) -> List[RefRow]:
    """Referenties en bijlagen uit het formulier (ook die zonder omschrijving maar met een bestand)."""
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    refs = []
    m = int(g("refcount")) if g("refcount").isdigit() else 0
    for i in range(1, min(m, 100) + 1):
        if g(f"ref_description_{i}") or g(f"ref_archive_{i}").isdigit() or g(f"ref_token_{i}"):
            refs.append(RefRow(description=g(f"ref_description_{i}"), code=g(f"ref_code_{i}"),
                               archive_id=g(f"ref_archive_{i}") if g(f"ref_archive_{i}").isdigit() else "",
                               folder=g(f"ref_folder_{i}"), confidential=g(f"ref_confidential_{i}") == "1",
                               token=g(f"ref_token_{i}")))
    return refs


def references(refs: List[RefRow]) -> List[Reference]:
    """Voor de bibliotheek; een nieuw bestand gaat met zijn inhoud mee (Form::save_reference)."""
    return [Reference(description=r.description or r.filename or "***", code=r.code or None,
                      archive_id=int(r.archive_id) if r.archive_id.isdigit() else None,
                      folder=r.folder or None, confidential=r.confidential, filename=r.filename or None,
                      data=r.data)
            for r in refs if r.description or r.archive_id or r.data is not None]


def form_from_view(view, nf: str, precision: int, copy: bool = False) -> GLForm:
    h = view.header
    def amt(v):
        return format_amount(v, nf, precision) if v else ""
    f = GLForm(id=None if copy else h["id"], fxadj=view.fxadj, reference=h.get("reference") or "",
               transdate=h["transdate"].isoformat() if h.get("transdate") else "",
               department_id=str(h.get("department_id") or "") if h.get("department_id") else "",
               currency=h.get("currency") or "",
               exchangerate=str(h["exchangerate"]) if h.get("exchangerate") not in (None, Decimal(1)) else "",
               description=h.get("description") or "", notes=h.get("notes") or "")
    for ln in view.lines:
        f.lines.append(Line(accno=f"{ln['accno']} {ln['description'] or ''}".strip(), debit=amt(ln["debit"]),
                            credit=amt(ln["credit"]), source=ln.get("source") or "", memo=ln.get("memo") or "",
                            project_id=str(ln["project_id"]) if ln.get("project_id") else "",
                            fx=bool(ln.get("fx_transaction")),
                            cleared="" if copy or not ln.get("cleared") else ln["cleared"].isoformat()))
    for r in view.references:
        if copy and r.get("archive_id"):
            continue                # bijlage blijft bij de oorspronkelijke boeking
        f.refs.append(RefRow(description=r.get("description") or "", code=r.get("code") or "",
                             archive_id=str(r.get("archive_id") or ""), folder=r.get("folder") or "",
                             confidential=bool(r.get("login")), filename=r.get("filename") or ""))
    return f


def _amount(text: str, nf: str) -> Decimal:
    if not text.strip():
        return Decimal(0)
    if not re.fullmatch(r"[0-9.,' -]+", text.strip()):
        raise ValueError(_("Ongeldig bedrag: {text}").format(text=text))
    try:
        return parse_amount(text, nf)
    except (InvalidOperation, ValueError):
        raise ValueError(_("Ongeldig bedrag: {text}").format(text=text))


def to_transaction(f: GLForm, nf: str, default_curr: Optional[str]) -> GLTransaction:
    if not f.transdate:
        raise ValueError(_(MESSAGES["Transaction Date missing!"]))
    try:
        transdate = date.fromisoformat(f.transdate)
    except ValueError:
        raise ValueError(_("Ongeldige datum: {value}").format(value=f.transdate))
    lines = []
    for i, ln in enumerate(f.lines, 1):
        accno = _accno(ln.accno)
        if not accno:
            raise ValueError(_("Regel {line}: kies een rekening.").format(line=i))
        cleared = None
        if ln.cleared:
            try:
                cleared = date.fromisoformat(ln.cleared)
            except ValueError:
                pass
        lines.append(JournalLine(accno, debit=_amount(ln.debit, nf), credit=_amount(ln.credit, nf),
                                 source=ln.source or None, memo=ln.memo or None,
                                 project_id=int(ln.project_id) if ln.project_id.isdigit() else None,
                                 cleared=cleared, fx_transaction=f.fxadj and ln.fx))
    currency = None if f.fxadj or not f.currency or f.currency == default_curr else f.currency
    rate = Decimal(1)
    if currency:
        rate = _amount(f.exchangerate, nf) if f.exchangerate else Decimal(0)
    refs = references(f.refs)
    return GLTransaction(transdate=transdate, lines=lines, id=f.id, reference=f.reference or None,
                         description=f.description or None, notes=f.notes or None,
                         department_id=int(f.department_id) if f.department_id.isdigit() else None,
                         currency=currency, exchangerate=rate, references=refs)


# ------------------------------------------------------------------ pagina

def _choices(lib: Lib, f: GLForm) -> Dict[str, Any]:
    gl = lib.gl
    with cursor(lib.conn) as cur:
        curr = [c for c in currencies(cur) if c]
        defaults = get_defaults(cur, "closedto", "revtrans", "lock_glnumber", "precision")
    transdate = None
    try:
        transdate = date.fromisoformat(f.transdate) if f.transdate else None
    except ValueError:
        pass
    projects = gl.projects(transdate)
    # projecten die op een bestaande regel staan blijven kiesbaar, ook als ze niet meer actief zijn
    used = {ln.project_id for ln in f.lines if ln.project_id}
    if used - {str(p["id"]) for p in projects}:
        projects += [p for p in gl.projects() if str(p["id"]) in used and p not in projects]
    return dict(accounts=gl.postable_accounts(), departments=gl.departments(), projects=projects,
                currencies=curr, defaultcurrency=curr[0] if curr else "",
                lock_glnumber=defaults.get("lock_glnumber") not in (None, "", "0"),
                precision=int(defaults.get("precision") or 2))


def _page(ctx: Ctx, lib: Lib, f: GLForm, *, status: int = 200, error: Optional[str] = None,
          confirm: Optional[str] = None, view=None, readonly_reason: Optional[str] = None, **kw):
    ch = _choices(lib, f)
    if f.currency and f.currency != ch["defaultcurrency"] and not f.exchangerate and f.transdate:
        try:
            rate = lib.gl.exchange_rate(f.currency, date.fromisoformat(f.transdate))
        except ValueError:
            rate = None
        if rate:
            f.exchangerate = format_amount(rate, ctx.user["numberformat"])
    if not readonly_reason:
        _pad(f)
    if f.fxadj:
        title = N_("Koersverschilboeking wijzigen") if f.id else N_("Koersverschilboeking toevoegen")
    else:
        title = N_("Memoriaalboeking wijzigen") if f.id else N_("Memoriaalboeking toevoegen")
    return render(ctx, "gl/form.html", status, f=f, ch=ch, error=error, confirm=confirm, title=title,
                  view=view, readonly_reason=readonly_reason, nf=ctx.user["numberformat"],
                  can_edit=ctx.has_role("bookkeeper"),
                  attachments=attach.saved_attachments(ctx, lib.conn, f.id), **kw)


def _lib(ctx: Ctx):
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    return conn, Lib(conn, ctx.user_config())


@router.get("/memoriaal/nieuw")
def gl_new(ctx: Ctx = Depends(ctx_company)):
    q = ctx.request.query_params
    fx = q.get("fx") == "1"
    require_gl(ctx, fx)
    require_role(ctx, "bookkeeper")
    conn, lib = _lib(ctx)
    with conn:
        copy = q.get("kopie", "")
        if q.get("btw") and not fx:
            from .routes_vat import proposal_form
            f, error = proposal_form(ctx, conn, q.get("btw", ""))
            if error:
                return _page(ctx, lib, GLForm(transdate=date.today().isoformat()), status=400, error=error)
            return _page(ctx, lib, f, vat_notice=True)
        if copy.isdigit():
            try:
                view = lib.gl.get_transaction(int(copy))
            except LedgerError:
                raise HTTPException(404, _("Boeking niet gevonden."))
            f = form_from_view(view, ctx.user["numberformat"], lib.precision(), copy=True)
            if _choices(lib, f)["lock_glnumber"]:
                f.reference = ""
        else:
            f = GLForm(fxadj=fx, transdate=date.today().isoformat())
        return _page(ctx, lib, f)


@router.get("/memoriaal/{trans_id}")
def gl_edit(trans_id: int, ctx: Ctx = Depends(ctx_company)):
    require_gl(ctx, True)
    conn, lib = _lib(ctx)
    with conn:
        editor = ctx.has_role("bookkeeper")
        try:
            view = lib.gl.get_transaction(trans_id, lock=editor)
        except LedgerError:
            raise HTTPException(404, _("Boeking niet gevonden."))
        f = form_from_view(view, ctx.user["numberformat"], lib.precision())
        reason = None
        if not editor:
            reason = _("Uw rol in deze administratie is alleen-lezen.")
        elif view.header.get("batchid"):
            reason = _("Deze boeking hoort bij een batch; batches vallen buiten deze versie.")
        elif view.locked:
            reason = (_("Boekingen kunnen niet gewijzigd worden (instelling: alleen tegenboeken).")
                      if view.defaults.get("revtrans") not in (None, "", "0")
                      else _("Deze boeking valt in een afgesloten periode."))
        elif view.readonly:
            reason = _("Deze boeking is in gebruik door {user}.").format(user=view.haslock)
        return _page(ctx, lib, f, view=view, readonly_reason=reason)


@router.get("/memoriaal-koers")
def gl_rate(ctx: Ctx = Depends(ctx_company)):
    """Voorgestelde koers voor valuta en datum (M7); leeg als er geen is."""
    q = ctx.request.query_params
    conn, lib = _lib(ctx)
    with conn:
        try:
            rate = lib.gl.exchange_rate(q.get("currency") or None, date.fromisoformat(q.get("transdate", "")))
        except ValueError:
            rate = None
    return JSONResponse({"rate": format_amount(rate, ctx.user["numberformat"]) if rate else ""})


@router.post("/memoriaal")
def gl_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(upload_form)):
    f = form_from_post(form)
    require_gl(ctx, f.fxadj)
    require_role(ctx, "bookkeeper")
    action = str(form.get("action", "update"))
    nf = ctx.user["numberformat"]
    conn, lib = _lib(ctx)
    with conn:
        if action == "cancel":
            lib.gl.unlock()
            raise Redirect("/memoriaal/nieuw" + ("?fx=1" if f.fxadj else ""))
        # bijlagen bij een nieuwe boeking (stap 4e): gaan als token mee tot Opslaan
        attach.check_pending(ctx, f.refs, form)
        if attach.drop_pending(ctx, form, f.refs):
            action = "update"
        upload_error = attach.take_upload(ctx, form, f.refs) if not f.id else None
        attach.mark_duplicates(ctx, lib.conn, f.refs)
        if upload_error:
            return _page(ctx, lib, f, status=400, error=upload_error)
        if action == "update":
            return _page(ctx, lib, f)
        if action == "new_number":
            try:
                transdate = date.fromisoformat(f.transdate) if f.transdate else None
            except ValueError:
                transdate = None
            f.reference = lib.gl.next_reference(transdate, None if f.fxadj else (f.currency or None),
                                                f.description or None)
            return _page(ctx, lib, f)
        if action in ("delete", "delete_confirmed"):
            if not f.id:
                return _page(ctx, lib, f, status=400, error=_("Er is nog niets opgeslagen om te verwijderen."))
            if action == "delete":
                return _page(ctx, lib, f, confirm="delete")
            try:
                view = lib.gl.get_transaction(f.id)
                lib.gl.delete_transaction(f.id)
            except LedgerError as exc:
                return _page(ctx, lib, f, status=400, error=message(exc))
            ctx.log("gl_deleted", id=f.id, reference=view.header.get("reference"))
            raise Redirect("/memoriaal/nieuw?" + ("fx=1&" if f.fxadj else "") + "ok=gl_deleted")
        if action not in ("post", "post_confirmed", "post_as_new"):
            raise HTTPException(400, _("Onbekende actie."))
        recurring = None
        if action == "post_as_new":
            f.id = None
            f.refs = [r for r in f.refs if not r.archive_id]     # bijlagen blijven bij het origineel
            if _choices(lib, f)["lock_glnumber"]:
                f.reference = ""
            for ln in f.lines:
                ln.cleared = ""
        elif f.id:
            if action == "post":
                return _page(ctx, lib, f, confirm="post")       # M3: bevestigen
            try:
                recurring = lib.gl.get_transaction(f.id).recurring   # herhaalschema blijft staan
            except LedgerError as exc:
                return _page(ctx, lib, f, status=400, error=message(exc))
        try:
            with cursor(conn) as cur:
                defcur = default_currency(cur)
            missing = attach.resolve_pending(ctx, f.refs)
            if missing:
                return _page(ctx, lib, f, status=400, error=missing)
            trans = to_transaction(f, nf, defcur)
            trans.recurring = recurring
            trans_id = lib.gl.post_transaction(trans)
        except ValueError as exc:
            return _page(ctx, lib, f, status=400, error=lib_message(exc))
        except LedgerError as exc:
            return _page(ctx, lib, f, status=400, error=message(exc))
        attach.discard_pending(ctx, f.refs)
        ref = lib.gl.get_transaction(trans_id).header.get("reference")
        ctx.log("gl_posted", id=trans_id, reference=ref, new=not f.id, fx=f.fxadj)
        if f.vat_period and not f.id:
            from .routes_vat import register
            register(ctx, conn, f.vat_period, trans_id)
            raise Redirect(f"/btw/aangifte?tijdvak={f.vat_period}&ok=vat_booked")
        raise Redirect(f"/memoriaal/{trans_id}?ok=gl_posted")


# ------------------------------------------------------------------ jaarafsluiting (M12)

def _yearend_default(today: date, month: int = 12) -> date:
    """Einde van het laatste afgelopen boekjaar."""
    import calendar
    year = today.year if (today.month, today.day) > (month, calendar.monthrange(today.year, month)[1]) \
        else today.year - 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _yearend_month(lib: Lib) -> int:
    with cursor(lib.conn) as cur:
        return get_yearend(cur)


def _yearend_values(form) -> Dict[str, str]:
    return {k: str(form.get(k, "")).strip() for k in ("todate", "reference", "description", "accno", "method")}


@router.get("/beheer/jaarafsluiting")
def yearend_form(ctx: Ctx = Depends(ctx_company)):
    require_role(ctx, "admin")
    conn, lib = _lib(ctx)
    with conn:
        accounts = lib.reports.earnings_accounts()
        # voorstel: de resultaatrekening van de vorige jaarafsluiting, anders de eerste (golf 2 F4)
        last = lib.reports.last_yearend_account()
        accno = last if last in {a["accno"] for a in accounts} else (accounts[0]["accno"] if accounts else "")
        v = {"todate": _yearend_default(date.today(), _yearend_month(lib)).isoformat(), "reference": "",
             "description": "", "accno": accno, "method": "accrual"}
        return _yearend_page(ctx, lib, v, accounts)


def _closedto(lib: Lib):
    """(afsluitdatum als getal jjjjmmdd zoals Perl, tekst voor het scherm)."""
    with cursor(lib.conn) as cur:
        raw = (get_defaults(cur, "closedto").get("closedto") or "").strip()
    if not raw.isdigit():
        return None, raw
    try:
        shown = format_date(date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])), lib.cfg.dateformat)
    except ValueError:
        shown = raw          # bv. 20173112 (komt voor in echte data)
    return int(raw), shown


def _yearend_page(ctx, lib, v, accounts, status=200, **kw):
    num, closedto = _closedto(lib)
    return render(ctx, "gl/yearend.html", status, v=v, accounts=accounts, closedto=closedto,
                  nf=ctx.user["numberformat"], precision=lib.precision(),
                  fmtdate=lambda d: format_date(d, ctx.user["dateformat"]), **kw)


@router.post("/beheer/jaarafsluiting")
def yearend_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_role(ctx, "admin")
    v = _yearend_values(form)
    if v["method"] not in ("accrual", "cash"):
        v["method"] = "accrual"
    action = str(form.get("action", "preview"))
    conn, lib = _lib(ctx)
    with conn:
        accounts = lib.reports.earnings_accounts()
        if v["accno"] not in {a["accno"] for a in accounts}:
            return _yearend_page(ctx, lib, v, accounts, 400, error=_("Kies de rekening voor het resultaat."))
        try:
            tdate = date.fromisoformat(v["todate"])
        except ValueError:
            return _yearend_page(ctx, lib, v, accounts, 400, error=_("Vul een geldige datum in."))
        try:
            if action == "post":
                trans_id = lib.reports.post_yearend(tdate, v["accno"], reference=v["reference"] or None,
                                                    description=v["description"] or None, method=v["method"])
                ctx.log("yearend_posted", id=trans_id, date=tdate.isoformat(), method=v["method"])
                modules = ctx.company["modules"] or []
                if "gl" in modules or "cash" in modules:
                    raise Redirect(f"/memoriaal/{trans_id}?ok=yearend_posted")
                # zonder Grootboek en Kas en bank is het memoriaal niet te openen (F37): melding hier
                raise Redirect("/beheer/jaarafsluiting?ok=yearend_posted")
            pre = lib.reports.yearend_preview(tdate, v["accno"], v["method"])
        except LedgerError as exc:
            return _yearend_page(ctx, lib, v, accounts, 400, error=message(exc))
        names = {a["accno"]: a["description"] for a in lib.gl.postable_accounts()}
        names.update({a["accno"]: a["description"] for a in accounts})
        with cursor(conn) as cur:
            cur.execute("SELECT transdate FROM yearend WHERE transdate >= %s ORDER BY 1", (tdate,))
            later = [r["transdate"] for r in cur.fetchall()]
        num, __ = _closedto(lib)
        closed = bool(num and int(tdate.strftime("%Y%m%d")) <= num)
        return _yearend_page(ctx, lib, v, accounts, preview=pre, names=names, later=later, closed=closed)
