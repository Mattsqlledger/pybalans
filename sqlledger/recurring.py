"""Terugkerende boekingen (arap.pl schedule/save_schedule, Form::save_recurring,
am.pl recurring_transactions/process_transactions, AM::recurring_transactions,
AM::recurring_details, AM::update_recurring).

Een herhaalschema hangt aan een bestaande boeking (tabel ``recurring``, id = id
van de boeking). "Verwerken" maakt een kopie van die boeking op de volgende
datum en schuift de volgende datum op.

Memoriaal (gl), verkoop/inkoop met en zonder artikelen (ar/ap) en verkoop-/inkooporders
(oe). Offertes en aanvragen krijgen geen schema (RH6, zoals Perl). Een kopie van een
factuur of order neemt prijzen, korting en omschrijvingen van het origineel over (RH1);
btw-tarieven en -instellingen gelden op de nieuwe datum (RH2); alle leverdatums schuiven
evenveel dagen mee (RH4); een orderkopie is een nieuwe open order zonder geleverde
aantallen, nabestelvlag of koppeling (RH5). Een verkoopkopie zonder voldoende voorraad
(artikel met "voorraad controleren") wordt overgeslagen (RH3).

Datums (T3): termijn i = start + i·(repeat × eenheid), steeds vanaf de startdatum
gerekend; bij maanden/jaren wordt de dag afgekapt op het einde van de maand
(31-01 → 28/29-02 → 31-03 → 30-04). Perl telde steeds op bij de vorige datum,
waardoor 31-01 → 29-02 → 29-03 → 29-04 werd.

Welke termijn is aan de beurt (k): er wordt geen teller bijgehouden (``howmany``
is het totaal aantal termijnen en wordt niet afgeteld, net als in Perl). Na het
boeken wordt de volgende datum de eerste termijn die ná zowel de geboekte datum
als de vorige volgende datum valt. Een te laat geboekte termijn verschuift het
schema dus niet, en wie de datum in de lijst vooruitzet, slaat de termijnen
daartussen over. Bij een nieuw schema is de volgende datum de eerste termijn ná
de boekdatum van de boeking zelf (start = boekdatum → tweede termijn, want de
boeking zelf is de eerste; start in de toekomst → de start).

Mailen en PDF na het verwerken (fase 4, SB12–SB13): het schema bewaart de documentsoorten in
``recurringemail``/``recurringprint`` (formnames zoals SL); het resultaat van het verwerken geeft ze per
nieuwe boeking door. Het mailen en de PDF zelf gebeuren in de webapp, ná het boeken, zodat ze het
doorschuiven nooit tegenhouden.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

import psycopg

from . import _db, support
from .amounts import round_amount, to_decimal
from .arap import ArapLedger
from .config import UserConfig
from .errors import LedgerError, NotFoundError, ValidationError
from .gl import GeneralLedger
from .invoice import InvoiceLedger
from .models import GLTransaction, JournalLine, Recurring
from .order import OrderLedger

UNITS = support.RECURRING_UNITS
MAX_REPEAT = 999
MAX_HOWMANY = 9999


# ------------------------------------------------------------------ datums

def add_interval(start: date, n: int, unit: str) -> date:
    """``start`` + n eenheden; maanden/jaren met de dag afgekapt op het
    maandeinde (zoals PostgreSQL ``date + interval`` vanaf dezelfde start)."""
    unit = unit.lower()
    if unit == "days":
        return start + timedelta(days=n)
    if unit == "weeks":
        return start + timedelta(days=7 * n)
    if unit in ("months", "years"):
        months = n * (12 if unit == "years" else 1)
        y, m = divmod(start.month - 1 + months, 12)
        year, month = start.year + y, m + 1
        return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
    raise ValidationError(f"Unknown unit: {unit}")


def term(s: Recurring, i: int) -> date:
    """Datum van termijn i (0 = start)."""
    return add_interval(s.startdate, i * int(s.repeat), s.unit)


def end_date(s: Recurring) -> date:
    return term(s, int(s.howmany) - 1)


def next_after(s: Recurring, after: date) -> Optional[date]:
    """Eerste termijn die ná ``after`` valt, of None als het schema dan afgelopen is."""
    for i in range(int(s.howmany)):
        t = term(s, i)
        if t > after:
            return t
    return None


def terms_due(startdate: Optional[date], repeat: int, unit: str, howmany: int, nextdate: Optional[date],
              enddate: Optional[date], today: date) -> int:
    """Hoeveel termijnen het schema achterloopt: de volgende datum zelf plus elke termijn daarna t/m vandaag, in de
    tijd gerekend (ook voorbij de einddatum of het aantal keer: een schema dat sinds 2015 stilstaat loopt ver achter,
    ook als het nog maar één keer zou boeken). 1 = gewoon aan de beurt; meer = achterstand (v1.0.1 PB16: Verwerken
    boekt er één en vraagt dan om bevestiging). ``enddate``/``howmany`` blijven voor de aanroep zoals de lijst ze
    kent."""
    if not (startdate and nextdate and repeat) or nextdate > today:
        return 0
    n = 1                                           # de volgende datum zelf
    for i in range(1, 100000):
        t = add_interval(startdate, i * int(repeat), unit)
        if t > today:
            break
        if t > nextdate:
            n += 1
    return n


def validate(s: Recurring) -> None:
    """T1: start verplicht, elke n ≥ 1, aantal ≥ 1, eenheid uit de lijst."""
    if not s.startdate:
        raise ValidationError("Startdate missing!")
    if (s.unit or "").lower() not in UNITS:
        raise ValidationError(f"Unknown unit: {s.unit}")
    try:
        repeat, howmany = int(s.repeat), int(s.howmany)
    except (TypeError, ValueError):
        raise ValidationError("Repeat and number of times must be whole numbers")
    if not 1 <= repeat <= MAX_REPEAT:
        raise ValidationError("Repeat must be at least 1")
    if not 1 <= howmany <= MAX_HOWMANY:
        raise ValidationError("Number of times must be at least 1")


# ------------------------------------------------------------------ resultaten

@dataclass
class RecurringRow:
    """Eén regel van de lijst (AM::recurring_transactions)."""

    id: int
    module: str                  # gl, ar, ap, oe
    transaction: str             # gl, ar, ap, so, po
    vc: str                      # customer, vendor of ''
    invoice: bool
    reference: Optional[str]
    description: Optional[str]   # schema-omschrijving
    trans_description: Optional[str]
    number: Optional[str]        # nummer van de boeking zelf
    name: Optional[str]
    vcnumber: Optional[str]
    vc_id: Optional[int]
    amount: Decimal
    currency: Optional[str]
    startdate: Optional[date]
    nextdate: Optional[date]
    enddate: Optional[date]
    repeat: int
    unit: str
    howmany: int
    payment: bool
    overdue: Optional[int]       # dagen tot de volgende datum (≤ 0: te laat of vandaag)
    expired: bool
    email: List[str] = field(default_factory=list)
    print: List[str] = field(default_factory=list)
    reason: Optional[str] = None  # waarom niet verwerkbaar
    behind: int = 0              # termijnen t/m vandaag (``terms_due``); > 1 = loopt achter (PB16)

    @property
    def processable(self) -> bool:
        return self.reason is None and not self.expired

    @property
    def due(self) -> bool:
        """Te verwerken (standaard aangevinkt): volgende datum vandaag of eerder."""
        return self.processable and self.overdue is not None and self.overdue <= 0


@dataclass
class ProcessItem:
    """Te verwerken schema.

    expected_nextdate: de volgende datum zoals de lijst hem toonde; is die
    intussen veranderd (bv. door een andere gebruiker al verwerkt), dan wordt
    het schema overgeslagen. transdate: aangepaste boekdatum (standaard de
    volgende datum)."""

    id: int
    expected_nextdate: Optional[date] = None
    transdate: Optional[date] = None


@dataclass
class ProcessResult:
    id: int
    module: Optional[str] = None
    ok: bool = False
    trans_id: Optional[int] = None
    number: Optional[str] = None
    transdate: Optional[date] = None
    nextdate: Optional[date] = None
    reason: Optional[str] = None
    # na het boeken (fase 4, SB12): documentsoorten (formnames) om te mailen/als PDF te maken, bericht
    email: List[str] = field(default_factory=list)
    print: List[str] = field(default_factory=list)
    message: Optional[str] = None


class Skip(LedgerError):
    """Schema overgeslagen (reden in de tekst)."""


QUOTATION = "Quotations cannot be scheduled"
#: documentsoort in recurringemail/recurringprint (formname zoals SL: invoice, sales_order, transaction …)
FORMNAME_RE = re.compile(r"^[a-z_0-9]{1,40}$")


# ------------------------------------------------------------------ bibliotheek

class RecurringTransactions:
    """Herhaalschema's opslaan/verwijderen, de lijst en verwerken."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ---------------------------------------------------------- boeking

    def kind(self, trans_id: int) -> Optional[Dict[str, Any]]:
        """Soort boeking bij een id: module (gl/ar/ap/oe), vc, invoice, quotation, transdate, nummer."""
        with _db.cursor(self.conn) as cur:
            cur.execute(
                """SELECT 'gl' AS module, '' AS vc, FALSE AS invoice, FALSE AS quotation, transdate,
                          reference AS number, description, NULL::int AS vc_id
                     FROM gl WHERE id = %(id)s
                   UNION ALL
                   SELECT 'ar', 'customer', invoice, FALSE, transdate, invnumber, description, customer_id
                     FROM ar WHERE id = %(id)s
                   UNION ALL
                   SELECT 'ap', 'vendor', invoice, FALSE, transdate, invnumber, description, vendor_id
                     FROM ap WHERE id = %(id)s
                   UNION ALL
                   SELECT 'oe', CASE WHEN customer_id > 0 THEN 'customer' ELSE 'vendor' END, FALSE,
                          coalesce(quotation, FALSE), transdate, CASE WHEN quotation THEN quonumber ELSE ordnumber END,
                          description, coalesce(nullif(customer_id, 0), vendor_id)
                     FROM oe WHERE id = %(id)s""", {"id": trans_id})
            row = cur.fetchone()
        if row:
            row["invoice"] = bool(row["invoice"])
            row["quotation"] = bool(row["quotation"])
        return row

    def get(self, trans_id: int) -> Optional[Recurring]:
        with _db.cursor(self.conn) as cur:
            return support.get_recurring(cur, trans_id)

    # ---------------------------------------------------------- schema

    def save(self, trans_id: int, schedule: Recurring, keep_output: bool = True) -> Recurring:
        """Sla het schema van een boeking op (vervangt een bestaand schema).

        Zonder ``schedule.nextdate`` wordt de volgende datum de eerste termijn
        ná de boekdatum van de boeking; met een volgende datum na de einddatum
        is het schema afgelopen (nextdate leeg, zoals Perl). Met ``keep_output``
        blijven afdruk-/mailinstellingen van een bestaand schema staan als het
        nieuwe er geen heeft; zonder (schemascherm, SB13) gelden precies
        ``schedule.email``/``schedule.print``/``schedule.message``."""
        validate(schedule)
        for form in [e[0] for e in schedule.email] + [p[0] for p in schedule.print]:
            if not FORMNAME_RE.match(form or ""):
                raise ValidationError(f"Unknown form: {form}")
        s = replace(schedule, unit=schedule.unit.lower(), repeat=int(schedule.repeat),
                    howmany=int(schedule.howmany))
        k = self.kind(trans_id)
        if not k:
            raise NotFoundError(f"Transaction {trans_id} not found")
        if k["quotation"]:
            raise ValidationError(QUOTATION)
        if k["module"] == "oe":
            s = replace(s, payment=False)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            old = support.get_recurring(cur, trans_id)
            if keep_output and old and not s.email and not s.print:
                s = replace(s, email=old.email, print=old.print, message=old.message)
            enddate = end_date(s)
            nextdate = s.nextdate or next_after(s, k["transdate"] or s.startdate)
            if nextdate and nextdate > enddate:
                nextdate = None
            support.save_recurring(cur, trans_id, replace(s, nextdate=nextdate or s.startdate))
            # Form::save_recurring schat een volgende datum als er geen is; hier
            # altijd de berekende (of lege) datum
            cur.execute("UPDATE recurring SET nextdate = %s, enddate = %s WHERE id = %s",
                        (nextdate, enddate, trans_id))
            support.audittrail(cur, self.config, trans_id=trans_id, tablename="recurring",
                               reference=s.reference or k["number"], formname="recurring", action="saved")
            return support.get_recurring(cur, trans_id)

    def delete(self, trans_id: int) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            old = support.get_recurring(cur, trans_id)
            if not old:
                return
            support.save_recurring(cur, trans_id, None)
            support.audittrail(cur, self.config, trans_id=trans_id, tablename="recurring",
                               reference=old.reference, formname="recurring", action="deleted")

    def stop(self, trans_id: int) -> bool:
        """Schema stoppen (v1.0.1 PB16): geen volgende datum meer, zoals een afgelopen schema; het schema en de
        boeking blijven staan. Opnieuw starten = op het schemascherm een volgende datum kiezen en opslaan."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("UPDATE recurring SET nextdate = NULL WHERE id = %s RETURNING reference", (trans_id,))
            row = cur.fetchone()
            if row is None:
                return False
            support.audittrail(cur, self.config, trans_id=trans_id, tablename="recurring",
                               reference=row["reference"], formname="recurring", action="stopped")
        return True

    # ---------------------------------------------------------- lijst

    def list(self, today: Optional[date] = None, sort: str = "nextdate") -> List[RecurringRow]:
        """Alle schema's (AM::recurring_transactions), ook afgelopen en niet verwerkbare;
        gegroepeerd per soort (ap, ar, gl, po, so) en daarbinnen op ``sort``."""
        today = today or date.today()
        with _db.cursor(self.conn) as cur:
            defcur = _db.default_currency(cur)
            precision = _db.get_precision(cur)
            cur.execute(
                """SELECT s.*, x.module, x.transaction, x.vc, x.invoice, x.number, x.trans_description,
                          x.name, x.vcnumber, x.vc_id, x.amount, x.curr
                   FROM recurring s JOIN (
                     SELECT a.id, 'ar' AS module, 'ar' AS transaction, 'customer' AS vc, a.invoice,
                            a.invnumber AS number, a.description AS trans_description, n.name,
                            n.customernumber AS vcnumber, n.id AS vc_id,
                            a.amount::numeric / coalesce(nullif(a.exchangerate, 0), 1)::numeric AS amount, a.curr
                       FROM ar a JOIN customer n ON (n.id = a.customer_id)
                     UNION ALL
                     SELECT a.id, 'ap', 'ap', 'vendor', a.invoice, a.invnumber, a.description, n.name,
                            n.vendornumber, n.id,
                            a.amount::numeric / coalesce(nullif(a.exchangerate, 0), 1)::numeric, a.curr
                       FROM ap a JOIN vendor n ON (n.id = a.vendor_id)
                     UNION ALL
                     SELECT g.id, 'gl', 'gl', '', FALSE, g.reference, g.description, NULL, NULL, NULL,
                            (SELECT sum(ac.amount::numeric) FROM acc_trans ac
                              WHERE ac.trans_id = g.id AND ac.amount > 0 AND NOT ac.fx_transaction),
                            coalesce(nullif(g.curr, ''), %s)
                       FROM gl g
                     UNION ALL
                     SELECT o.id, 'oe', CASE WHEN o.customer_id > 0 THEN 'so' ELSE 'po' END,
                            CASE WHEN o.customer_id > 0 THEN 'customer' ELSE 'vendor' END, FALSE,
                            o.ordnumber, o.description, coalesce(c.name, v.name),
                            coalesce(c.customernumber, v.vendornumber), coalesce(c.id, v.id),
                            o.amount::numeric, o.curr
                       FROM oe o LEFT JOIN customer c ON (c.id = o.customer_id AND o.customer_id > 0)
                       LEFT JOIN vendor v ON (v.id = o.vendor_id AND o.vendor_id > 0)
                      WHERE NOT o.quotation
                   ) x ON (x.id = s.id)""", (defcur,))
            rows = cur.fetchall()
            cur.execute("SELECT id, formname FROM recurringemail ORDER BY id, formname")
            emails: Dict[int, List[str]] = {}
            for r in cur.fetchall():
                emails.setdefault(r["id"], []).append(r["formname"])
            cur.execute("SELECT id, formname FROM recurringprint ORDER BY id, formname")
            prints: Dict[int, List[str]] = {}
            for r in cur.fetchall():
                prints.setdefault(r["id"], []).append(r["formname"])
        out = []
        for r in rows:
            reason = None
            nextdate, enddate = r["nextdate"], r["enddate"]
            out.append(RecurringRow(
                id=r["id"], module=r["module"], transaction=r["transaction"], vc=r["vc"],
                invoice=bool(r["invoice"]), reference=r["reference"], description=r["description"],
                trans_description=r["trans_description"], number=r["number"], name=r["name"],
                vcnumber=r["vcnumber"], vc_id=r["vc_id"], amount=round_amount(to_decimal(r["amount"]), precision),
                currency=(r["curr"] or "").strip() or defcur, startdate=r["startdate"], nextdate=nextdate,
                enddate=enddate, repeat=int(r["repeat"] or 0), unit=(r["unit"] or "").strip(),
                howmany=int(r["howmany"] or 0), payment=bool(r["payment"]),
                overdue=(nextdate - today).days if nextdate else None,
                expired=nextdate is None or (enddate is not None and nextdate > enddate),
                email=emails.get(r["id"], []), print=prints.get(r["id"], []), reason=reason,
                behind=terms_due(r["startdate"], int(r["repeat"] or 0), (r["unit"] or "").strip().lower(),
                                 int(r["howmany"] or 0), nextdate, enddate, today)
                if (r["unit"] or "").strip().lower() in UNITS else 0))
        keys = {
            "nextdate": lambda x: (x.nextdate is None, x.nextdate or date.min),
            "reference": lambda x: (x.reference or "").lower(),
            "description": lambda x: (x.description or "").lower(),
            "name": lambda x: (x.name or "").lower(),
            "enddate": lambda x: (x.enddate is None, x.enddate or date.min),
        }
        key = keys.get(sort, keys["nextdate"])
        # per soort (inkoop, verkoop, memoriaal, orders) zoals het Perl-scherm; verwerken gaat in deze volgorde
        out.sort(key=lambda x: (x.transaction, key(x), x.id))
        return out

    # ---------------------------------------------------------- verwerken

    def process(self, items: Iterable[Union[int, ProcessItem]],
                today: Optional[date] = None) -> List[ProcessResult]:
        """Verwerk schema's (am.pl process_transactions): per schema één
        databasetransactie met een lock op het schema; een fout of reden om over
        te slaan raakt alleen dat schema."""
        items = [i if isinstance(i, ProcessItem) else ProcessItem(int(i)) for i in items]
        # zonder verwachte datum: de datum van nu (twee gelijktijdige aanroepen
        # lezen dezelfde datum; de tweede ziet na de lock dat hij veranderd is)
        missing = [i.id for i in items if i.expected_nextdate is None]
        if missing:
            with _db.cursor(self.conn) as cur:
                cur.execute("SELECT id, nextdate FROM recurring WHERE id = ANY(%s)", (missing,))
                current = {r["id"]: r["nextdate"] for r in cur.fetchall()}
            items = [replace(i, expected_nextdate=current.get(i.id)) if i.expected_nextdate is None else i
                     for i in items]
        results = []
        seen = set()
        for item in items:
            if item.id in seen:
                continue
            seen.add(item.id)
            res = ProcessResult(id=item.id)
            try:
                self._process_one(item, res)
                res.ok = True
            except (LedgerError, ValueError) as exc:
                res.ok = False
                res.trans_id = res.number = None
                res.email, res.print, res.message = [], [], None
                res.reason = str(exc)
            results.append(res)
        return results

    def _process_one(self, item: ProcessItem, res: ProcessResult) -> None:
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT * FROM recurring WHERE id = %s FOR UPDATE", (item.id,))
            row = cur.fetchone()
            if not row:
                raise Skip("Schedule not found")
            k = self.kind(item.id)
            if not k:
                raise Skip("Transaction not found")
            res.module = k["module"]
            if row["nextdate"] != item.expected_nextdate:
                raise Skip("Next date changed in the meantime (already processed?)")
            if row["nextdate"] is None or (row["enddate"] and row["nextdate"] > row["enddate"]):
                raise Skip("Schedule has ended")
            if k["quotation"]:
                raise Skip(QUOTATION)
            s = support.get_recurring(cur, item.id)
            res.email = [e[0] for e in s.email]
            res.print = [p[0] for p in s.print]
            res.message = s.message
            transdate = item.transdate or row["nextdate"]
            if k["module"] == "gl":
                res.trans_id = self._copy_gl(cur, item.id, s, transdate)
                cur.execute("SELECT reference FROM gl WHERE id = %s", (res.trans_id,))
                res.number = cur.fetchone()["reference"]
            elif k["module"] == "oe":
                res.trans_id = self._copy_order(cur, item.id, s, transdate)
                cur.execute("SELECT ordnumber FROM oe WHERE id = %s", (res.trans_id,))
                res.number = cur.fetchone()["ordnumber"]
            else:
                copy = self._copy_invoice if k["invoice"] else self._copy_arap
                res.trans_id = copy(cur, item.id, k["vc"], s, transdate)
                cur.execute(f"SELECT invnumber FROM {k['module']} WHERE id = %s", (res.trans_id,))
                res.number = cur.fetchone()["invnumber"]
            res.transdate = transdate
            nextdate = next_after(s, max(transdate, row["nextdate"]))
            if nextdate and row["enddate"] and nextdate > row["enddate"]:
                nextdate = None
            cur.execute("UPDATE recurring SET nextdate = %s WHERE id = %s", (nextdate, item.id))
            res.nextdate = nextdate

    # ---------------------------------------------------------- kopieën

    def _rate(self, cur, currency: Optional[str], on: date) -> Decimal:
        """Koers op de datum uit de koerstabel (T7); ontbreekt die: overslaan."""
        cur.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s", (currency, on))
        r = cur.fetchone()
        rate = to_decimal(r["exchangerate"]) if r else Decimal(0)
        if not rate:
            raise Skip(f"Exchange rate missing for {currency} on {on.isoformat()}")
        return rate

    def _number(self, cur, table: str, column: str, reference: Optional[str],
                where: str = "", params: Sequence[Any] = ()) -> Optional[str]:
        """T4: het kenmerk van het schema alleen als dat nummer nog niet bestaat,
        anders (of zonder kenmerk) None = volgend nummer."""
        if not reference:
            return None
        cur.execute(f"SELECT 1 FROM {table} WHERE {column} = %s {where} LIMIT 1", (reference, *params))
        return None if cur.fetchone() else reference

    def _copy_gl(self, cur, trans_id: int, s: Recurring, transdate: date) -> int:
        gl = GeneralLedger(self.conn, self.config)
        view = gl.get_transaction(trans_id)
        h = view.header
        defcur = _db.default_currency(cur)
        currency = None if view.fxadj else h.get("currency")
        rate = Decimal(1)
        if currency and currency != defcur:
            rate = self._rate(cur, currency, transdate)
        else:
            currency = None
        lines = [JournalLine(accno=ln["accno"], debit=ln["debit"], credit=ln["credit"],
                             source=ln.get("source"), memo=ln.get("memo"), project_id=ln.get("project_id"),
                             fx_transaction=bool(view.fxadj and ln.get("fx_transaction")))
                 for ln in view.lines]
        trans = GLTransaction(
            transdate=transdate, lines=lines,
            reference=self._number(cur, "gl", "reference", s.reference),
            description=h.get("description"), notes=h.get("notes"),
            department_id=h.get("department_id") or None, currency=currency, exchangerate=rate)
        return gl.post_transaction(trans)

    def _shift_payments(self, cur, t, s: Recurring, shift: timedelta, foreign: bool, rate: Decimal) -> None:
        """T6: betalingen (en contante korting) schuiven evenveel dagen mee als de boekdatum, niet
        afgeletterd; zonder "betaling meenemen" geen betalingen en geen korting. T7: koers per betaaldatum."""
        if s.payment:
            for p in t.payments + ([t.discount] if t.discount else []):
                p.datepaid = (p.datepaid or t.transdate) + shift
                p.cleared = None
                if foreign:
                    p.exchangerate = (rate if p.datepaid == t.transdate + shift
                                      else self._rate(cur, t.currency, p.datepaid))
        else:
            t.payments, t.discount = [], None
        # T6: zelfde aantal dagen tot de vervaldatum, ook bij inkoop
        t.duedate = t.duedate + shift if t.duedate else None

    def _invnumber(self, cur, vc: str, vc_id: int, s: Recurring) -> Optional[str]:
        if vc == "customer":
            return self._number(cur, "ar", "invnumber", s.reference)
        return self._number(cur, "ap", "invnumber", s.reference, "AND vendor_id = %s", (vc_id,))

    def _bank_accno(self, lg: ArapLedger, vc: str, t) -> Optional[str]:
        # standaard betaalrekening zoals am.pl: die van de laatste meegenomen betaling,
        # anders de betaalrekening van de relatie (kan leeg zijn)
        return t.bank_accno or (t.payments[-1].accno if t.payments
                                else lg.vc_defaults(vc, t.vc_id)["payment_accno"])

    def _copy_arap(self, cur, trans_id: int, vc: str, s: Recurring, transdate: date) -> int:
        lg = ArapLedger(self.conn, self.config)
        t = lg.get_transaction(trans_id, vc)
        defcur = _db.default_currency(cur)
        foreign = bool(t.currency and t.currency != defcur)
        shift = transdate - t.transdate
        t.id = None
        rate = self._rate(cur, t.currency, transdate) if foreign else Decimal(1)
        self._shift_payments(cur, t, s, shift, foreign, rate)
        for ln in t.lines:
            ln.cleared = None
        t.invnumber = self._invnumber(cur, vc, t.vc_id, s)
        if vc == "customer":
            t.dcn = None                 # wordt bij boeken opnieuw berekend (sjabloon bankrekening)
        t.bank_accno = self._bank_accno(lg, vc, t)
        t.transdate = transdate
        t.exchangerate = rate
        t.printed, t.emailed, t.queued = set(), set(), {}
        t.recurring = None
        t.references = []
        return lg.post_transaction(t)

    def _copy_invoice(self, cur, trans_id: int, vc: str, s: Recurring, transdate: date) -> int:
        """Kopie van een factuur met artikelen (am.pl: is.pl/ir.pl invoice_links + IS/IR post_invoice).
        Voorraad, kostprijs (FIFO) en samengestelde artikelen zoals een nieuwe factuur."""
        il = InvoiceLedger(self.conn, self.config)
        inv = il.get_invoice(trans_id, vc)
        if inv.till:
            raise Skip("Point of sale receipts cannot be repeated")
        defcur = _db.default_currency(cur)
        foreign = bool(inv.currency and inv.currency != defcur)
        shift = transdate - inv.transdate
        rate = self._rate(cur, inv.currency, transdate) if foreign else Decimal(1)
        self._shift_payments(cur, inv, s, shift, foreign, rate)
        for ln in inv.lines:
            ln.id = None
            ln.orderitems_id = None
            if isinstance(ln.deliverydate, date):          # RH4 (tekst = ongeldige oude datum: blijft)
                ln.deliverydate = ln.deliverydate + shift
        inv.id = None
        inv.invnumber = self._invnumber(cur, vc, inv.vc_id, s)
        if vc == "customer":
            inv.dcn = None
        inv.bank_accno = self._bank_accno(il.arap, vc, inv)
        inv.transdate = transdate
        inv.exchangerate = rate
        inv.order_id = None                                  # RH6: geen koppeling met een order
        inv.taxaccounts_extra = set()                        # RH2: btw zoals een nieuwe factuur
        inv.printed, inv.emailed, inv.queued = set(), set(), {}
        inv.recurring = None
        inv.references = []
        return il.post_invoice(inv)

    def _copy_order(self, cur, trans_id: int, s: Recurring, transdate: date) -> int:
        """Kopie van een verkoop-/inkooporder (am.pl: oe.pl order_links + OE save). RH5: nieuwe open
        order, geleverd 0, geen nabestelvlag of koppeling (Perl nam ship en backorder over)."""
        ol = OrderLedger(self.conn, self.config)
        o = ol.get_order(trans_id)
        if o.quotation:
            raise Skip(QUOTATION)
        defcur = _db.default_currency(cur)
        foreign = bool(o.currency and o.currency != defcur)
        shift = transdate - o.transdate
        o.exchangerate = self._rate(cur, o.currency, transdate) if foreign else Decimal(1)
        o.reqdate = o.reqdate + shift if o.reqdate else None
        for ln in o.lines:
            ln.id = None
            ln.ship = None
            if isinstance(ln.reqdate, date):                 # RH4
                ln.reqdate = ln.reqdate + shift
        if o.vc == "customer":
            o.ordnumber = self._number(cur, "oe", "ordnumber", s.reference, "AND NOT quotation AND customer_id > 0")
        else:
            o.ordnumber = self._number(cur, "oe", "ordnumber", s.reference,
                                       "AND NOT quotation AND vendor_id = %s", (o.vc_id,))
        o.id = None
        o.transdate = transdate
        o.closed = o.backorder = False
        o.aa_id = o.invnumber = o.source_id = None
        o.printed, o.emailed, o.queued = set(), set(), {}
        o.recurring = None
        o.references = []
        return ol.save_order(o)
