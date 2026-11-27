"""Batch: documenten zoeken om in één keer te printen (samengevoegde PDF) of te mailen (fase 4 stap 4c,
SB9–SB11).

Zoals ``BP::get_spoolfiles`` met ``batch`` print/email (bp.pl), maar:

* ook verkoop/inkoop zonder artikelen (SL: alleen ``invoice = '1'``) en creditfacturen/debetnota's als
  eigen soort (SL: ``formname LIKE '%invoice'``, dus factuur en creditfactuur door elkaar);
* filters periode, relatie (naam of nummer), documentnummer, open/gesloten en (niet) geprint/(niet) gemaild
  als keuzes ja/nee/alle; SL toont zonder vinkje bij geprint/gemaild niets (``a.id = 0``);
* geparametriseerde SQL (SL plakt de formuliervelden in de query, Perl-fout 24);
* geen wachtrij/spool (D8).

Een soort wordt beschreven met één of meer :class:`Source` (pakbon en picklijst komen uit verkoopfacturen
én verkooporders). De vinkjes geprint/gemaild komen uit ``status`` met precies de ``formname`` van de soort.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg

from . import _db
from .amounts import to_decimal
from .gl import like

#: keuzes voor de filters open/gesloten en geprint/gemaild
STATES = ("all", "open", "closed")
YESNO = ("all", "yes", "no")


@dataclass(frozen=True)
class Source:
    """Waar documenten van een soort staan: tabel ``ar``/``ap``/``oe`` en de voorwaarden."""

    table: str                       # ar, ap of oe
    invoice: Optional[bool] = None   # ar/ap: met artikelen (True) of zonder (False)
    negative: Optional[bool] = None  # ar/ap: bedrag < 0 (creditfactuur, creditnota, debetnota)
    quotation: Optional[bool] = None  # oe: offerte/aanvraag
    vc: str = "customer"             # oe: klant of leverancier


@dataclass
class BatchFilter:
    datefrom: Optional[date] = None
    dateto: Optional[date] = None
    name: str = ""          # relatie: naam of nummer
    number: str = ""        # documentnummer
    state: str = "all"      # all/open/closed
    printed: str = "all"    # all/yes/no
    emailed: str = "all"    # all/yes/no


@dataclass
class BatchRow:
    id: int
    table: str
    number: str
    transdate: Optional[date]
    vc_id: int
    name: str
    vcnumber: str
    email: str
    amount: Decimal
    curr: str
    closed: bool
    printed: bool
    emailed: bool


def _where(src: Source, flt: BatchFilter, formname: str) -> Tuple[str, str, List[Any]]:
    """FROM- en WHERE-deel plus parameters voor één bron."""
    if src.table not in ("ar", "ap", "oe"):
        raise ValueError("ongeldige tabel")
    vc = src.vc if src.table == "oe" else ("customer" if src.table == "ar" else "vendor")
    conds: List[str] = []
    params: List[Any] = []
    if src.table == "oe":
        conds.append(f"a.{vc}_id > 0")
        conds.append("a.quotation = %s")
        params.append(bool(src.quotation))
        number = "CASE WHEN a.quotation THEN a.quonumber ELSE a.ordnumber END"
        closed = "coalesce(a.closed, false)"
    else:
        if src.invoice is not None:
            conds.append("coalesce(a.invoice, false) = %s")
            params.append(src.invoice)
        if src.negative is not None:
            conds.append("(a.amount < 0) = %s")
            params.append(src.negative)
        number = "a.invnumber"
        closed = "(coalesce(a.amount, 0) = coalesce(a.paid, 0))"
    if flt.datefrom:
        conds.append("a.transdate >= %s")
        params.append(flt.datefrom)
    if flt.dateto:
        conds.append("a.transdate <= %s")
        params.append(flt.dateto)
    if flt.name:
        conds.append(f"(lower(vc.name) LIKE %s OR lower(vc.{vc}number) LIKE %s)")
        params += [like(flt.name.lower())] * 2
    if flt.number:
        conds.append(f"lower(coalesce({number}, '')) LIKE %s")
        params.append(like(flt.number.lower()))
    if flt.state in ("open", "closed"):
        conds.append(closed if flt.state == "closed" else f"NOT {closed}")
    for col in ("printed", "emailed"):
        val = getattr(flt, col)
        if val in ("yes", "no"):
            q = (f"EXISTS (SELECT 1 FROM status s WHERE s.trans_id = a.id AND s.formname = %s "
                 f"AND coalesce(s.{col}, false))")
            conds.append(q if val == "yes" else f"NOT {q}")
            params.append(formname)
    select = f"""SELECT a.id, '{src.table}' AS tbl, coalesce({number}, '') AS number, a.transdate,
                        vc.id AS vc_id, vc.name, coalesce(vc.{vc}number, '') AS vcnumber,
                        coalesce(vc.email, '') AS email, coalesce(a.amount, 0) AS amount,
                        coalesce(a.curr, '') AS curr, {closed} AS closed
                 FROM {src.table} a JOIN {vc} vc ON vc.id = a.{vc}_id"""
    return select, " AND ".join(conds) or "true", params


def find(conn: psycopg.Connection, sources: Sequence[Source], formname: str, flt: BatchFilter,
         limit: int = 1000) -> Tuple[List[BatchRow], bool]:
    """Documenten van één soort volgens de filters, op datum en nummer; geeft (regels, meer_dan_limit)."""
    if flt.state not in STATES or flt.printed not in YESNO or flt.emailed not in YESNO:
        raise ValueError("ongeldig filter")
    parts, params = [], []
    for src in sources:
        select, where, p = _where(src, flt, formname)
        parts.append(f"{select} WHERE {where}")
        params += p
    if not parts:
        return [], False
    query = (" UNION ALL ".join(f"({p})" for p in parts)
             + " ORDER BY transdate, number, id LIMIT %s")
    params.append(limit + 1)
    with _db.cursor(conn) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        st: Dict[int, Tuple[bool, bool]] = {}
        if rows:
            cur.execute("SELECT trans_id, printed, emailed FROM status WHERE formname = %s AND trans_id = ANY(%s)",
                        (formname, [r["id"] for r in rows]))
            st = {r["trans_id"]: (bool(r["printed"]), bool(r["emailed"])) for r in cur.fetchall()}
    out = []
    for r in rows:
        printed, emailed = st.get(r["id"], (False, False))
        out.append(BatchRow(r["id"], r["tbl"], r["number"], r["transdate"], r["vc_id"], r["name"] or "",
                            r["vcnumber"], r["email"], to_decimal(r["amount"]), r["curr"], bool(r["closed"]),
                            printed, emailed))
    return out, more
