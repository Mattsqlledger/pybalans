"""Rapporten (stap 3) en de leespagina van een boeking."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from psycopg.types.json import Jsonb

from sqlledger import LedgerError
from sqlledger._db import cursor, get_defaults, get_yearend
from sqlledger.dates import fiscal_label, fiscal_start_year, fiscal_year, format_date

from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .plchart import DEFAULT_VIEW, VIEW_LABELS, VIEWS
from .reportpdf import pdf_response
from .reportdefs import REPORTS, YEAREND, Fmt, Lib, Report, default_values, mutations_link, parse_values
from .tables import Col, Row, Table, cell_text, to_csv, to_xlsx
from .web import Ctx, Redirect, ctx_company, render

router = APIRouter()

PAGE_SIZE = 500
CHOICE_TYPES = ("select", "radio", "checks")


def require_module(ctx: Ctx, module: str) -> None:
    if module not in (ctx.company["modules"] or []):
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


def _load_prefs(ctx: Ctx, key: str) -> Dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT params FROM report_pref WHERE user_id = %s AND company_id = %s AND report = %s",
        (ctx.user["id"], ctx.company["id"], key)).fetchone()
    return row["params"] if row else {}


def _save_prefs(ctx: Ctx, key: str, values: Dict[str, Any]) -> None:
    ctx.conn.execute(
        "INSERT INTO report_pref (user_id, company_id, report, params) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (user_id, company_id, report) DO UPDATE SET params = excluded.params, updated = now()",
        (ctx.user["id"], ctx.company["id"], key, Jsonb(values)))


def _chart_view(ctx: Ctx, key: str) -> str:
    """Tabel / Grafiek / Beide (v1.1, GR2): uit de url (dan onthouden), anders de laatste keuze van deze
    gebruiker bij deze administratie (zelfde opslag als de rapportinstellingen, eigen regel)."""
    pref = f"{key}:weergave"
    view = ctx.request.query_params.get("weergave")
    if view in VIEWS:
        if _load_prefs(ctx, pref).get("weergave") != view:
            _save_prefs(ctx, pref, {"weergave": view})
        return view
    view = _load_prefs(ctx, pref).get("weergave")
    return view if view in VIEWS else DEFAULT_VIEW


def _fmt(ctx: Ctx) -> Fmt:
    return Fmt(ctx.user["numberformat"], ctx.user["dateformat"])


def _query_without(ctx: Ctx, *drop: str) -> str:
    from urllib.parse import urlencode
    return urlencode([(k, v) for k, v in ctx.request.query_params.multi_items() if k not in drop])


def _company_error(ctx: Ctx, exc: psycopg.Error):
    return render(ctx, "error.html", 503, status=503,
                  message=_("De administratie is niet bereikbaar: {error}").format(
                      error=str(exc).strip().splitlines()[0]))


@router.get("/rapporten/{key}")
def report(key: str, ctx: Ctx = Depends(ctx_company)):
    rep: Report = REPORTS.get(key)
    if not rep:
        raise HTTPException(404, _("Rapport niet gevonden."))
    require_module(ctx, rep.module)
    q = ctx.request.query_params
    full = q.get("run") == "1"
    view = _chart_view(ctx, key) if rep.chart else "tabel"
    fmt = _fmt(ctx)
    result = error = None
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error as exc:
        return _company_error(ctx, exc)
    with conn:
        lib = Lib(conn, ctx.user_config())
        with cursor(conn) as cur:
            yearend = get_yearend(cur)
        base = default_values(rep.fields, date.today(), yearend)
        if "method" in base:
            # VW1: beginstand van "Stelsel" = Instellingen "Rapporten standaard op kasbasis" (Form.pm all_years)
            with cursor(conn) as cur:
                base["method"] = "cash" if get_defaults(cur, "method").get("method") == "cash" else "accrual"
        if not full:
            # laatst gebruikte instellingen alleen voor het formulier; een link met run=1
            # (bladwijzer, doorklikken, gedeeld) geeft voor iedereen hetzelfde resultaat
            base.update({k: v for k, v in _load_prefs(ctx, key).items() if k in base and k != YEAREND})
        values, errors = parse_values(rep.fields, q, base, full)
        values[YEAREND] = yearend
        if values.get("period") == "fiscal" and yearend == 12:
            # boekjaar = kalenderjaar (weer): de keuze "Boekjaar" staat dan niet op het scherm (JA3 B)
            values.update(period="year", year=values.get("fyear") or values.get("year"))
        choices = {f.name: _choices(f, lib) for f in rep.fields if f.type in CHOICE_TYPES}
        if errors:
            error = " ".join(errors)
        elif full or rep.autorun:
            try:
                result = rep.run(lib, values, fmt)
            except (LedgerError, ValueError) as exc:
                error = lib_message(exc)
            if result is not None and full:
                hidden = {f.name for f in rep.fields if f.type == "hidden"} | {YEAREND}  # doorklik, geen voorkeur
                _save_prefs(ctx, key, {k: v for k, v in values.items() if k not in hidden})
        with cursor(conn) as cur:
            company = get_defaults(cur, "company").get("company")

    export = q.get("export")
    chart = result.chart if result is not None and view != "tabel" else None
    if result is not None and export == "pdf":
        return pdf_response(ctx, result.tables if view != "grafiek" else [], _(rep.title),
                            [result.subtitle] + result.info, key, company, chart=chart)
    if result is not None and export in ("csv", "xlsx"):
        ctx.log("report_exported", report=key, format=export)
        info = [x for x in [company, result.subtitle] + result.info if x]
        if export == "csv":
            return Response(to_csv(result.tables, fmt.nf, fmt.df), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{key}.csv"'})
        return Response(to_xlsx(result.tables, _(rep.title), info, fmt.nf, fmt.df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{key}.xlsx"'})

    page, pages = 1, 1
    tables = result.tables if result else []
    if result is not None and result.paginate:
        p = q.get("page", "1")
        page = int(p) if p.isdigit() else 1
        paged = [t.page(page, PAGE_SIZE) for t in tables]
        tables = [t for t, __ in paged]
        pages = max(n for __, n in paged) if paged else 1
        page = min(page, pages)
    return render(ctx, "reports/page.html", rep=rep, values=values, choices=choices, result=result,
                  tables=tables, report_error=error, company_name=company, page=page, pages=pages,
                  cell=lambda c, r: cell_text(c, r, fmt.nf, fmt.df),
                  export_query=_query_without(ctx, "export", "page"), years=_years(),
                  fyears=_fiscal_years(yearend), yearend=yearend,
                  view=view, chart=chart, view_labels=VIEW_LABELS,
                  view_query=_query_without(ctx, "export", "page", "weergave"),
                  chart_query=_query_without(ctx, "export", "page", "grafiek"),
                  chart_hidden=[(k, v) for k, v in q.multi_items() if k not in ("export", "page", "grafiek")])


def _choices(f, lib: Lib):
    """Keuzes voor een zoekveld met vertaalde labels. Vaste lijsten bevatten
    N_-teksten; de functies (afdelingen, rekeningen, valuta) geven data terug
    die niet vertaald wordt (hun vaste tekst zoals "alle" vertalen ze zelf)."""
    if callable(f.options):
        return f.options(lib)
    return [(value, _(label)) for value, label in (f.options or [])]


def _years():
    y = date.today().year
    return [str(i) for i in range(y + 1, y - 16, -1)]


def _fiscal_years(yearend: int):
    """Keuzelijst boekjaren (JA4): (beginjaar, naam), zoals ``_years`` van volgend jaar tot 15 jaar terug."""
    y = fiscal_start_year(date.today(), yearend)
    return [(str(i), fiscal_label(i, yearend)) for i in range(y + 1, y - 16, -1)]


MODULE_LABELS = {"gl": N_("Memoriaal"), "ar": N_("Verkoop"), "ap": N_("Inkoop")}


@router.get("/boekingen/{trans_id}")
def booking(trans_id: int, ctx: Ctx = Depends(ctx_company)):
    """Leespagina van één boeking (R2); memoriaal en verkoop/inkoop zonder artikelen gaan naar
    het bewerkscherm, facturen met artikelen blijven hier (fase 3)."""
    fmt = _fmt(ctx)
    try:
        conn = connect_company(ctx.settings, ctx.company)
    except psycopg.Error as exc:
        return _company_error(ctx, exc)
    with conn:
        lib = Lib(conn, ctx.user_config())
        entry = lib.ca.journal_entry(trans_id)
        p = lib.precision()
        with cursor(conn) as cur:
            yearend = get_yearend(cur)
    if not entry:
        raise HTTPException(404, _("Boeking niet gevonden."))
    h = entry["header"]
    if h["module"] == "gl":
        raise Redirect(f"/memoriaal/{int(trans_id)}")     # bewerkscherm (stap 4)
    if h["module"] in (ctx.company["modules"] or []):
        if h["invoice"]:                                   # factuur met artikelen (fase 3, F9)
            prefix = "verkoopfactuur" if h["module"] == "ar" else "inkoopfactuur"
        else:
            prefix = "verkoop" if h["module"] == "ar" else "inkoop"
        raise Redirect(f"/{prefix}/{int(trans_id)}")      # bewerkscherm (stap 5)
    cols = [Col("transdate", _("Datum"), "date"), Col("accno", _("Rekening")), Col("description", _("Omschrijving")),
            Col("debit", _("Debet"), "amount", p), Col("credit", _("Credit"), "amount", p),
            Col("source", _("Kenmerk")), Col("memo", _("Memo")), Col("projectnumber", _("Project")),
            Col("cleared", _("Afgeletterd"), "date")]
    rows = []
    for ln in entry["lines"]:
        start, end = fiscal_year(ln["transdate"], yearend)      # boekjaar van de boeking (JA)
        url = mutations_link(ln["accno"], False, start, end)
        rows.append(Row("row", dict(ln), {"accno": url, "description": url}))
    rows.append(Row("total", {"description": _("Totaal"), "debit": entry["total_debit"],
                              "credit": entry["total_credit"]}))
    if ctx.request.query_params.get("export") == "pdf":
        title = f"{_(MODULE_LABELS[h['module']])} {h.get('reference') or trans_id}"
        return pdf_response(ctx, [Table(cols, rows)], title, [format_date(h["transdate"], fmt.df), h.get("name") or "", h.get("description") or ""], f"boeking-{int(trans_id)}")
    return render(ctx, "reports/booking.html", h=h, module=MODULE_LABELS[h["module"]],
                  table=Table(cols, rows), precision=p, fmt=fmt,
                  cell=lambda c, r: cell_text(c, r, fmt.nf, fmt.df))
