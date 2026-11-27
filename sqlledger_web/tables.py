"""Rapporttabellen: één model voor het scherm, CSV en Excel.

De bedragen komen ongewijzigd uit de bibliotheek (``display`` en ``rows``);
hier wordt alleen opgemaakt. Zo blijft wat op het scherm staat gelijk aan wat
tegen Perl is vergeleken (R8).
"""

from __future__ import annotations

import csv
import re
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlledger.amounts import format_amount, round_amount
from sqlledger.dates import format_date

from .i18n import gettext as _


@dataclass
class Col:
    key: str
    label: str
    kind: str = "text"      # text | date | amount
    places: int = 2
    dash: str = ""          # format_amount: '' = leeg bij 0, '-' = negatief tussen haakjes, '0' = 0,00 tonen


@dataclass
class Row:
    kind: str               # row | heading | section | subtotal | total | result | balance
    cells: Dict[str, Any]
    links: Dict[str, str] = field(default_factory=dict)
    dash: Optional[str] = None   # overschrijft Col.dash voor de bedragen in deze regel
    chart: Optional[str] = None  # sleutel voor de grafiek (winst en verlies, v1.1): rekening, kop of I/E


@dataclass
class Table:
    columns: List[Col]
    rows: List[Row]
    title: str = ""

    def page(self, page: int, size: int) -> Tuple["Table", int]:
        """Eén pagina regels; beginsaldo en eindtotaal blijven altijd staan (R9).
        Geeft (tabel, aantal pagina's)."""
        head = [r for r in self.rows[:1] if r.kind == "balance"]
        tail: List[Row] = []
        body = self.rows[len(head):]
        while body and body[-1].kind == "total":
            tail.insert(0, body.pop())
        pages = max(1, -(-len(body) // size))
        page = min(max(page, 1), pages)
        part = body[(page - 1) * size:page * size]
        return Table(self.columns, (head if page == 1 else []) + part + tail, self.title), pages


def cell_text(col: Col, row: Row, nf: str, df: str) -> str:
    v = row.cells.get(col.key)
    if v is None:
        return ""
    if col.kind == "amount":
        dash = col.dash if row.dash is None else row.dash
        return format_amount(v, nf, col.places, dash).replace("&nbsp;", "").strip()
    if isinstance(v, date):
        return format_date(v, df)
    if isinstance(v, bool):
        return "✓" if v else ""
    return str(v)


def safe_cell(value) -> str:
    """Geen formules in Excel: tekst die met =, +, -, @ begint krijgt een apostrof."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _plain_amount(v, places: int, nf: str) -> str:
    """Bedrag voor CSV: geen duizendtallen, decimaalteken van de gebruiker."""
    comma = nf in ("1.000,00", "1000,00")
    return format_amount(v, "1000,00" if comma else "1000.00", places, "0")


def to_csv(tables: List[Table], nf: str, df: str) -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    for n, t in enumerate(tables):
        if n:
            w.writerow([])
        if t.title:
            w.writerow([safe_cell(t.title)])
        w.writerow([_(c.label) for c in t.columns])     # Col-labels zijn al vertaald of N_-gemarkeerd
        for r in t.rows:
            line = []
            for c in t.columns:
                v = r.cells.get(c.key)
                if c.kind == "amount" and v is not None:
                    line.append(_plain_amount(v, c.places, nf))
                elif isinstance(v, date):
                    line.append(format_date(v, df))
                else:
                    line.append(safe_cell(cell_text(c, r, nf, df)))
            w.writerow(line)
    return "﻿" + out.getvalue()


def to_xlsx(tables: List[Table], title: str, info: List[str], nf: str, df: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\[\]:*?/\\]", "-", title or _("Rapport"))[:31]     # tekens die Excel weigert
    bold = Font(bold=True)
    ws.append([safe_cell(title)])
    ws.cell(1, 1).font = Font(bold=True, size=13)
    for line in info:
        ws.append([safe_cell(line)])
    widths: Dict[int, int] = {}
    for t in tables:
        ws.append([])
        if t.title:
            ws.append([safe_cell(t.title)])
            ws.cell(ws.max_row, 1).font = bold
        ws.append([_(c.label) for c in t.columns])
        for cell in ws[ws.max_row]:
            cell.font = bold
        for r in t.rows:
            line: List[Any] = []
            for c in t.columns:
                v = r.cells.get(c.key)
                if c.kind == "amount" and v is not None:
                    line.append(float(round_amount(v, c.places)))
                elif isinstance(v, date):
                    line.append(v)
                else:
                    line.append(safe_cell(cell_text(c, r, nf, df)) or None)
            ws.append(line)
            for i, c in enumerate(t.columns, start=1):
                cell = ws.cell(ws.max_row, i)
                if c.kind == "amount":
                    cell.number_format = "#,##0" + ("." + "0" * c.places if c.places else "")
                elif c.kind == "date" or isinstance(cell.value, date):
                    cell.number_format = "dd-mm-yyyy"
                if r.kind != "row":
                    cell.font = bold
                widths[i] = max(widths.get(i, 8), min(len(str(cell.value or "")) + 2, 50))
    for i, w in widths.items():
        ws.column_dimensions[get_column_letter(i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

