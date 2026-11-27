"""Verkoop > Aanmaningen en Verkoop > Rekeningoverzichten (fase 4 stap 4a, SB1–SB6, SB15).

Werkschermen (POST) in plaats van de alleen-lezen rapportlijst; de lijst ``/rapporten/aanmaningen`` blijft
voor CSV/Excel. Per klant en valuta een brief (``sqlledger.dunning``):

* **Voorbeeld**: PDF van de aangevinkte brieven, legt niets vast (alle rollen).
* **PDF**: één PDF van alle aangevinkte brieven; legt per factuur het aanmaanniveau vast met "geprint"
  (aanmaningen) of een audittrailregel (overzichten). Boekhouder en hoger.
* **Mailen**: eerst een overzicht (wie wordt gemaild, wie valt af zonder e-mailadres), dan per klant een
  eigen mail met de brief als PDF; vastleggen alleen na geslaagde verzending (SB10). Boekhouder en hoger.
* **Niveau opslaan** (aanmaningen): niveau per factuur handmatig zetten of terugzetten (0 = wissen).

Het niveau dat wordt vastgelegd is het niveau dat op het scherm stond (verborgen veld ``lvl_<id>``), zodat
PDF en mail van dezelfde brief het niveau één keer ophogen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import _db
from sqlledger.dates import format_date
from sqlledger.dunning import (MAX_LEVEL, STATEMENT_BUCKETS, Dunning, Letter, Statement, bucket_ranges,
                               letter_vars, statement_vars)
from sqlledger.reports_arap import BUCKETS

from . import doctypes, mailer
from .i18n import N_, gettext as _
from .mailer import MailError
from .routes_arap import _lib
from .routes_docs import print_data, renderer, safe_filename
from .routes_mail import _signature
from .routes_reports import require_module
from .web import Ctx, csrf_form, ctx_company, render, require_role

router = APIRouter()

#: zoveel brieven per keer mailen (daarboven: splitsen, zoals SB11)
MAX_MAIL = 200


# ================================================================== filters

@dataclass
class Filters:
    asof: date
    name: str = ""
    vcnumber: str = ""
    department_id: Optional[int] = None
    min_days: int = 0
    buckets: Tuple[str, ...] = STATEMENT_BUCKETS
    overdue: bool = False

    def hidden(self) -> List[Tuple[str, str]]:
        """Velden om de selectie mee te sturen (mailoverzicht)."""
        out = [("asof", self.asof.isoformat()), ("name", self.name), ("vcnumber", self.vcnumber),
               ("department_id", str(self.department_id or "")), ("min_days", str(self.min_days)),
               ("overdue", "1" if self.overdue else "")]
        out += [("buckets", b) for b in self.buckets]
        return out


def _filters(src, statement: bool = False) -> Tuple[Filters, Optional[str]]:
    error = None
    try:
        asof = date.fromisoformat(str(src.get("asof") or "")) if src.get("asof") else date.today()
    except ValueError:
        asof, error = date.today(), _("Ongeldige datum.")
    md = str(src.get("min_days") or "0").strip()
    min_days = int(md) if md.isdigit() else 0
    dep = str(src.get("department_id") or "")
    buckets = tuple(b for b in BUCKETS if b in set(src.getlist("buckets"))) if statement else STATEMENT_BUCKETS
    return Filters(asof, str(src.get("name") or "").strip(), str(src.get("vcnumber") or "").strip(),
                   int(dep) if dep.isdigit() else None, min_days,
                   buckets or STATEMENT_BUCKETS, src.get("overdue") == "1"), error


def _departments(conn) -> List[Dict[str, Any]]:
    with _db.cursor(conn) as cur:
        cur.execute("SELECT id, description FROM department ORDER BY description")
        return cur.fetchall()


def _pdf(content: bytes, name: str, download: bool = False) -> Response:
    disposition = "attachment" if download else "inline"
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{safe_filename(name)}.pdf"',
                             "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _mail_settings(ctx: Ctx):
    ms = mailer.load(ctx.conn, ctx.company["id"])
    if not ms.enabled:
        raise MailError(N_("E-mail is nog niet ingesteld (Beheer > E-mail)."))
    return ms


@dataclass
class Outgoing:
    """Eén brief om te mailen: sleutel, relatie, ontvangers, documentsoort, variabelen."""

    key: str
    name: str
    to: str
    cc: str
    bcc: str
    formname: str
    v: Dict[str, Any]
    filename: str
    language_code: str
    record: Callable[[], None]
    subject: str = ""
    label: str = ""                                 # naam in overzicht en resultaat (leeg: ``name``)
    body: str = ""                                  # ingevuld bericht (leeg: standaardtekst bij verzenden)
    mail_vars: Optional[Dict[str, Any]] = None      # plaatshouders (leeg: ``v`` met ``name``)
    noted: Optional[Callable[[Any], None]] = None   # na verzending met het verstuurde bericht (notitie)

    def placeholders(self) -> Dict[str, Any]:
        return dict(self.mail_vars if self.mail_vars is not None else self.v, name=self.name)


def _prepare(ctx: Ctx, rnd, ms, out: Outgoing, subject: str = "", body: str = "") -> Outgoing:
    """Onderwerp invullen; ``subject``/``body``: eigen tekst voor de hele reeks (plaatshouders per brief),
    leeg = standaardtekst van de soort in de taal van de relatie."""
    lang = rnd.language(out.language_code)
    mv = out.placeholders()
    out.subject = mailer.fill(subject or ms.text(lang, out.formname, "subject"), mv)
    if body:
        out.body = mailer.fill(body, mv)
    out.label = out.label or out.name
    return out


def _send_all(ctx: Ctx, rnd, ms, mails: List[Outgoing]) -> List[Dict[str, Any]]:
    """Per brief een eigen mail; een fout stopt de rest niet. Vastleggen alleen na verzending (SB10).
    De verbinding staat op autocommit: wat na een verzending is vastgelegd blijft staan."""
    results = []
    for m in mails:
        lang = rnd.language(m.language_code)
        body = (m.body or mailer.fill(ms.text(lang, m.formname, "body"), m.placeholders())) + _signature(ctx)
        label = m.label or m.name
        try:
            msg_out = mailer.compose(m.to, m.cc, m.bcc, m.subject, body)
            try:
                pdf = rnd.render_pdf(m.formname, m.v)
            except Exception as exc:                 # sjabloonfout: alleen dit document
                raise MailError(N_("Het document kon niet worden opgemaakt.")) from exc
            msg_out.attachments.append((m.filename, pdf, "application/pdf"))
            how = mailer.send(ctx.settings, ms, mailer.build_message(ms, msg_out, reply_to=ctx.user.get("email") or ""),
                              msg_out.recipients)
        except MailError as exc:
            cause = exc.__cause__ or exc
            ctx.log("letter_email_failed", formname=m.formname, key=m.key, error=str(cause)[:300])
            results.append({"name": label, "ok": False, "reason": exc.text()})
            continue
        m.record()
        if m.noted:
            m.noted(msg_out)
        ctx.log("letter_emailed", formname=m.formname, key=m.key, to=[a for __, a in msg_out.to], via=how)
        results.append({"name": label, "ok": True, "to": ", ".join(a for __, a in msg_out.to)})
    return results


# ================================================================== aanmaningen

def _chosen(form: FormData) -> Dict[int, int]:
    out = {}
    for k, v in form.items():
        if k.startswith("lvl_") and k[4:].isdigit() and str(v).isdigit() and 1 <= int(v) <= MAX_LEVEL:
            out[int(k[4:])] = int(v)
    return out


def _selected_letters(d: Dunning, f: Filters, form: FormData) -> Tuple[List[Letter], List[str]]:
    """Brieven met alleen de aangevinkte facturen en de niveaus van het scherm; brieven waarvan het totaal
    door de selectie ≤ 0 wordt vallen af (melding)."""
    ids = {int(x) for x in form.getlist("item") if str(x).isdigit()}
    chosen = _chosen(form)
    out, skipped = [], []
    for letter in d.letters(f.asof, min_days=f.min_days, name=f.name or None, customernumber=f.vcnumber or None,
                            department_id=f.department_id):
        mine = [i.id for i in letter.items if i.id in ids]
        if not mine:
            continue
        sub = letter.only(mine, chosen)
        if sub.total <= 0:
            skipped.append(sub.name)
            continue
        out.append(sub)
    return out, skipped


def _reminder_page(ctx: Ctx, conn, d: Dunning, f: Filters, status: int = 200, **kw):
    letters = d.letters(f.asof, min_days=f.min_days, name=f.name or None, customernumber=f.vcnumber or None,
                        department_id=f.department_id)
    emails = {}
    if letters:
        with _db.cursor(conn) as cur:
            cur.execute("SELECT id, email FROM customer WHERE id = ANY(%s)", (list({l.vc_id for l in letters}),))
            emails = {r["id"]: r["email"] or "" for r in cur.fetchall()}
    return render(ctx, "dunning/reminders.html", status, f=f, letters=letters, emails=emails,
                  fmtdate=lambda d: format_date(d, ctx.user["dateformat"]) if d else "", doctypes_label=doctypes.label,
                  departments=_departments(conn), can_send=ctx.has_role("bookkeeper"),
                  checked=kw.pop("checked", set()), **kw)   # niets vooraf aangevinkt (2026-10-11)


@router.get("/aanmaningen")
def reminders_page(ctx: Ctx = Depends(ctx_company)):
    require_module(ctx, "ar")
    f, error = _filters(ctx.request.query_params)
    conn, lib = _lib(ctx)
    with conn:
        return _reminder_page(ctx, conn, Dunning(conn, lib.cfg), f, 400 if error else 200, error=error)


@router.post("/aanmaningen")
def reminders_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_module(ctx, "ar")
    action = str(form.get("action") or "")
    f, error = _filters(form)
    conn, lib = _lib(ctx)
    with conn:
        d = Dunning(conn, lib.cfg)
        checked = {int(x) for x in form.getlist("item") if str(x).isdigit()}
        if error:
            return _reminder_page(ctx, conn, d, f, 400, error=error, checked=checked)
        if action == "levels":
            require_role(ctx, "bookkeeper")
            levels = {}
            for k, v in form.items():
                if k.startswith("set_") and k[4:].isdigit() and str(v).isdigit() and int(v) <= MAX_LEVEL:
                    old = str(form.get(f"cur_{k[4:]}") or "")
                    if str(v) != old:
                        levels[int(k[4:])] = int(v)
            changed = d.set_levels(levels)
            ctx.log("reminder_levels_saved", invoices=changed)
            return _reminder_page(ctx, conn, d, f, checked=checked,
                                  notice=_("Aanmaanniveau opgeslagen ({n} facturen).").format(n=len(changed)))
        letters, skipped = _selected_letters(d, f, form)
        note = (_("Geen aanmaning (tegoed groter dan of gelijk aan het openstaande bedrag): {names}").format(
            names=", ".join(skipped)) if skipped else None)
        if not letters:
            return _reminder_page(ctx, conn, d, f, 400, checked=checked,
                                  error=note or _("Vink minstens één factuur aan."))
        pd = print_data(ctx, conn, lib)
        rnd = renderer(ctx, conn)
        if action in ("preview", "pdf"):
            final = action == "pdf"
            if final:
                require_role(ctx, "bookkeeper")
            pdf = rnd.render_many([(l.formname, letter_vars(pd, l, f.asof)) for l in letters])
            if final:
                for l in letters:
                    d.record(l, printed=True)
                ctx.log("reminders_printed", letters=[l.key for l in letters],
                        invoices=[i.id for l in letters for i in l.items])
            return _pdf(pdf, f"{_('Aanmaningen')}-{f.asof.isoformat()}", download=final)
        if action in ("mail", "mail_send"):
            require_role(ctx, "bookkeeper")
            try:
                ms = _mail_settings(ctx)
            except MailError as exc:
                return _reminder_page(ctx, conn, d, f, 400, error=exc.text(), checked=checked)
            if len(letters) > MAX_MAIL:
                return _reminder_page(ctx, conn, d, f, 400, checked=checked, error=_(
                    "Te veel brieven in één keer ({n}); maximaal {max}. Maak de selectie kleiner.").format(
                        n=len(letters), max=MAX_MAIL))
            mails, without = [], []
            for l in letters:
                v = letter_vars(pd, l, f.asof)
                if not v.get("email"):
                    without.append(l.name)
                    continue
                mails.append(_prepare(ctx, rnd, ms, Outgoing(
                    l.key, l.name, v["email"], v.get("cc") or "", v.get("bcc") or "", l.formname, v,
                    safe_filename(f"{_(doctypes.label(l.formname))}-{l.name}") + ".pdf", l.language_code,
                    (lambda l=l: d.record(l, emailed=True)))))
            if action == "mail":
                return render(ctx, "dunning/mail_confirm.html", kind="reminders", f=f, mails=mails,
                              without=without, note=note, back="/aanmaningen",
                              hidden=f.hidden() + [("item", str(i.id)) for l in letters for i in l.items]
                              + [(f"lvl_{i}", str(n)) for l in letters for i, n in l.levels.items()])
            results = _send_all(ctx, rnd, ms, mails)
            return _reminder_page(ctx, conn, d, f, results=results, without=without, checked=set(),
                                  notice=_("Gemaild: {ok}, mislukt: {failed}, zonder e-mailadres: {none}.").format(
                                      ok=sum(r["ok"] for r in results), failed=sum(not r["ok"] for r in results),
                                      none=len(without)))
        raise HTTPException(400, _("Onbekende actie."))


# ================================================================== rekeningoverzichten

def _statements(d: Dunning, f: Filters) -> List[Statement]:
    return d.statements(f.asof, buckets=f.buckets, overdue=f.overdue, name=f.name or None,
                        customernumber=f.vcnumber or None, department_id=f.department_id)


def _statement_page(ctx: Ctx, conn, d: Dunning, f: Filters, status: int = 200, **kw):
    statements = _statements(d, f)
    emails = {}
    if statements:
        with _db.cursor(conn) as cur:
            cur.execute("SELECT id, email FROM customer WHERE id = ANY(%s)", (list({s.vc_id for s in statements}),))
            emails = {r["id"]: r["email"] or "" for r in cur.fetchall()}
    return render(ctx, "dunning/statements.html", status, f=f, statements=statements, emails=emails,
                  all_buckets=BUCKETS, ranges=dict(zip(f.buckets, bucket_ranges(f.buckets))),
                  departments=_departments(conn), can_send=ctx.has_role("bookkeeper"),
                  checked=kw.pop("checked", set()), **kw)   # niets vooraf aangevinkt (2026-10-11)


@router.get("/rekeningoverzichten")
def statements_page(ctx: Ctx = Depends(ctx_company)):
    require_module(ctx, "ar")
    q = ctx.request.query_params
    f, error = _filters(q, statement=True)
    if "asof" not in q:                      # eerste keer: standaardkolommen
        f.buckets = STATEMENT_BUCKETS
    conn, lib = _lib(ctx)
    with conn:
        return _statement_page(ctx, conn, Dunning(conn, lib.cfg), f, 400 if error else 200, error=error)


@router.post("/rekeningoverzichten")
def statements_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_module(ctx, "ar")
    action = str(form.get("action") or "")
    f, error = _filters(form, statement=True)
    conn, lib = _lib(ctx)
    with conn:
        d = Dunning(conn, lib.cfg)
        keys = set(str(x) for x in form.getlist("st"))
        if error:
            return _statement_page(ctx, conn, d, f, 400, error=error, checked=keys)
        chosen = [s for s in _statements(d, f) if s.key in keys]
        if not chosen:
            return _statement_page(ctx, conn, d, f, 400, error=_("Vink minstens één klant aan."), checked=keys)
        pd = print_data(ctx, conn, lib)
        rnd = renderer(ctx, conn)
        if action in ("preview", "pdf"):
            final = action == "pdf"
            if final:
                require_role(ctx, "bookkeeper")
            pdf = rnd.render_many([("statement", statement_vars(pd, s, f.asof)) for s in chosen])
            if final:
                for s in chosen:
                    d.record_statement(s)
                ctx.log("statements_printed", statements=[s.key for s in chosen])
            return _pdf(pdf, f"{_('Rekeningoverzichten')}-{f.asof.isoformat()}", download=final)
        if action in ("mail", "mail_send"):
            require_role(ctx, "bookkeeper")
            try:
                ms = _mail_settings(ctx)
            except MailError as exc:
                return _statement_page(ctx, conn, d, f, 400, error=exc.text(), checked=keys)
            if len(chosen) > MAX_MAIL:
                return _statement_page(ctx, conn, d, f, 400, checked=keys, error=_(
                    "Te veel brieven in één keer ({n}); maximaal {max}. Maak de selectie kleiner.").format(
                        n=len(chosen), max=MAX_MAIL))
            mails, without = [], []
            for s in chosen:
                v = statement_vars(pd, s, f.asof)
                if not v.get("email"):
                    without.append(s.name)
                    continue
                mails.append(_prepare(ctx, rnd, ms, Outgoing(
                    s.key, s.name, v["email"], v.get("cc") or "", v.get("bcc") or "", "statement", v,
                    safe_filename(f"{_('Rekeningoverzicht')}-{s.name}-{s.curr}") + ".pdf", s.language_code,
                    (lambda s=s: d.record_statement(s, emailed=True)))))
            if action == "mail":
                return render(ctx, "dunning/mail_confirm.html", kind="statements", f=f, mails=mails,
                              without=without, note=None, back="/rekeningoverzichten",
                              hidden=f.hidden() + [("st", s.key) for s in chosen])
            results = _send_all(ctx, rnd, ms, mails)
            return _statement_page(ctx, conn, d, f, results=results, without=without, checked=set(),
                                   notice=_("Gemaild: {ok}, mislukt: {failed}, zonder e-mailadres: {none}.").format(
                                       ok=sum(r["ok"] for r in results), failed=sum(not r["ok"] for r in results),
                                       none=len(without)))
        raise HTTPException(400, _("Onbekende actie."))


def sample_vars(ctx: Ctx, conn, lib, kind: str) -> Optional[Dict[str, Any]]:
    """Variabelen voor een voorbeeld in Beheer > Sjablonen: de eerste aanmaning of het eerste
    rekeningoverzicht van vandaag (None als er niets openstaat)."""
    d, pd, today = Dunning(conn, lib.cfg), print_data(ctx, conn, lib), date.today()
    if kind == "statement":
        st = next(iter(d.statements(today)), None)
        return statement_vars(pd, st, today) if st else None
    letter = next(iter(d.letters(today)), None)
    return letter_vars(pd, letter, today) if letter else None
