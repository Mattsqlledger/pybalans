"""Rapporten en lijsten als PDF (fase 4 stap 5, D14, RP1–RP10).

Zelfde tabelmodel als scherm, CSV en Excel (``tables.Table``): de celteksten komen uit ``cell_text``, dus
de bedragen in de PDF zijn letterlijk die van het scherm. Eén vaste opmaak (RP6,
``templates/reports/pdf.html``) in de taal en notatie van de gebruiker (RP5); alle regels, tot
``MAX_ROWS`` (RP2); staand of liggend en de lettergrootte volgen uit de breedte van de inhoud (RP4).
Past een tabel liggend op de kleinste letter niet, dan komt hij in kolomblokken op opeenvolgende pagina's, met in
elk blok de vaste tekstkolommen links herhaald (v1.0.1 PB28). Geen links, niets bewaard (RP7).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from fastapi.responses import Response

from sqlledger.dates import format_date

from .i18n import gettext as _
from .tables import Table, cell_text

#: meer regels dan dit (alle tabellen samen) → melding in plaats van PDF (RP2: ± 75 blz., ± 15 s)
MAX_ROWS = 5000

#: tekens die op een regel passen bij 8 pt (bruikbare breedte A4 staand / liggend, met marge per kolom)
PORTRAIT_CHARS = 118
LANDSCAPE_CHARS = 172
BASE_PT = 8.0
MIN_PT = 6.5
#: een tekstkolom telt voor de breedte hooguit zoveel tekens (lange omschrijvingen lopen door op een volgende regel)
TEXT_CAP = 34
#: ruimte tussen kolommen, in tekens
GAP = 2


class PdfTooLarge(ValueError):
    def __init__(self, rows: int):
        super().__init__(rows)
        self.rows = rows

    def message(self) -> str:
        return _("Dit overzicht heeft {rows} regels; een PDF kan er hoogstens {max}. Verfijn de selectie "
                 "of gebruik Excel.").format(rows=self.rows, max=MAX_ROWS)


@dataclass
class Layout:
    landscape: bool
    font_pt: float
    #: per tabel de kolomblokken (lijsten van kolomnummers); None = geen tabel gesplitst (PB28)
    parts: Optional[List[List[List[int]]]] = None


def _width(lengths: List[int]) -> int:
    return sum(n + GAP for n in lengths)


def _lengths(t: Table, rows: List[List[str]]) -> List[int]:
    """Breedte per kolom: de langste celtekst of het langste woord van de kop (tekstkolommen afgetopt)."""
    lengths = []
    for i, c in enumerate(t.columns):
        head = max((len(w) for w in _(c.label).split()), default=0)
        body = max((len(r[i]) for r in rows), default=0)
        if c.kind == "text":
            body = min(body, TEXT_CAP)
        lengths.append(max(head, body, 3))
    return lengths


def split_columns(columns, lengths: List[int], capacity: int) -> List[List[int]]:
    """Kolomblokken voor een te brede tabel (PB28). De niet-bedragkolommen links (rekening, omschrijving) staan in
    elk blok; de overige kolommen worden in volgorde over zo weinig mogelijk blokken van ongeveer gelijke grootte
    verdeeld, zodat de laatste kolom (het totaal) in het laatste blok staat. Paren (dit jaar / vorig jaar) blijven
    bij elkaar als het aantal kolommen even is."""
    n = len(columns)
    fixed = []
    for i, c in enumerate(columns):
        if c.kind == "amount":
            break
        fixed.append(i)
    rest = list(range(len(fixed), n))
    if not rest or not fixed and len(rest) <= 1:
        return [list(range(n))]
    room = max(capacity - _width([lengths[i] for i in fixed]), max(lengths[i] for i in rest) + GAP)
    for blocks in range(2, len(rest) + 1):
        size = math.ceil(len(rest) / blocks)
        if len(rest) % 2 == 0 and size % 2:
            size += 1
        chunks = [rest[k:k + size] for k in range(0, len(rest), size)]
        if all(_width([lengths[i] for i in ch]) <= room for ch in chunks):
            return [fixed + ch for ch in chunks]
    return [fixed + [i] for i in rest]


def choose_layout(tables: List[Table], texts: List[List[List[str]]]) -> Layout:
    """Staand als het bij 8 pt past, anders liggend; past het liggend niet, dan kleiner (minimaal 6,5 pt).
    Past het ook dan niet, dan kolomblokken (PB28) met de grootste letter (tot 8 pt) die voor het breedste blok past."""
    lengths = [_lengths(t, rows) for t, rows in zip(tables, texts)]
    widest = max((_width(ls) for ls in lengths), default=0)
    if widest <= PORTRAIT_CHARS:
        return Layout(False, BASE_PT)
    if widest <= LANDSCAPE_CHARS:
        return Layout(True, BASE_PT)
    capacity = int(LANDSCAPE_CHARS * BASE_PT / MIN_PT)
    if widest <= capacity:
        return Layout(True, max(MIN_PT, round(BASE_PT * LANDSCAPE_CHARS / widest, 1)))
    parts = [split_columns(t.columns, ls, capacity) if _width(ls) > capacity else [list(range(len(ls)))]
             for t, ls in zip(tables, lengths)]
    block = max(_width([ls[i] for i in p]) for ps, ls in zip(parts, lengths) for p in ps)
    font = min(BASE_PT, max(MIN_PT, math.floor(BASE_PT * LANDSCAPE_CHARS / block * 10) / 10))
    return Layout(True, font, parts)


def render_pdf(tables: List[Table], title: str, info: List[str], company: str, nf: str, df: str,
               now: Optional[datetime] = None, chart=None) -> bytes:
    """PDF van één of meer tabellen. ``info``: regels onder de titel (periode, filters). ``chart``: de grafiek
    bij winst en verlies (v1.1, GR3; ``plchart.PlChart``) boven de tabel, als SVG."""
    from weasyprint import HTML

    from .web import TEMPLATES

    count = sum(len(t.rows) for t in tables)
    if count > MAX_ROWS:
        raise PdfTooLarge(count)
    texts = [[[cell_text(c, r, nf, df) for c in t.columns] for r in t.rows] for t in tables]
    layout = choose_layout(tables, texts)
    now = now or datetime.now()
    printed = _("Afgedrukt op {date} {time}").format(date=format_date(now.date(), df), time=now.strftime("%H:%M"))
    html = TEMPLATES.env.get_template("reports/pdf.html").render(
        tables=list(zip(tables, texts)), title=title, info=[x for x in info if x], company=company,
        layout=layout, printed=printed, chart=chart)
    return write_pdf(HTML(string=html, base_url=None, url_fetcher=_no_fetch).render())


def write_pdf(document) -> bytes:
    """PDF-bytes van een opgemaakt WeasyPrint-document met PyBalans als maker (``Creator``; v1.0.1 NB1). ``Producer``
    blijft WeasyPrint: dat is de bibliotheek die de PDF schrijft."""
    from sqlledger import __version__
    document.metadata.generator = f"PyBalans {__version__}"
    return document.write_pdf()


def _no_fetch(url, *a, **kw):
    raise ValueError("geen externe bronnen in een rapport-PDF")


def pdf_response(ctx, tables: List[Table], title: str, info: List[str], filename: str,
                 company: Optional[str] = None, back: str = "", chart=None):
    """Antwoord voor een PDF-knop: de PDF, of bij te veel regels de foutpagina met uitleg (RP2).
    Schrijft een regel in het logboek zoals CSV/Excel (RP7)."""
    from urllib.parse import urlencode

    from .web import render

    if not back:
        q = urlencode([(k, v) for k, v in ctx.request.query_params.multi_items() if k != "export"])
        back = ctx.request.url.path + (f"?{q}" if q else "")
    nf, df = ctx.user["numberformat"], ctx.user["dateformat"]
    try:
        pdf = render_pdf(tables, title, info, company or ctx.company["name"], nf, df, chart=chart)
    except PdfTooLarge as exc:
        return render(ctx, "error.html", 413, status=413, message=exc.message(), back=back)
    ctx.log("report_exported", report=filename, format="pdf")
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}.pdf"'})
