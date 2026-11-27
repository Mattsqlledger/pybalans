"""Kas en bank > Betalingsregister en de betalingsspecificatie (fase 4 stap 4b, SB7–SB8, SB15).

Een betalingsspecificatie is een document per geboekte betaling per relatie (``sqlledger.paymentspec``):
``receipt`` bij een ontvangst van een klant, ``check`` bij een betaling aan een leverancier (namen zoals
SQL-Ledger). Te maken vanuit:

* de melding na het boeken (één relatie: direct; meerdere relaties: per relatie en alles samen);
* het corrigeerscherm (aangevinkte betalingen);
* het **betalingsregister** (``/betalingsregister``): lijst van geboekte betalingen met filters, vinkjes en
  de knoppen Voorbeeld, PDF en Mailen.

Alle knoppen posten naar ``/betalingsregister`` met ``soort`` en de sleutels ``sel`` (rekening|kenmerk|datum|
relatie, zoals de regels van het register). **Voorbeeld** legt niets vast (alle rollen met de module);
**PDF** (één PDF van alle aangevinkte specificaties) en **Mailen** (per relatie een eigen mail, vastleggen
alleen na verzending) schrijven een audittrailregel per factuur; boekhouder en hoger. Geen ``status``
(SL heeft er geen).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import _db
from sqlledger.amounts import format_amount
from sqlledger.dates import format_date
from sqlledger.paymentspec import PaymentSpec, PaymentSpecs, spec_vars

from .i18n import gettext as _
from .mailer import MailError
from .routes_arap import _date, _lib
from .routes_cp import KIND, _choices, _register_rows
from .routes_docs import print_data, renderer, safe_filename
from .routes_dunning import MAX_MAIL, Outgoing, _mail_settings, _pdf, _prepare, _send_all
from .routes_reports import require_module
from .web import Ctx, csrf_form, ctx_company, render, require_role

router = APIRouter()

#: soort in de url -> relatiesoort
SOORT = {"ontvangsten": "customer", "betalingen": "vendor"}
VC_SOORT = {v: k for k, v in SOORT.items()}
FILTER_KEYS = ("rekening", "relatie", "kenmerk_van", "kenmerk_tot", "van", "tot")


def _soort(src) -> str:
    """Relatiesoort uit ``soort`` (ontvangsten/betalingen, of customer/vendor zoals het corrigeerscherm)."""
    s = str(src.get("soort") or "")
    if s in SOORT:
        return SOORT[s]
    return s if s in VC_SOORT else "customer"


def _default_from() -> str:
    """Standaardperiode: de vorige en de lopende maand."""
    t = date.today()
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return date(y, m, 1).isoformat()


def _filters(src) -> Dict[str, str]:
    out = {k: str(src.get(k) or "").strip() for k in FILTER_KEYS}
    if not any(out.values()) and "van" not in src:
        out["van"] = _default_from()
    for k in ("van", "tot"):
        if out[k] and not _date(out[k]):
            out[k] = ""
    out["weergave"] = "exclude"
    return out


def _rows(lib, vc: str, flt: Dict[str, str]) -> List[Dict[str, Any]]:
    rows = [r for r in _register_rows(lib, vc, flt) if r["amount"] and not r["void"]]
    if rows:
        with _db.cursor(lib.conn) as cur:
            cur.execute(f"SELECT id, email FROM {vc} WHERE id = ANY(%s)", (list({r["vc_id"] for r in rows}),))
            emails = {r["id"]: r["email"] or "" for r in cur.fetchall()}
        for r in rows:
            r["email"] = emails.get(r["vc_id"], "")
    return rows


def register_page(ctx: Ctx, conn, lib, vc: str, flt: Dict[str, str], status: int = 200, **kw):
    ch = _choices(lib, vc)
    rows = _rows(lib, vc, flt)
    nf = ctx.user["numberformat"]
    return render(ctx, "cp/payregister.html", status, vc=vc, soort=VC_SOORT[vc], k=KIND[vc], ch=ch, flt=flt,
                  rows=rows, can_send=ctx.has_role("bookkeeper"), checked=kw.pop("checked", set()),
                  fmtdate=lambda d: format_date(d, ctx.user["dateformat"]) if d else "",
                  amount=lambda v, dash="": format_amount(v, nf, ch["precision"], dash).replace("&nbsp;", "").strip(),
                  query=urlencode({k: v for k, v in flt.items() if v and k != "weergave"}), **kw)


@router.get("/betalingsregister")
def payregister(ctx: Ctx = Depends(ctx_company)):
    require_module(ctx, "cash")
    q = ctx.request.query_params
    vc = _soort(q)
    conn, lib = _lib(ctx)
    with conn:
        return register_page(ctx, conn, lib, vc, _filters(q))


def _specs(lib, vc: str, keys: List[str]) -> Tuple[List[PaymentSpec], int]:
    ps = PaymentSpecs(lib.conn, lib.cfg)
    out, missing = [], 0
    for k in dict.fromkeys(keys):
        s = ps.find_key(vc, k)
        if s is None:
            missing += 1
        else:
            out.append(s)
    return out, missing


@router.post("/betalingsregister")
def payregister_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_module(ctx, "cash")
    action = str(form.get("action") or "")
    vc = _soort(form)
    flt = _filters(form)
    keys = [str(x) for x in form.getlist("sel")]
    conn, lib = _lib(ctx)
    with conn:
        if action == "search":
            return register_page(ctx, conn, lib, vc, flt)
        if action not in ("preview", "pdf", "mail", "mail_send"):
            raise HTTPException(400, _("Onbekende actie."))
        specs, missing = _specs(lib, vc, keys)
        note = (_("{n} betaling(en) niet (meer) gevonden of gestorneerd.").format(n=missing) if missing else None)
        if not specs:
            return register_page(ctx, conn, lib, vc, flt, 400, checked=set(keys),
                                 error=note or _("Vink minstens één betaling aan."))
        ps = PaymentSpecs(conn, lib.cfg)
        pd = print_data(ctx, conn, lib)
        rnd = renderer(ctx, conn)
        if action in ("preview", "pdf"):
            final = action == "pdf"
            if final:
                require_role(ctx, "bookkeeper")
            pdf = rnd.render_many([(s.formname, spec_vars(pd, s)) for s in specs])
            if final:
                for s in specs:
                    ps.record(s)
                ctx.log("payment_specs_printed", formname=specs[0].formname, payments=[s.key for s in specs])
            name = (f"{_('Betalingsspecificatie')}-{specs[0].name}-{specs[0].datepaid.isoformat()}" if len(specs) == 1
                    else f"{_('Betalingsspecificaties')}-{specs[0].datepaid.isoformat()}")
            return _pdf(pdf, name, download=final)
        require_role(ctx, "bookkeeper")
        try:
            ms = _mail_settings(ctx)
        except MailError as exc:
            return register_page(ctx, conn, lib, vc, flt, 400, error=exc.text(), checked=set(keys))
        if len(specs) > MAX_MAIL:
            return register_page(ctx, conn, lib, vc, flt, 400, checked=set(keys), error=_(
                "Te veel brieven in één keer ({n}); maximaal {max}. Maak de selectie kleiner.").format(
                    n=len(specs), max=MAX_MAIL))
        mails, without = [], []
        for s in specs:
            v = spec_vars(pd, s)
            if not v.get("email"):
                without.append(s.name)
                continue
            mails.append(_prepare(ctx, rnd, ms, Outgoing(
                s.key, s.name, v["email"], v.get("cc") or "", v.get("bcc") or "", s.formname, v,
                safe_filename(f"{_('Betalingsspecificatie')}-{s.name}-{s.datepaid.isoformat()}") + ".pdf",
                s.language_code, (lambda s=s: ps.record(s, emailed=True)))))
        if action == "mail":
            k = KIND[vc]
            return render(ctx, "dunning/mail_confirm.html", kind="payments", f=None, mails=mails, without=without,
                          note=note, back="/betalingsregister",
                          cancel="/betalingsregister?" + urlencode(dict(
                              {x: y for x, y in flt.items() if y and x != "weergave"}, soort=VC_SOORT[vc])),
                          title=_("Betalingsspecificaties mailen"), who=_(k["vc_label"]),
                          intro=_("Per betaling gaat er één mail met de specificatie als PDF. Onderwerp en bericht "
                                  "komen uit Beheer > E-mail (taal van de relatie)."),
                          hidden=[("soort", VC_SOORT[vc])] + [(x, y) for x, y in flt.items() if x != "weergave"]
                          + [("sel", s.key) for s in specs])
        results = _send_all(ctx, rnd, ms, mails)
        return register_page(ctx, conn, lib, vc, flt, results=results, without=without,
                             notice=_("Gemaild: {ok}, mislukt: {failed}, zonder e-mailadres: {none}.").format(
                                 ok=sum(r["ok"] for r in results), failed=sum(not r["ok"] for r in results),
                                 none=len(without)))


def posted_specs(lib, vc: str, keys: List[str]) -> List[Dict[str, Any]]:
    """Specificaties voor de melding na het boeken (sleutels uit de url, opgezocht in de administratie)."""
    specs, _missing = _specs(lib, vc, keys[:MAX_MAIL])
    return [{"key": s.key, "name": s.name, "source": s.source, "amount": s.total} for s in specs]


def sample_vars(ctx: Ctx, conn, lib, kind: str) -> Optional[Dict[str, Any]]:
    """Voorbeeld in Beheer > Sjablonen: de laatst geboekte ontvangst (``receipt``) of betaling (``check``)."""
    s = PaymentSpecs(conn, lib.cfg).latest("customer" if kind == "receipt" else "vendor")
    return spec_vars(print_data(ctx, conn, lib), s) if s else None

