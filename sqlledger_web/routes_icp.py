"""ICP-opgaaf (ICP_ANALYSE.md ICP1–ICP11; ``sqlledger.icp``).

* ``/btw/icp``: per afnemer landcode, btw-nummer, leveringen en diensten (3a van het portaal) en daaronder de
  ABC-leveringen (3b), in de volgorde van het portaal (ICP9); doorklikken per afnemer, PDF, Excel en CSV; totaal
  naast rubriek 3B van de aangifte (ICP3); verschil met de vastgelegde opgaaf (ICP8).
* ``POST /btw/icp/vastleggen``: opgaaf vastleggen als het tijdvak anders is dan dat van de btw-aangifte (anders gaat
  dat samen met de btw-boeking, ``routes_vat.register``).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import docsettings
from sqlledger._db import cursor, get_defaults
from sqlledger.amounts import format_amount
from sqlledger.dates import format_date
from sqlledger.icp import IcpLine, IcpReport, IcpReturns, free_period_key
from sqlledger.vatreturn import recent_periods

from .companies import connect_company
from .i18n import N_, gettext as _
from .reportpdf import pdf_response
from .routes_vat import BOX_LABELS, KINDS, PROBLEM_LABELS, parse_period, period_label, require_vat
from .tables import Col, Row, Table, cell_text, to_csv, to_xlsx
from .web import Ctx, Redirect, csrf_form, ctx_company, render

router = APIRouter()

#: ICP-soort bij de klant (klantscherm)
CUSTOMER_KIND_LABELS = (("", N_("Leveringen (standaard)")), ("services", N_("Diensten")),
                        ("abc", N_("ABC-leveringen (vereenvoudigde regeling)")))


def sync_icp_flag(ctx: Ctx, conn: psycopg.Connection) -> None:
    """Menu-item ICP-opgaaf aan/uit (ICP1): een btw-rekening of een klant op rubriek 3B."""
    on = IcpReturns(conn).configured()
    if bool(ctx.company.get("icp")) != on:
        ctx.conn.execute("UPDATE company SET icp = %s WHERE id = %s", (on, ctx.company["id"]))
        ctx.company["icp"] = on


def _open(ctx: Ctx) -> psycopg.Connection:
    try:
        return connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))


# ------------------------------------------------------------------ tabellen

def _amount_col(key: str, label: str) -> Col:
    return Col(key, label, "amount", 0, "0")


def _customer_link(ln: IcpLine) -> str:
    return f"/klanten/{ln.customers[0][0]}" if ln.customers else ""


def main_table(rep: IcpReport, link) -> Table:
    """3a van het portaal: landcode, btw-nummer, leveringen, diensten."""
    cols = [Col("country", N_("Landcode")), Col("number", N_("Btw-nummer")),
            _amount_col("goods", N_("Leveringen")), _amount_col("services", N_("Diensten")),
            Col("name", N_("Afnemer")), Col("problem", N_("Opmerking"))]
    rows: List[Row] = []
    for ln in rep.lines:
        if not (ln.rounded["goods"] or ln.rounded["services"] or ln.exact["goods"] or ln.exact["services"]):
            continue
        rows.append(Row("row", {"country": ln.country, "number": ln.number, "goods": ln.rounded["goods"],
                                "services": ln.rounded["services"], "name": ln.name,
                                "problem": _(PROBLEM_LABELS[ln.problem]) if ln.problem else ""},
                        links={"number": link(ln.key), "name": link(ln.key),
                               **({"problem": _customer_link(ln)} if ln.problem else {})}))
    rows.append(Row("total", {"number": _("Totaal"), "goods": rep.totals["goods"],
                              "services": rep.totals["services"]}))
    return Table(cols, rows, title=_("Intracommunautaire leveringen en diensten"))


def abc_table(rep: IcpReport, link) -> Table:
    """3b van het portaal: ABC-leveringen (vereenvoudigde regeling)."""
    cols = [Col("country", N_("Landcode")), Col("number", N_("Btw-nummer")), _amount_col("abc", N_("Bedrag")),
            Col("name", N_("Afnemer")), Col("problem", N_("Opmerking"))]
    rows = [Row("row", {"country": ln.country, "number": ln.number, "abc": ln.rounded["abc"], "name": ln.name,
                        "problem": _(PROBLEM_LABELS[ln.problem]) if ln.problem else ""},
                links={"number": link(ln.key), "name": link(ln.key)})
            for ln in rep.lines if ln.exact["abc"]]
    rows.append(Row("total", {"number": _("Totaal"), "abc": rep.totals["abc"]}))
    return Table(cols, rows, title=_("ABC-leveringen (vereenvoudigde regeling)"))


def detail_table(ln: IcpLine) -> Table:
    cols = [Col("transdate", N_("Datum"), "date"), Col("number", N_("Nummer")),
            Col("goods", N_("Leveringen"), "amount", 2, "-"), Col("services", N_("Diensten"), "amount", 2, "-"),
            Col("abc", N_("ABC"), "amount", 2, "-")]
    rows = [Row("row", {"transdate": t.transdate, "number": t.number or str(t.trans_id), "goods": t.goods,
                        "services": t.services, "abc": t.abc}, links={"number": f"/boekingen/{t.trans_id}"})
            for t in sorted(ln.transactions, key=lambda x: (x.transdate, x.number))]
    rows.append(Row("total", {"number": _("Totaal (exact)"), **ln.exact}))
    title = f"{ln.country}{ln.number} {ln.name}".strip() if not ln.problem else ln.name
    return Table(cols, rows, title=title)


# ------------------------------------------------------------------ overzicht

@router.get("/btw/icp")
def icp_return(ctx: Ctx = Depends(ctx_company)):
    require_vat(ctx)
    q = ctx.request.query_params
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    today = date.today()
    with _open(ctx) as conn:
        ir = IcpReturns(conn, ctx.user_config())
        kind = ir.period_kind()
        period = parse_period(q, kind, today)
        configured = ir.configured()
        rep = ir.report(period) if configured else None
        with cursor(conn) as cur:
            info = get_defaults(cur, "company")
            info["vatnumber"] = docsettings.load(conn).taxnumber  # V2: btw-nummer uit Documentinstellingen
    label = period_label(period, df)
    key = q.get("afnemer") or ""
    line = rep.line(key) if rep and key else None

    def link(k: str) -> str:
        return "/btw/icp?" + urlencode({"tijdvak": period.key, "afnemer": k}) + "#detail"

    title = _("ICP-opgaaf: {period}").format(period=label)
    head = [x for x in (_("Btw-nummer: {number}").format(number=info.get("vatnumber"))
                        if info.get("vatnumber") else "",
                        _("{fromdate} t/m {todate}").format(fromdate=format_date(period.fromdate, df),
                                                            todate=format_date(period.todate, df))) if x]
    tables = [main_table(rep, link)] if rep else []
    if rep and rep.totals_exact["abc"]:
        tables.append(abc_table(rep, link))
    export = q.get("export")
    if rep and export in ("pdf", "xlsx", "csv"):
        out = list(tables)
        if export == "xlsx":
            out += [detail_table(ln) for ln in rep.lines]
        elif line and export == "pdf":
            out.append(detail_table(line))
        filename = f"icp-opgaaf-{period.key}"
        if export == "pdf":
            return pdf_response(ctx, out, title, head, filename, info.get("company"))
        ctx.log("report_exported", report="icp-opgaaf", format=export)
        if export == "csv":
            return Response(to_csv(out, nf, df), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'})
        return Response(to_xlsx(out, title, [info.get("company") or ""] + head, nf, df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'})
    periods = [(p.key, period_label(p, df)) for p in recent_periods(kind, today, 12)]
    if period.key not in dict(periods):
        periods.insert(0, (period.key, label))
    return render(ctx, "vat/icp.html", rep=rep, configured=configured, period=period, label=label, title=title,
                  head=head, tables=tables, detail=detail_table(line) if line else None, key=key,
                  periods=periods, company_name=info.get("company") or ctx.company["name"],
                  cell=lambda c, r: cell_text(c, r, nf, df), problem_labels=PROBLEM_LABELS, kinds=KINDS,
                  box_label=BOX_LABELS["3B"], can_register=ctx.has_role("bookkeeper"),
                  export_query=urlencode({"tijdvak": period.key, "afnemer": key}),
                  customer_link=_customer_link,
                  amount=lambda v, p=0: format_amount(v, nf, p, "0").replace("&nbsp;", "").strip(),
                  date=lambda d: format_date(d, df) if d else "")


@router.post("/btw/icp/vastleggen")
def icp_register(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    """ICP8: de opgaaf vastleggen (na het indienen in het portaal)."""
    require_vat(ctx)
    if not ctx.has_role("bookkeeper"):
        raise HTTPException(403, _("Alleen een boekhouder of beheerder kan dit doen."))
    key = str(form.get("tijdvak") or "")
    period = free_period_key(key)
    if period is None:
        raise HTTPException(400, _("Ongeldige datum."))
    with _open(ctx) as conn:
        ir = IcpReturns(conn, ctx.user_config())
        if not ir.configured():
            raise Redirect("/btw/icp")
        ir.register(ir.report(period), None, ctx.employee_login)
    ctx.log("icp_return_registered", period=period.key)
    raise Redirect(f"/btw/icp?tijdvak={period.key}&ok=icp_registered")


def icp_todo(conn, ctx: Ctx, today: date, view) -> Dict[str, Any]:
    """Dashboard (ICP1): eigen regel bij de btw-taak."""
    due = IcpReturns(conn, ctx.user_config()).due(today)
    if not due:
        return {}
    p = due["period"]
    what = _("ICP-opgaaf {period}").format(period=period_label(p, ctx.user["dateformat"]))
    text = _("{what}: vóór {date}").format(what=what, date=view.date(due["deadline"]))
    return {"text": text, "href": f"/btw/icp?tijdvak={p.key}", "level": "warn"}
