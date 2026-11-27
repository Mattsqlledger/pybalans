"""Documenten > Batch (fase 4 stap 4c, SB9–SB11, SB14, SB15).

Eén scherm voor alle documentsoorten (zoals bp.pl): soort kiezen, filteren (periode, relatie, nummer,
open/gesloten, geprint, gemaild), aanvinken en dan:

* **Voorbeeld**: één samengevoegde PDF, legt niets vast (alle rollen met de module).
* **PDF**: één samengevoegde PDF (elk document met eigen sjabloon, taal en paginanummering, SB14); pas als
  die gelukt is per document ``status.printed`` + audittrail (SB10). Boekhouder en hoger.
* **Mailen**: eerst een overzicht (wie valt af zonder e-mailadres; onderwerp en bericht eventueel één keer
  voor de hele reeks aan te passen, met plaatshouders per document), dan per document een eigen mail.
  Alleen na verzending ``status.emailed``, de notitie ``[email]`` en audittrail (SB10, SB11). Eén fout
  stopt de rest niet. Boekhouder en hoger.

Maximaal ``MAX_BATCH`` documenten per keer voor PDF en mailen (SB11); de lijst toont er hoogstens
``MAX_LIST``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import batch
from sqlledger.amounts import format_amount
from sqlledger.dates import format_date
from sqlledger.errors import NotFoundError
from sqlledger.printdata import add_intnote, mark_status

from . import doctypes, mailer
from .i18n import gettext as _
from .mailer import MailError
from .routes_arap import _date, _lib
from .routes_docs import filename, print_data, renderer
from .routes_dunning import MAX_MAIL, Outgoing, _mail_settings, _pdf, _prepare, _send_all
from .routes_mail import _intnote, mail_vars
from .web import Ctx, csrf_form, ctx_company, render, require_role

router = APIRouter()

#: zoveel documenten per keer in één PDF of mailreeks (SB11)
MAX_BATCH = MAX_MAIL
#: zoveel regels in de lijst (daarboven: filters verfijnen)
MAX_LIST = 1000
FILTER_KEYS = ("soort", "van", "tot", "relatie", "nummer", "staat", "geprint", "gemaild")
YESNO = {"alle": "all", "ja": "yes", "nee": "no"}
STATE = {"alle": "all", "open": "open", "gesloten": "closed"}


def _kinds(ctx: Ctx) -> List[Dict[str, Any]]:
    """Soorten waarvan minstens één bron in een module zit die aan staat."""
    mods = set(ctx.company["modules"] or [])
    out = []
    for key, kind in doctypes.KINDS.items():
        if any(doctypes.source_module(s) in mods for s in doctypes.batch_sources(key)):
            out.append({"key": key, "label": doctypes.long_label(key), "vc": kind.vc})
    return out


def _sources(ctx: Ctx, key: str):
    mods = set(ctx.company["modules"] or [])
    return [s for s in doctypes.batch_sources(key) if doctypes.source_module(s) in mods]


def _default_from() -> str:
    """Standaardperiode: de vorige en de lopende maand (zoals het betalingsregister)."""
    t = date.today()
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return date(y, m, 1).isoformat()


def _filters(ctx: Ctx, src) -> Tuple[Dict[str, str], Optional[str]]:
    kinds = [k["key"] for k in _kinds(ctx)]
    if not kinds:
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))
    out = {k: str(src.get(k) or "").strip() for k in FILTER_KEYS}
    # eerste keer (uit het menu, eventueel met alleen ?soort=): standaardfilters
    first = not any(k in src for k in FILTER_KEYS if k != "soort")
    error = None
    if out["soort"] not in kinds:
        out["soort"] = kinds[0]
    if first:
        out.update(van=_default_from(), geprint="nee", gemaild="nee", staat="alle")
    for k in ("van", "tot"):
        if out[k] and not _date(out[k]):
            out[k], error = "", _("Ongeldige datum.")
    out["staat"] = out["staat"] if out["staat"] in STATE else "alle"
    for k in ("geprint", "gemaild"):
        out[k] = out[k] if out[k] in YESNO else "alle"
    return out, error


def _batch_filter(flt: Dict[str, str]) -> batch.BatchFilter:
    return batch.BatchFilter(_date(flt["van"]) if flt["van"] else None, _date(flt["tot"]) if flt["tot"] else None,
                             flt["relatie"], flt["nummer"], STATE[flt["staat"]], YESNO[flt["geprint"]],
                             YESNO[flt["gemaild"]])


def _hidden(flt: Dict[str, str]) -> List[Tuple[str, str]]:
    return [(k, flt[k]) for k in FILTER_KEYS]


def _page(ctx: Ctx, conn, flt: Dict[str, str], status: int = 200, **kw):
    key = flt["soort"]
    rows, more = batch.find(conn, _sources(ctx, key), doctypes.KINDS[key].formname, _batch_filter(flt), MAX_LIST)
    nf = ctx.user["numberformat"]
    return render(ctx, "batch/batch.html", status, flt=flt, kinds=_kinds(ctx), rows=rows, more=more,
                  kind=doctypes.KINDS[key], can_send=ctx.has_role("bookkeeper"), checked=kw.pop("checked", set()),
                  max_batch=MAX_BATCH, max_list=MAX_LIST,
                  fmtdate=lambda d: format_date(d, ctx.user["dateformat"]) if d else "",
                  amount=lambda v: format_amount(v, nf, 2, "0").replace("&nbsp;", "").strip(),
                  screen=lambda r: _screen_url(key, r), **kw)


def _screen_url(key: str, r: batch.BatchRow) -> str:
    """Scherm van het document (factuur-, order- of boekingsscherm)."""
    if r.table == "oe":
        otype = {"customer": ("sales_order", "sales_quotation"), "vendor": ("purchase_order", "request_quotation")}
        kind = doctypes.KINDS[key]
        quo = key in ("sales_quotation", "request_quotation")
        vc = "customer" if key in ("packing_list", "pick_list") else kind.vc
        return f"/{doctypes.SCREEN_URL[('order', otype[vc][quo])]}/{r.id}"
    return f"/boekingen/{r.id}"


@router.get("/documenten/batch")
def batch_page(ctx: Ctx = Depends(ctx_company)):
    flt, error = _filters(ctx, ctx.request.query_params)
    conn, lib = _lib(ctx)
    with conn:
        return _page(ctx, conn, flt, 400 if error else 200, error=error)


def _selected(ctx: Ctx, conn, key: str, form: FormData) -> Tuple[List[doctypes.Located], int]:
    """Aangevinkte documenten in de volgorde van de lijst; wat niet (meer) bij de soort past of in een
    module zit die uit staat telt als ontbrekend."""
    mods = set(ctx.company["modules"] or [])
    out, missing = [], 0
    for x in dict.fromkeys(str(v) for v in form.getlist("sel")):
        loc = doctypes.locate(conn, key, int(x)) if x.isdigit() else None
        if loc is None or loc.module not in mods:
            missing += 1
            continue
        out.append(loc)
    return out, missing


def _vars(ctx: Ctx, conn, lib, locs: List[doctypes.Located]):
    """Sjabloonvariabelen per document; documenten die niet te lezen zijn komen in de tweede lijst."""
    pd = print_data(ctx, conn, lib)
    good, bad = [], []
    for loc in locs:
        try:
            good.append((loc, doctypes.print_vars(pd, loc)))
        except NotFoundError:
            bad.append(loc.number or str(loc.trans_id))
    return good, bad


@router.post("/documenten/batch")
def batch_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    action = str(form.get("action") or "")
    flt, error = _filters(ctx, form)
    key = flt["soort"]
    conn, lib = _lib(ctx)
    with conn:
        if action == "search" or error:
            return _page(ctx, conn, flt, 400 if error else 200, error=error)
        if action not in ("preview", "pdf", "mail", "mail_send"):
            raise HTTPException(400, _("Onbekende actie."))
        locs, missing = _selected(ctx, conn, key, form)
        checked = {loc.trans_id for loc in locs}
        note = _("{n} document(en) niet (meer) gevonden.").format(n=missing) if missing else None
        if not locs:
            return _page(ctx, conn, flt, 400, checked=checked, error=note or _("Vink minstens één document aan."))
        if len(locs) > MAX_BATCH:
            return _page(ctx, conn, flt, 400, checked=checked, error=_(
                "Te veel documenten in één keer ({n}); maximaal {max}. Maak de selectie kleiner.").format(
                    n=len(locs), max=MAX_BATCH))
        if action != "preview":
            require_role(ctx, "bookkeeper")
        docs, bad = _vars(ctx, conn, lib, locs)
        if bad:
            return _page(ctx, conn, flt, 400, checked=checked, error=_(
                "Deze documenten konden niet worden gelezen: {list}").format(list=", ".join(bad)))
        rnd = renderer(ctx, conn)
        label = _(doctypes.KINDS[key].label)
        if action in ("preview", "pdf"):
            try:
                pdf = rnd.render_many([(key, v) for __, v in docs])
            except Exception as exc:
                ctx.log("batch_pdf_failed", formname=key, error=str(exc)[:300])
                return _page(ctx, conn, flt, 400, checked=checked,
                             error=_("De PDF kon niet worden gemaakt; er is niets vastgelegd."))
            if action == "pdf":
                for loc, __ in docs:
                    mark_status(conn, lib.cfg, loc.trans_id, loc.kind.formname, printed=True, table=loc.table,
                                reference=loc.number)
                ctx.log("batch_printed", formname=key, trans_ids=[loc.trans_id for loc, __ in docs])
            name = (filename(docs[0][0])[:-4] if len(docs) == 1
                    else f"{label}-{len(docs)}-{date.today().isoformat()}")
            return _pdf(pdf, name, download=action == "pdf")
        # mailen
        try:
            ms = _mail_settings(ctx)
        except MailError as exc:
            return _page(ctx, conn, flt, 400, checked=checked, error=exc.text())
        subject = str(form.get("onderwerp") or "").strip()
        body = str(form.get("bericht") or "").replace("\r\n", "\n").strip()[:5000]
        if any(c in subject for c in "\r\n"):
            subject = ""
        mails, without = [], []
        for loc, v in docs:
            who = v.get("name") or ""
            if not v.get("email"):
                without.append(f"{loc.number} – {who}")
                continue
            mails.append(_prepare(ctx, rnd, ms, Outgoing(
                f"{key}:{loc.trans_id}", who, v["email"], v.get("cc") or "", v.get("bcc") or "", key, v,
                filename(loc), str(v.get("language_code") or ""),
                (lambda loc=loc: mark_status(conn, lib.cfg, loc.trans_id, loc.kind.formname, emailed=True,
                                             table=loc.table, reference=loc.number)),
                label=f"{loc.number} – {who}", mail_vars=mail_vars(loc, v),
                noted=(lambda out, loc=loc: add_intnote(conn, loc.trans_id, _intnote(ctx, out), table=loc.table))),
                subject=subject, body=body))
        if action == "mail":
            return render(ctx, "dunning/mail_confirm.html", kind="batch", f=None, mails=mails, without=without,
                          note=note, back="/documenten/batch", custom={"subject": subject, "body": body},
                          placeholders=list(mailer.PLACEHOLDERS)[0 if ctx.user.get("language", "nl") == "nl" else 1::2],
                          cancel="/documenten/batch?" + urlencode(_hidden(flt)),
                          title=_("{soort} mailen").format(soort=label), who=_("Document"),
                          intro=_("Per document gaat er één mail met het document als PDF naar het adres van de "
                                  "relatie. Laat onderwerp en bericht leeg voor de standaardtekst uit Beheer > "
                                  "E-mail (in de taal van de relatie), of vul een eigen tekst in voor alle "
                                  "documenten; plaatshouders worden per document ingevuld."),
                          hidden=_hidden(flt) + [("sel", str(loc.trans_id)) for loc, __ in docs])
        results = _send_all(ctx, rnd, ms, mails)
        ctx.log("batch_emailed", formname=key, sent=sum(r["ok"] for r in results),
                failed=sum(not r["ok"] for r in results), without=len(without))
        return _page(ctx, conn, flt, results=results, without=without,
                     notice=_("Gemaild: {ok}, mislukt: {failed}, zonder e-mailadres: {none}.").format(
                         ok=sum(r["ok"] for r in results), failed=sum(not r["ok"] for r in results),
                         none=len(without)))
