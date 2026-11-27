"""Kas en bank > Betaalbestand maken (SEPA, BEHEER_EXTRA_ANALYSE.md IM9 A1 / IM10 A, keuzes SP1–SP13).

* ``GET /betalingen/betaalbestand``: betaalrekening (met IBAN), uitvoerdatum (standaard de volgende werkdag),
  vervaldatum t/m (standaard de uitvoerdatum), leverancier en versie; daaronder de open inkoopfacturen in euro met
  vinkje, voorgesteld bedrag (openstaand min korting), IBAN, omschrijving en opmerking. Wat al eens in een
  betaalbestand zat of niet kan (geen/ongeldige IBAN, land buiten SEPA, adres ontbreekt) staat uit.
* ``POST``: ``action=update`` rekent opnieuw (vinkjes en bedragen blijven, behalve bij een andere uitvoerdatum);
  ``action=create`` maakt het bestand (``sqlledger.sepa.SepaExport.create``) en gaat terug naar het scherm met de
  downloadlink. Er wordt niets geboekt.
* ``GET /betalingen/betaalbestand/<id>.xml``: een gemaakt bestand (opnieuw) downloaden.

Rechten zoals Betalingen: boekhouder en hoger met de module Kas en bank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import LedgerError
from sqlledger.amounts import format_amount, to_decimal
from sqlledger.dates import format_date
from sqlledger.sepa import DEFAULT_VERSION, VERSIONS, Candidate, Pick, SepaChangedError, SepaExport, next_workday

from .i18n import N_, gettext as _
from .libmsg import lib_message
from .routes_arap import _amount, _date, _lib
from .routes_cp import require_cash
from .web import Ctx, Redirect, csrf_form, ctx_company, render

router = APIRouter()

URL = "/betalingen/betaalbestand"

#: redenen uit sqlledger.sepa.PROBLEMS
PROBLEM_LABELS = {
    "iban_missing": N_("Leverancier heeft geen IBAN."),
    "iban_invalid": N_("IBAN van de leverancier is niet geldig."),
    "iban_not_sepa": N_("IBAN van de leverancier is van een land buiten SEPA."),
    "address_missing": N_("Adres van de leverancier ontbreekt (verplicht bij een betaling buiten de EER)."),
}
VERSION_LABELS = {"03": N_("pain.001.001.03 (Nederlandse banken)"), "09": N_("pain.001.001.09 (nieuwer)")}


@dataclass
class Row:
    c: Candidate
    checked: bool
    amount: str                         # zoals ingevuld
    error: str = ""


@dataclass
class Form:
    accno: str = ""
    execdate: str = ""
    oldexecdate: str = ""
    duedateto: str = ""
    vendor: str = ""
    version: str = DEFAULT_VERSION
    rows: List[Row] = field(default_factory=list)
    total: Decimal = Decimal(0)
    count: int = 0


def _fmt(v: Decimal, nf: str, dash: str = "") -> str:
    return format_amount(v, nf, 2, dash).replace("&nbsp;", "").strip()


def _page(ctx: Ctx, sx: SepaExport, f: Form, *, posted: Dict[str, str], status: int = 200,
          error: Optional[str] = None, created: Optional[Dict[str, Any]] = None):
    """Selectie opbouwen; ``posted``: vinkjes/bedragen van het vorige scherm (leeg = standaard)."""
    nf = ctx.user["numberformat"]
    accounts = sx.accounts()
    ok_accounts = [a for a in accounts if a.iban]
    if f.accno not in {a.accno for a in ok_accounts}:
        f.accno = ok_accounts[0].accno if ok_accounts else ""
    execdate = _date(f.execdate) or next_workday(date.today())
    f.execdate = execdate.isoformat()
    duedateto = _date(f.duedateto)
    if f.duedateto and not duedateto:
        error = error or _("Vervaldatum t/m is geen geldige datum.")
    if f.version not in VERSIONS:
        f.version = DEFAULT_VERSION
    sel = sx.candidates(execdate, duedateto=duedateto, vendor=f.vendor)
    same_date = f.oldexecdate == f.execdate
    f.rows, f.total, f.count = [], Decimal(0), 0
    for c in sel.candidates:
        i = c.trans_id
        if f"present_{i}" in posted:
            checked = c.selectable and posted.get(f"sel_{i}") == "1"
            amount = posted.get(f"bedrag_{i}", "") if same_date else _fmt(c.suggested, nf)
        else:
            checked, amount = c.default_checked(execdate), _fmt(c.suggested, nf)
        row = Row(c, checked, amount)
        if checked:
            try:
                value = _amount(amount, nf)
                if value <= 0:
                    row.error = _("Het bedrag moet groter zijn dan 0.")
                elif value > c.due:
                    row.error = _("Het bedrag is hoger dan wat openstaat.")
                else:
                    f.total += value
                    f.count += 1
            except ValueError as exc:
                row.error = str(exc)
        f.rows.append(row)
    f.oldexecdate = f.execdate
    by_vc: Dict[int, List[str]] = {}
    for n in sel.credit_notes:
        by_vc.setdefault(n.vc_id, []).append(f"{n.invnumber} ({_fmt(-n.due, nf)})")
    names = {r.c.vc_id: r.c.name for r in f.rows}
    credit_notes = [dict(name=names.get(vc, ""), notes=", ".join(v)) for vc, v in by_vc.items()]
    return render(ctx, "cp/sepa.html", status, f=f, accounts=accounts, ok_accounts=ok_accounts, error=error,
                  created=created, credit_notes=credit_notes, other_currency=sel.other_currency,
                  exports=sx.exports(), versions=VERSION_LABELS, problems=PROBLEM_LABELS,
                  amount=lambda v, dash="": _fmt(v, nf, dash), today=date.today().isoformat(),
                  fd=lambda d: format_date(d, ctx.user["dateformat"]) if d else "")


def _created(sx: SepaExport, export_id: str, nf: str) -> Optional[Dict[str, Any]]:
    if not export_id.isdigit():
        return None
    e = next((e for e in sx.exports(50) if e["id"] == int(export_id)), None)
    if not e:
        return None
    return dict(id=e["id"], count=e["count"], total=_fmt(to_decimal(e["total"]), nf),
                execdate=e["execdate"], url=f"{URL}/{e['id']}.xml")


@router.get(URL, name="sepa_new")
def sepa_new(ctx: Ctx = Depends(ctx_company)):
    require_cash(ctx)
    q = ctx.request.query_params
    conn, _lib_ = _lib(ctx)
    with conn:
        sx = SepaExport(conn, ctx.user_config())
        f = Form(accno=q.get("rekening", ""), execdate=q.get("uitvoerdatum", ""), duedateto=q.get("tot", ""),
                 vendor=q.get("leverancier", ""), version=q.get("versie", DEFAULT_VERSION))
        if not f.duedateto:
            f.duedateto = (_date(f.execdate) or next_workday(date.today())).isoformat()
        return _page(ctx, sx, f, posted={}, created=_created(sx, q.get("bestand", ""), ctx.user["numberformat"]))


@router.post(URL, name="sepa_post")
def sepa_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_cash(ctx)
    g = lambda k: str(form.get(k, "")).strip()  # noqa: E731
    posted = {k: str(v) for k, v in form.items()}
    f = Form(accno=g("accno"), execdate=g("execdate"), oldexecdate=g("oldexecdate"), duedateto=g("duedateto"),
             vendor=g("vendor"), version=g("version"))
    action = g("action") or "update"
    if action not in ("update", "create"):
        raise HTTPException(400, _("Onbekende actie."))
    nf = ctx.user["numberformat"]
    conn, _lib_ = _lib(ctx)
    with conn:
        sx = SepaExport(conn, ctx.user_config())
        if action == "update":
            return _page(ctx, sx, f, posted=posted)
        if f.oldexecdate != f.execdate:
            # uitvoerdatum gewijzigd zonder Bijwerken: eerst de bedragen (korting) opnieuw laten voorstellen
            return _page(ctx, sx, f, posted=posted, status=400,
                         error=_("De uitvoerdatum is gewijzigd; controleer de bedragen en klik opnieuw op "
                                 "“Betaalbestand maken”."))
        picks: List[Pick] = []
        for k in form.keys():
            if k.startswith("sel_") and k[4:].isdigit() and g(k) == "1":
                i = int(k[4:])
                try:
                    picks.append(Pick(i, _amount(g(f"bedrag_{i}"), nf), to_decimal(g(f"due_{i}")) if g(f"due_{i}")
                                      else None))
                except ValueError as exc:
                    return _page(ctx, sx, f, posted=posted, status=400, error=str(exc))
        execdate = _date(f.execdate)
        if not execdate:
            return _page(ctx, sx, f, posted=posted, status=400, error=_("Vul een geldige uitvoerdatum in."))
        try:
            res = sx.create(f.accno, execdate, f.version if f.version in VERSIONS else DEFAULT_VERSION, picks)
        except SepaChangedError as exc:
            # niets gemaakt: opnieuw voorstellen zoals de database nu is
            return _page(ctx, sx, f, posted={}, status=409, error=lib_message(exc))
        except LedgerError as exc:
            return _page(ctx, sx, f, posted=posted, status=400, error=lib_message(exc))
        ctx.log("sepa_export", export_id=res.id, msgid=res.msgid, version=VERSIONS[res.version], count=res.count,
                total=str(res.total), execdate=res.execdate.isoformat(), account=f.accno)
        raise Redirect(f"{URL}?" + urlencode(dict(rekening=f.accno, uitvoerdatum=f.execdate, tot=f.duedateto,
                                                  leverancier=f.vendor, versie=f.version, bestand=res.id)))


@router.get(URL + "/{export_id}.xml", name="sepa_file")
def sepa_file(export_id: int, ctx: Ctx = Depends(ctx_company)):
    require_cash(ctx)
    conn, _lib_ = _lib(ctx)
    with conn:
        found = SepaExport(conn, ctx.user_config()).export_file(export_id)
    if not found:
        raise HTTPException(404, _("Betaalbestand niet gevonden."))
    name, data = found
    return Response(data, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
