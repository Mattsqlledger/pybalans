"""Jaarlijkse klantenlisting België (BTW_LANDEN_ANALYSE.md BE5; art. 53quinquies Btw-Wetboek, KB nr. 23).

Per Belgische btw-plichtige klant (Belgisch btw-nummer) de omzet zonder btw en de btw van het kalenderjaar, vóór
31 maart van het volgende jaar in te dienen via Intervat. Een klant komt op de lijst als zijn omzet **meer dan
€ 250** bedraagt (``THRESHOLD``; zo in de toelichtingen, de wettekst zelf is niet nagelezen). Creditnota's gaan er af.
Zijn er geen klanten boven de drempel, dan is het een nihil-listing. Bedragen in hele euro's (keuze gebruiker
2026-10-13: elke aangifte zonder decimalen), omlaag; de exacte bedragen staan erbij.

Bron van de bedragen: de verkoopboekingen (``ar``) van het jaar, goedgekeurd: ``netamount`` = omzet,
``amount − netamount`` = btw. Het btw-nummer komt van de klant (``customer.taxnumber``); zonder landcode telt een
nummer van 10 cijfers dat met 0 of 1 begint als Belgisch.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db
from .amounts import to_decimal

ZERO = Decimal(0)
THRESHOLD = Decimal(250)


def be_vat_number(raw: Optional[str]) -> Tuple[str, str]:
    """(genormaliseerd nummer 'BE0123456789', probleem) of ('', '') als het geen Belgisch nummer is.
    Problemen: ``format`` (geen 10 cijfers beginnend met 0 of 1) of ``check`` (controlegetal modulo 97 klopt niet)."""
    n = re.sub(r"[\s.\-/]", "", (raw or "")).upper()
    if n.startswith("BE"):
        digits = n[2:]
    elif re.fullmatch(r"[01]\d{9}", n):
        digits = n
    else:
        return "", ""
    if not re.fullmatch(r"[01]\d{9}", digits):
        return "BE" + digits, "format"
    if 97 - int(digits[:8]) % 97 != int(digits[8:]):
        return "BE" + digits, "check"
    return "BE" + digits, ""


@dataclass
class ListingLine:
    customer_id: int
    name: str
    vatnumber: str
    base_exact: Decimal
    tax_exact: Decimal
    count: int

    @property
    def base(self) -> Decimal:
        return Decimal(math.floor(self.base_exact))

    @property
    def tax(self) -> Decimal:
        return Decimal(math.floor(self.tax_exact))


@dataclass
class ListingReport:
    year: int
    lines: List[ListingLine]                    # boven de drempel, op btw-nummer
    below: List[ListingLine]                    # Belgisch btw-nummer, niet meer dan de drempel
    problems: List[Dict[str, Any]] = field(default_factory=list)   # klanten met een ongeldig Belgisch nummer

    @property
    def nil(self) -> bool:
        return not self.lines

    @property
    def totals(self) -> Tuple[Decimal, Decimal]:
        return (sum((x.base for x in self.lines), ZERO), sum((x.tax for x in self.lines), ZERO))


class ClientListing:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def report(self, year: int) -> ListingReport:
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.id, c.name, c.taxnumber, count(*) AS n,
                                  sum(a.netamount::numeric) AS base, sum((a.amount - a.netamount)::numeric) AS tax
                           FROM ar a JOIN customer c ON (c.id = a.customer_id)
                           WHERE a.transdate BETWEEN %s AND %s AND coalesce(a.approved, true)
                           GROUP BY c.id, c.name, c.taxnumber""", (date(year, 1, 1), date(year, 12, 31)))
            rows = cur.fetchall()
        lines, below, problems = [], [], []
        for r in rows:
            number, problem = be_vat_number(r["taxnumber"])
            if not number:
                continue
            line = ListingLine(r["id"], r["name"] or "", number, to_decimal(r["base"] or 0), to_decimal(r["tax"] or 0),
                               r["n"])
            if problem:
                problems.append({"id": r["id"], "name": line.name, "taxnumber": number, "problem": problem,
                                 "base": line.base_exact})
                continue
            (lines if line.base_exact > THRESHOLD else below).append(line)
        lines.sort(key=lambda x: x.vatnumber)
        below.sort(key=lambda x: x.vatnumber)
        problems.sort(key=lambda x: x["name"])
        return ListingReport(year, lines, below, problems)
