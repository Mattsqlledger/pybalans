"""Aanmaningen en rekeningoverzichten (fase 4 stap 4a, SB1–SB6).

SQL-Ledger (rp.pl ``reminder``/``aging``, RP.pm): één aanmaning per factuur, niveau alleen met de knop
"Save Level", bij printen/mailen niets vastgelegd; rekeningoverzicht per klant uit de ouderdomsanalyse.
Hier (beslispunten SB1–SB6, zie FASE4_ANALYSE.md):

* **Aanmaning** per klant en valuta met alle vervallen facturen (SB1). Het niveau blijft per factuur in
  ``status`` als ``reminder1..3`` (zoals SL, dus Perl leest het ook); de brief krijgt het hoogste niveau
  van de opgenomen facturen. Negatieve posten (creditnota's, vooruitbetalingen) staan onder "te
  verrekenen" en gaan van het totaal af; een klant met een totaal ≤ 0 krijgt geen aanmaning (SB2).
* Definitief printen of geslaagd mailen legt het niveau vast (+1, hoogstens 3) met het vinkje
  geprint/gemaild in dezelfde statusregel en een audittrailregel (SB3). Het niveau dat wordt vastgelegd
  is het niveau dat op het scherm stond (``Letter.levels``), zodat PDF en mail van dezelfde brief het
  niveau niet twee keer ophogen.
* **Rekeningoverzicht** per klant en valuta (Perl-fout 19) uit de ouderdomsanalyse, openstaand per
  peildatum (``reports_arap.open_asof``, SB5). Geen ``status`` (SL heeft er geen), wel audittrail.

Bedragen staan in de valuta van de factuur (zoals rp.pl: openstaand gedeeld door de koers van de
factuurdatum).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import psycopg

from . import _db, support
from .amounts import round_amount, to_decimal
from .config import UserConfig
from .reports import Reports
from .reports_arap import BUCKET_DAYS, BUCKETS, open_asof

ZERO = Decimal(0)
MAX_LEVEL = 3
REMINDER_FORMS = tuple(f"reminder{n}" for n in range(1, MAX_LEVEL + 1))
STATEMENT_BUCKETS = ("c0", "c30", "c60", "c90")


@dataclass
class Item:
    """Een openstaande post op een aanmaning of overzicht (bedrag in de valuta van de factuur)."""

    id: int
    invnumber: str
    transdate: Optional[date]
    duedate: Optional[date]
    due: Decimal
    days: int = 0                   # dagen na de vervaldatum (op de peildatum)
    level: int = 0                  # vastgelegd aanmaanniveau (0 = nog geen)
    ordnumber: str = ""
    ponumber: str = ""
    description: str = ""
    dcn: str = ""
    module: str = "ar"
    bank: Dict[str, Any] = field(default_factory=dict)

    @property
    def next_level(self) -> int:
        return min(self.level + 1, MAX_LEVEL)


@dataclass
class Letter:
    """Aanmaning voor één klant in één valuta."""

    vc_id: int
    curr: str
    name: str
    customernumber: str
    language_code: str
    items: List[Item]
    credits: List[Item] = field(default_factory=list)
    #: niveau per factuur zoals het op het scherm stond (zie ``levels``); leeg = volgend niveau
    chosen: Dict[int, int] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.vc_id}-{self.curr}"

    @property
    def total_due(self) -> Decimal:
        return sum((i.due for i in self.items), ZERO)

    @property
    def total_credit(self) -> Decimal:
        return sum((i.due for i in self.credits), ZERO)

    @property
    def total(self) -> Decimal:
        return self.total_due + self.total_credit

    @property
    def level(self) -> int:
        return max(self.levels.values(), default=1)

    @property
    def formname(self) -> str:
        return f"reminder{self.level}"

    @property
    def levels(self) -> Dict[int, int]:
        """Niveau per factuur dat bij printen/mailen wordt vastgelegd: het niveau dat op het scherm stond
        (``chosen``, 1–3), anders het volgende niveau. Zo hogen PDF en mail van dezelfde brief het niveau
        niet twee keer op."""
        out = {}
        for i in self.items:
            c = self.chosen.get(i.id)
            out[i.id] = c if c in range(1, MAX_LEVEL + 1) else i.next_level
        return out

    def only(self, ids: Iterable[int], chosen: Optional[Mapping[int, int]] = None) -> "Letter":
        """Dezelfde brief met alleen de aangevinkte facturen (te verrekenen posten blijven) en eventueel
        de niveaus van het scherm."""
        keep = {int(x) for x in ids}
        return Letter(self.vc_id, self.curr, self.name, self.customernumber, self.language_code,
                      [i for i in self.items if i.id in keep], list(self.credits),
                      {int(k): int(v) for k, v in (chosen or {}).items() if int(k) in keep})


@dataclass
class Statement:
    """Rekeningoverzicht voor één klant in één valuta."""

    vc_id: int
    curr: str
    name: str
    customernumber: str
    language_code: str
    buckets: List[str]
    rows: List[Dict[str, Any]]
    totals: Dict[str, Decimal]
    overdue: bool = False           # ouderdom vanaf de vervaldatum (anders vanaf de factuurdatum)

    @property
    def key(self) -> str:
        return f"{self.vc_id}-{self.curr}"

    @property
    def total(self) -> Decimal:
        return sum(self.totals.values(), ZERO)


def bucket_ranges(buckets: Sequence[str]) -> List[str]:
    """Dagen per gekozen ouderdomskolom, zoals ``Reports.aging`` ze vult: "0–30", "31–60", "> 90"."""
    days = [BUCKET_DAYS[b] for b in buckets]
    out = []
    for n, d in enumerate(days):
        if n == len(days) - 1:
            out.append(f"> {d}" if len(days) > 1 else f"≥ {d}")
        elif n == 0:
            out.append(f"{d}–{days[1]}")
        else:
            out.append(f"{d + 1}–{days[n + 1]}")
    return out


class Dunning:
    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    def _precision(self, cur) -> int:
        return int(to_decimal(_db.get_defaults(cur, "precision").get("precision") or 2))

    # ============================================================ aanmaningen

    def letters(self, asof: Optional[date] = None, *, min_days: int = 0, customer_id: Optional[int] = None,
                name: Optional[str] = None, customernumber: Optional[str] = None,
                department_id: Optional[int] = None) -> List[Letter]:
        """Aanmaningen op de peildatum: per klant en valuta de vervallen facturen (minstens ``min_days`` na
        de vervaldatum) en de te verrekenen negatieve posten. Klanten met een totaal ≤ 0 vallen af."""
        asof = asof or date.today()
        rep = Reports(self.conn, self.config).reminders(customer_id=customer_id, name=name,
                                                        customernumber=customernumber,
                                                        department_id=department_id, asof=asof)
        with _db.cursor(self.conn) as cur:
            precision = self._precision(cur)
            letters: Dict[tuple, Letter] = {}
            for r in rep.rows:
                days = (asof - r["duedate"]).days if r.get("duedate") else 0
                due = round_amount(to_decimal(r["due"]) / (r.get("exchangerate") or 1), precision)
                if due > 0 and days < int(min_days or 0):
                    continue
                if due <= 0:
                    continue            # negatieve posten: hieronder, ook als ze nog niet vervallen zijn
                k = (r["vc_id"], r["curr"])
                if k not in letters:
                    letters[k] = Letter(r["vc_id"], r["curr"], r["name"] or "", r.get("customernumber") or "",
                                        r.get("language_code") or "", [])
                bank = {x: r.get(x) for x in ("iban", "bic", "membernumber", "clearingnumber", "rvc")}
                letters[k].items.append(Item(
                    r["id"], r.get("invnumber") or "", r.get("transdate"), r.get("duedate"), due, days,
                    r["level"] - 1, r.get("ordnumber") or "", r.get("ponumber") or "",
                    r.get("invdescription") or "", r.get("dcn") or "", r.get("module") or "ar", bank))
            for (vc_id, curr), letter in letters.items():
                letter.credits = self._credits(cur, vc_id, curr, asof, department_id, precision)
        out = [l for l in letters.values() if l.total > 0]
        out.sort(key=lambda l: (l.name.lower(), l.vc_id, l.curr))
        return out

    def _credits(self, cur, vc_id: int, curr: str, asof: date, department_id: Optional[int],
                 precision: int) -> List[Item]:
        openamt = open_asof("ar", "asof")
        dept = "AND a.department_id = %(dept)s" if department_id else ""
        cur.execute(f"""SELECT a.id, a.invnumber, a.transdate, a.duedate, a.ordnumber, a.ponumber,
                               a.description, a.dcn, a.invoice, a.till, {openamt} AS due,
                               (SELECT exchangerate FROM exchangerate e
                                WHERE a.curr = e.curr AND e.transdate = a.transdate) AS exchangerate
                        FROM ar a
                        WHERE a.customer_id = %(vc)s AND a.curr = %(curr)s AND a.approved = '1'
                          AND a.transdate <= %(asof)s {dept} AND {openamt} < 0
                        ORDER BY a.transdate, a.invnumber""",
                    {"vc": vc_id, "curr": curr, "asof": asof, "dept": department_id})
        out = []
        for r in cur.fetchall():
            rate = to_decimal(r["exchangerate"]) or Decimal(1)
            module = "ps" if r["till"] else ("is" if r["invoice"] else "ar")
            out.append(Item(r["id"], r["invnumber"] or "", r["transdate"], r["duedate"],
                            round_amount(to_decimal(r["due"]) / rate, precision), 0, 0, r["ordnumber"] or "",
                            r["ponumber"] or "", r["description"] or "", r["dcn"] or "", module))
        return out

    def find_letter(self, vc_id: int, curr: str, asof: Optional[date] = None, *, min_days: int = 0,
                    department_id: Optional[int] = None) -> Optional[Letter]:
        for letter in self.letters(asof, min_days=min_days, customer_id=vc_id, department_id=department_id):
            if letter.curr == curr:
                return letter
        return None

    def record(self, letter: Letter, *, printed: bool = False, emailed: bool = False) -> None:
        """Brief verstuurd (definitieve PDF of geslaagde mail): per factuur het niveau van de brief
        vastleggen met het vinkje in dezelfde statusregel, plus audittrail (SB3)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for item in letter.items:
                level = letter.levels[item.id]
                formname = f"reminder{level}"
                cur.execute("SELECT formname, printed, emailed FROM status "
                            "WHERE trans_id = %s AND formname LIKE 'reminder_'", (item.id,))
                old = {r["formname"]: r for r in cur.fetchall()}
                same = old.get(formname) or {}
                cur.execute("DELETE FROM status WHERE trans_id = %s AND formname LIKE 'reminder_'", (item.id,))
                cur.execute("INSERT INTO status (trans_id, printed, emailed, formname) VALUES (%s, %s, %s, %s)",
                            (item.id, bool(same.get("printed")) or printed, bool(same.get("emailed")) or emailed,
                             formname))
                support.audittrail(cur, self.config, trans_id=item.id, tablename="ar", reference=item.invnumber,
                                   formname=formname, action="emailed" if emailed else "printed")

    def set_levels(self, levels: Mapping[int, int]) -> List[int]:
        """Niveau per factuur handmatig zetten of terugzetten (0 = wissen); alleen verkoopfacturen.
        Geeft de gewijzigde facturen terug; audittrail ``saved``/``deleted`` per factuur."""
        changed = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for trans_id, level in levels.items():
                trans_id, level = int(trans_id), int(level)
                if not 0 <= level <= MAX_LEVEL:
                    raise ValueError("ongeldig aanmaanniveau")
                cur.execute("SELECT invnumber FROM ar WHERE id = %s", (trans_id,))
                row = cur.fetchone()
                if not row:
                    continue
                cur.execute("SELECT formname, printed, emailed FROM status "
                            "WHERE trans_id = %s AND formname LIKE 'reminder_' ORDER BY formname DESC", (trans_id,))
                old = cur.fetchall()
                current = int(old[0]["formname"][-1]) if old and old[0]["formname"][-1].isdigit() else 0
                if current == level and len(old) <= 1:
                    continue
                cur.execute("DELETE FROM status WHERE trans_id = %s AND formname LIKE 'reminder_'", (trans_id,))
                if level:
                    keep = next((r for r in old if r["formname"] == f"reminder{level}"), {})
                    cur.execute("INSERT INTO status (trans_id, printed, emailed, formname) VALUES (%s, %s, %s, %s)",
                                (trans_id, bool(keep.get("printed")), bool(keep.get("emailed")), f"reminder{level}"))
                support.audittrail(cur, self.config, trans_id=trans_id, tablename="ar", reference=row["invnumber"],
                                   formname=f"reminder{level or current}", action="saved" if level else "deleted")
                changed.append(trans_id)
        return changed

    # ============================================================ rekeningoverzichten

    def statements(self, asof: Optional[date] = None, *, buckets: Sequence[str] = STATEMENT_BUCKETS,
                   overdue: bool = False, customer_id: Optional[int] = None, name: Optional[str] = None,
                   customernumber: Optional[str] = None, department_id: Optional[int] = None) -> List[Statement]:
        """Rekeningoverzichten per klant en valuta uit de ouderdomsanalyse op de peildatum."""
        sel = [b for b in BUCKETS if b in set(buckets)] or list(STATEMENT_BUCKETS)
        rep = Reports(self.conn, self.config).aging("ar", todate=asof or date.today(), buckets=sel, overdue=overdue,
                                                    vc_id=customer_id, name=name, vcnumber=customernumber,
                                                    department_id=department_id)
        out: Dict[tuple, Statement] = {}
        for r in rep.rows:
            k = (r["vc_id"], r["curr"])
            st = out.get(k)
            if st is None:
                st = out[k] = Statement(r["vc_id"], r["curr"], r.get("name") or "", r.get("customernumber") or "",
                                        r.get("language_code") or "", sel, [], {b: ZERO for b in sel}, overdue)
            st.rows.append(r)
            for b in sel:
                st.totals[b] += to_decimal(r.get(b))
        return sorted(out.values(), key=lambda s: (s.name.lower(), s.vc_id, s.curr))

    def find_statement(self, vc_id: int, curr: str, asof: Optional[date] = None, **kw) -> Optional[Statement]:
        for st in self.statements(asof, customer_id=vc_id, **kw):
            if st.curr == curr:
                return st
        return None

    def record_statement(self, st: Statement, *, emailed: bool = False) -> None:
        """Overzicht verstuurd: alleen audittrail (SL legt niets vast; geen statusregel per factuur)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            support.audittrail(cur, self.config, trans_id=st.vc_id, tablename="customer",
                               reference=st.customernumber or st.name, formname="statement",
                               action="emailed" if emailed else "printed")


# ================================================================== sjabloonvariabelen

def _company(pd, vc_id: int) -> tuple:
    """(bedrijf- en klantgegevens, aantal decimalen)."""
    with _db.cursor(pd.conn) as cur:
        v = pd.company_details(cur, "customer", vc_id, None)
        precision = int(to_decimal(_db.get_defaults(cur, "precision").get("precision") or 2))
    return v, precision


def _item_vars(pd, i: Item, p: int) -> Dict[str, Any]:
    return {"id": i.id, "invnumber": i.invnumber, "invdate": pd.date(i.transdate), "duedate": pd.date(i.duedate),
            "days": i.days, "due": pd.amt(i.due, p, "0"), "level": i.level, "ordnumber": i.ordnumber,
            "ponumber": i.ponumber, "invdescription": i.description, "dcn": i.dcn}


def letter_vars(pd, letter: Letter, asof: date) -> Dict[str, Any]:
    """Sjabloonvariabelen van een aanmaning (``reminder1..3``): bedrijf en klant zoals de facturen
    (``company_details``), ``items`` en ``credits`` als lijsten van regels, totalen, niveau, bank van de
    eerste factuur (zoals rp.pl ``bank_id`` van de factuur)."""
    v, p = _company(pd, letter.vc_id)
    first = letter.items[0] if letter.items else None
    bank = (first.bank if first else {}) or {}
    v.update({
        "level": letter.level, "formname": letter.formname, "statementdate": pd.date(asof),
        "currency": letter.curr, "language_code": letter.language_code,
        "items": [_item_vars(pd, i, p) for i in letter.items],
        "credits": [_item_vars(pd, i, p) for i in letter.credits],
        "total_due": pd.amt(letter.total_due, p, "0"), "total_credit": pd.amt(letter.total_credit, p, "0"),
        "total": pd.amt(letter.total, p, "0"),
        "iban": bank.get("iban") or "", "bic": bank.get("bic") or "", "membernumber": bank.get("membernumber") or "",
        "dcn": first.dcn if first and len(letter.items) == 1 else "",
        # voor plaatshouders in de mail ({nummer}, {datum}, {bedrag})
        "invnumber": ", ".join(i.invnumber for i in letter.items), "invdate": pd.date(asof),
        "invtotal": pd.amt(letter.total, p, "0"), "duedate": "",
    })
    return v


def statement_vars(pd, st: Statement, asof: date) -> Dict[str, Any]:
    """Sjabloonvariabelen van een rekeningoverzicht (``statement``): ``rows`` met per regel de
    ouderdomskolommen, ``columns`` (sleutel + dagen), totalen per kolom en eindtotaal."""
    v, p = _company(pd, st.vc_id)
    rows = []
    for r in st.rows:
        rows.append({"invnumber": r.get("invnumber") or "", "invdate": pd.date(r.get("transdate")),
                     "duedate": pd.date(r.get("duedate")), "ordnumber": r.get("ordnumber") or "",
                     "ponumber": r.get("ponumber") or "", "invdescription": r.get("invdescription") or "",
                     **{b: pd.amt(r.get(b), p, "") for b in st.buckets},
                     "total": pd.amt(sum((to_decimal(r.get(b)) for b in st.buckets), ZERO), p, "0")})
    # bank van de nieuwste openstaande factuur (rp.pl statement heeft geen bankgegevens; een vaste rekening
    # uit Documentinstellingen gaat voor, zie DocumentRenderer.apply_bank)
    bank: Dict[str, Any] = {}
    ids = [r["id"] for r in sorted(st.rows, key=lambda r: (r.get("transdate") or date.min, r["id"]), reverse=True)]
    if ids:
        with _db.cursor(pd.conn) as cur:
            cur.execute("""SELECT b.iban, b.bic, b.membernumber FROM ar a JOIN bank b ON (b.id = a.bank_id)
                           WHERE a.id = ANY(%s) AND coalesce(b.iban, '') <> ''
                           ORDER BY a.transdate DESC, a.id DESC LIMIT 1""", (ids,))
            bank = cur.fetchone() or {}
    v.update({
        "iban": bank.get("iban") or "", "bic": bank.get("bic") or "", "membernumber": bank.get("membernumber") or "",
        "formname": "statement", "statementdate": pd.date(asof), "currency": st.curr, "overdue": st.overdue,
        "language_code": st.language_code, "rows": rows,
        "columns": [{"key": b, "days": d} for b, d in zip(st.buckets, bucket_ranges(st.buckets))],
        "totals": {b: pd.amt(st.totals[b], p, "0") for b in st.buckets}, "total": pd.amt(st.total, p, "0"),
        "invnumber": "", "invdate": pd.date(asof), "invtotal": pd.amt(st.total, p, "0"), "duedate": "",
    })
    return v
