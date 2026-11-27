"""Afletteren (fase 2, rc.pl): rekening tegen een afschrift aansluiten (L1–L5).

* ``/afletteren``: rekening (standaard betaalrekeningen, vinkje "alle
  balansrekeningen"), periode, samenvatting/detail en koersverschillen kiezen
  (GET); daarna het scherm met de regels, vinkjes, afschriftsaldo en verschil
  (POST). Het verschil rekent alleen de server (``static/rc.js`` vraagt het op
  met ``action=calc``; zonder JavaScript via "Bijwerken"). Opslaan kan alleen
  bij verschil 0 en slaat altijd de vinkjes op die op het scherm staan (L2).
* ``/afletteren/rapport``: afgeletterde posten met afletterdatum in de periode
  (rc.pl ``report=1``), met CSV/Excel.

Een vinkje draagt de sleutels (trans_id, datum, kenmerk) van de regel; regels
die een sleutel delen worden samen afgeletterd (zelfde ``data-group``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional, Set
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import LedgerError, OutOfBalanceError
from sqlledger._db import cursor, get_defaults
from sqlledger.amounts import format_amount
from sqlledger.dates import last_day_of_month
from sqlledger.errors import NotFoundError, ValidationError
from sqlledger.reconcile import Key, ReconcileResult, Reconciliation, Statement, parse_tokens

from .i18n import N_, gettext as _
from .libmsg import lib_message
from .routes_arap import _amount, _date, _lib
from .tables import Col, Row, Table, cell_text, to_csv, to_xlsx
from .reportpdf import pdf_response
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

ZERO = Decimal(0)

MESSAGES = {
    "Out of balance!": N_("Het verschil is niet 0: controleer de vinkjes en het afschriftsaldo."),
    "The lines changed meanwhile; reload the statement":
        N_("De regels zijn intussen gewijzigd (geboekt of afgeletterd door een ander); "
           "het scherm is opnieuw opgebouwd."),
    "From date is after to date": N_("De van-datum ligt na de tot-datum."),
    "Statement date missing!": N_("Vul de tot-datum (afschriftdatum) in."),
}


def message(exc: Exception) -> str:
    text = str(exc)
    if text in MESSAGES:
        return _(MESSAGES[text])
    return lib_message(exc)


def require_cash(ctx: Ctx, role: str = "bookkeeper") -> None:
    if "cash" not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("De module Kas en bank staat uit voor deze administratie."))
    require_role(ctx, role)


async def rc_form(request: Request) -> FormData:
    """Formulier met (veel) vinkjes: Starlette staat standaard maar 1.000 velden toe."""
    await request.form(max_fields=100_000, max_part_size=4 * 1024 * 1024)
    return await csrf_form(request)


@dataclass
class Sel:
    """Keuze rekening/periode (GET-parameters, ook verborgen in het POST-formulier)."""
    rekening: str = ""
    van: str = ""
    tot: str = ""
    weergave: str = "samenvatting"
    koers: bool = True
    alle: bool = False

    @property
    def summary(self) -> bool:
        return self.weergave != "detail"

    def params(self, **extra) -> str:
        d = dict(rekening=self.rekening, van=self.van, tot=self.tot, weergave=self.weergave,
                 koers="1" if self.koers else "0", alle="1" if self.alle else "", **extra)
        return urlencode({k: v for k, v in d.items() if v != ""})


def _last(values: List[str], default: str = "") -> str:
    return str(values[-1]).strip() if values else default


def sel_from(get) -> Sel:
    """``get(name)`` geeft de lijst waarden (checkbox met verborgen 0 ervoor: de laatste telt)."""
    return Sel(rekening=_last(get("rekening")), van=_last(get("van")), tot=_last(get("tot")),
               weergave="detail" if _last(get("weergave")) == "detail" else "samenvatting",
               koers=_last(get("koers"), "1") != "0", alle=_last(get("alle")) == "1")


def _dates(s: Sel, errors: List[str]):
    out = []
    for name in ("van", "tot"):
        text = getattr(s, name)
        d = _date(text)
        if text and not d:
            errors.append(_("Ongeldige datum: {value}").format(value=text))
        out.append(d)
    return out


def _defaults(rc: Reconciliation, s: Sel) -> None:
    """Standaardperiode: tot = einde vorige maand; van = dag na de vorige ronde, anders begin van die maand."""
    if s.van or s.tot:
        return
    tot = date.today().replace(day=1) - timedelta(days=1)
    van = tot.replace(day=1)
    try:
        hist = rc.history(s.rekening, 1)
    except NotFoundError:
        hist = []
    if hist:
        nxt = hist[0]["statementdate"] + timedelta(days=1)
        van = nxt
        if nxt > tot:
            tot = last_day_of_month(nxt)
    s.van, s.tot = van.isoformat(), tot.isoformat()


@dataclass
class View:
    st: Statement
    groups: Set[int]
    balance_text: str
    balance: Optional[Decimal]
    errors: List[str] = field(default_factory=list)

    @property
    def cleared_total(self) -> Decimal:
        return self.st.cleared_total(self.groups)

    @property
    def cleared_balance(self) -> Decimal:
        return self.st.cleared_balance(self.groups)

    @property
    def difference(self) -> Optional[Decimal]:
        return None if self.balance is None else self.st.difference(self.balance, self.groups)


def _view(st: Statement, nf: str, sel_keys: Optional[List[Key]] = None, balance_text: Optional[str] = None,
          errors: Optional[List[str]] = None) -> View:
    errors = errors if errors is not None else []
    if sel_keys is None:
        groups = {ln.group for ln in st.lines if ln.checked}
    else:
        groups = st.selected_groups(sel_keys)
        known = {k for ln in st.lines for k in ln.keys}
        if set(sel_keys) - known:
            errors.append(_(MESSAGES["The lines changed meanwhile; reload the statement"]))
    if balance_text is None:
        balance_text = format_amount(st.statementbalance, nf, st.precision, "0")
    try:
        balance = _amount(balance_text, nf, "afschriftsaldo")
    except ValueError as exc:
        errors.append(lib_message(exc))
        balance = None
    return View(st=st, groups=groups, balance_text=balance_text, balance=balance, errors=errors)


def page(ctx: Ctx, s: Sel, accounts, v: Optional[View] = None, *, status: int = 200, error=None, **kw):
    nf = ctx.user["numberformat"]
    p = v.st.precision if v else 2
    return render(ctx, "rc/statement.html", status, s=s, accounts=accounts, v=v, error=error,
                  amount=lambda x, dash="0": format_amount(x, nf, p, dash).replace("&nbsp;", "").strip(), **kw)


@router.get("/afletteren", name="rc_statement")
def statement_get(ctx: Ctx = Depends(ctx_company)):
    require_cash(ctx)
    q = ctx.request.query_params
    s = sel_from(q.getlist)
    conn, lib = _lib(ctx)
    rc = Reconciliation(conn, lib.cfg)
    with conn:
        accounts = rc.accounts(s.alle)
        if s.rekening and s.rekening not in {a["accno"] for a in accounts}:
            s.alle = True
            accounts = rc.accounts(True)
            if s.rekening not in {a["accno"] for a in accounts}:
                return page(ctx, s, accounts, status=404, error=_("Onbekende rekening."))
        if not s.rekening:
            return page(ctx, s, accounts)
        _defaults(rc, s)
        errors: List[str] = []
        van, tot = _dates(s, errors)
        if errors:
            return page(ctx, s, accounts, status=400, error=" ".join(errors))
        try:
            st = rc.statement(s.rekening, van, tot, summary=s.summary, fx=s.koers)
        except LedgerError as exc:
            return page(ctx, s, accounts, status=400, error=message(exc))
        notice = _("Afletterronde opgeslagen.") if q.get("ok") == "rc_saved" else None
        return page(ctx, s, accounts, _view(st, ctx.user["numberformat"]), notice=notice)


@dataclass
class Outcome:
    view: Optional[View] = None
    error: Optional[str] = None
    status: int = 200
    result: Optional[ReconcileResult] = None


def process(rc: Reconciliation, s: Sel, action: str, tokens: List[str], balance_text: str, nf: str) -> Outcome:
    """Wat het POST-formulier doet (ook gebruikt door de Perl-vergelijking): scherm opnieuw
    opbouwen met de vinkjes, rekenen (calc/update/select_all/deselect_all) of opslaan (save)."""
    errors: List[str] = []
    van, tot = _dates(s, errors)
    if errors or not s.rekening:
        return Outcome(error=" ".join(errors) or _("Kies een rekening."), status=400)
    try:
        st = rc.statement(s.rekening, van, tot, summary=s.summary, fx=s.koers)
        keys: List[Key] = []
        for t in tokens:
            keys += parse_tokens(str(t))
    except LedgerError as exc:
        return Outcome(error=message(exc), status=400)
    if action == "select_all":
        keys = [k for ln in st.lines for k in ln.keys]
    elif action == "deselect_all":
        keys = []
    v = _view(st, nf, keys, balance_text.strip(), errors)
    if action in ("calc", "update", "select_all", "deselect_all"):
        return Outcome(view=v, error=" ".join(v.errors) or None)
    if action != "save":
        raise HTTPException(400, _("Onbekende actie."))
    if not tot:
        return Outcome(view=v, error=_(MESSAGES["Statement date missing!"]), status=400)
    if v.errors:
        return Outcome(view=v, error=" ".join(v.errors), status=409 if keys else 400)
    if v.difference != 0:
        return Outcome(view=v, error=_(MESSAGES["Out of balance!"]), status=400)
    try:
        res = rc.reconcile(s.rekening, tot, v.balance, keys, van, summary=s.summary, fx=s.koers)
    except OutOfBalanceError as exc:
        return Outcome(view=v, error=message(exc), status=400)
    except (ValidationError, NotFoundError) as exc:
        st = rc.statement(s.rekening, van, tot, summary=s.summary, fx=s.koers)
        return Outcome(view=_view(st, nf), error=message(exc), status=409)
    return Outcome(view=v, result=res)


@router.post("/afletteren", name="rc_post")
def statement_post(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(rc_form)):
    require_cash(ctx)
    s = sel_from(form.getlist)
    action = str(form.get("action", "update"))
    conn, lib = _lib(ctx)
    rc = Reconciliation(conn, lib.cfg)
    with conn:
        accounts = rc.accounts(s.alle)
        out = process(rc, s, action, [str(t) for t in form.getlist("sel")],
                      str(form.get("statementbalance", "")), ctx.user["numberformat"])
        if out.result is None:
            if action == "calc":             # alleen het blok met de saldi ververst (rc.js)
                return page(ctx, s, accounts, out.view, status=out.status, calc_error=out.error)
            return page(ctx, s, accounts, out.view, status=out.status, error=out.error)
        res = out.result
        ctx.log("reconciled", accno=s.rekening, fromdate=s.van or None, statementdate=s.tot,
                statementbalance=str(out.view.balance), lines=res.lines, cleared=res.cleared,
                uncleared=res.uncleared, reconciliation_id=res.id)
    raise Redirect("/afletteren?" + s.params(ok="rc_saved"))


# ------------------------------------------------------------------ rapport

def report_table(st: Statement, title: str = "") -> Table:
    p = st.precision
    cols = [Col("transdate", _("Datum"), "date"), Col("source", _("Kenmerk")), Col("name", _("Omschrijving")),
            Col("cleared", _("Afgeletterd"), "date"), Col("debit", _("Debet"), "amount", p),
            Col("credit", _("Credit"), "amount", p)]
    rows = []
    for ln in st.lines:
        links = {"source": f"/boekingen/{ln.trans_ids[0]}"} if len(ln.trans_ids) == 1 else {}
        rows.append(Row("row", {"transdate": ln.transdate, "source": ln.source, "name": ", ".join(ln.names),
                                "cleared": ln.cleared, "debit": ln.debit or None, "credit": ln.credit or None},
                        links))
    rows.append(Row("total", {"name": _("Totaal"), "debit": st.total_debit, "credit": st.total_credit}, dash="0"))
    return Table(cols, rows, title)


@router.get("/afletteren/rapport", name="rc_report")
def report(ctx: Ctx = Depends(ctx_company)):
    require_cash(ctx, "readonly")
    q = ctx.request.query_params
    s = sel_from(q.getlist)
    df, nf = ctx.user["dateformat"], ctx.user["numberformat"]
    conn, lib = _lib(ctx)
    rc = Reconciliation(conn, lib.cfg)
    table = None
    error = None
    with conn:
        accounts = rc.accounts(s.alle)
        if s.rekening and s.rekening not in {a["accno"] for a in accounts}:
            s.alle = True
            accounts = rc.accounts(True)
        with cursor(conn) as cur:
            company = get_defaults(cur, "company").get("company")
        if s.rekening:
            errors: List[str] = []
            van, tot = _dates(s, errors)
            if errors:
                error = " ".join(errors)
            else:
                try:
                    st = rc.report(s.rekening, van, tot, summary=s.summary, fx=s.koers)
                    table = report_table(st, f"{st.accno} {st.description}")
                except LedgerError as exc:
                    error = message(exc)
    export = q.get("export")
    if table is not None and export == "pdf":
        period = _("{fromdate} t/m {todate}").format(fromdate=s.van or "…", todate=s.tot or "…")
        return pdf_response(ctx, [table], _("Afgeletterde posten"), [period], "afgeletterd", company)
    if table is not None and export in ("csv", "xlsx"):
        ctx.log("report_exported", report="afgeletterd", format=export)
        if export == "csv":
            return Response(to_csv([table], nf, df), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="afgeletterd.csv"'})
        info = [x for x in (company, _("{fromdate} t/m {todate}").format(fromdate=s.van or "…", todate=s.tot or "…"))
                if x]
        return Response(to_xlsx([table], _("Afgeletterde posten"), info, nf, df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="afgeletterd.xlsx"'})
    return render(ctx, "rc/report.html", 400 if error else 200, s=s, accounts=accounts, table=table,
                  error=error, cell=lambda c, r: cell_text(c, r, nf, df), company_name=company,
                  can_reconcile=ctx.has_role("bookkeeper"))
