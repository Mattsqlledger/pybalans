"""Dashboard: kerncijfers en te-doenlijst voor het startscherm (fase 4 stap 5b, DB1–DB13).

Elk getal komt uit dezelfde rekenregels als het rapport waar het dashboard naar doorklikt (DB7):

* **Bank en kas** (DB13): betaalrekeningen (koppeling ``AR_paid``/``AP_paid``) uit de balans-activa, saldo
  zoals de balans per vandaag (``Reports.balance_sheet``).
* **Te ontvangen / te betalen**: openstaand per peildatum zoals de ouderdomsanalyse (``open_asof``,
  goedgekeurde boekingen met factuurdatum t/m vandaag), in de standaardvaluta; ouderdom vanaf de vervaldatum.
  Eén query in plaats van ``Reports.aging`` (die per relatie en valuta zoekt: administratie A 5 s); de toetsen bewijzen per
  factuur dezelfde bedragen als de ouderdomsanalyse.
* **Resultaat**: ``Reports.income_statement`` met maandkolommen en vorig jaar (jaarafsluiting telt niet mee).
* **Te doen** (DB4/DB5): btw-aangifte van het laatst afgesloten tijdvak, bankregels, vervallen facturen,
  terugkerende boekingen, artikelen onder bestelniveau, orders met verstreken leverdatum.

Het resultaat gaat over het boekjaar (``defaults.yearend``, JA6; leeg = kalenderjaar). Heeft het lopende
boekjaar nog geen boekingen, dan gaat het resultaat over het laatste boekjaar met boekingen (DB9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from . import _db
from .amounts import round_amount, to_decimal
from .config import UserConfig
from .dates import fiscal_dates, fiscal_label, fiscal_start_year, last_day_of_month
from .errors import ValidationError
from .extensions import ensure_schema
from .reports import Reports
from .reports_arap import open_asof

ZERO = Decimal(0)

#: ouderdomsklassen op het dashboard: dagen na de vervaldatum
BUCKETS = ("current", "d30", "d60", "older")
#: aangiftetijdvak btw (DB5); standaard kwartaal
VAT_PERIODS = ("quarter", "month", "year", "none")
VAT_SETTING = "vat_return_period"
#: binnen zoveel dagen vervallende inkoopfacturen tellen mee in de te-doenlijst (DB4)
SOON_DAYS = 7
#: startlijst "Aan de slag" (eerste start ES8, keuze 12): punt -> module die aan moet staan (None = altijd)
START_ITEMS = {"company": None, "style": None, "vat": ("ar", "ap"), "bank": ("cash",),
               "opening": ("gl",), "invoice": ("ar",)}
#: vlag in ``x_setting``: deze administratie toont de startlijst (ES10 A: alleen in de app aangemaakt of via de wizard
#: gekoppeld, niet bij elke bestaande SL-database)
START_SETTING = "start_list"
#: vaste datum voor de startlijst in ``x_task_done`` (de sleutel heeft een datum; de startlijst is per administratie)
START_DAY = date(2000, 1, 1)
#: overstaplijst "Overstap uit SQL-Ledger" (6d-2, I5): punt -> module die aan moet staan (None = altijd). "restored"
#: is altijd klaar; "check" en "numbering" vinkt de beheerder zelf af ("gedaan"); "users", "backup" en "rgs" komen
#: uit de app-database / de datamap (``external`` bij ``switch_list``; None = punt niet tonen)
SWITCH_ITEMS = {"restored": None, "rma": None, "check": None, "company": None, "vat": ("ar", "ap"), "bank": ("cash",),
                "style": None, "numbering": None, "recurring": ("recurring",), "users": None, "backup": None,
                "closedto": None, "rgs": None}
SWITCH_MANUAL = ("rma", "check", "numbering")
#: een betaalrekening zonder saldo en zonder mutaties in zoveel dagen wordt verborgen (DB13)
QUIET_DAYS = 365


@dataclass
class StartItem:
    key: str
    done: bool                          # automatisch gecontroleerd
    skipped: bool                       # op "niet nodig" gezet
    target: Optional[int] = None        # bank: de eerste betaalrekening zonder IBAN (chart.id)
    unavailable: bool = False           # vat: geen aangifteformulier voor dit land (VP3), daarom "niet nodig"


@dataclass
class SwitchStep:
    """Deelstap van een punt van de overstaplijst (6d-2): wat er nog ontbreekt, zonder tekst (die maakt de web-laag).
    ``rows``: de rekeningen of afschriften waar het om gaat (id, accno, description; bij afschriften ook
    ``statement_id`` en ``warning``); ``detail``: overige gegevens (bv. afsluitdatums)."""
    key: str
    done: bool
    rows: List[Dict[str, Any]] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)


def _numdate(value: Optional[int]) -> Optional[date]:
    """Afsluitdatum als getal jjjjmmdd (``gl._closedto``) naar een datum; None als het geen geldige datum is."""
    if not value:
        return None
    try:
        return date(value // 10000, value // 100 % 100, value % 100)
    except ValueError:
        return None


@dataclass
class BankAccount:
    accno: str
    description: str
    balance: Decimal
    statement_to: Optional[date]        # einddatum laatst ingelezen afschrift
    open_lines: int                     # bankregels met status open
    active: bool                        # saldo of mutaties in de laatste QUIET_DAYS dagen


@dataclass
class OpenItems:
    """Openstaande verkoop- of inkoopfacturen per vandaag (standaardvaluta)."""
    arap: str
    total: Decimal = ZERO
    count: int = 0
    buckets: Dict[str, Decimal] = field(default_factory=lambda: {b: ZERO for b in BUCKETS})
    overdue_count: int = 0
    overdue: Decimal = ZERO
    soon_count: int = 0                 # vervalt vandaag t/m over SOON_DAYS dagen
    soon: Decimal = ZERO
    rows: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Figures:
    income: Decimal = ZERO
    expense: Decimal = ZERO

    @property
    def result(self) -> Decimal:
        return self.income - self.expense


@dataclass
class Results:
    year: int                           # beginjaar van het boekjaar (JA4)
    fromdate: date
    todate: date                        # t/m (vandaag, of het einde van het laatste boekjaar met boekingen)
    fallback: bool                      # DB9: lopend boekjaar zonder boekingen
    month: int                          # maand van "deze maand"
    month_from: date
    month_to: date
    ytd: Figures = field(default_factory=Figures)
    ytd_prev: Figures = field(default_factory=Figures)
    mtd: Figures = field(default_factory=Figures)
    mtd_prev: Figures = field(default_factory=Figures)
    #: resultaat per maand van het boekjaar, in boekjaarvolgorde: (dit jaar of None na todate, vorig jaar)
    months: List[Tuple[Optional[Decimal], Decimal]] = field(default_factory=list)
    yearend: int = 12                   # laatste maand van het boekjaar

    @property
    def label(self) -> str:
        """Naam van het boekjaar: ``2026`` of ``2025/2026``."""
        return fiscal_label(self.year, self.yearend)

    @property
    def prev_label(self) -> str:
        return fiscal_label(self.year - 1, self.yearend)

    @property
    def month_starts(self) -> List[date]:
        """Eerste dag van elke maand van het boekjaar, in volgorde (bij ``months``)."""
        out, d = [], self.fromdate
        for _ in range(12):
            out.append(d)
            d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        return out


@dataclass
class VatReturn:
    period: str                         # quarter | month | year
    fromdate: date
    todate: date
    deadline: date
    number: int                         # kwartaal- of maandnummer (jaar: 0)


@dataclass
class Todo:
    kind: str                           # vat | bank | ar_overdue | ap_due | recurring | parts_short | orders_late
    count: int = 0
    amount: Optional[Decimal] = None
    detail: Dict[str, Any] = field(default_factory=dict)


class Dashboard:
    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------------ bank en kas

    def bank(self, today: Optional[date] = None) -> List[BankAccount]:
        """Betaalrekeningen in de balans-activa met hun saldo per ``today`` (= balans per die datum)."""
        today = today or date.today()
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.id, c.accno, c.description FROM chart c
                           WHERE c.charttype = 'A' AND c.category = 'A' AND NOT coalesce(c.closed, false)
                             AND (c.link LIKE '%%AR_paid%%' OR c.link LIKE '%%AP_paid%%')
                           ORDER BY c.accno""")
            accounts = cur.fetchall()
            if not accounts:
                return []
            ids = [a["id"] for a in accounts]
            cur.execute("""SELECT chart_id, max(transdate) AS last FROM acc_trans
                           WHERE chart_id = ANY(%s) AND transdate <= %s GROUP BY chart_id""", (ids, today))
            last = {r["chart_id"]: r["last"] for r in cur.fetchall()}
            cur.execute("""SELECT chart_id, max(enddate) AS enddate FROM x_bank_statement
                           WHERE chart_id = ANY(%s) GROUP BY chart_id""", (ids,))
            stmt = {r["chart_id"]: r["enddate"] for r in cur.fetchall()}
            cur.execute("""SELECT chart_id, count(*) AS n FROM x_bank_line
                           WHERE chart_id = ANY(%s) AND status = 'open' GROUP BY chart_id""", (ids,))
            lines = {r["chart_id"]: r["n"] for r in cur.fetchall()}
        bs = Reports(self.conn, self.config).balance_sheet(todate=today, l_heading=False, l_subtotal=False)
        amounts = bs.accounts.get("A", {})
        out = []
        for a in accounts:
            bal = to_decimal((amounts.get(a["accno"]) or {}).get("amounts", {}).get(("this", 0)) or 0)
            recent = last.get(a["id"])
            active = bool(bal) or bool(lines.get(a["id"])) or (
                recent is not None and recent > today - timedelta(days=QUIET_DAYS))
            out.append(BankAccount(a["accno"], a["description"] or "", bal, stmt.get(a["id"]),
                                   int(lines.get(a["id"], 0)), active))
        return out

    # ------------------------------------------------------------------ openstaande posten

    def open_items(self, arap: str, today: Optional[date] = None, rows: bool = False) -> OpenItems:
        """Openstaand per ``today`` zoals de ouderdomsanalyse (``open_asof``), per ouderdom vanaf de vervaldatum:
        ``current`` = niet vervallen (vervaldatum vandaag of later), ``d30`` 1–30, ``d60`` 31–60, ``older`` > 60
        dagen. ``rows``: ook de boekingen zelf (voor de toetsen)."""
        if arap not in ("ar", "ap"):
            raise ValueError("arap moet 'ar' of 'ap' zijn")
        today = today or date.today()
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            # open_asof != 0 kan alleen als amount != paid of als er na de peildatum nog iets op de boeking
            # staat; eerst daarop selecteren scheelt de subquery voor alle betaalde facturen (administratie A: 6 s → 0,1 s)
            cur.execute(f"""SELECT a.id, a.invnumber, a.transdate, coalesce(a.duedate, a.transdate) AS duedate,
                                   a.curr, {open_asof(arap)} AS open
                            FROM {arap} a
                            WHERE a.approved = '1' AND a.transdate <= %(todate)s
                              AND (a.amount::numeric != a.paid::numeric
                                   OR EXISTS (SELECT 1 FROM acc_trans x WHERE x.trans_id = a.id
                                              AND x.transdate > %(todate)s::date))""", {"todate": today})
            found = [r for r in cur.fetchall() if to_decimal(r["open"]) != 0]
        out = OpenItems(arap)
        for r in found:
            amount = round_amount(to_decimal(r["open"]), precision)
            days = (today - r["duedate"]).days
            bucket = "current" if days <= 0 else "d30" if days <= 30 else "d60" if days <= 60 else "older"
            out.buckets[bucket] += amount
            out.total += amount
            out.count += 1
            if days > 0:
                out.overdue += amount
                out.overdue_count += 1
            elif days >= -SOON_DAYS:
                out.soon += amount
                out.soon_count += 1
            if rows:
                out.rows.append(dict(r, open=amount, bucket=bucket))
        return out

    # ------------------------------------------------------------------ resultaat

    def results(self, today: Optional[date] = None) -> Optional[Results]:
        """Omzet, kosten en resultaat dit boekjaar t/m vandaag en deze maand, met dezelfde periode een jaar eerder,
        en het resultaat per maand (vorig boekjaar heel). Geen enkele boeking in de W&V: ``None``."""
        today = today or date.today()
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT max(ac.transdate) AS last FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                           WHERE c.category IN ('I', 'E') AND ac.approved = '1' AND ac.transdate <= %s""", (today,))
            last = cur.fetchone()["last"]
            ye = _db.get_yearend(cur)
        if last is None:
            return None
        fallback = fiscal_start_year(last, ye) < fiscal_start_year(today, ye)
        year = fiscal_start_year(last if fallback else today, ye)
        fromdate, yearto = fiscal_dates(year, ye)
        todate = yearto if fallback else today
        month = last.month if fallback else today.month
        month_from = (last if fallback else today).replace(day=1)
        month_to = last_day_of_month(month_from) if fallback else today
        rep = Reports(self.conn, self.config)
        st = rep.income_statement(fromdate=fromdate, todate=todate, includeperiod="month",
                                  previousyear=True, l_heading=False, l_subtotal=False)
        pfrom, pto = fiscal_dates(year - 1, ye)
        prev = rep.income_statement(fromdate=pfrom, todate=pto,
                                    includeperiod="month", l_heading=False, l_subtotal=False)

        def fig(s, col) -> Figures:
            return Figures(to_decimal(s.totals.get("I", {}).get(col) or 0),
                           to_decimal(s.totals.get("E", {}).get(col) or 0))

        def idx(s, m: int) -> Optional[int]:
            for i, p in s.periods.items():
                if i and p.this[0] and p.this[0].month == m:
                    return i
            return None

        out = Results(year, fromdate, todate, fallback, month, month_from, month_to,
                      ytd=fig(st, ("this", 0)), ytd_prev=fig(st, ("previous", 0)), yearend=ye)
        mi = idx(st, month)
        if mi is not None:
            out.mtd, out.mtd_prev = fig(st, ("this", mi)), fig(st, ("previous", mi))
        elif month_from == fromdate or not st.periods.get(1):
            # alleen de eerste maand van het boekjaar: de W&V maakt dan geen maandkolom, de periode zelf is de maand
            out.mtd, out.mtd_prev = out.ytd, out.ytd_prev
        for start in out.month_starts:
            m = start.month
            i, pi = idx(st, m), idx(prev, m)
            this = None
            if start <= todate:
                this = fig(st, ("this", i)).result if i is not None else \
                    (out.ytd.result if start == fromdate else ZERO)
            before = fig(prev, ("this", pi)).result if pi is not None else ZERO
            out.months.append((this, before))
        return out

    # ------------------------------------------------------------------ btw-aangifte (DB5)

    def vat_period(self) -> str:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT value FROM x_setting WHERE key = %s", (VAT_SETTING,))
            r = cur.fetchone()
        v = r["value"] if r else ""
        return v if v in VAT_PERIODS else "quarter"

    def save_vat_period(self, value: str, login: Optional[str] = None) -> bool:
        """Aangiftetijdvak opslaan; geeft True als het gewijzigd is."""
        if value not in VAT_PERIODS:
            raise ValidationError("Onbekend aangiftetijdvak.")
        current = self.vat_period()
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM x_setting WHERE key = %s", (VAT_SETTING,))
            stored = cur.fetchone() is not None
        if value == current and stored:
            return False
        # ook de standaard (kwartaal) vastleggen: dan is het tijdvak gekozen (startlijst, eerste start keuze 12)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, %s, %s)
                           ON CONFLICT (key) DO UPDATE SET value = excluded.value, login = excluded.login,
                                                           updated = now()""", (VAT_SETTING, value, login))
        return value != current

    def _vat_country(self) -> str:
        from .vatreturn import VatReturns
        return VatReturns(self.conn).country

    def vat_return(self, today: Optional[date] = None) -> Optional[VatReturn]:
        """Het laatst afgesloten aangiftetijdvak als dat nog niet is afgevinkt en er boekingen in staan.
        Termijn: einde van de maand na het tijdvak (jaar: 31 maart). Het is een herinnering: na de termijn vervalt
        hij (None) en volgt het volgende tijdvak zodra dat is afgesloten."""
        today = today or date.today()
        period = self.vat_period()
        if period == "none":
            return None
        from .vatreturn import VatReturns, last_closed      # hier: vatreturn gebruikt dashboard niet

        vrs = VatReturns(self.conn)
        p = last_closed(period, today, vrs.quarter_offset() if period == "quarter" else 0)
        start, end, number = p.fromdate, p.todate, p.number
        if period == "year":
            deadline = date(end.year + 1, 3, 31)
        else:                       # NL: einde van de maand na het tijdvak; VK: 1 maand en 7 dagen (BL7); BE: 20e/25e
            deadline = vrs.form.deadline(end, period)
        if today > deadline:
            return None
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM x_task_done WHERE kind = 'vat' AND period_end = %s", (end,))
            if cur.fetchone():
                return None
            cur.execute("SELECT 1 FROM acc_trans WHERE transdate BETWEEN %s AND %s LIMIT 1", (start, end))
            if not cur.fetchone():
                return None
        return VatReturn(period, start, end, deadline, number)

    def vat_done(self, period_end: date, login: Optional[str] = None) -> bool:
        """Tijdvak afvinken (Afgehandeld); False als het al afgevinkt was."""
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_task_done (kind, period_end, login) VALUES ('vat', %s, %s)
                           ON CONFLICT DO NOTHING""", (period_end, login))
            return cur.rowcount == 1

    # ------------------------------------------------------------------ startlijst (eerste start, keuze 12)

    def start_list(self, modules: List[str]) -> Optional[List[StartItem]]:
        """Punten van "Aan de slag" met hun stand; None als de lijst verborgen is of alles klaar/niet nodig is.
        "Logo en huisstijl" (HS10 A) is klaar zodra de huisstijl één keer is opgeslagen (``style.saved``); een
        eerder op "niet nodig" gezet punt "logo" (van vóór de huisstijl) telt ook als niet nodig."""
        ensure_schema(self.conn)
        mods = set(modules)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT kind FROM x_task_done WHERE kind LIKE 'start\\_%%' AND period_end = %s",
                        (START_DAY,))
            marked = {r["kind"][len("start_"):] for r in cur.fetchall()}
            if "logo" in marked:
                marked.add("style")
            if "hidden" in marked:
                return None
            cur.execute("SELECT 1 FROM x_setting WHERE key = %s AND value = '1'", (START_SETTING,))
            if not cur.fetchone():
                return None
            d = _db.get_defaults(cur, "company", "address")
            done = {"company": bool((d.get("company") or "").strip() and (d.get("address") or "").strip())}
            cur.execute("SELECT EXISTS (SELECT 1 FROM x_doc_setting WHERE key = 'style.saved') AS x")
            done["style"] = cur.fetchone()["x"]
            cur.execute("SELECT EXISTS (SELECT 1 FROM x_setting WHERE key = %s) AS x", (VAT_SETTING,))
            done["vat"] = cur.fetchone()["x"]
            cur.execute("""SELECT EXISTS (SELECT 1 FROM chart c JOIN bank b ON (b.id = c.id)
                           WHERE (c.link LIKE '%%AR_paid%%' OR c.link LIKE '%%AP_paid%%')
                             AND coalesce(b.iban, '') <> '') AS x""")
            done["bank"] = cur.fetchone()["x"]
            cur.execute("""SELECT c.id FROM chart c LEFT JOIN bank b ON (b.id = c.id)
                           WHERE c.charttype = 'A' AND NOT coalesce(c.closed, false)
                             AND (c.link LIKE '%%AR_paid%%' OR c.link LIKE '%%AP_paid%%')
                             AND coalesce(b.iban, '') = ''
                           -- een bank vóór de kas: naam met bank/banque of een IBAN in de omschrijving
                           ORDER BY (c.description ~* 'ban[kq]|[A-Z]{2}[0-9]{2}[A-Z]{4}[0-9]') DESC, c.accno
                           LIMIT 1""")
            first = cur.fetchone()
            target = {"bank": first["id"] if first else None}
            cur.execute("SELECT EXISTS (SELECT 1 FROM gl) AS x")
            done["opening"] = cur.fetchone()["x"]
            cur.execute("SELECT EXISTS (SELECT 1 FROM ar) AS x")
            done["invoice"] = cur.fetchone()["x"]
        items = [StartItem(k, done[k], k in marked, target.get(k)) for k, need in START_ITEMS.items()
                 if need is None or mods & set(need)]
        if all(i.done or i.skipped for i in items):
            return None
        return items

    def enable_start_list(self, login: Optional[str] = None) -> None:
        """Startlijst aanzetten voor deze administratie (nieuwe administratie, koppelen via de wizard)."""
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_setting (key, value, login) VALUES (%s, '1', %s)
                           ON CONFLICT (key) DO NOTHING""", (START_SETTING, login))

    def start_mark(self, key: str, login: Optional[str] = None) -> None:
        """Punt op "niet nodig" zetten, of met ``key`` = "hidden" de hele lijst verbergen."""
        if key not in START_ITEMS and key != "hidden":
            raise ValidationError("Onbekend punt.")
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_task_done (kind, period_end, login) VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""", (f"start_{key}", START_DAY, login))

    # ------------------------------------------------------------------ overstaplijst (6d-2, I4/I5)

    def switch_list(self, modules: List[str], external: Optional[Dict[str, Optional[bool]]] = None
                    ) -> Optional[List[StartItem]]:
        """Punten van "Overstap uit SQL-Ledger" met hun stand; None als de administratie niet uit een back-up
        komt (``switchover.restore_info``), de lijst verborgen is of alles klaar/niet nodig is. ``external``:
        stand van "users", "backup" en "rgs" (None = niet tonen). Een punt dat de beheerder zelf afvinkt staat in
        ``x_task_done`` als ``switchdone_<punt>``; "niet nodig" als ``switch_<punt>``."""
        from .gl import _closedto
        from .switchover import restore_info
        info = restore_info(self.conn)
        if info is None:
            return None
        external = external or {}
        mods = set(modules)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT kind FROM x_task_done WHERE kind LIKE 'switch%%' AND period_end = %s", (START_DAY,))
            kinds = {r["kind"] for r in cur.fetchall()}
            if "switch_hidden" in kinds:
                return None
            skipped = {k[len("switch_"):] for k in kinds if k.startswith("switch_")}
            marked = {k[len("switchdone_"):] for k in kinds if k.startswith("switchdone_")}
            d = _db.get_defaults(cur, "company", "address", "closedto")
            done: Dict[str, bool] = {"restored": True,
                                     "company": bool((d.get("company") or "").strip() and (d.get("address") or "").strip())}
            cur.execute("SELECT EXISTS (SELECT 1 FROM x_doc_setting WHERE key = 'style.saved') AS x")
            done["style"] = cur.fetchone()["x"]
            cur.execute("SELECT value FROM x_setting WHERE key = %s", (VAT_SETTING,))
            r = cur.fetchone()
            vat_unavailable = False
            if r is None:
                done["vat"] = False
                from .vatreturn import VatReturns
                vat_unavailable = VatReturns(self.conn).unavailable() is not None
            elif r["value"] == "none":
                done["vat"] = True
            else:
                cur.execute("""SELECT NOT EXISTS (SELECT 1 FROM chart c LEFT JOIN x_vat_box b
                                                  ON (b.chart_id = c.id AND b.country = %(country)s)
                                                  WHERE c.id IN (SELECT chart_id FROM tax)
                                                    AND NOT coalesce(c.closed, false) AND b.box IS NULL)
                                      AND EXISTS (SELECT 1 FROM x_setting WHERE key = 'vat_settlement_accno'
                                                  AND value <> '') AS x""", {"country": self._vat_country()})
                done["vat"] = cur.fetchone()["x"]
            cur.execute("""SELECT EXISTS (SELECT 1 FROM chart c JOIN bank b ON (b.id = c.id)
                           WHERE (c.link LIKE '%%AR_paid%%' OR c.link LIKE '%%AP_paid%%')
                             AND coalesce(b.iban, '') <> '')
                           AND EXISTS (SELECT 1 FROM x_bank_statement)
                           -- het eerste afschrift per rekening sluit aan op het grootboek (waarschuwing bij inlezen)
                           AND NOT EXISTS (SELECT 1 FROM x_bank_statement s
                                           WHERE s.id = (SELECT min(f.id) FROM x_bank_statement f
                                                         WHERE f.chart_id = s.chart_id)
                                             AND coalesce(s.warnings, '') LIKE '%%Grootboeksaldo%%') AS x""")
            done["bank"] = cur.fetchone()["x"]
            cur.execute("SELECT to_regclass('rma.import_report') IS NOT NULL AS x")
            rma = cur.fetchone()["x"]
        now = _closedto({"closedto": d.get("closedto")}, self.config.dateformat)
        then = _closedto({"closedto": info.get("closedto")}, self.config.dateformat)
        done["closedto"] = bool(now) and (not then or now > then)
        schedules, stale = self._stale_schedules(info)
        done["recurring"] = not stale
        for key in SWITCH_MANUAL:
            done[key] = key in marked
        items = []
        for key, need in SWITCH_ITEMS.items():
            if need is not None and not mods & set(need):
                continue
            if key in ("users", "backup", "rgs"):
                if external.get(key) is None:
                    continue
                done[key] = bool(external[key])
            if key == "recurring" and not schedules:          # geen herhaalschema's: punt niet nodig
                continue
            if key == "rma" and not rma:                       # VP5: alleen uit Run my Accounts / ledger123
                continue
            if key == "vat" and vat_unavailable and not done[key]:
                items.append(StartItem(key, False, True, unavailable=True))
                continue
            items.append(StartItem(key, done[key], key in skipped and not done[key]))
        if all(i.done or i.skipped for i in items):
            return None
        return items

    def _stale_schedules(self, info: Dict[str, Any]) -> Tuple[int, List[Dict[str, Any]]]:
        """(aantal herhaalschema's, schema's met een volgende datum vóór de laatste boeking bij het inlezen)
        (v1.0.1 PB16): die heeft SQL-Ledger niet meer verwerkt; Verwerken zou ze alsnog boeken."""
        try:
            cutoff = date.fromisoformat(info.get("last_transdate") or "")
        except ValueError:
            cutoff = date.today()
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT count(*) AS n FROM recurring")
            n = cur.fetchone()["n"]
            if not n:
                return 0, []
            cur.execute("""SELECT r.id, r.reference, r.description, r.nextdate FROM recurring r
                           WHERE r.nextdate IS NOT NULL AND r.nextdate < %s
                             AND (r.enddate IS NULL OR r.nextdate <= r.enddate)
                           ORDER BY r.nextdate, r.id""", (cutoff,))
            return n, cur.fetchall()

    def switch_steps(self) -> Dict[str, List[SwitchStep]]:
        """Deelstappen van de automatische punten "company", "vat", "bank" en "closedto" (zelfde voorwaarden als
        ``switch_list``: een punt is klaar als al zijn deelstappen klaar zijn)."""
        from .gl import _closedto
        from .switchover import restore_info
        info = restore_info(self.conn) or {}
        out: Dict[str, List[SwitchStep]] = {}
        with _db.cursor(self.conn) as cur:
            d = _db.get_defaults(cur, "company", "address", "closedto")
            out["company"] = [SwitchStep("name", bool((d.get("company") or "").strip())),
                              SwitchStep("address", bool((d.get("address") or "").strip()))]
            cur.execute("SELECT value FROM x_setting WHERE key = %s", (VAT_SETTING,))
            r = cur.fetchone()
            vat = [SwitchStep("period", r is not None, detail={"period": r["value"] if r else None})]
            if r is None or r["value"] != "none":          # "geen herinnering": punt klaar, rest niet nodig
                cur.execute("""SELECT DISTINCT c.id, c.accno, c.description FROM chart c LEFT JOIN x_vat_box b
                                 ON (b.chart_id = c.id AND b.country = %s)
                               WHERE c.id IN (SELECT chart_id FROM tax)
                                 AND NOT coalesce(c.closed, false) AND b.box IS NULL ORDER BY c.accno""",
                            (self._vat_country(),))
                rows = cur.fetchall()
                vat.append(SwitchStep("boxes", not rows, rows))
                cur.execute("SELECT value FROM x_setting WHERE key = 'vat_settlement_accno'")
                r = cur.fetchone()
                vat.append(SwitchStep("settlement", bool(r and r["value"] != "")))
            out["vat"] = vat
            cur.execute("""SELECT c.id, c.accno, c.description, coalesce(b.iban, '') <> '' AS has_iban,
                                  coalesce(c.closed, false) AS closed
                           FROM chart c LEFT JOIN bank b ON (b.id = c.id)
                           WHERE c.link LIKE '%%AR_paid%%' OR c.link LIKE '%%AP_paid%%' ORDER BY c.accno""")
            pay = cur.fetchall()
            with_iban = any(p["has_iban"] for p in pay)
            missing = [{"id": p["id"], "accno": p["accno"], "description": p["description"]}
                       for p in pay if not p["has_iban"] and not p["closed"]]
            cur.execute("SELECT EXISTS (SELECT 1 FROM x_bank_statement) AS x")
            has_statement = cur.fetchone()["x"]
            cur.execute("""SELECT s.id AS statement_id, c.id, c.accno, c.description, s.warnings
                           FROM x_bank_statement s JOIN chart c ON (c.id = s.chart_id)
                           WHERE s.id = (SELECT min(f.id) FROM x_bank_statement f WHERE f.chart_id = s.chart_id)
                             AND coalesce(s.warnings, '') LIKE '%%Grootboeksaldo%%' ORDER BY c.accno""")
            bad = []
            for r in cur.fetchall():
                lines = [w for w in r.pop("warnings").splitlines() if "Grootboeksaldo" in w]
                bad.append(dict(r, warning=lines[0] if lines else ""))
            out["bank"] = [SwitchStep("iban", with_iban, [] if with_iban else missing),
                           SwitchStep("statement", has_statement),
                           SwitchStep("match", has_statement and not bad, bad)]
        now = _closedto({"closedto": d.get("closedto")}, self.config.dateformat)
        then = _closedto({"closedto": info.get("closedto")}, self.config.dateformat)
        _n, stale = self._stale_schedules(info)
        out["recurring"] = [SwitchStep("stale", not stale, stale)]
        out["closedto"] = [SwitchStep("later", bool(now) and (not then or now > then),
                                      detail={"now": now, "now_date": _numdate(now),
                                              "then": then, "then_date": _numdate(then)})]
        return out

    def switch_mark(self, key: str, done: bool = False, login: Optional[str] = None) -> None:
        """Punt van de overstaplijst afvinken (``done``: "gedaan", alleen bij ``SWITCH_MANUAL``) of op "niet nodig"
        zetten; ``key`` = "hidden" verbergt de hele lijst."""
        if key != "hidden" and key not in SWITCH_ITEMS:
            raise ValidationError("Onbekend punt.")
        if done and key not in SWITCH_MANUAL:
            raise ValidationError("Onbekend punt.")
        ensure_schema(self.conn)
        kind = f"switchdone_{key}" if done else f"switch_{key}"
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""INSERT INTO x_task_done (kind, period_end, login) VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""", (kind, START_DAY, login))

    def next_numbers(self) -> Dict[str, str]:
        """Laatst gebruikte nummers uit ``defaults`` (SL hoogt ze op bij de volgende boeking): facturen, orders,
        memoriaal, relaties."""
        with _db.cursor(self.conn) as cur:
            return {k: (v or "") for k, v in _db.get_defaults(
                cur, "sinumber", "vinumber", "sonumber", "ponumber", "sqnumber", "rfqnumber", "glnumber",
                "customernumber", "vendornumber").items()}

    # ------------------------------------------------------------------ te doen (DB4)

    def todo(self, modules: List[str], today: Optional[date] = None) -> List[Todo]:
        """Te-doenregels voor de modules die aanstaan; alleen regels met iets te doen."""
        today = today or date.today()
        out: List[Todo] = []
        mods = set(modules)
        if mods & {"ar", "ap"}:
            vr = self.vat_return(today)
            if vr:
                out.append(Todo("vat", 1, detail={"vat": vr}))
        if "cash" in mods:
            ensure_schema(self.conn)
            with _db.cursor(self.conn) as cur:
                cur.execute("""SELECT c.accno, c.description, count(*) AS n FROM x_bank_line l
                               JOIN chart c ON (c.id = l.chart_id) WHERE l.status = 'open'
                               GROUP BY c.accno, c.description ORDER BY c.accno""")
                rows = cur.fetchall()
            if rows:
                out.append(Todo("bank", sum(r["n"] for r in rows), detail={"accounts": rows}))
        if "ar" in mods:
            ar = self.open_items("ar", today)
            if ar.overdue_count:
                out.append(Todo("ar_overdue", ar.overdue_count, ar.overdue))
        if "ap" in mods:
            ap = self.open_items("ap", today)
            if ap.overdue_count or ap.soon_count:
                out.append(Todo("ap_due", ap.overdue_count + ap.soon_count, ap.overdue + ap.soon,
                                detail={"overdue": ap.overdue_count, "soon": ap.soon_count}))
        if "recurring" in mods:
            from .recurring import RecurringTransactions
            n = sum(1 for r in RecurringTransactions(self.conn, self.config).list(today)
                    if r.due and _module_on(r, mods))
            if n:
                out.append(Todo("recurring", n))
        if "ic" in mods:
            with _db.cursor(self.conn) as cur:
                cur.execute("SELECT count(*) AS n FROM parts p WHERE p.onhand < p.rop")
                n = cur.fetchone()["n"]
            if n:
                out.append(Todo("parts_short", n))
        if "oe" in mods:
            with _db.cursor(self.conn) as cur:
                cur.execute("""SELECT count(*) AS n FROM oe o
                               WHERE NOT o.quotation AND o.customer_id > 0 AND NOT o.closed
                                 AND o.reqdate < %s""", (today,))
                n = cur.fetchone()["n"]
            if n:
                out.append(Todo("orders_late", n))
        return out


def _module_on(row, mods) -> bool:
    """Terugkerend schema telt alleen als de module van de boeking aanstaat."""
    m = getattr(row, "module", "") or ""
    need = {"ar": {"ar"}, "ap": {"ap"}, "gl": {"gl"}, "oe": {"oe"}}.get(m)
    return not need or bool(need & mods)
