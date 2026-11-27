"""Btw-aangifte: overzicht per rubriek en de btw-boeking (BTW_ANALYSE.md BA1–BA9; per land BTW_LANDEN_ANALYSE.md).

De rekenkern is voor elk land gelijk; het formulier (rubrieken, koppelingen, afronding, totalen) staat per land in
``sqlledger.vatforms`` (BL1). Het land van de aangifte is de instelling ``vat_country`` (BL2), anders het land van
het rekeningschema, anders Nederland.

SQL-Ledger kent geen aangifte en geen koppeling btw-rekening → rubriek; die staan in eigen tabellen
(migratie 13): ``x_vat_box`` (rubriek per btw-rekening, BA3), ``x_vat_customer_box`` (rubriek per klant voor omzet
zonder btw, BA4) en ``x_vat_return`` (vastgelegde aangifte met btw-boeking, BA6/BA7). De afdrachtrekening en de
rekening afrondingsverschillen staan in ``x_setting``.

Rekenregels (bedragen in de standaardvaluta, ``acc_trans`` positief = credit):

* **Btw** per rubriek = de mutaties op de btw-rekeningen van die rubriek in het tijdvak (``acc_trans.transdate``),
  alleen goedgekeurde boekingen. Boekingen die op de afdrachtrekening boeken tellen niet mee (BA7: de btw-boeking zelf
  en oude handmatige afdrachtboekingen).
* **Grondslag** per btw-rekening, per boeking:
  - één btw-rekening: het nettobedrag van de boeking (verkoop/inkoop: ``netamount``; memoriaal: de regels op
    opbrengst- en kostenrekeningen);
  - factuur met artikelen: per factuurregel bij de btw-rekeningen van het artikel (``partstax``) die op de factuur
    staan; regels zonder btw-rekening = zonder btw; het afrondingsverschil met ``netamount`` gaat naar de grootste;
  - anders (meer tarieven zonder artikelen, kassabonnen): btw ÷ tarief; het verschil met het nettobedrag gaat naar het
    hoogste tarief (een 0%-rekening op de boeking krijgt het verschil), en is het groter dan ``SPLIT_TOLERANCE`` dan
    telt het als omzet zonder btw en komt er een melding.
* **Omzet zonder btw** (BA4/BL4): verkoopboekingen zonder btw-regel tellen in de koppeling van de klant (NL 1E, 3A,
  3B; VK 6, 8), anders in de standaard van het formulier. Inkoop zonder btw telt alleen mee als het formulier dat vraagt
  (VK box 7).
* **Inkoop per kostenrekening** (BL3, BE 81/82/83): de grondslag van elke inkoopboeking (ook zonder btw) en van
  memorialen met aftrekbare btw wordt verdeeld over de kosten- en activarekeningen op de boeking, naar de rubriek die
  op *Btw-rubrieken* bij die rekening staat (``x_vat_account_box``, migratie 21), anders het voorstel uit de klasse.
* **Creditnota's** (BE1): het formulier kan per boeking negatieve bedragen naar eigen roosters verplaatsen
  (``VatForm.adjust``).
* **Afronden** (BA5/BL6): volgens het formulier; sinds 2026-10-13 elke aangifte in hele bedragen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import psycopg

from . import _db
from .amounts import round_amount, to_decimal
from .dates import last_day_of_month
from .errors import ValidationError
from .extensions import ensure_schema
from .vatforms import (CURRENCY_COUNTRY, FORMS, NL, Box, Target, VatForm, form_for, nl_round, nl_suggest)

ZERO = Decimal(0)
CENT = Decimal("0.01")
#: verschil (in euro's) tussen nettobedrag en btw ÷ tarief dat nog als afronding geldt
SPLIT_TOLERANCE = Decimal("1.00")

SETTLEMENT_SETTING = "vat_settlement_accno"
ROUNDING_SETTING = "vat_rounding_accno"
COUNTRY_SETTING = "vat_country"             # BL2: land van de aangifte
OFFSET_SETTING = "vat_quarter_offset"       # BL7/UK3: kwartaal eindigt 0/1/2 maanden na het kalenderkwartaal


#: Nederlandse rubrieken (het eerste formulier; namen bleven voor bestaande code en toetsen)
BOXES: Tuple[Box, ...] = NL.boxes
BOX = NL.box
ACCOUNT_BOXES = NL.account_boxes
CUSTOMER_BOXES = NL.customer_choices
TOTALS = NL.totals
round_boxes = nl_round
suggest_box = nl_suggest


# ---------------------------------------------------------------------------- tijdvakken

@dataclass(frozen=True)
class Period:
    kind: str           # quarter | month | year | free
    fromdate: date
    todate: date
    year: int
    number: int         # kwartaal of maand (jaar en vrij: 0)

    @property
    def key(self) -> str:
        return f"{self.fromdate.isoformat()}_{self.todate.isoformat()}"


def period_of(kind: str, d: date, offset: int = 0) -> Period:
    """Het tijdvak (maand, kwartaal of jaar) waar ``d`` in valt. ``offset``: kwartalen die 1 of 2 maanden later
    eindigen dan de kalenderkwartalen (Britse "stagger"-groepen, BL7); het nummer is dan 0."""
    if kind == "month":
        return Period(kind, date(d.year, d.month, 1), last_day_of_month(d), d.year, d.month)
    if kind == "quarter" and offset:
        k = (d.month - 1 - offset) % 3                  # maanden sinds het begin van het kwartaal
        m, y = d.month - k, d.year
        if m < 1:
            m, y = m + 12, y - 1
        start = date(y, m, 1)
        em, ey = m + 2, y
        if em > 12:
            em, ey = em - 12, ey + 1
        return Period(kind, start, last_day_of_month(date(ey, em, 1)), ey, 0)
    if kind == "quarter":
        q = (d.month - 1) // 3 + 1
        start = date(d.year, 3 * q - 2, 1)
        return Period(kind, start, last_day_of_month(date(d.year, 3 * q, 1)), d.year, q)
    if kind == "year":
        return Period(kind, date(d.year, 1, 1), date(d.year, 12, 31), d.year, 0)
    raise ValidationError("Onbekend aangiftetijdvak.")


def last_closed(kind: str, today: date, offset: int = 0) -> Period:
    """Het laatst afgesloten tijdvak (zoals de taak op het dashboard)."""
    return period_of(kind, period_of(kind, today, offset).fromdate - timedelta(days=1), offset)


def recent_periods(kind: str, today: date, count: int = 12, offset: int = 0) -> List[Period]:
    """Het lopende tijdvak en de ``count`` tijdvakken daarvoor, nieuwste eerst."""
    out = [period_of(kind, today, offset)]
    while len(out) <= count:
        out.append(period_of(kind, out[-1].fromdate - timedelta(days=1), offset))
    return out


def free_period(fromdate: date, todate: date) -> Period:
    if todate < fromdate:
        raise ValidationError("De einddatum ligt voor de begindatum.")
    for kind, offset in (("month", 0), ("quarter", 0), ("quarter", 1), ("quarter", 2), ("year", 0)):
        p = period_of(kind, fromdate, offset)
        if p.fromdate == fromdate and p.todate == todate:
            return p
    return Period("free", fromdate, todate, fromdate.year, 0)


# ---------------------------------------------------------------------------- resultaat

@dataclass
class BoxAmount:
    base_exact: Decimal = ZERO
    tax_exact: Decimal = ZERO
    base: Decimal = ZERO        # afgerond (BA5)
    tax: Decimal = ZERO


@dataclass
class TransVat:
    """Bijdrage van één boeking aan de rubrieken (voor doorklikken en vergelijken)."""
    trans_id: int
    kind: str                   # ar | ap | gl
    invoice: bool
    number: str
    transdate: date
    name: str
    boxes: Dict[str, List[Decimal]] = field(default_factory=dict)   # rubriek -> [grondslag, btw]
    note: str = ""              # split | customer | default (BA4)
    diff: Decimal = ZERO        # bij note == split: deel zonder btw

    def add(self, box: str, base: Decimal, tax: Decimal) -> None:
        slot = self.boxes.setdefault(box, [ZERO, ZERO])
        slot[0] += base
        slot[1] += tax


@dataclass
class AccountTotal:
    chart_id: int
    accno: str
    description: str
    box: str
    amount: Decimal             # som van de mutaties (credit positief)


@dataclass
class Difference:
    """BA7: verschil tussen de vastgelegde aangifte en de boekhouding van nu."""
    boxes: Dict[str, Tuple[Decimal, Decimal, Decimal, Decimal]]   # rubriek -> (grondslag toen, nu, btw toen, nu)
    total: Tuple[Decimal, Decimal]                                # 5G toen, nu
    transactions: List[Dict[str, Any]]                            # boekingen die het veroorzaken


@dataclass
class VatReport:
    period: Period
    boxes: Dict[str, BoxAmount]
    totals: Dict[str, Decimal]          # 5A..5G afgerond
    total_exact: Decimal                # 5G exact (som van de btw-mutaties)
    transactions: List[TransVat]
    accounts: List[AccountTotal]
    unmapped: List[Dict[str, Any]]      # btw-rekeningen met mutaties maar zonder rubriek
    settlement_excluded: int            # boekingen op de afdrachtrekening (niet meegeteld)
    customer_count: int                 # BA4: omzet zonder btw via de rubriek van de klant
    default_count: int                  # BA4: omzet zonder btw in 1E
    split_warnings: List[TransVat]
    stored: Optional[Dict[str, Any]] = None       # x_vat_return van dit tijdvak
    country: str = "NL"
    difference: Optional[Difference] = None

    def box_transactions(self, box: str) -> List[TransVat]:
        return [t for t in self.transactions if box in t.boxes]


# ---------------------------------------------------------------------------- de aangifte

class VatReturns:
    def __init__(self, conn: psycopg.Connection, config=None, country: Optional[str] = None):
        self.conn = conn
        self.config = config
        self._country = country.upper() if country else None

    # -------------------------------------------------------------- land (BL2)

    @property
    def country(self) -> str:
        """Land van de aangifte: instelling, anders het land van het schema, anders Nederland."""
        if self._country is None:
            c = self.setting(COUNTRY_SETTING) or self.setting("country")
            self._country = c.upper() if c and c.upper() in FORMS else "NL"
        return self._country

    @property
    def form(self) -> VatForm:
        return form_for(self.country)

    def country_saved(self) -> bool:
        return bool(self.setting(COUNTRY_SETTING))

    def suggest_country(self) -> str:
        """Voorstel als er nog niets is gekozen: land van het schema, anders uit de valuta (GBP → VK, EUR → NL).
        Kan een land zonder formulier zijn (bv. CH) of leeg (onbekend); dan geen voorstel (VP3). Een administratie
        uit Run my Accounts / ledger123 (Zwitserland, Duitsland) krijgt bij EUR geen Nederlands voorstel."""
        c = (self.setting("country") or "").upper()
        if c:
            return c
        with _db.cursor(self.conn) as cur:
            cur_ = (_db.default_currency(cur) or "").upper()
            cur.execute("SELECT to_regclass('rma.import_report') IS NOT NULL AS x")
            from_rma = cur.fetchone()["x"]
        if from_rma and cur_ != "CHF":
            return ""
        return CURRENCY_COUNTRY.get(cur_, "")

    def unavailable(self) -> Optional[str]:
        """VP3: nog niets gekozen en het voorgestelde land heeft geen formulier: dat land ("" = onbekend); anders
        None."""
        if self.country_saved() or self.configured():
            return None
        c = self.suggest_country()
        return None if c in FORMS else c

    def quarter_offset(self) -> int:
        """BL7: 0 = kalenderkwartalen, 1/2 = kwartalen die een of twee maanden later eindigen (alleen als het formulier
        dat kent)."""
        try:
            v = int(self.setting(OFFSET_SETTING) or 0)
        except ValueError:
            v = 0
        return v if v in self.form.quarter_offsets else 0

    def save_country(self, country: str, offset: int = 0, login: Optional[str] = None) -> List[str]:
        country = (country or "").upper()
        if country not in FORMS:
            raise ValidationError("Onbekend land voor de btw-aangifte.")
        if offset not in form_for(country).quarter_offsets:
            raise ValidationError("Ongeldige keuze voor het einde van het kwartaal.")
        ensure_schema(self.conn)
        changed = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for key, value in ((COUNTRY_SETTING, country), (OFFSET_SETTING, str(offset) if offset else "")):
                cur.execute("SELECT value FROM x_setting WHERE key = %s", (key,))
                r = cur.fetchone()
                if (r["value"] if r else "") == value:
                    continue
                cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                               ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                               updated = now()""", (key, value, login))
                changed.append(key)
        self._country = country
        return changed

    # -------------------------------------------------------------- instellingen (BA3)

    def tax_accounts(self) -> List[Dict[str, Any]]:
        """Alle btw-rekeningen (in ``tax``) met hun huidige tarief, link en ingestelde rubriek."""
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.id, c.accno, c.description, c.link, c.category,
                                  coalesce(c.closed, false) AS closed,
                                  (SELECT t.rate FROM tax t WHERE t.chart_id = c.id
                                   ORDER BY t.validto DESC NULLS FIRST LIMIT 1) AS rate,
                                  b.box
                           FROM chart c LEFT JOIN x_vat_box b ON (b.chart_id = c.id AND b.country = %s)
                           WHERE c.id IN (SELECT chart_id FROM tax)
                           ORDER BY c.accno""", (self.country,))
            rows = cur.fetchall()
        for r in rows:
            r["rate"] = to_decimal(r["rate"]) if r["rate"] is not None else None
            r["suggested"] = self.form.suggest(r)
        return rows

    def boxes(self) -> Dict[int, str]:
        """Ingestelde rubriek per btw-rekening (chart.id), ook 'none'."""
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT chart_id, box FROM x_vat_box WHERE country = %s", (self.country,))
            return {r["chart_id"]: r["box"] for r in cur.fetchall()}

    def configured(self) -> bool:
        """Er is minstens één btw-rekening aan een rubriek gekoppeld (BA8)."""
        return any(b != "none" for b in self.boxes().values())

    def setting(self, key: str) -> str:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT value FROM x_setting WHERE key = %s", (key,))
            r = cur.fetchone()
        return r["value"] if r else ""

    def accounts_setting(self) -> Tuple[str, str]:
        """(afdrachtrekening, rekening afrondingsverschillen) als rekeningnummers ('' = niet ingesteld)."""
        return self.setting(SETTLEMENT_SETTING), self.setting(ROUNDING_SETTING)

    def suggest_accounts(self) -> Tuple[str, str]:
        """Voorstel voor de twee rekeningen uit de omschrijving (administratie A: 1840 Rc BTW)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT accno, lower(description) AS d, category FROM chart
                           WHERE charttype = 'A' AND NOT coalesce(closed, false) ORDER BY accno""")
            rows = cur.fetchall()
        tax_ids = {a["accno"] for a in self.tax_accounts()}
        settle = next((r["accno"] for r in rows if r["accno"] not in tax_ids and r["category"] == "L"
                       and any(w in r["d"] for w in ("rc btw", "r/c btw", "saldo btw", "rekening-courant btw", "rekening courant btw",
                                                     "af te dragen btw", "btw-afdracht", "afdracht btw",
                                                     "omzetbelasting"))), "")
        rounding = next((r["accno"] for r in rows if r["category"] in ("I", "E")
                         and any(w in r["d"] for w in ("afrondingsverschil", "afronding", "betalingsverschil"))), "")
        return settle, rounding

    def save_settings(self, boxes: Mapping[int, str], settlement: str, rounding: str,
                      login: Optional[str] = None) -> List[str]:
        """Rubrieken per btw-rekening en de twee rekeningen opslaan; geeft de gewijzigde onderdelen terug."""
        ensure_schema(self.conn)
        known = {a["id"]: a for a in self.tax_accounts()}
        for chart_id, box in boxes.items():
            if int(chart_id) not in known:
                raise ValidationError("Onbekende btw-rekening.")
            if box not in self.form.account_boxes:
                raise ValidationError("Onbekende rubriek.")
        accnos = self._accnos()
        for accno in (settlement, rounding):
            if accno and accno not in accnos:
                raise ValidationError(f"Rekening {accno} bestaat niet of is niet boekbaar.")
        # een btw-rekening als afdrachtrekening alleen als hij niet in de aangifte telt (administratie C: 1520 "Payments to/from
        # HMC&E" heeft in SQL-Ledger een btw-koppeling met tarief 0)
        settle_acc = next((a for a in known.values() if a["accno"] == settlement), None) if settlement else None
        if settle_acc and boxes.get(settle_acc["id"], settle_acc["box"]) != "none":
            raise ValidationError("De afdrachtrekening mag geen btw-rekening zijn.")
        if settlement and settlement == rounding:
            raise ValidationError("Afdrachtrekening en rekening afrondingsverschillen moeten verschillen.")
        changed: List[str] = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for chart_id, box in boxes.items():
                if known[int(chart_id)]["box"] != box:
                    cur.execute("""INSERT INTO x_vat_box (chart_id, box, country, login) VALUES (%s, %s, %s, %s)
                                   ON CONFLICT (chart_id, country) DO UPDATE SET box = excluded.box, login = excluded.login,
                                                                         updated = now()""",
                                (int(chart_id), box, self.country, login))
                    changed.append(known[int(chart_id)]["accno"])
            for key, value in ((SETTLEMENT_SETTING, settlement), (ROUNDING_SETTING, rounding)):
                cur.execute("SELECT value FROM x_setting WHERE key = %s", (key,))
                r = cur.fetchone()
                if (r["value"] if r else "") == value:
                    continue
                cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                               ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                               updated = now()""", (key, value, login))
                changed.append(key)
        return changed

    def _accnos(self) -> Dict[str, int]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT accno, id FROM chart WHERE charttype = 'A'")
            return {r["accno"]: r["id"] for r in cur.fetchall()}

    # -------------------------------------------------------------- rubriek per klant (BA4)

    def customer_box(self, customer_id: int) -> str:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT box FROM x_vat_customer_box WHERE customer_id = %s AND country = %s",
                        (customer_id, self.country))
            r = cur.fetchone()
        return r["box"] if r else ""

    def save_customer_box(self, customer_id: int, box: str, login: Optional[str] = None) -> bool:
        """'' = geen (standaard van het formulier, NL 1E); geeft True als het gewijzigd is."""
        if box and box not in self.form.customer_choices:
            raise ValidationError("Onbekende rubriek.")
        if box == self.customer_box(customer_id):
            return False
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if box:
                cur.execute("""INSERT INTO x_vat_customer_box (customer_id, box, country, login) VALUES (%s, %s, %s, %s)
                               ON CONFLICT (customer_id, country) DO UPDATE SET box = excluded.box,
                                   login = excluded.login, updated = now()""",
                            (customer_id, box, self.country, login))
            else:
                cur.execute("DELETE FROM x_vat_customer_box WHERE customer_id = %s AND country = %s",
                            (customer_id, self.country))
        return True

    # -------------------------------------------------------------- berekenen

    def report(self, period: Period) -> VatReport:
        ensure_schema(self.conn)
        f, t = period.fromdate, period.todate
        form = self.form
        target = form.target
        boxes = self.boxes()
        settle_accno, _r = self.accounts_setting()
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, accno, description, category, link FROM chart WHERE charttype = 'A'")
            charts = {r["id"]: r for r in cur.fetchall()}
            settle_id = next((i for i, c in charts.items() if c["accno"] == settle_accno), None) if settle_accno else None
            cur.execute("SELECT chart_id, rate, validto FROM tax ORDER BY chart_id, validto NULLS LAST")
            rates: Dict[int, List[Tuple[Optional[date], Decimal]]] = {}
            for r in cur.fetchall():
                rates.setdefault(r["chart_id"], []).append((r["validto"], to_decimal(r["rate"] or 0)))
            tax_ids = list(rates)
            p = {"f": f, "t": t, "tax": tax_ids or [0], "settle": settle_id or 0}
            approved = "coalesce(ar.approved, ap.approved, gl.approved, true)"
            headers = """LEFT JOIN ar ON (ar.id = ac.trans_id) LEFT JOIN ap ON (ap.id = ac.trans_id)
                         LEFT JOIN gl ON (gl.id = ac.trans_id)"""
            not_settle = """NOT EXISTS (SELECT 1 FROM acc_trans s WHERE s.trans_id = ac.trans_id
                                        AND s.chart_id = %(settle)s)"""
            cur.execute(f"""SELECT ac.trans_id, ac.chart_id, sum(ac.amount::numeric) AS amount
                            FROM acc_trans ac {headers}
                            WHERE ac.chart_id = ANY(%(tax)s) AND ac.transdate BETWEEN %(f)s AND %(t)s
                              AND {approved} AND {not_settle}
                            GROUP BY ac.trans_id, ac.chart_id""", p)
            taxlines: Dict[int, Dict[int, Decimal]] = {}
            for r in cur.fetchall():
                taxlines.setdefault(r["trans_id"], {})[r["chart_id"]] = to_decimal(r["amount"])
            excluded = 0
            if settle_id:
                cur.execute(f"""SELECT count(DISTINCT ac.trans_id) AS n FROM acc_trans ac {headers}
                                WHERE ac.chart_id = ANY(%(tax)s) AND ac.transdate BETWEEN %(f)s AND %(t)s
                                  AND {approved} AND NOT {not_settle}""", p)
                excluded = cur.fetchone()["n"]
            # BA4: verkoop zonder btw-regel
            cur.execute("""SELECT a.id FROM ar a
                           WHERE a.transdate BETWEEN %(f)s AND %(t)s AND a.approved AND a.netamount <> 0
                             AND NOT EXISTS (SELECT 1 FROM acc_trans x WHERE x.trans_id = a.id
                                             AND x.chart_id = ANY(%(tax)s))
                             AND NOT EXISTS (SELECT 1 FROM acc_trans s WHERE s.trans_id = a.id
                                             AND s.chart_id = %(settle)s)""", p)
            untaxed_ids = [r["id"] for r in cur.fetchall()]
            if form.untaxed_purchases or form.expense_boxes:    # VK box 7, BE 81–83: ook inkoop zonder btw
                cur.execute("""SELECT a.id FROM ap a
                               WHERE a.transdate BETWEEN %(f)s AND %(t)s AND a.approved AND a.netamount <> 0
                                 AND NOT EXISTS (SELECT 1 FROM acc_trans x WHERE x.trans_id = a.id
                                                 AND x.chart_id = ANY(%(tax)s))
                                 AND NOT EXISTS (SELECT 1 FROM acc_trans s WHERE s.trans_id = a.id
                                                 AND s.chart_id = %(settle)s)""", p)
                untaxed_ids += [r["id"] for r in cur.fetchall()]
            ids = list(taxlines) + untaxed_ids
            heads = self._headers(cur, ids)
            invoice_ids = [i for i in ids if heads.get(i, {}).get("invoice")]
            lines = self._invoice_lines(cur, invoice_ids)
            gl_ids = [i for i in taxlines if heads.get(i, {}).get("kind") == "gl"]
            glnet: Dict[int, Decimal] = {}
            if gl_ids:
                cur.execute("""SELECT ac.trans_id, sum(ac.amount::numeric) AS amount FROM acc_trans ac
                               JOIN chart c ON (c.id = ac.chart_id)
                               WHERE ac.trans_id = ANY(%s) AND c.category IN ('I', 'E')
                                 AND NOT (ac.chart_id = ANY(%s))
                               GROUP BY ac.trans_id""", (gl_ids, tax_ids or [0]))
                glnet = {r["trans_id"]: to_decimal(r["amount"]) for r in cur.fetchall()}
            cur.execute("SELECT customer_id, box FROM x_vat_customer_box WHERE country = %s", (self.country,))
            cust_box = {r["customer_id"]: r["box"] for r in cur.fetchall()}
            expense_lines: Dict[int, Dict[int, Decimal]] = {}
            if form.expense_boxes:
                exp_ids = [i for i in ids if heads.get(i, {}).get("kind") in ("ap", "gl")]
                expense_lines = self._expense_lines(cur, exp_ids, tax_ids)
                acc_box = self.expense_boxes_saved()

        result: Dict[str, BoxAmount] = {b.code: BoxAmount() for b in form.boxes}
        acc_sum: Dict[int, Decimal] = {}
        unmapped: Dict[int, Decimal] = {}
        trans: List[TransVat] = []
        warnings: List[TransVat] = []
        n_customer = n_default = 0

        def rate_at(chart_id: int, d: date) -> Decimal:
            for validto, rate in rates.get(chart_id, []):
                if validto is None or validto >= d:
                    return rate
            return ZERO

        for tid in ids:
            h = heads.get(tid)
            if not h:
                continue
            tv = TransVat(tid, h["kind"], bool(h["invoice"]), h["number"] or "", h["transdate"], h["name"] or "")
            taxes = taxlines.get(tid, {})
            for cid, amt in taxes.items():
                box = boxes.get(cid)
                if box and box != "none":
                    acc_sum[cid] = acc_sum.get(cid, ZERO) + amt
                elif not box and amt:
                    unmapped[cid] = unmapped.get(cid, ZERO) + amt
            if h["kind"] == "gl":
                net = glnet.get(tid, ZERO)
                sales = any(target[boxes[c]].side == "out" for c in taxes if boxes.get(c) in target)
            else:
                net = to_decimal(h["netamount"]) * (1 if h["kind"] == "ar" else -1)
                sales = h["kind"] == "ar"
            bases, untaxed, warn = split_base(net, taxes, {c: rate_at(c, h["transdate"]) for c in taxes},
                                              lines.get(tid) if h["invoice"] else None)
            for cid, amt in taxes.items():
                box = boxes.get(cid)
                if box not in target:
                    continue
                add(tv, target[box], bases.get(cid, ZERO), amt)
            if untaxed and sales:
                if warn:
                    tv.note, tv.diff = "split", untaxed
                cbox = cust_box.get(h["vc_id"]) if h["kind"] == "ar" else None
                if cbox not in target:
                    cbox = None
                if cbox:
                    n_customer += 1
                    tv.note = tv.note or "customer"
                else:
                    n_default += 1
                    tv.note = tv.note or "default"
                add(tv, target[cbox or form.default_untaxed], untaxed, ZERO)
            elif untaxed and form.untaxed_purchases:
                if warn:
                    tv.note, tv.diff = "split", untaxed
                n_default += 1
                tv.note = tv.note or "default"
                add(tv, target[form.untaxed_purchases], untaxed, ZERO)
            elif warn:
                tv.note, tv.diff = "split", untaxed
            if form.expense_boxes:
                in_tax = sum((a for c, a in taxes.items() if boxes.get(c) in target
                              and target[boxes[c]].side == "in"), ZERO)
                if h["kind"] == "ap" or (h["kind"] == "gl" and in_tax):
                    lines_ = expense_lines.get(tid, {})
                    if h["kind"] == "gl":           # memoriaal: de kostenregels aan de kant van de aftrek
                        lines_ = {c: a for c, a in lines_.items() if (a < 0) == (in_tax < 0)}
                        total = sum(lines_.values(), ZERO)
                    else:
                        lines_ = {c: a for c, a in lines_.items() if (a < 0) == (net < 0)}
                        total = net
                    for ebox, share in split_expense(total, lines_, lambda c: acc_box.get(c) or form.expense_suggest(
                            charts.get(c) or {}), form.expense_default).items():
                        add(tv, target[ebox], share, ZERO)
            if form.adjust:
                form.adjust(tv.boxes)
            if tv.note == "split":
                warnings.append(tv)
            if tv.boxes:
                trans.append(tv)

        for tv in trans:
            for box, (base, tax) in tv.boxes.items():
                result[box].base_exact += base
                result[box].tax_exact += tax
        totals = form.rounding(result)
        trans.sort(key=lambda x: (x.transdate, x.number, x.trans_id))
        accounts = [AccountTotal(cid, charts[cid]["accno"], charts[cid]["description"] or "", boxes[cid], amt)
                    for cid, amt in sorted(acc_sum.items(), key=lambda kv: charts[kv[0]]["accno"])]
        rep = VatReport(period=period, boxes=result, totals=totals,
                        total_exact=sum((a.amount for a in accounts), ZERO), transactions=trans,
                        accounts=accounts,
                        unmapped=[{"accno": charts[c]["accno"], "description": charts[c]["description"], "amount": a}
                                  for c, a in sorted(unmapped.items(), key=lambda kv: charts[kv[0]]["accno"])],
                        settlement_excluded=excluded, customer_count=n_customer, default_count=n_default,
                        split_warnings=warnings, country=self.country)
        rep.stored = self.stored(period)
        if rep.stored:
            rep.difference = compare(rep.stored["snapshot"], rep, heads_of=lambda ids: self._describe(ids),
                                     payable=form.payable)
        return rep

    def _headers(self, cur, ids: List[int]) -> Dict[int, Dict[str, Any]]:
        if not ids:
            return {}
        cur.execute("""SELECT a.id, 'ar' AS kind, a.invoice, a.invnumber AS number, a.transdate, a.netamount,
                              n.name, a.customer_id AS vc_id
                       FROM ar a LEFT JOIN customer n ON (n.id = a.customer_id) WHERE a.id = ANY(%(ids)s)
                       UNION ALL
                       SELECT a.id, 'ap', a.invoice, a.invnumber, a.transdate, a.netamount, n.name, a.vendor_id
                       FROM ap a LEFT JOIN vendor n ON (n.id = a.vendor_id) WHERE a.id = ANY(%(ids)s)
                       UNION ALL
                       SELECT g.id, 'gl', false, g.reference, g.transdate, 0, g.description, NULL
                       FROM gl g WHERE g.id = ANY(%(ids)s)""", {"ids": ids})
        return {r["id"]: r for r in cur.fetchall()}

    def _describe(self, ids: List[int]) -> Dict[int, Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            return self._headers(cur, ids)

    def _expense_lines(self, cur, ids: List[int], tax_ids: List[int]) -> Dict[int, Dict[int, Decimal]]:
        """BL3: per inkoopboeking/memoriaal de regels op kosten- en activarekeningen (credit positief) op de
        boekingsdatum, zonder btw-, debiteuren-, crediteuren- en betaalrekeningen (betalingen tellen zo niet mee)."""
        if not ids:
            return {}
        cur.execute("""SELECT ac.trans_id, ac.chart_id, sum(ac.amount::numeric) AS amount
                       FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                       LEFT JOIN ap ON (ap.id = ac.trans_id) LEFT JOIN gl ON (gl.id = ac.trans_id)
                       WHERE ac.trans_id = ANY(%s) AND ac.transdate = coalesce(ap.transdate, gl.transdate)
                         AND NOT (ac.chart_id = ANY(%s)) AND c.category IN ('A', 'E')
                         AND NOT (string_to_array(coalesce(c.link, ''), ':')
                                  && ARRAY['AR', 'AP', 'AR_paid', 'AP_paid'])
                       GROUP BY ac.trans_id, ac.chart_id""", (ids, tax_ids or [0]))
        out: Dict[int, Dict[int, Decimal]] = {}
        for r in cur.fetchall():
            amt = to_decimal(r["amount"])
            if amt:
                out.setdefault(r["trans_id"], {})[r["chart_id"]] = amt
        return out

    # -------------------------------------------------------------- inkoop per kostenrekening (BL3, BE 81/82/83)

    def expense_boxes_saved(self) -> Dict[int, str]:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT chart_id, box FROM x_vat_account_box WHERE country = %s", (self.country,))
            return {r["chart_id"]: r["box"] for r in cur.fetchall()}

    def expense_accounts(self) -> List[Dict[str, Any]]:
        """Kosten- en activarekeningen voor de indeling van de inkoop (alleen als het formulier dat vraagt): alle
        kostenrekeningen, voorraad- en vaste-activarekeningen (klasse 2/3, koppeling IC) en rekeningen die al een
        keuze hebben; met de ingestelde rubriek en het voorstel."""
        form = self.form
        if not form.expense_boxes:
            return []
        saved = self.expense_boxes_saved()
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.id, c.accno, c.description, c.category, c.link, coalesce(c.closed, false) AS closed,
                                  coalesce(c.contra, false) AS contra
                           FROM chart c WHERE c.charttype = 'A' AND c.id NOT IN (SELECT chart_id FROM tax)
                           ORDER BY c.accno""")
            rows = cur.fetchall()
        out = []
        for r in rows:
            links = (r["link"] or "").split(":")
            if r["id"] not in saved and (r["contra"] or r["closed"] or any(k in links for k in (
                    "AR", "AP", "AR_paid", "AP_paid"))):
                continue
            if r["id"] in saved or r["category"] == "E" or (r["category"] == "A" and (
                    r["accno"][:1] in ("2", "3") or "IC" in links)):
                r["box"] = saved.get(r["id"], "")
                r["suggested"] = form.expense_suggest(r)
                out.append(r)
        return out

    def save_expense_boxes(self, boxes: Mapping[int, str], login: Optional[str] = None) -> List[str]:
        form = self.form
        known = {a["id"]: a for a in self.expense_accounts()}
        for chart_id, box in boxes.items():
            if int(chart_id) not in known:
                raise ValidationError("Onbekende rekening.")
            if box not in form.expense_boxes:
                raise ValidationError("Onbekende rubriek.")
        changed = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for chart_id, box in boxes.items():
                if known[int(chart_id)]["box"] != box:
                    cur.execute("""INSERT INTO x_vat_account_box (chart_id, box, country, login) VALUES (%s, %s, %s, %s)
                                   ON CONFLICT (chart_id, country) DO UPDATE SET box = excluded.box,
                                       login = excluded.login, updated = now()""",
                                (int(chart_id), box, self.country, login))
                    changed.append(known[int(chart_id)]["accno"])
        return changed

    def _invoice_lines(self, cur, ids: List[int]) -> Dict[int, List[Tuple[Decimal, frozenset]]]:
        """Per factuur: (regelbedrag credit positief, btw-rekeningen van het artikel)."""
        if not ids:
            return {}
        cur.execute("""SELECT i.trans_id, i.qty, i.sellprice,
                              array(SELECT pt.chart_id FROM partstax pt WHERE pt.parts_id = i.parts_id) AS taxes
                       FROM invoice i WHERE i.trans_id = ANY(%s) AND NOT coalesce(i.assemblyitem, false)""", (ids,))
        out: Dict[int, List[Tuple[Decimal, frozenset]]] = {}
        for r in cur.fetchall():
            amount = to_decimal(r["qty"] or 0) * to_decimal(r["sellprice"] or 0)
            out.setdefault(r["trans_id"], []).append((amount, frozenset(r["taxes"] or ())))
        return out

    # -------------------------------------------------------------- btw-boeking (BA6)

    def proposal(self, rep: VatReport) -> Dict[str, Any]:
        """Memoriaalregels voor de btw-boeking: elke btw-rekening tegen, het te betalen bedrag (NL 5G, VK box 5) op de
        afdrachtrekening, het afrondingsverschil op de rekening afrondingsverschillen. Bedragen credit positief."""
        settle, rounding = self.accounts_setting()
        if not settle:
            raise ValidationError("Stel eerst de afdrachtrekening in (Btw-rubrieken).")
        payable = self.form.payable
        total = rep.totals[payable]
        diff = rep.total_exact - total
        if diff and not rounding:
            raise ValidationError("Stel eerst de rekening afrondingsverschillen in (Btw-rubrieken).")
        lines = [{"accno": a.accno, "amount": -a.amount, "memo": a.box} for a in rep.accounts if a.amount]
        if not lines:
            raise ValidationError("Er is niets te boeken in dit tijdvak.")
        if total:
            lines.append({"accno": settle, "amount": total, "memo": payable})
        if diff:
            lines.append({"accno": rounding, "amount": diff, "memo": ""})
        return {"transdate": rep.period.todate, "lines": lines}

    def register(self, rep: VatReport, gl_id: int, login: Optional[str] = None) -> None:
        """Na het opslaan van de btw-boeking: tijdvak vastleggen met de bedragen (BA6/BA7)."""
        snap = snapshot(rep)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_vat_return (country, fromdate, todate, gl_id, snapshot, login)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (country, fromdate, todate) DO UPDATE SET gl_id = excluded.gl_id,
                               snapshot = excluded.snapshot, login = excluded.login, created = now()""",
                        (self.country, rep.period.fromdate, rep.period.todate, gl_id, json.dumps(snap), login))
            cur.execute("""INSERT INTO x_task_done (kind, period_end, login) VALUES ('vat', %s, %s)
                           ON CONFLICT DO NOTHING""", (rep.period.todate, login))

    def stored(self, period: Period) -> Optional[Dict[str, Any]]:
        """De vastgelegde aangifte van dit tijdvak; ``gl_exists`` False als de boeking verwijderd is."""
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT r.*, g.reference, (g.id IS NOT NULL) AS gl_exists FROM x_vat_return r
                           LEFT JOIN gl g ON (g.id = r.gl_id)
                           WHERE r.country = %s AND r.fromdate = %s AND r.todate = %s""",
                        (self.country, period.fromdate, period.todate))
            return cur.fetchone()

    def returns(self) -> List[Dict[str, Any]]:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT r.fromdate, r.todate, r.gl_id, r.login, r.created, g.reference
                           FROM x_vat_return r LEFT JOIN gl g ON (g.id = r.gl_id) WHERE r.country = %s
                           ORDER BY r.todate DESC""", (self.country,))
            return cur.fetchall()


# ---------------------------------------------------------------------------- rekenregels

def add(tv: TransVat, tg: Target, base: Decimal, tax: Decimal) -> None:
    """Bijdrage van één btw-rekening (of omzet zonder btw) aan de vakken van de koppeling (credit positief in,
    op het formulier positief aan de kant van de koppeling)."""
    sign = 1 if tg.side == "out" else -1
    if tg.tax and tg.tax in tg.base:
        tv.add(tg.tax, sign * base, sign * tax)
        rest = [b for b in tg.base if b != tg.tax]
    else:
        if tg.tax:
            tv.add(tg.tax, ZERO, sign * tax)
        rest = list(tg.base)
    for b in rest:
        tv.add(b, sign * base, ZERO)


def split_expense(total: Decimal, lines: Mapping[int, Decimal], box_of, default: str) -> Dict[str, Decimal]:
    """BL3: ``total`` (credit positief) verdelen naar rato van de regels per kosten-/activarekening over hun rubriek
    (81/82/83); afronding op centen, het restje naar het grootste deel; zonder regels alles naar ``default``."""
    if not total:
        return {}
    weights: Dict[str, Decimal] = {}
    for chart_id, amount in lines.items():
        b = box_of(chart_id)
        weights[b] = weights.get(b, ZERO) + abs(amount)
    w = sum(weights.values(), ZERO)
    if not w:
        return {default: total}
    out = {b: round_amount(total * v / w, 2) for b, v in weights.items()}
    rest = total - sum(out.values(), ZERO)
    if rest:
        top = max(out, key=lambda b: (abs(out[b]), b))
        out[top] += rest
    return {b: v for b, v in out.items() if v}


def split_base(net: Decimal, taxes: Mapping[int, Decimal], rates: Mapping[int, Decimal],
               lines: Optional[List[Tuple[Decimal, frozenset]]] = None
               ) -> Tuple[Dict[int, Decimal], Decimal, bool]:
    """Grondslag per btw-rekening van één boeking (credit positief).

    Geeft (grondslag per rekening, deel zonder btw, melding). ``lines``: factuurregels (bedrag, btw-rekeningen
    van het artikel) bij een factuur met artikelen.

    Verlegde btw (paar −21 % op 2A/4A/4B en +21 % op 5B): elke helft heeft het hele nettobedrag als grondslag.
    De rekeningen met een negatief tarief worden daarom apart verdeeld over het nettobedrag met omgekeerd teken
    (hun btw staat ook aan de andere kant, Perl IR/AA ``rate * ml > 0``): bij een inkoop is de grondslag van
    4B dan positief, net als bij verkoop. Het deel zonder btw komt uit de positieve rekeningen."""
    neg = [c for c in taxes if rates.get(c, ZERO) < 0]
    if neg:
        pos = {c: a for c, a in taxes.items() if c not in neg}
        nb, nu, nw = _split_base(-net, {c: taxes[c] for c in neg}, {c: -rates[c] for c in neg},
                                 [(-a, ch) for a, ch in lines] if lines is not None else None)
        bases = dict(nb)
        if not pos:
            return bases, ZERO, nw
        pb, pu, pw = _split_base(net, pos, rates, lines)
        bases.update(pb)
        return bases, pu, nw or pw
    return _split_base(net, taxes, rates, lines)


def _split_base(net: Decimal, taxes: Mapping[int, Decimal], rates: Mapping[int, Decimal],
                lines: Optional[List[Tuple[Decimal, frozenset]]] = None
                ) -> Tuple[Dict[int, Decimal], Decimal, bool]:
    accs = list(taxes)
    if not accs:
        return {}, net, False
    if lines is not None:
        bases = {c: ZERO for c in accs}
        untaxed = ZERO
        total = ZERO
        for amount, charts in lines:
            total += amount
            hit = [c for c in accs if c in charts]
            for c in hit:
                bases[c] += amount
            if not hit:
                untaxed += amount
        bases = {c: round_amount(v, 2) for c, v in bases.items()}
        untaxed = round_amount(untaxed, 2)
        diff = net - round_amount(total, 2)
        if diff:
            target = max(accs, key=lambda c: (abs(bases[c]), rates.get(c, ZERO)))
            if bases[target]:
                bases[target] += diff
            else:
                untaxed += diff
        return bases, untaxed, abs(diff) > SPLIT_TOLERANCE
    if len(accs) == 1:
        return {accs[0]: net}, ZERO, False
    bases: Dict[int, Decimal] = {}
    zero_rate = [c for c in accs if not rates.get(c)]
    for c in accs:
        if rates.get(c):
            bases[c] = round_amount(taxes[c] / rates[c], 2)
    rest = net - sum(bases.values(), ZERO)
    if zero_rate:
        bases[zero_rate[0]] = rest
        for c in zero_rate[1:]:
            bases[c] = ZERO
        return bases, ZERO, False
    if abs(rest) <= SPLIT_TOLERANCE:
        top = max(accs, key=lambda c: rates.get(c, ZERO))
        bases[top] += rest
        return bases, ZERO, False
    return bases, rest, True


def snapshot(rep: VatReport) -> Dict[str, Any]:
    def s(v: Decimal) -> str:
        return str(v)
    return {
        "boxes": {c: [s(b.base_exact), s(b.tax_exact), s(b.base), s(b.tax)] for c, b in rep.boxes.items()},
        "totals": {k: s(v) for k, v in rep.totals.items()},
        "total_exact": s(rep.total_exact),
        "trans": {str(t.trans_id): {b: [s(v[0]), s(v[1])] for b, v in t.boxes.items()} for t in rep.transactions},
    }


def compare(snap: Mapping[str, Any], rep: VatReport, heads_of=None, payable: str = "5G") -> Optional[Difference]:
    """BA7: vastgelegde bedragen tegen die van nu; None als alles gelijk is."""
    D = to_decimal
    boxes = {}
    for code, b in rep.boxes.items():
        old = snap.get("boxes", {}).get(code, ["0", "0", "0", "0"])
        if D(old[2]) != b.base or D(old[3]) != b.tax:
            boxes[code] = (D(old[2]), b.base, D(old[3]), b.tax)
    old_total = D(snap.get("totals", {}).get(payable, "0"))
    oldtrans = snap.get("trans", {})
    now = {str(t.trans_id): t for t in rep.transactions}
    changed: List[Dict[str, Any]] = []
    for tid in sorted(set(oldtrans) | set(now), key=int):
        old = {b: (D(v[0]), D(v[1])) for b, v in oldtrans.get(tid, {}).items()}
        cur = {b: (v[0], v[1]) for b, v in now[tid].boxes.items()} if tid in now else {}
        if old != cur:
            tax_old = sum((v[1] for v in old.values()), ZERO)
            tax_now = sum((v[1] for v in cur.values()), ZERO)
            base_old = sum((v[0] for v in old.values()), ZERO)
            base_now = sum((v[0] for v in cur.values()), ZERO)
            changed.append({"trans_id": int(tid), "status": "new" if not old else "gone" if not cur else "changed",
                            "base": base_now - base_old, "tax": tax_now - tax_old})
    if not boxes and old_total == rep.totals[payable] and not changed:
        return None
    if heads_of and changed:
        heads = heads_of([c["trans_id"] for c in changed])
        for c in changed:
            h = heads.get(c["trans_id"]) or {}
            c.update(kind=h.get("kind"), number=h.get("number") or "", transdate=h.get("transdate"),
                     name=h.get("name") or "", invoice=bool(h.get("invoice")))
    return Difference(boxes=boxes, total=(old_total, rep.totals[payable]), transactions=changed)
