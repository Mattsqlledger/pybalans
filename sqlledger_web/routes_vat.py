"""Btw-aangifte (BTW_ANALYSE.md, BA1–BA9; ``sqlledger.vatreturn``).

* ``/btw/aangifte``: overzicht per rubriek zoals het aangifteformulier (BA1), doorklikken per rubriek naar de
  boekingen, PDF en Excel, knop **Btw-boeking maken** (BA6), vergelijking met de vastgelegde aangifte (BA7).
* ``/btw/rubrieken``: rubriek per btw-rekening, afdrachtrekening en rekening afrondingsverschillen (BA3/BA6).
* De btw-boeking zelf is een ingevuld memoriaal (``/memoriaal/nieuw?btw=<tijdvak>``, ``routes_gl``); na opslaan
  legt ``register`` het tijdvak vast.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.datastructures import FormData

from sqlledger import LedgerError, docsettings
from sqlledger._db import cursor, get_defaults
from sqlledger.amounts import format_amount
from sqlledger.dashboard import Dashboard
from sqlledger.dates import format_date
from sqlledger.icp import IcpReturns, vat_number_problems
from sqlledger.vatforms import FORMS, VatForm
from sqlledger.vatreturn import (ACCOUNT_BOXES, BOX, BOXES, Period, VatReport, VatReturns, free_period,
                                 last_closed, recent_periods)

from .companies import connect_company
from .i18n import N_, gettext as _
from .libmsg import lib_message
from .reportpdf import pdf_response
from .tables import Col, Row, Table, cell_text, to_xlsx
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role
from . import production as menu_flags

router = APIRouter()

#: omschrijving per rubriek (aangifteformulier omzetbelasting)
BOX_LABELS = {
    "1A": N_("Leveringen/diensten belast met hoog tarief"),
    "1B": N_("Leveringen/diensten belast met laag tarief"),
    "1C": N_("Leveringen/diensten belast met overige tarieven, behalve 0%"),
    "1D": N_("Privégebruik"),
    "1E": N_("Leveringen/diensten belast met 0% of niet bij u belast"),
    "2A": N_("Leveringen/diensten waarbij de heffing van omzetbelasting naar u is verlegd"),
    "3A": N_("Leveringen naar landen buiten de EU (uitvoer)"),
    "3B": N_("Leveringen naar of diensten in landen binnen de EU"),
    "3C": N_("Installatie/afstandsverkopen binnen de EU"),
    "4A": N_("Leveringen/diensten uit landen buiten de EU"),
    "4B": N_("Leveringen/diensten uit landen binnen de EU"),
    "5A": N_("Verschuldigde omzetbelasting (rubrieken 1 t/m 4)"),
    "5B": N_("Voorbelasting"),
    "5C": N_("Subtotaal (5A min 5B)"),
    "5D": N_("Vermindering volgens de kleineondernemersregeling"),
    "5E": N_("Schatting vorige aangifte(n)"),
    "5F": N_("Schatting deze aangifte"),
    "5G": N_("Totaal te betalen / terug te vragen"),
    "none": N_("niet in de aangifte"),
}
#: VAT Return (VK, VAT Notice 700/12): omschrijving per box en per koppeling op Btw-rubrieken / bij de klant
GB_LABELS = {
    "1": N_("Btw verschuldigd over verkopen en andere prestaties"),
    "2": N_("Btw verschuldigd over verwervingen van goederen uit de EU (Noord-Ierland)"),
    "3": N_("Totaal verschuldigde btw (box 1 + 2)"),
    "4": N_("Terug te vragen btw over inkopen en andere ontvangen prestaties"),
    "5": N_("Netto te betalen of terug te vragen btw (box 3 min 4)"),
    "6": N_("Totale waarde verkopen en andere prestaties, zonder btw"),
    "7": N_("Totale waarde inkopen en andere ontvangen prestaties, zonder btw"),
    "8": N_("Waarde leveringen van goederen naar de EU, zonder btw (Noord-Ierland)"),
    "9": N_("Waarde verwervingen van goederen uit de EU, zonder btw (Noord-Ierland)"),
    "none": N_("niet in de aangifte"),
}
GB_CHOICE_LABELS = {
    "1": N_("Box 1 btw op verkopen (grondslag in box 6)"),
    "1R": N_("Box 1 verlegd binnenland of uitgestelde invoer-btw (grondslag niet in box 6)"),
    "2": N_("Box 2 verwerving goederen uit de EU, Noord-Ierland (grondslag in box 9)"),
    "4": N_("Box 4 voorbelasting (grondslag in box 7)"),
    "6": N_("Box 6 (standaard)"),
    "8": N_("Box 8 goederen Noord-Ierland naar de EU (ook in box 6)"),
    "none": N_("niet in de aangifte"),
}
#: periodieke btw-aangifte België (Intervat-help kader II–VI): omschrijving per rooster
BE_LABELS = {
    "00": N_("Handelingen onderworpen aan een bijzondere regeling"),
    "01": N_("Handelingen waarvoor de btw verschuldigd is door de aangever – tarief 6 %"),
    "02": N_("Handelingen waarvoor de btw verschuldigd is door de aangever – tarief 12 %"),
    "03": N_("Handelingen waarvoor de btw verschuldigd is door de aangever – tarief 21 %"),
    "44": N_("Diensten waarvoor de buitenlandse btw verschuldigd is door de medecontractant"),
    "45": N_("Handelingen waarvoor de btw verschuldigd is door de medecontractant"),
    "46": N_("Vrijgestelde intracommunautaire leveringen verricht in België en ABC-verkopen"),
    "47": N_("Andere vrijgestelde handelingen en andere handelingen verricht in het buitenland"),
    "48": N_("Uitgereikte creditnota's en negatieve verbeteringen met betrekking tot de roosters 44 en 46"),
    "49": N_("Uitgereikte creditnota's en negatieve verbeteringen met betrekking tot andere handelingen van kader II"),
    "81": N_("Handelsgoederen, grond- en hulpstoffen"),
    "82": N_("Diensten en diverse goederen"),
    "83": N_("Bedrijfsmiddelen"),
    "84": N_("Ontvangen creditnota's en negatieve verbeteringen met betrekking tot de roosters 86 en 88"),
    "85": N_("Ontvangen creditnota's en negatieve verbeteringen met betrekking tot andere inkomende handelingen"),
    "86": N_("Intracommunautaire verwervingen verricht in België en ABC-verkopen"),
    "87": N_("Andere inkomende handelingen waarvoor de btw verschuldigd is door de aangever"),
    "88": N_("Intracommunautaire diensten met verlegging van heffing"),
    "54": N_("Btw op de handelingen aangegeven in de roosters 01, 02 en 03"),
    "55": N_("Btw op de handelingen aangegeven in de roosters 86 en 88"),
    "56": N_("Btw op de handelingen aangegeven in rooster 87, met uitzondering van invoer met verlegging van heffing"),
    "57": N_("Btw op invoer met verlegging van heffing"),
    "61": N_("Diverse btw-regularisaties in het voordeel van de Staat"),
    "63": N_("Terug te storten btw vermeld op ontvangen creditnota's"),
    "XX": N_("Totaal van de roosters 54, 55, 56, 57, 61 en 63"),
    "59": N_("Aftrekbare btw"),
    "62": N_("Diverse btw-regularisaties in het voordeel van de aangever"),
    "64": N_("Te recupereren btw vermeld op uitgereikte creditnota's"),
    "YY": N_("Totaal van de roosters 59, 62 en 64"),
    "71": N_("Verschuldigd aan de Staat"),
    "72": N_("Sommen verschuldigd door de Staat"),
    "none": N_("niet in de aangifte"),
}
BE_CHOICE_LABELS = {
    "01": N_("grondslag 6 %, btw in 54"), "02": N_("grondslag 12 %, btw in 54"), "03": N_("grondslag 21 %, btw in 54"),
    "59": N_("aftrekbare btw"),
    "86": N_("verlegd: intracommunautaire verwerving goederen (grondslag 86, btw 55)"),
    "88": N_("verlegd: intracommunautaire diensten (grondslag 88, btw 55)"),
    "87": N_("verlegd binnenland, medecontractant (grondslag 87, btw 56)"),
    "57": N_("verlegd: invoer (grondslag 87, btw 57)"),
    "00": N_("bijzondere regeling"), "44": N_("diensten aan een afnemer in de EU (verlegd)"),
    "45": N_("medecontractant: de klant voldoet de btw"), "46": N_("intracommunautaire leveringen en ABC"),
    "47": N_("47 vrijgesteld, uitvoer of buiten België (standaard)"),
    "81": N_("handelsgoederen, grond- en hulpstoffen"), "82": N_("diensten en diverse goederen"),
    "83": N_("bedrijfsmiddelen"),
    "none": N_("niet in de aangifte"),
}
#: kaders van het Belgische formulier: (code, titel, roosters)
BE_SECTIONS = (
    ("II", N_("Uitgaande handelingen"), ("00", "01", "02", "03", "44", "45", "46", "47", "48", "49")),
    ("III", N_("Inkomende handelingen"), ("81", "82", "83", "84", "85", "86", "87", "88")),
    ("IV", N_("Verschuldigde belastingen"), ("54", "55", "56", "57", "61", "63", "XX")),
    ("V", N_("Aftrekbare belastingen"), ("59", "62", "64", "YY")),
    ("VI", N_("Saldo"), ("71", "72")),
)
#: per land: omschrijving per vak, per keuze, en de standaard voor omzet zonder btw op het klantscherm
LABELS = {"NL": BOX_LABELS, "GB": GB_LABELS, "BE": BE_LABELS}
CHOICE_LABELS = {"NL": BOX_LABELS, "GB": GB_CHOICE_LABELS, "BE": BE_CHOICE_LABELS}
COUNTRY_LABELS = {"NL": N_("Nederland"), "BE": N_("België"), "GB": N_("Verenigd Koninkrijk")}
#: landen zonder aangifteformulier in PyBalans (VP3), voor de melding
OTHER_COUNTRIES = {"CH": N_("Zwitserland"), "DE": N_("Duitsland"), "FR": N_("Frankrijk")}
#: VK (BL7): kwartaal eindigt in …
OFFSET_LABELS = {0: N_("maart, juni, september, december"), 1: N_("april, juli, oktober, januari"),
                 2: N_("mei, augustus, november, februari")}
SECTIONS = (("1", N_("Prestaties binnenland")), ("2", N_("Verleggingsregelingen binnenland")),
            ("3", N_("Prestaties naar of in het buitenland")), ("4", N_("Prestaties vanuit het buitenland aan u verricht")),
            ("5", N_("Voorbelasting, kleineondernemersregeling, schatting en totaal")))
QUARTERS = {1: N_("1e kwartaal"), 2: N_("2e kwartaal"), 3: N_("3e kwartaal"), 4: N_("4e kwartaal")}
MONTHS = (N_("januari"), N_("februari"), N_("maart"), N_("april"), N_("mei"), N_("juni"), N_("juli"),
          N_("augustus"), N_("september"), N_("oktober"), N_("november"), N_("december"))
NOTES = {"split": N_("btw past niet bij het nettobedrag"), "customer": N_("zonder btw, rubriek van de klant"),
         "default": N_("zonder btw")}
KINDS = {"ar": N_("Verkoop"), "ap": N_("Inkoop"), "gl": N_("Memoriaal")}
#: problemen met het btw-nummer (sqlledger.icp.PROBLEMS)
PROBLEM_LABELS = {
    "missing": N_("geen btw-nummer"),
    "no_country": N_("landcode ontbreekt"),
    "greece": N_("gebruik EL voor Griekenland"),
    "not_eu": N_("geen EU-land"),
    "domestic": N_("Nederlands btw-nummer"),
    "format": N_("ongeldig btw-nummer"),
}


def require_vat(ctx: Ctx) -> None:
    if not set(ctx.company["modules"] or []) & {"ar", "ap"}:
        raise HTTPException(403, _("Deze module staat uit voor deze administratie."))


def period_label(p: Period, df: str) -> str:
    if p.kind == "quarter" and not p.number:          # kwartaal dat niet met het kalenderkwartaal samenvalt (VK)
        to_month = p.todate.month
        if p.fromdate.year == p.todate.year:
            return f"{_(MONTHS[p.fromdate.month - 1])} – {_(MONTHS[to_month - 1])} {p.todate.year}"
        return f"{_(MONTHS[p.fromdate.month - 1])} {p.fromdate.year} – {_(MONTHS[to_month - 1])} {p.todate.year}"
    if p.kind == "quarter":
        return _("{year} {quarter}").format(year=p.year, quarter=_(QUARTERS[p.number]))
    if p.kind == "month":
        return f"{_(MONTHS[p.number - 1])} {p.year}"
    if p.kind == "year":
        return str(p.year)
    return _("{fromdate} t/m {todate}").format(fromdate=format_date(p.fromdate, df), todate=format_date(p.todate, df))


def booking_description(p: Period, df: str, country: str = "NL") -> str:
    if country == "GB":
        return _("VAT Return {period}").format(period=period_label(p, df))
    if country == "BE":
        return _("Btw-aangifte België {period}").format(period=period_label(p, df))
    return _("Btw-aangifte {period}").format(period=period_label(p, df))


def return_title(country: str, label: str) -> str:
    if country == "GB":
        return _("VAT Return (Verenigd Koninkrijk): {period}").format(period=label)
    if country == "BE":
        return _("Periodieke btw-aangifte (België): {period}").format(period=label)
    return _("Btw-aangifte (factuurstelsel): {period}").format(period=label)


def company_flags(conn: psycopg.Connection, config) -> Dict[str, bool]:
    """``company.vat_return``/``icp`` uit de administratie zelf: btw-rubrieken ingesteld en tijdvak niet "geen"
    (BA2/BA8; per aangifteland); ICP-rubriek 3B (ICP1). Ook meteen na Back-up inlezen (F25)."""
    return {"vat_return": VatReturns(conn).configured() and Dashboard(conn, config).vat_period() != "none",
            "icp": IcpReturns(conn).configured()}


def sync_flag(ctx: Ctx, conn: psycopg.Connection) -> None:
    """Menu-item Btw-aangifte aan/uit (BA2/BA8): rubrieken ingesteld en tijdvak niet "geen"."""
    on = company_flags(conn, ctx.user_config())["vat_return"]
    if bool(ctx.company.get("vat_return")) != on:
        ctx.conn.execute("UPDATE company SET vat_return = %s WHERE id = %s", (on, ctx.company["id"]))
        ctx.company["vat_return"] = on
    from .routes_icp import sync_icp_flag
    sync_icp_flag(ctx, conn)                    # ICP-opgaaf (ICP1): rubriek 3B ingesteld


def _open(ctx: Ctx) -> psycopg.Connection:
    try:
        return connect_company(ctx.settings, ctx.company)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))


def _period_kind(conn, ctx) -> str:
    kind = Dashboard(conn, ctx.user_config()).vat_period()
    return "quarter" if kind == "none" else kind


def parse_period(q, kind: str, today: date, offset: int = 0) -> Period:
    """``tijdvak=<van>_<tot>`` of ``van``/``tot``; standaard het laatst afgesloten tijdvak."""
    key = q.get("tijdvak") or ""
    fromtext, totext = (key.split("_", 1) + [""])[:2] if key else (q.get("van") or "", q.get("tot") or "")
    if fromtext or totext:
        try:
            return free_period(date.fromisoformat(fromtext), date.fromisoformat(totext))
        except ValueError:
            raise HTTPException(400, _("Ongeldige datum."))
    return last_closed(kind, today, offset)


# ------------------------------------------------------------------ tabellen

def _amount_col(key: str, label: str) -> Col:
    return Col(key, label, "amount", 0, "0")


def gb_overview_table(rep: VatReport, link) -> Table:
    """VAT Return: box 1–9 in één kolom, hele ponden."""
    cols = [Col("box", N_("Box")), Col("label", N_("Omschrijving")), Col("amount", N_("Bedrag"), "amount", 0, "0")]
    rows: List[Row] = []
    for code in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
        if code in rep.totals:
            kind = "total" if code == "5" else "subtotal"
            rows.append(Row(kind, {"box": code, "label": _(GB_LABELS[code]), "amount": rep.totals[code]}))
            continue
        b = rep.boxes[code]
        value = b.base if code in ("6", "7", "8", "9") else b.tax
        rows.append(Row("row", {"box": code, "label": _(GB_LABELS[code]), "amount": value},
                        links={"box": link(code), "label": link(code)}))
    return Table(cols, rows)


def be_overview_table(rep: VatReport, link) -> Table:
    """Periodieke aangifte België: kader II–VI, één bedrag per rooster, hele euro's."""
    form = FORMS["BE"]
    cols = [Col("box", N_("Rooster")), Col("label", N_("Omschrijving")), Col("amount", N_("Bedrag"), "amount", 0, "0")]
    rows: List[Row] = []
    for code, title, grids in BE_SECTIONS:
        rows.append(Row("heading", {"box": code, "label": _(title)}))
        for g in grids:
            if g in rep.totals:
                rows.append(Row("total" if g in ("71", "72") else "subtotal",
                                {"box": g, "label": _(BE_LABELS[g]), "amount": rep.totals[g]}))
                continue
            b, fb = rep.boxes[g], form.box[g]
            r = Row("row", {"box": g, "label": _(BE_LABELS[g]), "amount": b.base if fb.base else b.tax})
            if g not in form.manual:
                r.links = {"box": link(g), "label": link(g)}
            rows.append(r)
    return Table(cols, rows)


def overview_table(rep: VatReport, link) -> Table:
    if rep.country == "GB":
        return gb_overview_table(rep, link)
    if rep.country == "BE":
        return be_overview_table(rep, link)
    cols = [Col("box", N_("Rubriek")), Col("label", N_("Omschrijving")),
            _amount_col("base", N_("Bedrag waarover btw")), _amount_col("tax", N_("Btw"))]
    rows: List[Row] = []
    for section, title in SECTIONS:
        rows.append(Row("heading", {"box": section, "label": _(title)}))
        if section == "5":
            for code in ("5A", "5B", "5C", "5D", "5G"):
                kind = {"5A": "subtotal", "5C": "subtotal", "5G": "total"}.get(code, "row")
                r = Row(kind, {"box": code, "label": _(BOX_LABELS[code]), "tax": rep.totals[code]})
                if code == "5B":
                    r.links = {"box": link("5B"), "label": link("5B")}
                rows.append(r)
            continue
        for b in BOXES:
            if not b.code.startswith(section):
                continue
            amt = rep.boxes[b.code]
            cells: Dict[str, Any] = {"box": b.code, "label": _(BOX_LABELS[b.code]), "base": amt.base}
            if b.tax or amt.tax:
                cells["tax"] = amt.tax
            rows.append(Row("row", cells, links={"box": link(b.code), "label": link(b.code)}))
    return Table(cols, rows)


def detail_table(rep: VatReport, box: str) -> Table:
    exact = rep.boxes.get(box)
    form = FORMS[rep.country]
    labels = LABELS[rep.country]
    cols = [Col("transdate", N_("Datum"), "date"), Col("kind", N_("Soort")), Col("number", N_("Nummer")),
            Col("name", N_("Naam / omschrijving")), Col("base", N_("Grondslag"), "amount", 2, "-"),
            Col("tax", N_("Btw"), "amount", 2, "-"), Col("note", N_("Opmerking"))]
    rows: List[Row] = []
    for t in rep.box_transactions(box):
        base, tax = t.boxes[box]
        rows.append(Row("row", {"transdate": t.transdate, "kind": _(KINDS[t.kind]), "number": t.number,
                                "name": t.name, "base": base, "tax": tax,
                                "note": _(NOTES[t.note]) if t.note else ""},
                        links={"number": f"/boekingen/{t.trans_id}"}))
    if exact:
        fb = form.box[box]
        rows.append(Row("total", {"name": _("Totaal (exact)"), "base": exact.base_exact if fb.base else None,
                                  "tax": exact.tax_exact if fb.tax or exact.tax_exact else None}))
    return Table(cols, rows, title=f"{box} {_(labels[box])}")


def accounts_table(rep: VatReport) -> Table:
    cols = [Col("accno", N_("Rekening")), Col("description", N_("Omschrijving")), Col("box", N_("Rubriek")),
            Col("amount", N_("Saldo tijdvak (credit)"), "amount", 2, "-")]
    rows = [Row("row", {"accno": a.accno, "description": a.description, "box": a.box, "amount": a.amount})
            for a in rep.accounts]
    rows.append(Row("total", {"description": _("Totaal (exact)"), "amount": rep.total_exact}))
    return Table(cols, rows, title=_("Btw-rekeningen"))


# ------------------------------------------------------------------ VAT Return (VK): formulier en bestand

#: officiële tekst van de boxes (VAT Notice 700/12); het formulier staat in de taal van het land (BL10)
GB_FORM_TEXT = (
    ("1", "VAT due in the period on sales and other outputs"),
    ("2", "VAT due in the period on acquisitions of goods made in Northern Ireland from EU member states"),
    ("3", "Total VAT due (the sum of boxes 1 and 2)"),
    ("4", "VAT reclaimed in the period on purchases and other inputs (including acquisitions from the EU)"),
    ("5", "Net VAT to pay to HMRC or reclaim (the difference between boxes 3 and 4)"),
    ("6", "Total value of sales and all other outputs excluding any VAT"),
    ("7", "Total value of purchases and all other inputs excluding any VAT"),
    ("8", "Total value of supplies of goods and related costs, excluding any VAT, to EU member states"),
    ("9", "Total value of acquisitions of goods and related costs, excluding any VAT, from EU member states"),
)


def gb_values(rep: VatReport, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Decimal]:
    """Box 1–9 zoals op het formulier (box 5 met teken: positief = betalen)."""
    if snapshot:
        boxes = {c: (Decimal(v[2]), Decimal(v[3])) for c, v in snapshot["boxes"].items()}
        totals = {k: Decimal(v) for k, v in snapshot["totals"].items()}
    else:
        boxes = {c: (b.base, b.tax) for c, b in rep.boxes.items()}
        totals = dict(rep.totals)
    out: Dict[str, Decimal] = {}
    for code, _text in GB_FORM_TEXT:
        if code in totals:
            out[code] = totals[code]
        else:
            base, tax = boxes.get(code, (Decimal(0), Decimal(0)))
            out[code] = base if code in ("6", "7", "8", "9") else tax
    return out


def gb_csv(rep: VatReport, vrn: str) -> str:
    """UK2: CSV voor bridging software (een "digital link", VAT Notice 700/22 §3.2.1): box 1–9 in hele ponden met
    ".00" (de HMRC-koppeling vraagt twee decimalen), box 5 als absoluut bedrag zoals HMRC vraagt."""
    import csv
    import io

    vals = gb_values(rep)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["VRN", vrn])
    w.writerow(["Period start", rep.period.fromdate.isoformat()])
    w.writerow(["Period end", rep.period.todate.isoformat()])
    w.writerow(["Box", "Description", "Value"])
    for code, text in GB_FORM_TEXT:
        v = abs(vals[code]) if code == "5" else vals[code]
        w.writerow([code, text, f"{v:.2f}"])
    w.writerow(["5 direction", "pay" if vals["5"] >= 0 else "reclaim", ""])
    return buf.getvalue()


def gb_form_data(rep: VatReport, ctx: Ctx, title: str, company: str, taxnumber: str, link=None) -> Dict[str, Any]:
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    stored = rep.stored if rep.stored and rep.stored.get("gl_exists") else None
    vals = gb_values(rep, stored["snapshot"] if stored else None)
    rows = []
    for code, text in GB_FORM_TEXT:
        v = abs(vals[code]) if code == "5" else vals[code]
        rows.append({"code": code, "text": text, "label": _(GB_LABELS[code]),
                     "value": format_amount(v, nf, 0, "0").replace("&nbsp;", "").strip(),
                     "link": link(code) if link and code not in ("3", "5") else ""})
    if stored:
        status = _("Definitief: btw-boeking {reference} van {date} ({login}).").format(
            reference=stored.get("reference") or stored["gl_id"], date=format_date(stored["created"].date(), df),
            login=stored.get("login") or "?")
        if rep.difference:
            status += " " + _("De boekhouding is daarna gewijzigd; zie het overzicht.")
    else:
        status = _("Proefafdruk: de bedragen kunnen nog veranderen tot de btw-boeking is gemaakt.")
    p = rep.period
    return {"draft": not stored, "pay": vals["5"] >= 0, "rows": rows, "title": title, "company": company,
            "taxnumber": taxnumber, "status": status, "period": p,
            # officiële Engelse kop van het HMRC-formulier (BL10)
            "bar": "VAT Return", "vrn": f"VAT registration number {taxnumber}",
            "period_text": f"Period {p.fromdate:%d %m %Y} to {p.todate:%d %m %Y}"}


# ------------------------------------------------------------------ periodieke aangifte België: formulier

#: officiële tekst per rooster in het Nederlands en het Frans (model bijlage I KB nr. 1, Intervat; BL10)
BE_FORM_TEXT = {
    "00": ("Handelingen onderworpen aan een bijzondere regeling", "Opérations soumises à un régime particulier"),
    "01": ("Handelingen waarvoor de btw verschuldigd is door de aangever – 6 %",
           "Opérations pour lesquelles la TVA est due par le déclarant – 6 %"),
    "02": ("Handelingen waarvoor de btw verschuldigd is door de aangever – 12 %",
           "Opérations pour lesquelles la TVA est due par le déclarant – 12 %"),
    "03": ("Handelingen waarvoor de btw verschuldigd is door de aangever – 21 %",
           "Opérations pour lesquelles la TVA est due par le déclarant – 21 %"),
    "44": ("Diensten waarvoor de buitenlandse btw verschuldigd is door de medecontractant",
           "Services pour lesquels la TVA étrangère est due par le cocontractant"),
    "45": ("Handelingen waarvoor de btw verschuldigd is door de medecontractant",
           "Opérations pour lesquelles la TVA est due par le cocontractant"),
    "46": ("Vrijgestelde intracommunautaire leveringen verricht in België en ABC-verkopen",
           "Livraisons intracommunautaires exemptées effectuées en Belgique et ventes ABC"),
    "47": ("Andere vrijgestelde handelingen en andere handelingen verricht in het buitenland",
           "Autres opérations exemptées et autres opérations effectuées à l'étranger"),
    "48": ("Uitgereikte creditnota's en negatieve verbeteringen m.b.t. de roosters 44 en 46",
           "Notes de crédit délivrées et corrections négatives relatives aux grilles 44 et 46"),
    "49": ("Uitgereikte creditnota's en negatieve verbeteringen m.b.t. andere handelingen van kader II",
           "Notes de crédit délivrées et corrections négatives relatives aux autres opérations du cadre II"),
    "81": ("Handelsgoederen, grond- en hulpstoffen", "Marchandises, matières premières et matières auxiliaires"),
    "82": ("Diensten en diverse goederen", "Services et biens divers"),
    "83": ("Bedrijfsmiddelen", "Biens d'investissement"),
    "84": ("Ontvangen creditnota's en negatieve verbeteringen m.b.t. de roosters 86 en 88",
           "Notes de crédit reçues et corrections négatives relatives aux grilles 86 et 88"),
    "85": ("Ontvangen creditnota's en negatieve verbeteringen m.b.t. andere inkomende handelingen",
           "Notes de crédit reçues et corrections négatives relatives aux autres opérations à l'entrée"),
    "86": ("Intracommunautaire verwervingen verricht in België en ABC-verkopen",
           "Acquisitions intracommunautaires effectuées en Belgique et ventes ABC"),
    "87": ("Andere inkomende handelingen waarvoor de btw verschuldigd is door de aangever",
           "Autres opérations à l'entrée pour lesquelles la TVA est due par le déclarant"),
    "88": ("Intracommunautaire diensten met verlegging van heffing",
           "Services intracommunautaires avec report de perception"),
    "54": ("Btw op de handelingen aangegeven in de roosters 01, 02 en 03",
           "TVA relative aux opérations déclarées en grilles 01, 02 et 03"),
    "55": ("Btw op de handelingen aangegeven in de roosters 86 en 88",
           "TVA relative aux opérations déclarées en grilles 86 et 88"),
    "56": ("Btw op de handelingen aangegeven in rooster 87, met uitzondering van invoer met verlegging van heffing",
           "TVA relative aux opérations déclarées en grille 87, à l'exception des importations avec report de perception"),
    "57": ("Btw op invoer met verlegging van heffing", "TVA relative aux importations avec report de perception"),
    "61": ("Diverse btw-regularisaties in het voordeel van de Staat", "Diverses régularisations TVA en faveur de l'État"),
    "63": ("Terug te storten btw vermeld op ontvangen creditnota's",
           "TVA à reverser mentionnée sur les notes de crédit reçues"),
    "XX": ("Totaal van de roosters 54, 55, 56, 57, 61 en 63", "Total des grilles 54, 55, 56, 57, 61 et 63"),
    "59": ("Aftrekbare btw", "TVA déductible"),
    "62": ("Diverse btw-regularisaties in het voordeel van de aangever",
           "Diverses régularisations TVA en faveur du déclarant"),
    "64": ("Te recupereren btw vermeld op uitgereikte creditnota's",
           "TVA à récupérer mentionnée sur les notes de crédit délivrées"),
    "YY": ("Totaal van de roosters 59, 62 en 64", "Total des grilles 59, 62 et 64"),
    "71": ("Belasting verschuldigd aan de Staat", "Taxe due à l'État"),
    "72": ("Sommen verschuldigd door de Staat", "Sommes dues par l'État"),
}
BE_FORM_SECTIONS = {"II": ("Uitgaande handelingen", "Opérations à la sortie"),
                    "III": ("Inkomende handelingen", "Opérations à l'entrée"),
                    "IV": ("Verschuldigde belastingen", "Taxes dues"),
                    "V": ("Aftrekbare belastingen", "Taxes déductibles"),
                    "VI": ("Saldo", "Solde")}
#: teruggaaf (BE6): minimum per tijdvak (kwartaal 1–3 / laatste tijdvak van het jaar)
BE_REFUND_MIN = (Decimal(400), Decimal(50))


def be_form_lang(ctx: Ctx) -> int:
    """BL10: het formulier in de officiële taal; Frans voor een Franstalige gebruiker, anders Nederlands."""
    return 1 if (ctx.user.get("language") or ctx.user.get("countrycode") or "").lower().startswith("fr") else 0


def be_form_data(rep: VatReport, ctx: Ctx, title: str, company: str, taxnumber: str, link=None) -> Dict[str, Any]:
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    lang = be_form_lang(ctx)
    form = FORMS["BE"]
    stored = rep.stored if rep.stored and rep.stored.get("gl_exists") else None
    D = Decimal
    if stored:
        snap = stored["snapshot"]
        values = {c: (D(v[2]) if form.box[c].base else D(v[3])) for c, v in snap["boxes"].items() if c in form.box}
        totals = {k: D(v) for k, v in snap["totals"].items()}
    else:
        values = {c: (b.base if form.box[c].base else b.tax) for c, b in rep.boxes.items()}
        totals = dict(rep.totals)

    def fmt(v):
        return format_amount(v, nf, 0, "0").replace("&nbsp;", "").strip()

    sections = []
    for code, _title, grids in BE_SECTIONS:
        rows = []
        for g in grids:
            v = totals[g] if g in totals else values.get(g, D(0))
            rows.append({"code": g, "text": BE_FORM_TEXT[g][lang], "label": _(BE_LABELS[g]), "value": fmt(v),
                         "manual": g in form.manual, "total": g in ("XX", "YY", "71", "72"),
                         "link": link(g) if link and g not in totals and g not in form.manual else ""})
        sections.append({"code": code, "title": BE_FORM_SECTIONS[code][lang], "rows": rows})
    if stored:
        status = _("Definitief: btw-boeking {reference} van {date} ({login}).").format(
            reference=stored.get("reference") or stored["gl_id"], date=format_date(stored["created"].date(), df),
            login=stored.get("login") or "?")
        if rep.difference:
            status += " " + _("De boekhouding is daarna gewijzigd; zie het overzicht.")
    else:
        status = _("Proefafdruk: de bedragen kunnen nog veranderen tot de btw-boeking is gemaakt.")
    p = rep.period
    return {"draft": not stored, "sections": sections, "title": title, "company": company, "taxnumber": taxnumber,
            "status": status, "lang": "fr" if lang else "nl",
            "bar": ("Periodieke btw-aangifte", "Déclaration périodique à la TVA")[lang],
            "vatno": (("Btw-nummer: {n}", "Numéro de TVA : {n}")[lang]).format(n=taxnumber) if taxnumber else "",
            "period_text": ("Tijdvak {f} – {t}", "Période {f} – {t}")[lang].format(
                f=p.fromdate.strftime("%d/%m/%Y"), t=p.todate.strftime("%d/%m/%Y")),
            "unit": ("Bedragen in euro, zonder centen", "Montants en euros, sans cents")[lang],
            "manual_note": ("niet berekend: zelf invullen", "non calculé : à compléter")[lang]}


def be_refund(rep: VatReport) -> Optional[Dict[str, Any]]:
    """BE6: bij rooster 72 het advies over de teruggaaf: minimum € 400 per tijdvak, € 50 in het laatste tijdvak van
    het jaar (aangifte van december of het vierde kwartaal)."""
    amount = rep.totals.get("72") or Decimal(0)
    if amount <= 0:
        return None
    last = rep.period.todate.month == 12
    minimum = BE_REFUND_MIN[1] if last else BE_REFUND_MIN[0]
    return {"amount": amount, "minimum": minimum, "ok": amount >= minimum, "last": last}


# ------------------------------------------------------------------ overzicht

@router.get("/btw/aangifte")
def vat_return(ctx: Ctx = Depends(ctx_company)):
    require_vat(ctx)
    q = ctx.request.query_params
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    today = date.today()
    with _open(ctx) as conn:
        vr = VatReturns(conn, ctx.user_config())
        country, offset = vr.country, vr.quarter_offset()
        kind = _period_kind(conn, ctx)
        period = parse_period(q, kind, today, offset)
        configured = vr.configured()
        rep = vr.report(period) if configured else None
        settle, rounding = vr.accounts_setting()
        vat_problems = vat_number_problems(conn, rep) if rep and country == "NL" else []
        with cursor(conn) as cur:
            info = get_defaults(cur, "company")
            info["vatnumber"] = docsettings.load(conn).taxnumber  # V2: btw-nummer uit Documentinstellingen
    label = period_label(period, df)
    form = FORMS[country]
    box = q.get("rubriek") or ""
    if box and box not in form.box:
        box = ""

    def link(code: str) -> str:
        return "/btw/aangifte?" + urlencode({"tijdvak": period.key, "rubriek": code}) + "#detail"

    tables = [overview_table(rep, link)] if rep else []
    export = q.get("export")
    title = return_title(country, label)
    head = [x for x in (_("Btw-nummer: {number}").format(number=info.get("vatnumber"))
                        if info.get("vatnumber") else "",
                        _("{fromdate} t/m {todate}").format(fromdate=format_date(period.fromdate, df),
                                                            todate=format_date(period.todate, df))) if x]
    if rep and export == "csv" and country == "GB":     # UK2: bestand voor bridging software (MTD)
        ctx.log("report_exported", report="vat-return", format="csv")
        return Response(gb_csv(rep, info.get("vatnumber") or ""), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="vat-return-{period.key}.csv"'})
    if rep and export in ("pdf", "xlsx"):
        out = list(tables)
        if export == "xlsx":            # BA5: de exacte bedragen staan in Excel
            out.append(accounts_table(rep))
            out += [detail_table(rep, b.code) for b in form.boxes if rep.box_transactions(b.code)]
        elif box:
            out.append(detail_table(rep, box))
        ctx.log("report_exported", report="btw-aangifte", format=export)
        if export == "pdf":
            return pdf_response(ctx, out, title, head, f"btw-aangifte-{period.key}", info.get("company"))
        return Response(to_xlsx(out, title, [info.get("company") or ""] + head, nf, df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="btw-aangifte-{period.key}.xlsx"'})
    periods = [(p.key, period_label(p, df))
               for p in recent_periods(kind, today, 12 if kind != "year" else 5, offset)]
    if period.key not in dict(periods):
        periods.insert(0, (period.key, label))
    detail = detail_table(rep, box) if rep and box else None
    modules = ctx.company["modules"] or []
    return render(ctx, "vat/return.html", rep=rep, configured=configured, period=period, label=label, title=title,
                  head=head, tables=tables, detail=detail, box=box, periods=periods,
                  company_name=info.get("company") or ctx.company["name"],
                  cell=lambda c, r: cell_text(c, r, nf, df), box_labels=LABELS[country], notes=NOTES, kinds=KINDS,
                  country=country, is_gb=country == "GB", is_nl=country == "NL", is_be=country == "BE",
                  payable=form.payable, refund=be_refund(rep) if rep and country == "BE" else None,
                  can_book=ctx.has_role("bookkeeper") and "gl" in modules,
                  icp=bool(ctx.company.get("icp")), vat_problems=vat_problems, problem_labels=PROBLEM_LABELS,
                  settle=settle, rounding=rounding, export_query=urlencode({"tijdvak": period.key, "rubriek": box}),
                  amount=lambda v, p=2: format_amount(v, nf, p, "0").replace("&nbsp;", "").strip(),
                  date=lambda d: format_date(d, df) if d else "")


# ------------------------------------------------------------------ aangifteformulier (opmaak Belastingdienst)

FORM_NOTES = {
    "2A": N_("Let op! Voorbelasting invullen bij vraag 5b"),
    "5A": N_("Tel de omzetbelasting van de rubrieken 1a t/m 4b op"),
    "5D": N_("Wordt niet berekend; vul zelf in als het van toepassing is"),
    "5E": N_("Alleen als de Belastingdienst u toestemming heeft gegeven"),
    "5F": N_("Alleen als de Belastingdienst u toestemming heeft gegeven"),
}


def form_data(rep: VatReport, ctx: Ctx, title: str, company: str, taxnumber: str, link=None) -> Dict[str, Any]:
    """Bedragen voor het formulier: vastgelegd (na de btw-boeking) of van nu (proefafdruk)."""
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    stored = rep.stored if rep.stored and rep.stored.get("gl_exists") else None
    D = Decimal
    if stored:
        snap = stored["snapshot"]
        boxes = {c: (D(v[2]), D(v[3])) for c, v in snap["boxes"].items()}
        totals = {k: D(v) for k, v in snap["totals"].items()}
    else:
        boxes = {c: (b.base, b.tax) for c, b in rep.boxes.items()}
        totals = dict(rep.totals)

    def fmt(v):
        return format_amount(v, nf, 0, "0").replace("&nbsp;", "").strip()

    sections = []
    for section, stitle in SECTIONS:
        rows = []
        if section == "5":
            for code in ("5A", "5B", "5C", "5D", "5E", "5F", "5G"):
                v = totals.get(code, Decimal(0))
                if code == "5G":
                    v = abs(v)
                rows.append({"code": code.lower(), "label": _(BOX_LABELS[code]), "base": None, "tax": fmt(v),
                             "note": _(FORM_NOTES[code]) if code in FORM_NOTES else "",
                             "link": link(code) if link and code == "5B" else ""})
        else:
            for b in BOXES:
                if not b.code.startswith(section):
                    continue
                base, tax = boxes.get(b.code, (Decimal(0), Decimal(0)))
                rows.append({"code": b.code.lower(), "label": _(BOX_LABELS[b.code]), "base": fmt(base),
                             "tax": fmt(tax) if b.tax or tax else None,
                             "note": _(FORM_NOTES[b.code]) if b.code in FORM_NOTES else "",
                             "link": link(b.code) if link else ""})
        sections.append({"code": section, "title": _(stitle), "rows": rows})
    if stored:
        status = _("Definitief: btw-boeking {reference} van {date} ({login}).").format(
            reference=stored.get("reference") or stored["gl_id"], date=format_date(stored["created"].date(), df),
            login=stored.get("login") or "?")
        if rep.difference:
            status += " " + _("De boekhouding is daarna gewijzigd; zie het overzicht.")
    else:
        status = _("Proefafdruk: de bedragen kunnen nog veranderen tot de btw-boeking is gemaakt.")
    return {"draft": not stored, "pay": totals.get("5G", Decimal(0)) >= 0, "sections": sections,
            "title": title, "company": company, "taxnumber": taxnumber, "status": status}


@router.get("/btw/aangifte/formulier")
def vat_form(ctx: Ctx = Depends(ctx_company)):
    require_vat(ctx)
    q = ctx.request.query_params
    df = ctx.user["dateformat"]
    with _open(ctx) as conn:
        vr = VatReturns(conn, ctx.user_config())
        country = vr.country
        period = parse_period(q, _period_kind(conn, ctx), date.today(), vr.quarter_offset())
        if not vr.configured():
            raise Redirect("/btw/aangifte")
        rep = vr.report(period)
        with cursor(conn) as cur:
            info = get_defaults(cur, "company")
            info["vatnumber"] = docsettings.load(conn).taxnumber  # V2: btw-nummer uit Documentinstellingen
    title = return_title(country, period_label(period, df))
    company = info.get("company") or ctx.company["name"]
    make = {"GB": gb_form_data, "BE": be_form_data}.get(country, form_data)
    part = {"GB": "vat/_form_gb.html", "BE": "vat/_form_be.html"}.get(country, "vat/_form.html")
    if q.get("export") == "pdf":
        from datetime import datetime
        from pathlib import Path

        from weasyprint import HTML

        from .reportpdf import _no_fetch, write_pdf
        from .web import TEMPLATES

        form = make(rep, ctx, title, company, info.get("vatnumber") or "")
        css = (Path(__file__).parent / "static" / "vatform.css").read_text()
        now = datetime.now()
        printed = _("Afgedrukt op {date} {time}").format(date=format_date(now.date(), df), time=now.strftime("%H:%M"))
        html = TEMPLATES.env.get_template("vat/form_pdf.html").render(form=form, css=css, printed=printed, part=part)
        ctx.log("report_exported", report="btw-formulier", format="pdf")
        return Response(write_pdf(HTML(string=html, base_url=None, url_fetcher=_no_fetch).render()),
                        media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="btw-aangifte-{period.key}.pdf"'})

    def link(code: str) -> str:
        return "/btw/aangifte?" + urlencode({"tijdvak": period.key, "rubriek": code}) + "#detail"

    form = make(rep, ctx, title, company, info.get("vatnumber") or "", link)
    return render(ctx, "vat/form.html", form=form, period=period, part=part, country=country, is_gb=country == "GB",
                  is_be=country == "BE")


# ------------------------------------------------------------------ rubrieken (BA3)

def _rubrieken_page(ctx: Ctx, vr: VatReturns, *, status: int = 200, error: Optional[str] = None,
                    values: Optional[Dict[str, str]] = None):
    saved_country = vr.country_saved()
    unavailable = None
    if values and values.get("country") in FORMS:
        vr = VatReturns(vr.conn, vr.config, country=values["country"])
    elif not saved_country and not vr.configured():           # BL2: voorstel uit schema of valuta
        suggestion = vr.suggest_country()
        if suggestion in FORMS:
            vr = VatReturns(vr.conn, vr.config, country=suggestion)
        else:                                                 # VP3: geen formulier voor dit land, geen voorstel
            unavailable = suggestion
    country = vr.country
    offset = int(values.get("offset") or 0) if values and (values.get("offset") or "0").isdigit() else vr.quarter_offset()
    accounts = vr.tax_accounts()
    settle, rounding = vr.accounts_setting()
    suggested = not settle and not rounding
    if suggested:
        settle, rounding = vr.suggest_accounts()
    if values:
        for a in accounts:
            a["box"] = values.get(f"box_{a['id']}", a["box"])
        settle, rounding = values.get("settlement", settle), values.get("rounding", rounding)
    with cursor(vr.conn) as cur:
        cur.execute("""SELECT accno, description, category FROM chart WHERE charttype = 'A'
                       AND NOT coalesce(closed, false) ORDER BY accno""")
        charts = cur.fetchall()
    tax_accnos = {a["accno"] for a in accounts}
    expense = vr.expense_accounts()         # BL3: BE 81/82/83 per kosten-/activarekening
    if values:
        for a in expense:
            a["box"] = values.get(f"exp_{a['id']}", a["box"])
    return render(ctx, "vat/boxes.html", status, accounts=accounts, error=error, box_labels=CHOICE_LABELS[country],
                  expense=expense, expense_choices=vr.form.expense_boxes, is_be=country == "BE",
                  choices=vr.form.account_boxes, settle=settle, rounding=rounding, suggested=suggested,
                  country=country, is_gb=country == "GB", countries=COUNTRY_LABELS, country_saved=saved_country,
                  offset=offset, offsets=[(o, OFFSET_LABELS[o]) for o in vr.form.quarter_offsets]
                  if len(vr.form.quarter_offsets) > 1 else [],
                  settle_accounts=[c for c in charts if c["category"] in ("A", "L") and c["accno"] not in tax_accnos],
                  rounding_accounts=[c for c in charts if c["category"] in ("I", "E")],
                  any_saved=any(a["box"] for a in accounts), unavailable=unavailable,
                  unavailable_label=_(OTHER_COUNTRIES.get(unavailable or "", "")) if unavailable else "",
                  rate=lambda r: rate_text(r, ctx.user["numberformat"]))


def rate_text(r, nf: str) -> str:
    """Tarief als percentage met zijn decimalen (VP4: 8,1 % niet als 8 %)."""
    if r is None:
        return ""
    pct = (Decimal(str(r)) * 100).normalize()
    places = max(0, -pct.as_tuple().exponent)
    return format_amount(pct, nf, places, "0") + "%"


@router.get("/btw/rubrieken")
def vat_boxes(ctx: Ctx = Depends(ctx_company)):
    require_vat(ctx)
    require_role(ctx, "admin")
    with _open(ctx) as conn:
        return _rubrieken_page(ctx, VatReturns(conn, ctx.user_config()))


@router.post("/btw/rubrieken")
def vat_boxes_save(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_vat(ctx)
    require_role(ctx, "admin")
    values = {k: str(v) for k, v in form.items()}
    with _open(ctx) as conn:
        vr = VatReturns(conn, ctx.user_config())
        if "country" in values and not values["country"]:      # VP3: lege keuze ("—"), niets opslaan
            return _rubrieken_page(ctx, vr, status=400, error=_("Kies het land van de btw-aangifte."))
        country = values.get("country") or vr.country
        if country not in FORMS:
            raise HTTPException(400, _("Onbekend land."))
        try:
            offset = int(values.get("offset") or 0)
        except ValueError:
            offset = 0
        if offset not in FORMS[country].quarter_offsets:
            offset = 0
        try:
            changed_country = vr.save_country(country, offset, ctx.employee_login)
            menu_flags.forget(ctx.company)          # menu: land bepaalt RGS, btw-rubrieken en betaalbestand
        except LedgerError as exc:
            return _rubrieken_page(ctx, vr, status=400, error=lib_message(exc), values=values)
        if values.get("switch"):            # alleen van land gewisseld (BL2): de koppelingen van dat land tonen
            sync_flag(ctx, conn)
            ctx.log("vat_country_saved", changed=changed_country)
            raise Redirect("/btw/rubrieken?ok=vat_boxes_saved")
        boxes = {}
        for a in vr.tax_accounts():
            v = values.get(f"box_{a['id']}")
            if v:
                boxes[a["id"]] = v
        expense = {}
        for a in vr.expense_accounts():
            v = values.get(f"exp_{a['id']}")
            if v:
                expense[a["id"]] = v
        try:
            changed = vr.save_settings(boxes, values.get("settlement", ""), values.get("rounding", ""),
                                       ctx.employee_login)
            changed += vr.save_expense_boxes(expense, ctx.employee_login)
        except LedgerError as exc:
            return _rubrieken_page(ctx, vr, status=400, error=lib_message(exc), values=values)
        sync_flag(ctx, conn)
    ctx.log("vat_boxes_saved", changed=changed + changed_country)
    raise Redirect("/btw/rubrieken?ok=vat_boxes_saved")


# ------------------------------------------------------------------ btw-boeking (BA6)

def proposal_form(ctx: Ctx, conn, key: str):
    """Ingevuld memoriaal voor de btw-boeking van tijdvak ``key``; (GLForm, None) of (None, foutmelding)."""
    from .routes_gl import GLForm, Line

    df, nf = ctx.user["dateformat"], ctx.user["numberformat"]
    try:
        fromtext, totext = key.split("_", 1)
        period = free_period(date.fromisoformat(fromtext), date.fromisoformat(totext))
    except ValueError:
        raise HTTPException(400, _("Ongeldige datum."))
    vr = VatReturns(conn, ctx.user_config())
    try:
        prop = vr.proposal(vr.report(period))
    except LedgerError as exc:
        return None, lib_message(exc)
    lines = []
    for ln in prop["lines"]:
        amt: Decimal = ln["amount"]
        text = format_amount(abs(amt), nf, 2, "0").replace("&nbsp;", "").strip()
        lines.append(Line(accno=ln["accno"], debit=text if amt < 0 else "", credit=text if amt > 0 else "",
                          memo=ln["memo"]))
    f = GLForm(transdate=prop["transdate"].isoformat(), description=booking_description(period, df, vr.country),
               lines=lines)
    f.vat_period = period.key
    return f, None


def register(ctx: Ctx, conn, key: str, gl_id: int) -> None:
    """Na opslaan van de btw-boeking: tijdvak vastleggen (BA6), taak op het dashboard afgehandeld."""
    try:
        fromtext, totext = key.split("_", 1)
        period = free_period(date.fromisoformat(fromtext), date.fromisoformat(totext))
    except ValueError:
        return
    vr = VatReturns(conn, ctx.user_config())
    rep = vr.report(period)
    vr.register(rep, gl_id, ctx.employee_login)
    ctx.log("vat_return_booked", period=key, gl_id=gl_id)
    if IcpReturns(conn, ctx.user_config()).register_with_vat(rep, gl_id, ctx.employee_login):   # ICP8
        ctx.log("icp_return_registered", period=key, gl_id=gl_id)


# ------------------------------------------------------------------ jaarlijkse klantenlisting België (BE5)

LISTING_PROBLEMS = {"format": N_("ongeldig Belgisch btw-nummer"), "check": N_("controlegetal klopt niet")}


def listing_table(rep) -> Table:
    cols = [Col("vatnumber", N_("Btw-nummer")), Col("name", N_("Klant")),
            Col("base", N_("Omzet zonder btw"), "amount", 0, "0"), Col("tax", N_("Btw"), "amount", 0, "0")]
    rows = [Row("row", {"vatnumber": x.vatnumber, "name": x.name, "base": x.base, "tax": x.tax},
                links={"name": f"/klanten/{x.customer_id}"}) for x in rep.lines]
    base, tax = rep.totals
    rows.append(Row("total", {"name": _("Totaal ({n} klanten)").format(n=len(rep.lines)), "base": base, "tax": tax}))
    return Table(cols, rows, title=_("Jaarlijkse klantenlisting {year}").format(year=rep.year))


@router.get("/btw/klantenlisting")
def vat_listing(ctx: Ctx = Depends(ctx_company)):
    require_vat(ctx)
    from sqlledger.vatlisting import THRESHOLD, ClientListing

    q = ctx.request.query_params
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    this_year = date.today().year
    try:
        year = int(q.get("jaar") or this_year - 1)
    except ValueError:
        raise HTTPException(400, _("Ongeldig jaar."))
    with _open(ctx) as conn:
        rep = ClientListing(conn).report(year)
        with cursor(conn) as cur:
            info = get_defaults(cur, "company")
        vatnumber = docsettings.load(conn).taxnumber
    title = _("Jaarlijkse klantenlisting {year}").format(year=year)
    head = [x for x in (_("Btw-nummer: {number}").format(number=vatnumber) if vatnumber else "",
                        _("Indienen vóór {date} via Intervat").format(date=format_date(date(year + 1, 3, 31), df)))
            if x]
    table = listing_table(rep)
    export = q.get("export")
    if export in ("pdf", "xlsx"):
        ctx.log("report_exported", report="klantenlisting", format=export)
        if export == "pdf":
            return pdf_response(ctx, [table], title, head, f"klantenlisting-{year}", info.get("company"))
        return Response(to_xlsx([table], title, [info.get("company") or ""] + head, nf, df),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="klantenlisting-{year}.xlsx"'})
    years = list(range(this_year, this_year - 6, -1))
    if year not in years:
        years = sorted(set(years) | {year}, reverse=True)
    return render(ctx, "vat/listing.html", rep=rep, year=year, years=years,
                  title=title, head=head, table=table, company_name=info.get("company") or ctx.company["name"],
                  threshold=THRESHOLD, problem_labels=LISTING_PROBLEMS, cell=lambda c, r: cell_text(c, r, nf, df),
                  amount=lambda v, p=0: format_amount(v, nf, p, "0").replace("&nbsp;", "").strip())
