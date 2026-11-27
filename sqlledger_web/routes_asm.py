"""Samengestelde artikelen (fase 3, SA4–SA9): assembleren/demonteren, "hoeveel kan ik maken",
stuklijsten (uitgeklapt), waar gebruikt en productiehistorie.

Het artikel zelf met zijn stuklijst wordt bewerkt in het gewone artikelscherm (``routes_ic``).
Assembleren boekt niets in het grootboek (zoals Perl): alleen de voorraad van product en
onderdelen verandert, vastgelegd in de productiehistorie.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import LedgerError
from sqlledger.assembly import Assemblies, InsufficientStock

from .i18n import N_, gettext as _
from .libmsg import lib_message
from .routes_ic import _decimals, _lib, _num, _parse, _precision, require_ic
from .tables import Col, Row, Table, cell_text, to_csv, to_xlsx
from .reportpdf import pdf_response
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()


def _asm(ctx: Ctx):
    conn, lib = _lib(ctx)
    return conn, lib, Assemblies(conn, ctx.user_config())


def _qdec(rows: List[Dict[str, Any]], *keys: str) -> int:
    return max([0] + [_decimals(Decimal(r[k])) for r in rows for k in keys if r.get(k) is not None])


def _pdec(rows: List[Dict[str, Any]], precision: int, *keys: str) -> int:
    """Prijsdecimalen: minstens de standaard, meer als een waarde ze heeft (max. 6)."""
    return min(6, max([precision] + [_decimals(Decimal(r[k]).quantize(Decimal("0.000001")))
                                     for r in rows for k in keys if r.get(k) is not None]))


def _export(ctx: Ctx, table: Table, name: str, title: str):
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    fmt = ctx.request.query_params.get("export")
    if fmt == "pdf":
        return pdf_response(ctx, [table], title, [], name)
    ctx.log("report_exported", report=name, format=fmt)
    if fmt == "csv":
        return Response(to_csv([table], nf, df), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    return Response(to_xlsx([table], title, [ctx.company["name"]], nf, df),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})


def _list(ctx: Ctx, table: Table, *, name: str, title: str, url: str, filters: List[Dict[str, Any]],
          intro: str = "", back: Optional[Dict[str, str]] = None, extra: Optional[Dict[str, Any]] = None):
    """Lijstpagina met filters en CSV/Excel (sjabloon ic/asmlist.html)."""
    q = ctx.request.query_params
    if q.get("export") in ("csv", "xlsx", "pdf"):
        return _export(ctx, table, name, title)
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    export_query = urlencode([(a, b) for a, b in q.multi_items() if a not in ("export", "ok")] or [("run", "1")])
    return render(ctx, "ic/asmlist.html", table=table, title=title, url=url, filters=filters, intro=intro,
                  back=back, export_query=export_query, count=sum(1 for r in table.rows if r.kind == "row"),
                  cell=lambda c, r: cell_text(c, r, nf, df), **(extra or {}))


def _text_filter(q, key: str, label: str, cls: str = "") -> Dict[str, Any]:
    return {"type": "text", "name": key, "label": label, "value": (q.get(key) or "").strip()[:200], "cls": cls}


def _date(q, key: str) -> Optional[date]:
    try:
        return date.fromisoformat(q.get(key)) if q.get(key) else None
    except ValueError:
        return None


def _get_product(lib, part_id: int):
    try:
        p = lib.get(part_id)
    except LedgerError:
        raise HTTPException(404, _("Artikel niet gevonden."))
    return p


# ------------------------------------------------------------------ assembleren (SA4–SA6)

def _build_page(ctx: Ctx, part_id: int, *, status: int = 200, error: Optional[str] = None,
                values: Optional[Dict[str, str]] = None, warning: Optional[List[Dict[str, Any]]] = None):
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    values = values or {"transdate": date.today().isoformat(), "mode": "build", "qty": "", "reference": "",
                        "notes": ""}
    conn, lib, asm = _asm(ctx)
    with conn:
        p = _get_product(lib, part_id)
        if p.kind != "assembly":
            raise HTTPException(404, _("Dit is geen samengesteld artikel."))
        try:
            qty = _parse(values["qty"], nf) if values["qty"] and any(c.isdigit() for c in values["qty"]) else Decimal(1)
        except ValueError:
            qty = Decimal(1)
        qty = abs(qty) or Decimal(1)
        signed = -qty if values["mode"] == "unbuild" else qty
        reqs = asm.requirements(part_id, signed)
        builds = asm.builds(part_id)
    qd = _qdec(reqs, "perassembly", "need", "onhand", "after")
    table = Table([Col("partnumber", _("Onderdeel"), "text"), Col("description", _("Omschrijving"), "text"),
                   Col("perassembly", _("Per stuk"), "amount", qd), Col("qty", _("Beweging"), "amount", qd),
                   Col("onhand", _("Voorraad nu"), "amount", qd, "0"), Col("after", _("Voorraad daarna"), "amount", qd, "0"),
                   Col("unit", _("Eenheid"), "text")],
                  [Row("short" if r["short"] else "row",
                       {**r, "qty": r["qty"] if r["stocked"] else None,
                        "onhand": r["onhand"] if r["stocked"] else None,
                        "after": r["after"] if r["stocked"] else None,
                        "description": r["description"] if r["stocked"] else
                        f'{r["description"]} ({_("dienst, geen voorraad")})'},
                       {"partnumber": f"/artikelen/{r['id']}"}) for r in reqs])
    hd = _qdec(builds, "qty")
    history = Table([Col("transdate", _("Datum"), "date"), Col("qty", _("Aantal"), "amount", hd),
                     Col("reference", _("Kenmerk"), "text"), Col("notes", _("Reden/notitie"), "text"),
                     Col("employee", _("Medewerker"), "text")],
                    [Row("row", dict(b), {"transdate": f"/artikelen-productie/{b['id']}"}) for b in reversed(builds)])
    return render(ctx, "ic/build.html", status, p=p, onhand=_num(p.onhand, nf, max(qd, _decimals(p.onhand))) or "0",
                  table=table, history=history, values=values, error=error, warning=warning,
                  shortages=[r for r in reqs if r["short"]], qty=_num(qty, nf, _decimals(qty)),
                  can_edit=ctx.has_role("bookkeeper") and not p.project_id,
                  cell=lambda c, r: cell_text(c, r, nf, df))


@router.get("/artikelen/{part_id}/assembleren", name="asm_build")
def build_page(part_id: int, ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    q = ctx.request.query_params
    values = None
    if q.get("aantal"):
        values = {"transdate": date.today().isoformat(), "mode": "unbuild" if q.get("demonteren") else "build",
                  "qty": q.get("aantal", "")[:30], "reference": "", "notes": ""}
    return _build_page(ctx, part_id, values=values)


@router.post("/artikelen/{part_id}/assembleren", name="asm_build_post")
def build_post(part_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_ic(ctx)
    require_role(ctx, "bookkeeper")
    values = {k: str(form.get(k, "")).strip()[:500] for k in ("transdate", "mode", "qty", "reference", "notes")}
    values["mode"] = "unbuild" if values["mode"] == "unbuild" else "build"
    action = str(form.get("action", "update"))
    if action == "update":
        return _build_page(ctx, part_id, values=values)
    if action not in ("build", "build_confirmed"):
        raise HTTPException(400, _("Onbekende actie."))
    nf = ctx.user["numberformat"]
    conn, lib, asm = _asm(ctx)
    with conn:
        try:
            if not values["qty"] or not any(c.isdigit() for c in values["qty"]):
                raise ValueError(_("Vul een aantal in."))
            qty = _parse(values["qty"], nf)
            if qty <= 0:
                raise ValueError(_("Vul een aantal groter dan 0 in; kies Demonteren om terug te halen."))
            try:
                d = date.fromisoformat(values["transdate"])
            except ValueError:
                raise ValueError(_("Ongeldige datum: {value}").format(value=values["transdate"]))
            r = asm.build(part_id, -qty if values["mode"] == "unbuild" else qty, transdate=d,
                          reference=values["reference"], notes=values["notes"],
                          allow_short=action == "build_confirmed")
        except InsufficientStock as exc:
            if exc.blocking:
                return _build_page(ctx, part_id, status=400, error=lib_message(exc), values=values)
            return _build_page(ctx, part_id, status=200, values=values, warning=exc.shortages)
        except (ValueError, LedgerError) as exc:
            return _build_page(ctx, part_id, status=400, error=lib_message(exc), values=values)
    ctx.log("asm_built", id=part_id, qty=str(r["qty"]), build=r["id"])
    raise Redirect(f"/artikelen/{part_id}/assembleren?ok={'asm_unbuilt' if r['qty'] < 0 else 'asm_built'}")


# ------------------------------------------------------------------ hoeveel kan ik maken (overzicht assembleren)

@router.get("/artikelen-assembleren", name="asm_overview")
def overview(ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    q = ctx.request.query_params
    filters = [_text_filter(q, "partnumber", N_("Nummer"), "short"), _text_filter(q, "description", N_("Omschrijving")),
               {"type": "check", "name": "obsolete", "label": N_("ook vervallen"), "value": q.get("obsolete") == "1"}]
    conn, lib, asm = _asm(ctx)
    with conn:
        rows = asm.can_make(filters[0]["value"], filters[1]["value"], filters[2]["value"])
    qd = _qdec(rows, "onhand", "rop")
    table = Table([Col("partnumber", _("Nummer"), "text"), Col("description", _("Omschrijving"), "text"),
                   Col("onhand", _("Voorraad"), "amount", qd, "0"), Col("rop", _("Bestelniveau"), "amount", qd),
                   Col("can_make", _("Te maken"), "amount", 0, "0"), Col("limit", _("Beperkt door"), "text"),
                   Col("unit", _("Eenheid"), "text"), Col("action", "", "text")],
                  [Row("row", {**r, "can_make": r["can_make"], "limit": r["limit"] if r["can_make"] is not None
                               else _("geen onderdelen met voorraad"),
                               "action": _("Assembleren")},
                       {"partnumber": f"/artikelen/{r['id']}", "action": f"/artikelen/{r['id']}/assembleren"})
                   for r in rows])
    return _list(ctx, table, name="assembleren", title=_("Assembleren"), url="/artikelen-assembleren",
                 filters=filters, intro=_("Hoeveel er van elk samengesteld artikel te maken is met de huidige "
                                          "voorraad van de directe onderdelen. Diensten tellen niet mee."))


# ------------------------------------------------------------------ stuklijsten (SA9)

def _bom_table(rows: List[Dict[str, Any]], precision: int, with_product: bool) -> Table:
    qd = _qdec(rows, "perassembly", "qty", "onhand")
    pd = _pdec(rows, precision, "lastcost", "linecost")
    out = []
    for r in rows:
        cells = {**r, "indent": r["level"]}
        if r["level"] == 0:
            out.append(Row("heading", cells, {"partnumber": f"/artikelen/{r['id']}"}))
            continue
        if not r.get("stocked", True):
            cells["onhand"] = None
        out.append(Row("row", cells, {"partnumber": f"/artikelen/{r['id']}"}))
    cols = [Col("level", _("Niveau"), "amount", 0), Col("partnumber", _("Nummer"), "text"),
            Col("description", _("Omschrijving"), "text"), Col("perassembly", _("Per stuk"), "amount", qd),
            Col("qty", _("Totaal"), "amount", qd), Col("unit", _("Eenheid"), "text"),
            Col("lastcost", _("Kostprijs (laatste inkoop)"), "amount", pd, "0"),
            Col("linecost", _("Totaal kostprijs"), "amount", pd, "0"),
            Col("onhand", _("Voorraad"), "amount", qd, "0")]
    return Table(cols, out)


@router.get("/artikelen/{part_id}/stuklijst", name="asm_explode")
def explode(part_id: int, ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    q = ctx.request.query_params
    nf = ctx.user["numberformat"]
    conn, lib, asm = _asm(ctx)
    with conn:
        p = _get_product(lib, part_id)
        if p.kind != "assembly":
            raise HTTPException(404, _("Dit is geen samengesteld artikel."))
        raw = (q.get("aantal") or "1").strip()[:30]
        try:
            qty = _parse(raw, nf) if any(c.isdigit() for c in raw) else Decimal(1)
        except ValueError:
            qty = Decimal(1)
        qty = qty if qty > 0 else Decimal(1)
        rows = asm.explode(part_id, qty)
        precision = _precision(conn)
    filters = [{"type": "text", "name": "aantal", "label": N_("Aantal producten"), "value": _num(qty, nf), "cls": "short"}]
    title = _("Stuklijst {number}").format(number=p.partnumber)
    return _list(ctx, _bom_table(rows, precision, False), name="stuklijst", title=title,
                 url=f"/artikelen/{part_id}/stuklijst", filters=filters,
                 intro=f"{p.description} — " + _("alle niveaus; Totaal = aantal voor het gekozen aantal producten."),
                 back={"url": f"/artikelen/{part_id}", "label": _("Terug naar het artikel")})


@router.get("/artikelen-stuklijsten", name="asm_bom_list")
def bom_list(ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    q = ctx.request.query_params
    filters = [_text_filter(q, "partnumber", N_("Nummer"), "short"), _text_filter(q, "description", N_("Omschrijving")),
               {"type": "check", "name": "top", "label": N_("alleen eindproducten"), "value": q.get("top") == "1"},
               {"type": "check", "name": "obsolete", "label": N_("ook vervallen"), "value": q.get("obsolete") == "1"}]
    conn, lib, asm = _asm(ctx)
    with conn:
        rows = asm.bom_list(filters[0]["value"], filters[1]["value"], filters[3]["value"], filters[2]["value"])
        precision = _precision(conn)
    return _list(ctx, _bom_table(rows, precision, True), name="stuklijsten", title=_("Stuklijsten"),
                 url="/artikelen-stuklijsten", filters=filters,
                 intro=_("Per samengesteld artikel de onderdelen over alle niveaus, voor één stuk."))


@router.get("/artikelen/{part_id}/waar-gebruikt", name="asm_where_used")
def where_used(part_id: int, ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    conn, lib, asm = _asm(ctx)
    with conn:
        p = _get_product(lib, part_id)
        rows = asm.where_used(part_id)
    qd = _qdec(rows, "perassembly", "qty", "onhand")
    table = Table([Col("level", _("Niveau"), "amount", 0), Col("partnumber", _("Samengesteld artikel"), "text"),
                   Col("description", _("Omschrijving"), "text"), Col("via", _("Via"), "text"),
                   Col("perassembly", _("Aantal in stuklijst"), "amount", qd),
                   Col("qty", _("Aantal per stuk product"), "amount", qd),
                   Col("onhand", _("Voorraad product"), "amount", qd, "0")],
                  [Row("row", {**r, "indent": r["level"] - 1}, {"partnumber": f"/artikelen/{r['id']}"}) for r in rows])
    return _list(ctx, table, name="waar-gebruikt", title=_("Waar gebruikt: {number}").format(number=p.partnumber),
                 url=f"/artikelen/{part_id}/waar-gebruikt", filters=[], intro=p.description,
                 back={"url": f"/artikelen/{part_id}", "label": _("Terug naar het artikel")})


# ------------------------------------------------------------------ productiehistorie

@router.get("/artikelen-productie", name="asm_history")
def history(ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    q = ctx.request.query_params
    datefrom, dateto = _date(q, "datefrom"), _date(q, "dateto")
    filters = [_text_filter(q, "partnumber", N_("Nummer"), "short"),
               {"type": "date", "name": "datefrom", "label": N_("Datum van"),
                "value": datefrom.isoformat() if datefrom else ""},
               {"type": "date", "name": "dateto", "label": N_("tot en met"),
                "value": dateto.isoformat() if dateto else ""}]
    conn, lib, asm = _asm(ctx)
    with conn:
        rows = asm.builds(None, datefrom, dateto, filters[0]["value"])
    qd = _qdec(rows, "qty")
    table = Table([Col("transdate", _("Datum"), "date"), Col("partnumber", _("Nummer"), "text"),
                   Col("description", _("Omschrijving"), "text"), Col("what", _("Soort"), "text"),
                   Col("qty", _("Aantal"), "amount", qd), Col("reference", _("Kenmerk"), "text"),
                   Col("notes", _("Reden/notitie"), "text"), Col("employee", _("Medewerker"), "text")],
                  [Row("row", {**r, "what": _("assembleren") if r["qty"] > 0 else _("demonteren")},
                       {"transdate": f"/artikelen-productie/{r['id']}", "partnumber": f"/artikelen/{r['parts_id']}"})
                   for r in rows])
    return _list(ctx, table, name="productie", title=_("Productiehistorie"), url="/artikelen-productie",
                 filters=filters)


@router.get("/artikelen-productie/{build_id}", name="asm_history_detail")
def history_detail(build_id: int, ctx: Ctx = Depends(ctx_company)):
    require_ic(ctx)
    conn, lib, asm = _asm(ctx)
    with conn:
        try:
            head, lines = asm.build_detail(build_id)
        except LedgerError:
            raise HTTPException(404, _("Niet gevonden."))
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    qd = _qdec(lines + [head], "perassembly", "qty", "oldonhand")
    table = Table([Col("partnumber", _("Onderdeel"), "text"), Col("description", _("Omschrijving"), "text"),
                   Col("perassembly", _("Per stuk"), "amount", qd), Col("qty", _("Beweging"), "amount", qd),
                   Col("oldonhand", _("Voorraad was"), "amount", qd, "0"), Col("unit", _("Eenheid"), "text")],
                  [Row("row", dict(r), {"partnumber": f"/artikelen/{r['parts_id']}"}) for r in lines])
    title = (_("Assembleren") if head["qty"] > 0 else _("Demonteren")) + f" {head['partnumber'] or ''}"
    facts = [(_("Datum"), cell_text(Col("d", "", "date"), Row("row", {"d": head["transdate"]}), nf, df)),
             (_("Aantal"), _num(head["qty"], nf, qd)),
             (_("Voorraad product was"), _num(head["oldonhand"], nf, qd) or "0"),
             (_("Kenmerk"), head["reference"] or ""), (_("Reden/notitie"), head["notes"] or ""),
             (_("Medewerker"), head["employee"] or head["login"] or "")]
    return _list(ctx, table, name="productie", title=title, url=f"/artikelen-productie/{build_id}", filters=[],
                 intro=head["description"] or "",
                 back={"url": f"/artikelen/{head['parts_id']}/assembleren", "label": _("Naar assembleren")},
                 extra={"facts": facts})
