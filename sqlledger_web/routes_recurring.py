"""Terugkerende boekingen (fase 2, T1–T8; fase 3 R1, RH1–RH6): herhaalschema bij een
boeking (knop "Herhalen" op memoriaal-, verkoop-, inkoop-, factuur- en orderscherm, zoals
arap.pl/oe.pl schedule) en de lijst "Terugkerende boekingen" met verwerken (am.pl
recurring_transactions/process_transactions). Rekenen gebeurt in
``sqlledger.recurring``; hier wordt alleen ingelezen en getoond.

Fase 4 stap 4d (SB12–SB13): op het schemascherm de keuzes **Mailen** (documentsoorten + bericht) en **PDF**
(documentsoorten), opgeslagen als formnames zoals SL in ``recurringemail``/``recurringprint``. Na het verwerken
krijgt elk nieuw document een eigen mail (gedeelde code van Aanmaningen/Batch: ``routes_dunning._send_all``);
de PDF-soorten van alle nieuwe documenten komen samen in één PDF via een knop op de resultaatpagina
(``DocumentRenderer.render_many``). Beide gebeuren ná het boeken: een mislukte mail of PDF laat de boeking en
het doorschuiven van het schema staan (Perl-fout 23); ``emailed``/``printed`` alleen na succes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import LedgerError, Recurring
from sqlledger.dates import format_date
from sqlledger.errors import NotFoundError
from sqlledger.printdata import add_intnote, mark_status
from sqlledger.recurring import QUOTATION, UNITS, ProcessItem, ProcessResult, RecurringTransactions, next_after

from . import doctypes, mailer
from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .reportdefs import Lib
from .routes_is import message as is_message
from .routes_oe import MESSAGES as OE_MESSAGES
from .mailer import MailError
from .routes_docs import filename, print_data, renderer
from .routes_dunning import MAX_MAIL, Outgoing, _mail_settings, _pdf, _prepare, _send_all
from .routes_mail import _intnote, mail_vars
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

UNIT_LABELS = {"days": N_("dag(en)"), "weeks": N_("week/weken"), "months": N_("maand(en)"), "years": N_("jaar")}
TYPE_LABELS = {"gl": N_("Memoriaal"), "ar": N_("Verkoop"), "ap": N_("Inkoop"), "so": N_("Verkooporder"),
               "po": N_("Inkooporder")}

MESSAGES = {
    "Startdate missing!": N_("Vul de startdatum in."),
    "Repeat must be at least 1": N_("Vul bij Elke een getal van 1 tot en met 999 in."),
    "Number of times must be at least 1": N_("Vul bij Aantal keer een getal van 1 tot en met 9999 in."),
    "Repeat and number of times must be whole numbers": N_("Elke en Aantal keer moeten hele getallen zijn."),
    "Schedule has ended": N_("Het schema is afgelopen."),
    "Next date changed in the meantime (already processed?)":
        N_("De volgende datum is intussen gewijzigd (al verwerkt?); niet opnieuw geboekt. Vernieuw de lijst."),
    QUOTATION: N_("Offertes en offerteaanvragen kunnen geen herhaalschema krijgen; zet ze eerst om naar een order."),
    "Point of sale receipts cannot be repeated": N_("Kassabonnen kunnen niet herhaald worden."),
    "Schedule not found": N_("Het schema bestaat niet meer."),
    "Transaction not found": N_("De boeking bestaat niet meer."),
}


def message(exc) -> str:
    text = str(exc)
    if text in MESSAGES:
        return _(MESSAGES[text])
    if text in OE_MESSAGES:
        return _(OE_MESSAGES[text])
    return is_message(exc)


def _lib(ctx: Ctx):
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    return conn, Lib(conn, ctx.user_config())


def require_recurring(ctx: Ctx, module: Optional[str] = None) -> None:
    modules = ctx.company["modules"] or []
    if "recurring" not in modules or (module and module not in modules):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    require_role(ctx, "bookkeeper")


def booking_url(kind: Dict) -> str:
    """Bewerkscherm van de boeking, factuur of order."""
    tid = int(kind["id"])
    if kind["module"] == "gl":
        return f"/memoriaal/{tid}"
    if kind["module"] in ("ar", "ap"):
        sale = kind["module"] == "ar"
        if kind.get("invoice"):
            return f"/{'verkoopfactuur' if sale else 'inkoopfactuur'}/{tid}"
        return f"/{'verkoop' if sale else 'inkoop'}/{tid}"
    if kind["module"] == "oe":
        sale = kind.get("vc") == "customer"
        if kind.get("quotation"):
            return f"/{'offerte' if sale else 'offerteaanvraag'}/{tid}"
        return f"/{'verkooporder' if sale else 'inkooporder'}/{tid}"
    return "/terugkerend"


# ------------------------------------------------------------------ schema (T1)

@dataclass
class ScheduleForm:
    reference: str = ""
    description: str = ""
    startdate: str = ""
    nextdate: str = ""
    repeat: str = "1"
    unit: str = "months"
    howmany: str = ""
    payment: bool = False
    email: List[str] = field(default_factory=list)     # formnames om te mailen (SB13)
    print: List[str] = field(default_factory=list)     # formnames voor de PDF
    message: str = ""


#: lengte van het bericht in het schema
MAX_MESSAGE = 5000


def form_from_schedule(s: Optional[Recurring], transdate: Optional[date]) -> ScheduleForm:
    if not s:
        return ScheduleForm(startdate=transdate.isoformat() if transdate else "")
    return ScheduleForm(reference=s.reference or "", description=s.description or "",
                        startdate=s.startdate.isoformat() if s.startdate else "",
                        nextdate=s.nextdate.isoformat() if s.nextdate else "", repeat=str(s.repeat or 1),
                        unit=s.unit or "months", howmany=str(s.howmany or ""), payment=bool(s.payment),
                        email=[e[0] for e in s.email], print=[p[0] for p in s.print], message=s.message or "")


def form_from_post(form: FormData) -> ScheduleForm:
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    return ScheduleForm(reference=g("reference")[:100], description=g("description")[:500],
                        startdate=g("startdate"), nextdate=g("nextdate"), repeat=g("repeat"), unit=g("unit"),
                        howmany=g("howmany"), payment=g("payment") == "1",
                        email=list(dict.fromkeys(str(v) for v in form.getlist("email"))),
                        print=list(dict.fromkeys(str(v) for v in form.getlist("print"))),
                        message=str(form.get("message", "")).replace("\r\n", "\n").strip()[:MAX_MESSAGE])


def output_choices(conn, kind: Dict) -> List[Dict[str, str]]:
    """Documentsoorten die bij de boeking horen (SB13), met de formname zoals SL; memoriaal: geen."""
    if kind["module"] == "gl" or kind.get("quotation"):
        return []
    screen = doctypes.screen_of(conn, int(kind["id"]))
    return [{"formname": k.formname, "key": k.key, "label": k.label} for k in doctypes.kinds_for(screen or "")]


def with_outputs(s: Recurring, f: ScheduleForm, choices: List[Dict[str, str]], old: Optional[Recurring]) -> Recurring:
    """Mailen/PDF van het formulier op het schema zetten. Alleen soorten uit ``choices``; soorten uit een
    Perl-schema die hier niet bestaan (bv. work_order) blijven bewaard, net als de printernaam (D7: genegeerd)."""
    allowed = {c["formname"] for c in choices}
    old_email = {e[0]: e[1] for e in (old.email if old else [])}
    old_print = {p[0]: (p[1], p[2]) for p in (old.print if old else [])}
    email = [(n, "pdf") for n in f.email if n in allowed]
    email += [(n, fmt) for n, fmt in old_email.items() if n not in allowed]
    prints = [(n, "pdf", old_print.get(n, ("", ""))[1] or "") for n in f.print if n in allowed]
    prints += [(n, fmt, printer) for n, (fmt, printer) in old_print.items() if n not in allowed]
    return replace(s, email=email, print=prints, message=(f.message or None) if email else None)


def _date(text: str, msg: str) -> Optional[date]:
    """``msg``: foutmelding (N_) met plaatshouder {value}."""
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(_(msg).format(value=text))


def to_schedule(f: ScheduleForm, payment_allowed: bool) -> Recurring:
    """Formulier → Recurring (validatie van getallen hier, de rest in de bibliotheek)."""
    start = _date(f.startdate, N_("Ongeldige startdatum: {value}"))
    if not start:
        raise ValueError(_(MESSAGES["Startdate missing!"]))
    if not f.repeat.isdigit():
        raise ValueError(_(MESSAGES["Repeat must be at least 1"]))
    if not f.howmany.isdigit():
        raise ValueError(_(MESSAGES["Number of times must be at least 1"]))
    if f.unit not in UNITS:
        raise ValueError(_("Kies dagen, weken, maanden of jaren."))
    return Recurring(startdate=start, repeat=int(f.repeat), unit=f.unit, howmany=int(f.howmany),
                     reference=f.reference or None, description=f.description or None,
                     payment=bool(f.payment and payment_allowed), nextdate=_date(f.nextdate, N_("Ongeldige volgende datum: {value}")))


def _kind(rt: RecurringTransactions, trans_id: int) -> Dict:
    k = rt.kind(trans_id)
    if not k:
        raise HTTPException(404, _("Boeking niet gevonden."))
    k["id"] = trans_id
    return k


def _type_label(kind: Dict) -> str:
    m = kind["module"]
    if m in ("ar", "ap") and kind.get("invoice"):
        return N_("Verkoopfactuur") if m == "ar" else N_("Inkoopfactuur")
    if m == "oe":
        if kind.get("quotation"):
            return N_("Offerte") if kind.get("vc") == "customer" else N_("Offerteaanvraag")
        return TYPE_LABELS["so" if kind.get("vc") == "customer" else "po"]
    return TYPE_LABELS.get(m, "")


def _upcoming(s: Optional[Recurring], n: int = 6) -> List[date]:
    """De volgende termijnen vanaf de volgende datum (ter controle op het scherm)."""
    if not s or not s.nextdate:
        return []
    out, d = [s.nextdate], s.nextdate
    while len(out) < n:
        d = next_after(s, d)
        if not d or (s.enddate and d > s.enddate):
            break
        out.append(d)
    return out


def _schedule_page(ctx: Ctx, rt: RecurringTransactions, kind: Dict, f: ScheduleForm, *, status: int = 200,
                   error: Optional[str] = None):
    s = rt.get(kind["id"])
    choices = output_choices(rt.conn, kind)
    known = {c["formname"] for c in choices}
    kept = sorted(({e[0] for e in (s.email if s else [])} | {p[0] for p in (s.print if s else [])}) - known)
    supported = not kind.get("quotation")
    name = None
    if kind["vc"] and kind.get("vc_id"):
        with rt.conn.cursor() as cur:
            cur.execute(f"SELECT name FROM {kind['vc']} WHERE id = %s", (kind["vc_id"],))
            r = cur.fetchone()
            name = r[0] if r else None
    fmt = lambda d: format_date(d, ctx.user["dateformat"]) if d else ""  # noqa: E731
    return render(ctx, "recurring/schedule.html", status, f=f, kind=kind, schedule=s, supported=supported,
                  vc_name=name, back=booking_url(kind), error=error, units=UNIT_LABELS,
                  type_label=_type_label(kind), upcoming=[fmt(d) for d in _upcoming(s)],
                  enddate=fmt(s.enddate) if s else "", fmt=fmt, choices=choices, kept=kept,
                  max_message=MAX_MESSAGE,
                  placeholders=list(mailer.PLACEHOLDERS)[0 if ctx.user.get("language", "nl") == "nl" else 1::2])


@router.get("/herhalen/{trans_id}")
def schedule_form(trans_id: int, ctx: Ctx = Depends(ctx_company)):
    conn, lib = _lib(ctx)
    with conn:
        rt = RecurringTransactions(conn, lib.cfg)
        kind = _kind(rt, trans_id)
        require_recurring(ctx, kind["module"])
        return _schedule_page(ctx, rt, kind, form_from_schedule(rt.get(trans_id), kind["transdate"]))


@router.post("/herhalen/{trans_id}")
def schedule_post(trans_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    action = str(form.get("action", "save"))
    conn, lib = _lib(ctx)
    with conn:
        rt = RecurringTransactions(conn, lib.cfg)
        kind = _kind(rt, trans_id)
        require_recurring(ctx, kind["module"])
        f = form_from_post(form)
        if kind["quotation"]:
            return _schedule_page(ctx, rt, kind, f, status=400, error=_(MESSAGES[QUOTATION]))
        if action == "delete":
            rt.delete(trans_id)
            ctx.log("recurring_deleted", id=trans_id, module=kind["module"])
            raise Redirect(f"{booking_url(kind)}?ok=recurring_deleted")
        if action != "save":
            raise HTTPException(400, _("Onbekende actie."))
        try:
            new = with_outputs(to_schedule(f, kind["module"] in ("ar", "ap")), f, output_choices(conn, kind),
                               rt.get(trans_id))
            s = rt.save(trans_id, new, keep_output=False)
        except ValueError as exc:
            return _schedule_page(ctx, rt, kind, f, status=400, error=lib_message(exc))
        except LedgerError as exc:
            return _schedule_page(ctx, rt, kind, f, status=400, error=message(exc))
        ctx.log("recurring_saved", id=trans_id, module=kind["module"], startdate=s.startdate.isoformat(),
                repeat=s.repeat, unit=s.unit, howmany=s.howmany,
                nextdate=s.nextdate.isoformat() if s.nextdate else None,
                email=[e[0] for e in s.email], print=[p[0] for p in s.print])
        raise Redirect(f"{booking_url(kind)}?ok=recurring_saved")


# ------------------------------------------------------------------ lijst + verwerken (T2)

def _list_page(ctx: Ctx, rt: RecurringTransactions, *, results=None, checked=None, nextdates=None,
               status: int = 200, error: Optional[str] = None, prints=None, confirmed=None):
    rows = rt.list()
    fmt = lambda d: format_date(d, ctx.user["dateformat"]) if d else ""  # noqa: E731
    for r in rows:
        r.url = booking_url({"id": r.id, "module": r.module, "invoice": r.invoice, "vc": r.vc})
        # een schema dat meer dan één termijn achterloopt, staat niet standaard aan (PB16)
        r.checked = (r.id in checked) if checked is not None else (r.due and r.behind <= 1)
        r.confirmed = r.id in (confirmed or set())
        r.reason_nl = _(MESSAGES[r.reason]) if r.reason in MESSAGES else None
        r.next_value = (nextdates or {}).get(r.id) or (r.nextdate.isoformat() if r.nextdate else "")
        r.email_labels = [_label(f, r.module) for f in r.email]
        r.print_labels = [_label(f, r.module) for f in r.print]
    return render(ctx, "recurring/list.html", status, rows=rows, results=results, error=error, prints=prints or [],
                  units=UNIT_LABELS, types=TYPE_LABELS, fmt=fmt, nf=ctx.user["numberformat"],
                  precision=rt_precision(rt))


def _label(formname: str, module: str) -> str:
    key = doctypes.key_for_formname(formname, module)
    return _(doctypes.KINDS[key].label) if key else formname


def rt_precision(rt: RecurringTransactions) -> int:
    with rt.conn.cursor() as cur:
        cur.execute("SELECT fldvalue FROM defaults WHERE fldname = 'precision'")
        r = cur.fetchone()
    try:
        return int(r[0]) if r and r[0] else 2
    except ValueError:
        return 2


def items_from_post(form: FormData) -> List[ProcessItem]:
    """Aangevinkte schema's met de getoonde (verwachte) en eventueel aangepaste volgende datum."""
    items = []
    for v in form.getlist("sel"):
        if not str(v).isdigit():
            continue
        tid = int(v)
        old = str(form.get(f"old_{tid}", "")).strip()
        new = str(form.get(f"next_{tid}", "")).strip()
        try:
            expected = date.fromisoformat(old) if old else None
        except ValueError:
            expected = None
        try:
            transdate = date.fromisoformat(new) if new else None
        except ValueError:
            raise ValueError(_("Ongeldige datum: {value}").format(value=new))
        # zonder getoonde datum (afgelopen schema) niets verwachten: de bibliotheek weigert dan
        items.append(ProcessItem(tid, expected_nextdate=expected or date.min,
                                 transdate=transdate if transdate != expected else None))
    return items


@router.get("/terugkerend")
def recurring_list(ctx: Ctx = Depends(ctx_company)):
    require_recurring(ctx)
    conn, lib = _lib(ctx)
    with conn:
        return _list_page(ctx, RecurringTransactions(conn, lib.cfg))


@router.post("/terugkerend")
def recurring_process(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_recurring(ctx)
    conn, lib = _lib(ctx)
    with conn:
        rt = RecurringTransactions(conn, lib.cfg)
        checked = {int(v) for v in form.getlist("sel") if str(v).isdigit()}
        nextdates = {int(k[5:]): str(v).strip() for k, v in form.items() if k.startswith("next_") and k[5:].isdigit()}
        try:
            items = items_from_post(form)
        except ValueError as exc:
            return _list_page(ctx, rt, checked=checked, nextdates=nextdates, status=400, error=lib_message(exc))
        if not items:
            return _list_page(ctx, rt, checked=checked, nextdates=nextdates, status=400,
                              error=_("Vink minstens één schema aan."))
        modules = ctx.company["modules"] or []
        allowed, results, kinds = [], [], {}
        # PB16: een schema dat meer dan één termijn achterloopt alleen met "toch verwerken" (verwerken boekt er
        # één, zoals altijd; daarna staat de volgende datum op de eerstvolgende termijn)
        confirmed = {int(v) for v in form.getlist("behind_ok") if str(v).isdigit()}
        behind = {r.id: r.behind for r in rt.list()}
        for it in items:
            k = kinds[it.id] = rt.kind(it.id)
            if behind.get(it.id, 0) > 1 and it.id not in confirmed:
                results.append(dict(id=it.id, ok=False, url=None, number=None, transdate="", nextdate="",
                                    reason=_("loopt {n} termijnen achter; vink \"toch verwerken\" aan of pas de "
                                             "volgende datum aan op het schema").format(n=behind[it.id])))
                ctx.log("recurring_skipped", id=it.id, reason="behind", terms=behind[it.id])
            elif k and k["module"] not in modules:
                results.append(dict(id=it.id, ok=False, reason=_("Deze module staat uit voor deze administratie."),
                                    url=None, number=None, transdate="", nextdate=""))
            else:
                allowed.append(it)
        fmt = lambda d: format_date(d, ctx.user["dateformat"]) if d else ""  # noqa: E731
        processed = rt.process(allowed)
        for r in processed:
            url = None
            if r.ok:
                k = kinds.get(r.id) or {}
                url = booking_url({"id": r.trans_id, "module": r.module, "invoice": k.get("invoice"),
                                   "vc": k.get("vc")})
            results.append(dict(id=r.id, ok=r.ok, number=r.number, url=url, transdate=fmt(r.transdate),
                                nextdate=fmt(r.nextdate), reason=None if r.ok else message(r.reason)))
            ctx.log("recurring_processed" if r.ok else "recurring_skipped", id=r.id, module=r.module,
                    trans_id=r.trans_id, number=r.number, transdate=r.transdate.isoformat() if r.transdate else None,
                    reason=r.reason)
        # SB12: pas na het boeken (elk schema is al vastgelegd); niets hiervan kan het doorschuiven tegenhouden
        notes, prints = after_processing(ctx, conn, lib, [r for r in processed if r.ok])
        for res in results:
            res["outputs"] = notes.get(res["id"], [])
        return _list_page(ctx, rt, results=results, checked=set(), prints=prints)


@router.post("/terugkerend/{trans_id}/stoppen", name="recurring_stop")
def recurring_stop(trans_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    """Schema stoppen (PB16, ook vanaf de overstaplijst): geen volgende datum meer."""
    conn, lib = _lib(ctx)
    with conn:
        rt = RecurringTransactions(conn, lib.cfg)
        kind = _kind(rt, trans_id)
        require_recurring(ctx, kind["module"])
        rt.stop(trans_id)
    ctx.log("recurring_stopped", id=trans_id, module=kind["module"])
    back = str(form.get("back") or "")
    raise Redirect((back if back in ("/", "/beheer/ingebruikname", "/terugkerend") else "/terugkerend")
                   + "?ok=recurring_stopped")


# ------------------------------------------------------------------ mailen en PDF na verwerken (SB12)

def after_processing(ctx: Ctx, conn, lib, done: List[ProcessResult]
                     ) -> Tuple[Dict[int, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """Per schema de uitkomst van het mailen (``{"ok", "text"}``) en de documenten voor de samengevoegde PDF
    (``{"doc": "<soort>:<id>", "label"}``). Een fout raakt alleen dat document; ook een onverwachte fout
    wordt een melding, want de boekingen staan er al."""
    notes: Dict[int, List[Dict[str, Any]]] = {r.id: [] for r in done}
    prints: List[Dict[str, Any]] = []
    if not any(r.email or r.print for r in done):
        return notes, prints
    try:
        _outputs(ctx, conn, lib, done, notes, prints)
    except Exception as exc:                        # pragma: no cover - vangnet
        ctx.log("recurring_output_failed", error=str(exc)[:300])
        for r in done:
            if r.email:
                notes[r.id].append({"ok": False, "text": _("Mailen is niet gelukt; mail het document vanaf de "
                                                           "boeking.")})
    return notes, prints


def _outputs(ctx: Ctx, conn, lib, done: List[ProcessResult], notes, prints) -> None:
    mods = set(ctx.company["modules"] or [])
    pd = rnd = ms = None
    ms_error: Optional[str] = None
    mails: List[Outgoing] = []
    owners: List[int] = []

    def note(r: ProcessResult, ok: bool, text: str) -> None:
        notes[r.id].append({"ok": ok, "text": text})

    for r in done:
        for what, formname in [("email", f) for f in r.email] + [("print", f) for f in r.print]:
            key = doctypes.key_for_formname(formname, r.module)
            loc = doctypes.locate(conn, key, r.trans_id) if key else None
            label = f"{_(doctypes.KINDS[key].label) if key else formname} {r.number or ''}".strip()
            if loc is None or loc.module not in mods:
                note(r, False, _("{doc}: deze documentsoort hoort niet bij de boeking; niet gemaakt.").format(doc=label))
                ctx.log("recurring_output_skipped", id=r.id, trans_id=r.trans_id, formname=formname, what=what)
                continue
            if what == "print":
                prints.append({"doc": f"{key}:{r.trans_id}", "label": label})
                continue
            if pd is None:
                pd, rnd = print_data(ctx, conn, lib), renderer(ctx, conn)
                try:
                    ms = _mail_settings(ctx)
                except MailError as exc:
                    ms_error = exc.text()
            if ms_error:
                note(r, False, _("{doc} niet gemaild: {reason}").format(doc=label, reason=ms_error))
                ctx.log("recurring_email_failed", id=r.id, trans_id=r.trans_id, formname=formname, error="no settings")
                continue
            try:
                v = doctypes.print_vars(pd, loc)
            except NotFoundError:
                note(r, False, _("{doc} niet gemaild: het document kon niet worden gelezen.").format(doc=label))
                continue
            if not v.get("email"):
                note(r, False, _("{doc} niet gemaild: de relatie heeft geen e-mailadres.").format(doc=label))
                ctx.log("recurring_email_failed", id=r.id, trans_id=r.trans_id, formname=formname, error="no address")
                continue
            mails.append(_prepare(ctx, rnd, ms, Outgoing(
                f"recurring:{r.id}:{key}:{r.trans_id}", v.get("name") or "", v["email"], v.get("cc") or "",
                v.get("bcc") or "", key, v, filename(loc), str(v.get("language_code") or ""),
                (lambda loc=loc: mark_status(conn, lib.cfg, loc.trans_id, loc.kind.formname, emailed=True,
                                             table=loc.table, reference=loc.number)),
                label=label, mail_vars=mail_vars(loc, v),
                noted=(lambda out, loc=loc: add_intnote(conn, loc.trans_id, _intnote(ctx, out), table=loc.table))),
                body=r.message or ""))
            owners.append(r.id)
    if mails:
        by_id = {r.id: r for r in done}
        for owner, res in zip(owners, _send_all(ctx, rnd, ms, mails)):
            if res["ok"]:
                note(by_id[owner], True, _("{doc} gemaild aan {to}").format(doc=res["name"], to=res["to"]))
            else:
                note(by_id[owner], False, _("{doc} niet gemaild: {reason}").format(doc=res["name"],
                                                                                  reason=res["reason"]))


@router.post("/terugkerend/pdf")
def recurring_pdf(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    """Eén PDF van de nieuwe documenten (``recurringprint``); pas als die gelukt is ``printed`` + audittrail."""
    require_recurring(ctx)
    conn, lib = _lib(ctx)
    with conn:
        mods = set(ctx.company["modules"] or [])
        locs = []
        for value in dict.fromkeys(str(v) for v in form.getlist("doc")):
            key, __, tid = value.partition(":")
            loc = doctypes.locate(conn, key, int(tid)) if key in doctypes.KINDS and tid.isdigit() else None
            if loc is not None and loc.module in mods:
                locs.append(loc)
        rt = RecurringTransactions(conn, lib.cfg)
        if not locs:
            return _list_page(ctx, rt, status=400, error=_("Er zijn geen documenten om als PDF te maken."))
        if len(locs) > MAX_MAIL:
            return _list_page(ctx, rt, status=400, error=_(
                "Te veel documenten in één keer ({n}); maximaal {max}. Maak de PDF vanuit Verkoop > Documenten in batch.").format(
                    n=len(locs), max=MAX_MAIL))
        pd = print_data(ctx, conn, lib)
        try:
            docs = [(loc, doctypes.print_vars(pd, loc)) for loc in locs]
            pdf = renderer(ctx, conn).render_many([(loc.kind.key, v) for loc, v in docs])
        except Exception as exc:
            ctx.log("recurring_pdf_failed", error=str(exc)[:300])
            return _list_page(ctx, rt, status=400, error=_("De PDF kon niet worden gemaakt; er is niets vastgelegd. "
                                                           "Maak de documenten vanaf de boeking of via Verkoop > Documenten in batch."))
        for loc, __ in docs:
            mark_status(conn, lib.cfg, loc.trans_id, loc.kind.formname, printed=True, table=loc.table,
                        reference=loc.number)
        ctx.log("recurring_printed", docs=[f"{loc.kind.key}:{loc.trans_id}" for loc in locs])
        name = filename(locs[0])[:-4] if len(locs) == 1 else f"{_('Terugkerend')}-{len(locs)}-{date.today().isoformat()}"
        return _pdf(pdf, name, download=True)
