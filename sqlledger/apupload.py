"""Inkoopfactuur uploaden (fase 4 stap 4e, beslispunten IU3–IU5).

Bij een geüploade inkoopfactuur kiest de gebruiker alleen leverancier, datum, nummer en totaal;
de rest stelt deze module voor:

- kostenrekening (IU3): de vaste kostenrekening van de leverancier (eigen tabel
  ``x_vendor_default``), anders die van de laatste boeking zonder artikelen van deze leverancier
  (op datum, dan volgorde van invoeren); had die meer kostenregels, dan de grootste;
- btw (IU4): de btw-rekening van die laatste boeking voor zover nog aan de leverancier gekoppeld
  (bij meer: de grootste), had die boeking geen btw dan geen btw; zonder bruikbare historie de
  gekoppelde btw-rekening (bij twee of meer: kiezen). Het totaal is inclusief btw:
  netto = totaal / (1 + tarief), btw = totaal − netto (``split_total``);
- overige velden (IU5): termijn/vervaldatum, valuta en crediteurenrekening van de leverancier
  (``ArapLedger.vc_defaults``), afdeling van de laatste boeking.

Boeken gebeurt daarna via ``ArapLedger.post_transaction``, dus met dezelfde controles als het
gewone scherm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db
from .amounts import Number, round_amount, to_decimal
from .arap import ArapLedger
from .config import UserConfig
from .errors import ValidationError
from .extensions import ensure_schema

__all__ = ["PurchaseUpload", "UploadSuggestion", "split_total"]


def split_total(total: Number, rate: Optional[Number], precision: int = 2,
                tax: Optional[Number] = None) -> Tuple[Decimal, Decimal]:
    """Totaal inclusief btw splitsen in (netto, btw). Met ``tax`` (bedrag van de factuur) is het
    netto het totaal min dat bedrag; anders netto = totaal / (1 + tarief) afgerond, btw = de rest."""
    total = to_decimal(total)
    if tax is not None:
        tax = round_amount(to_decimal(tax), precision)
        return round_amount(total - tax, precision), tax
    if not rate:
        return round_amount(total, precision), Decimal(0)
    net = round_amount(total / (1 + to_decimal(rate)), precision)
    return net, round_amount(total - net, precision)


@dataclass
class UploadSuggestion:
    """Voorstel voor een inkoopboeking van één leverancier op een factuurdatum."""

    vendor_id: int
    transdate: date
    expense_accno: Optional[str] = None
    expense_source: str = ""                  # "vast", "laatste" of "" (zelf kiezen)
    several_lines: bool = False               # laatste boeking had meer kostenrekeningen
    tax_accno: Optional[str] = None           # "" = geen btw; None = kiezen uit tax_choice
    tax_choice: List[str] = field(default_factory=list)
    several_taxes: bool = False               # laatste boeking had meer btw-rekeningen
    rates: Dict[str, Decimal] = field(default_factory=dict)
    last_id: Optional[int] = None
    department_id: Optional[int] = None
    arap_accno: Optional[str] = None
    currency: str = ""
    terms: int = 0
    duedate: Optional[date] = None


class PurchaseUpload:
    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------ vaste kostenrekening

    def default_expense(self, vendor_id: int) -> Optional[str]:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno FROM x_vendor_default d JOIN chart c ON (c.id = d.expense_chart_id)
                           WHERE d.vendor_id = %s""", (vendor_id,))
            r = cur.fetchone()
        return r["accno"] if r else None

    def set_default_expense(self, vendor_id: int, accno: Optional[str]) -> None:
        """Vaste kostenrekening van een leverancier zetten (leeg: weghalen). Alleen een
        rekening voor inkoopregels (koppeling AP_amount)."""
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM vendor WHERE id = %s", (vendor_id,))
            if not cur.fetchone():
                raise ValidationError("Leverancier niet gevonden.")
            accno = (accno or "").strip()
            if not accno:
                cur.execute("DELETE FROM x_vendor_default WHERE vendor_id = %s", (vendor_id,))
                return
            cur.execute("SELECT id, link, charttype FROM chart WHERE accno = %s", (accno,))
            c = cur.fetchone()
            if not c or c["charttype"] != "A" or "AP_amount" not in (c["link"] or "").split(":"):
                raise ValidationError("Kies als vaste kostenrekening een rekening voor inkoopregels.")
            cur.execute("""INSERT INTO x_vendor_default (vendor_id, expense_chart_id, login) VALUES (%s, %s, %s)
                           ON CONFLICT (vendor_id) DO UPDATE SET expense_chart_id = excluded.expense_chart_id,
                           login = excluded.login, updated = now()""",
                        (vendor_id, c["id"], self.config.short_login))

    # ------------------------------------------------------------ voorstel

    def last_booking(self, vendor_id: int, before: Optional[Tuple[date, int]] = None) -> Optional[Dict[str, Any]]:
        """Laatste inkoopboeking zonder artikelen van deze leverancier (op datum, dan id);
        ``before`` = (datum, id): alleen boekingen daarvóór (terugrekenen op echte data)."""
        query = "SELECT id, department_id FROM ap WHERE vendor_id = %s AND invoice IS NOT TRUE"
        params: List[Any] = [vendor_id]
        if before:
            query += " AND (transdate, id) < (%s, %s)"
            params += list(before)
        with _db.cursor(self.conn) as cur:
            cur.execute(query + " ORDER BY transdate DESC, id DESC LIMIT 1", params)
            return cur.fetchone()

    def suggest(self, vendor_id: int, transdate: date,
                before: Optional[Tuple[date, int]] = None) -> UploadSuggestion:
        al = ArapLedger(self.conn, self.config)
        d = al.vc_defaults("vendor", vendor_id, transdate)
        rates: Dict[str, Decimal] = dict(d["taxrates"])
        s = UploadSuggestion(vendor_id=vendor_id, transdate=transdate, rates=rates, tax_choice=sorted(rates),
                             arap_accno=d["arap_accno"], currency=d["currency"] or "",
                             terms=int(d["terms"] or 0), duedate=d["duedate"])
        last = self.last_booking(vendor_id, before)
        t = al.get_transaction(last["id"], "vendor") if last else None
        if last:
            s.last_id, s.department_id = last["id"], last["department_id"] or None

        fixed = self.default_expense(vendor_id)
        if fixed:
            s.expense_accno, s.expense_source = fixed, "vast"
        elif t and t.lines:
            best = max(t.lines, key=lambda ln: abs(to_decimal(ln.amount)))
            s.expense_accno, s.expense_source = best.accno, "laatste"
            s.several_lines = len({ln.accno for ln in t.lines}) > 1

        def linked() -> Optional[str]:
            if len(rates) == 1:
                return next(iter(rates))
            return "" if not rates else None
        if t is None:
            s.tax_accno = linked()
        else:
            used = {a: to_decimal(v) for a, v in t.taxes.items() if to_decimal(v)}
            still = [a for a in sorted(used) if a in rates]
            if still:
                s.tax_accno = max(still, key=lambda a: abs(used[a]))
                s.several_taxes = len(still) > 1
            elif used:
                s.tax_accno = linked()
            else:
                s.tax_accno = ""
        return s
