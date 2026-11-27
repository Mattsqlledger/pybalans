"""Orders en offertes (port van SL/OE.pm en bin/mozilla/oe.pl, fase 3 O1–O6, ON1–ON4, SA11).

Een order of offerte is een rij in ``oe`` met regels in ``orderitems``. Soort (``Order.type``):
verkooporder (``sales_order``), inkooporder (``purchase_order``), offerte (``sales_quotation``) en
offerteaanvraag (``request_quotation``); ``oe.quotation`` en ``customer_id``/``vendor_id`` leggen dat vast.
Bedragen en btw worden gerekend zoals OE.pm ``save`` (btw-tarieven uit de database, F2).

Verschillen met Perl (zie README "Bewuste afwijkingen"):

* Voorraad beweegt niet door orders (O3): opslaan, omzetten en verwijderen raken ``parts.onhand`` niet
  en schrijven geen ``inventory``-regels; dat doet de factuur. ``orderitems.ship`` krijgt bij het boeken
  van de factuur het gefactureerde aantal (zoals Perl bij omzetten).
* Omzetten (O2, ON4): offerte/aanvraag -> order sluit de bron pas bij het opslaan van de order; order (of
  offerte) -> factuur sluit de bron pas bij het boeken van de factuur, in dezelfde transactie, met een
  koppeling in ``x_oe_link``. "Order -> offerte" sluit de order niet. Deellevering (O4, ON2): wat niet
  gefactureerd is wordt bij het boeken van de factuur een nabestelling (nieuwe order, ``backorder``).
* Een gefactureerde of gesloten order, en een order in een afgesloten periode, kan niet gewijzigd of
  verwijderd worden (O6, ON1); een gesloten order zonder factuur kan heropend worden.
* Aanbetalingen op orders (O5) worden niet ondersteund: een order met grootboekregels wordt geweigerd.
* ``parts.weight`` wordt niet overschreven; vrachtgegevens (``cargo``) blijven bij hun regel staan.
* Alle SQL geparametriseerd.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

import psycopg

from . import _db, support
from .amounts import _plain, round_amount, to_decimal
from .arap import ArapLedger, _save_status
from .config import UserConfig
from .errors import ClosedPeriodError, LockedTransactionError, NotFoundError, ValidationError
from .extensions import ensure_schema
from .gl import _closedto, _num, like
from .models import Recurring, Reference
from .numbering import next_number

ZERO = Decimal(0)
ONE = Decimal(1)
DECIMALS = 6
SHIPTO_FIELDS = ("name", "address1", "address2", "city", "state", "zipcode", "country", "contact",
                 "phone", "fax", "email")

#: soort -> (relatie, offerte?, nummerveld in oe, teller in defaults)
TYPES = {
    "sales_order": ("customer", False, "ordnumber", "sonumber"),
    "purchase_order": ("vendor", False, "ordnumber", "ponumber"),
    "sales_quotation": ("customer", True, "quonumber", "sqnumber"),
    "request_quotation": ("vendor", True, "quonumber", "rfqnumber"),
}
#: omzetten naar een andere order/offerte: (bron, doel) -> sluit de bron bij opslaan van het doel?
CONVERSIONS = {
    ("sales_quotation", "sales_order"): True,
    ("request_quotation", "purchase_order"): True,
    ("sales_order", "sales_quotation"): False,
    ("purchase_order", "request_quotation"): False,
}


def order_type(vc: str, quotation: bool) -> str:
    if vc == "customer":
        return "sales_quotation" if quotation else "sales_order"
    return "request_quotation" if quotation else "purchase_order"


@dataclass
class OrderLine:
    """Orderregel zoals ingevoerd (aantal kan negatief zijn: retour)."""

    parts_id: Optional[int]
    qty: Decimal = ZERO
    sellprice: Decimal = ZERO             # prijs per stuk in de valuta van de order, vóór korting
    discount: Decimal = ZERO              # korting in procenten
    description: Optional[str] = None     # leeg = omschrijving van het artikel
    unit: Optional[str] = None
    reqdate: Optional[date] = None        # leverdatum
    project_id: Optional[int] = None
    serialnumber: Optional[str] = None
    ordernumber: Optional[str] = None
    ponumber: Optional[str] = None        # inkoopordernummer van de klant (customerponumber)
    itemnotes: Optional[str] = None
    lineitemdetail: bool = False
    cost: Optional[Decimal] = None        # kostprijs/leverancier per regel (bewaard, niet getoond; F8)
    vendor: Optional[str] = None
    vendor_id: Optional[int] = None
    ship: Optional[Decimal] = None        # geleverd/gefactureerd aantal (bewaard; zet de factuur)
    id: Optional[int] = None              # bestaande regel (orderitems.id): vrachtgegevens blijven erbij


@dataclass
class Order:
    type: str
    vc_id: int
    transdate: date
    lines: List[OrderLine] = field(default_factory=list)
    id: Optional[int] = None
    ordnumber: Optional[str] = None
    quonumber: Optional[str] = None
    ponumber: Optional[str] = None
    reqdate: Optional[date] = None        # leverdatum (order) of geldig tot (offerte)
    terms: int = 0
    description: Optional[str] = None
    notes: Optional[str] = None
    intnotes: Optional[str] = None
    currency: Optional[str] = None
    exchangerate: Decimal = ONE
    taxincluded: bool = False
    department_id: Optional[int] = None
    employee_id: Optional[int] = None
    language_code: Optional[str] = None
    shippingpoint: Optional[str] = None
    shipvia: Optional[str] = None
    waybill: Optional[str] = None
    warehouse_id: Optional[int] = None    # bewaard (magazijnen vallen buiten fase 3)
    closed: bool = False
    backorder: bool = False
    shipto: Dict[str, str] = field(default_factory=dict)
    printed: Set[str] = field(default_factory=set)
    emailed: Set[str] = field(default_factory=set)
    queued: Dict[str, str] = field(default_factory=dict)
    recurring: Optional[Recurring] = None
    references: List[Reference] = field(default_factory=list)
    source_id: Optional[int] = None       # omzetten: offerte/aanvraag die bij opslaan gesloten wordt (O2)
    # alleen lezen (get_order)
    aa_id: Optional[int] = None
    invnumber: Optional[str] = None       # factuur bij aa_id
    amount: Optional[Decimal] = None
    netamount: Optional[Decimal] = None
    locked: bool = False                  # afgesloten periode
    readonly: bool = False                # in gebruik door een ander
    haslock: Optional[str] = None
    prepaid: bool = False                 # aanbetaling (grootboekregels) uit Perl: niet ondersteund

    @property
    def vc(self) -> str:
        return TYPES[self.type][0]

    @property
    def quotation(self) -> bool:
        return TYPES[self.type][1]

    @property
    def number(self) -> Optional[str]:
        return self.quonumber if self.quotation else self.ordnumber


@dataclass
class OrderLineCalc:
    index: int                            # positie in Order.lines
    part: Dict[str, Any]
    qty: Decimal
    fxsellprice: Decimal                  # opgeslagen prijs (orderitems.sellprice)
    discount: Decimal                     # fractie (0.1 = 10%)
    sellprice: Decimal                    # netto stuksprijs (na korting)
    linetotal: Decimal                    # regelbedrag, afgerond
    taxaccounts: List[str]


@dataclass
class OrderCalc:
    lines: List[OrderLineCalc]
    taxes: Dict[str, Decimal]             # btw per rekening, afgerond
    netamount: Decimal                    # oe.netamount
    amount: Decimal                       # oe.amount
    subtotal: Decimal                     # som regelbedragen (scherm)


def _d(v: Any) -> Decimal:
    return to_decimal(v)


def _decimals(v: Decimal) -> int:
    s = _plain(v)
    return len(s.split(".", 1)[1]) if "." in s else 0


def _opt_d(v: Any) -> Optional[Decimal]:
    return None if v is None else _d(v)


class OrderLedger:
    """Orders en offertes: doorrekenen, opslaan, laden, verwijderen, heropenen, omzetten, lijst."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config
        self.arap = ArapLedger(conn, config)

    # ------------------------------------------------------------ artikelen

    def _invoices(self):
        from .invoice import InvoiceLedger
        return InvoiceLedger(self.conn, self.config)

    def find_items(self, vc: str, text: str = "", **kw) -> List[Dict[str, Any]]:
        """Artikelen voor een orderregel: zoals bij de factuur (verkoop: met opbrengstrekening; inkoop: geen
        samengestelde artikelen)."""
        return self._invoices().find_items(vc, text, **kw)

    def item_defaults(self, vc: str, parts_id: int, exchangerate: Any = None) -> Dict[str, Any]:
        return self._invoices().item_defaults(vc, parts_id, exchangerate)

    # ------------------------------------------------------------- rekenen

    def tax_rates(self, o: Order) -> Dict[str, Decimal]:
        """Btw-tarieven van de relatie op de orderdatum (F2; OE.pm gebruikte de tarieven uit het formulier)."""
        return dict(self.arap.vc_defaults(o.vc, o.vc_id, o.transdate)["taxrates"]) if o.vc_id else {}

    def calculate(self, o: Order, rates: Optional[Dict[str, Decimal]] = None, _cur=None) -> OrderCalc:
        """Reken de order door zoals OE.pm save: prijs afgerond op zijn eigen decimalen (minstens de
        standaard), korting per stuk, regelbedrag afgerond, btw per rekening over de regelbedragen,
        nettobedrag = som van prijs x aantal (onafgerond)."""
        if _cur is None:
            with _db.cursor(self.conn) as cur:
                return self.calculate(o, rates, cur)
        cur = _cur
        precision = _db.get_precision(cur)
        if rates is None:
            rates = self.tax_rates(o)
        parts = self._invoices().parts_info(cur, [l.parts_id for l in o.lines])
        taxacc: Dict[str, Decimal] = {}
        out: List[OrderLineCalc] = []
        netamount = ZERO
        for idx, l in enumerate(o.lines):
            if not l.parts_id:
                continue
            part = parts.get(int(l.parts_id))
            if part is None:
                raise ValidationError(f"Onbekend artikel {l.parts_id}")
            qty = _d(l.qty)
            if not qty:
                continue
            sp = _d(l.sellprice)
            dp = max(_decimals(sp), precision)
            fxsellprice = round_amount(sp, dp)
            pct = _d(l.discount)
            discount = round_amount(sp * pct / 100, dp)
            sellprice = round_amount(sp - discount, dp)
            linetotal = round_amount(sellprice * qty, precision)
            taxaccounts = list(part["taxaccounts"])
            for ml in (1, -1):
                taxrate = sum((rates[a] for a in taxaccounts if a in rates and rates[a] * ml > 0), ZERO) * ml
                for a in taxaccounts:
                    r = rates.get(a)
                    if r is not None and r * ml > 0:
                        if o.taxincluded:
                            taxacc[a] = taxacc.get(a, ZERO) + linetotal * r / (1 + taxrate)
                        else:
                            taxacc[a] = taxacc.get(a, ZERO) + linetotal * r
            netamount += sellprice * qty
            out.append(OrderLineCalc(index=idx, part=part, qty=qty, fxsellprice=fxsellprice, discount=pct / 100,
                                     sellprice=sellprice, linetotal=linetotal, taxaccounts=taxaccounts))
        taxes = {a: round_amount(v, precision) for a, v in sorted(taxacc.items())}
        tax = sum(taxes.values(), ZERO)
        if o.taxincluded:
            netamount -= tax
        amount = round_amount(netamount + tax, precision)
        return OrderCalc(lines=out, taxes=taxes, netamount=round_amount(netamount, precision), amount=amount,
                         subtotal=sum((c.linetotal for c in out), ZERO))

    # ------------------------------------------------------------ validatie

    def _validate(self, cur, o: Order, defaultcurrency: str) -> None:
        if o.type not in TYPES:
            raise ValidationError(f"Unknown order type {o.type}")
        if not o.transdate:
            raise ValidationError("Quotation Date missing!" if o.quotation else "Order Date missing!")
        if not o.vc_id:
            raise ValidationError("Customer missing!" if o.vc == "customer" else "Vendor missing!")
        cur.execute(f"SELECT id FROM {o.vc} WHERE id = %s", (o.vc_id,))
        if not cur.fetchone():
            raise ValidationError(f"Unknown {o.vc} {o.vc_id}")
        currency = o.currency or defaultcurrency
        if currency != defaultcurrency and not _d(o.exchangerate):
            raise ValidationError("Exchange rate missing!")
        for i, l in enumerate(o.lines, 1):
            if not l.parts_id:
                continue
            for v in (l.qty, l.sellprice, l.discount):
                v = _d(v)
                if round_amount(v, DECIMALS) != v:
                    raise ValidationError(f"Regel {i}: meer dan {DECIMALS} decimalen")
            if not 0 <= _d(l.discount) <= 100:
                raise ValidationError(f"Regel {i}: korting moet tussen 0 en 100 liggen")

    def _check_parts(self, o: Order, calc: OrderCalc) -> None:
        ar = o.vc == "customer"
        for c in calc.lines:
            p = c.part
            n = c.index + 1
            if not (p["inventory_accno_id"] or p["income_accno_id"] or p["expense_accno_id"]):
                raise ValidationError(f"Regel {n}: kits worden (nog) niet ondersteund")
            if ar and not p["income_accno_id"]:
                raise ValidationError(f"Regel {n}: artikel {p['partnumber']} heeft geen opbrengstrekening")
            if not ar and p["assembly"]:
                raise ValidationError(f"Regel {n}: een samengesteld artikel kan niet ingekocht worden")

    def _editable(self, cur, row: Dict[str, Any], closedto, *, deleting: bool = False) -> None:
        """Server weigert wijzigen/verwijderen van een gefactureerde, gesloten of afgesloten order (O6, ON1)."""
        if row["aa_id"]:
            raise LockedTransactionError("Order is invoiced" if not deleting else "Cannot delete an invoiced order")
        if row["closed"]:
            raise LockedTransactionError("Order is closed")
        if closedto and row["transdate"] and _num(row["transdate"]) <= closedto:
            raise ClosedPeriodError("Order is in a closed period")
        cur.execute("SELECT 1 FROM acc_trans WHERE trans_id = %s LIMIT 1", (row["id"],))
        if cur.fetchone():
            raise ValidationError("Aanbetalingen op orders worden niet ondersteund")
        holder = support.lock_holder(cur, self.config, row["id"])
        if holder:
            raise LockedTransactionError(f"Transaction is locked by {holder}")

    # ------------------------------------------------------------- opslaan

    def save_order(self, o: Order, *, _force: bool = False) -> int:
        """Sla een order/offerte op (OE.pm save). ``_force`` (alleen voor de echte-datatoets) slaat de
        weigering van gesloten/gefactureerde orders over."""
        ensure_schema(self.conn)
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            defaultcurrency = _db.default_currency(cur)
            self._validate(cur, o, defaultcurrency)
            defaults = _db.get_defaults(cur, "closedto", "precision")
            closedto = _closedto(defaults, cfg.dateformat)
            rates = self.tax_rates(o)
            calc = self.calculate(o, rates, cur)
            self._check_parts(o, calc)
            vc, quotation, numfield, counter = TYPES[o.type]
            currency = o.currency or defaultcurrency
            rate = _d(o.exchangerate) or ONE
            if currency == defaultcurrency:
                rate = ONE

            source = None
            if o.source_id:
                source = self._lock_row(cur, o.source_id)
                if not source:
                    raise NotFoundError(f"oe {o.source_id} not found")
                stype = order_type("customer" if source["customer_id"] else "vendor", bool(source["quotation"]))
                if not CONVERSIONS.get((stype, o.type)):
                    raise ValidationError(f"Cannot convert {stype} to {o.type}")
                if source["closed"] or source["aa_id"]:
                    raise ValidationError("De offerte is al gesloten of omgezet")

            cargo: Dict[int, Dict[str, Any]] = {}
            if o.id:
                _keep_blanks(cur, o)
                old = self._lock_row(cur, o.id)
                if not old:
                    raise NotFoundError(f"oe {o.id} not found")
                otype = order_type("customer" if old["customer_id"] else "vendor", bool(old["quotation"]))
                if otype != o.type:
                    raise ValidationError(f"Order {o.id} is a {otype}, not a {o.type}")
                if not _force:
                    self._editable(cur, old, closedto)
                cur.execute("SELECT * FROM cargo WHERE trans_id = %s", (o.id,))
                cargo = {r["id"]: r for r in cur.fetchall()}
                for tb in ("dpt_trans", "orderitems", "shipto", "cargo"):
                    cur.execute(f"DELETE FROM {tb} WHERE trans_id = %s", (o.id,))
                oe_id = o.id
            else:
                employee_id = o.employee_id or _db.get_employee(cur, cfg.short_login)[1]
                cur.execute("INSERT INTO oe (employee_id) VALUES (%s) RETURNING id", (employee_id,))
                oe_id = cur.fetchone()["id"]

            self._write_lines(cur, oe_id, o, calc, cargo)

            number = o.number or next_number(cur, counter, dateformat=cfg.dateformat, transdate=o.transdate,
                                             currency=currency, fields={"description": o.description})
            if quotation:
                o.quonumber = number
            else:
                o.ordnumber = number
            employee_id = o.employee_id or _db.get_employee(cur, cfg.short_login)[1]
            cur.execute(
                """UPDATE oe SET ordnumber = %s, quonumber = %s, description = %s, transdate = %s,
                          vendor_id = %s, customer_id = %s, amount = %s, netamount = %s, reqdate = %s,
                          taxincluded = %s, shippingpoint = %s, shipvia = %s, waybill = %s, notes = %s,
                          intnotes = %s, curr = %s, closed = %s, quotation = %s, department_id = %s,
                          employee_id = %s, language_code = %s, ponumber = %s, terms = %s, warehouse_id = %s,
                          exchangerate = %s, backorder = %s
                   WHERE id = %s""",
                (o.ordnumber, o.quonumber, o.description, o.transdate,
                 o.vc_id if vc == "vendor" else 0, o.vc_id if vc == "customer" else 0, calc.amount,
                 calc.netamount, o.reqdate, bool(o.taxincluded), o.shippingpoint, o.shipvia, o.waybill, o.notes,
                 o.intnotes, currency, bool(o.closed), quotation, int(o.department_id or 0), employee_id,
                 o.language_code or "", o.ponumber, int(o.terms or 0), int(o.warehouse_id or 0), rate,
                 bool(o.backorder), oe_id))
            self._write_shipto(cur, oe_id, o.shipto)
            _save_status(cur, oe_id, o.printed, o.emailed, o.queued)
            support.save_references(cur, cfg, oe_id, o.type, o.references)
            support.update_exchangerate(cur, currency, o.transdate, rate, defaultcurrency)
            if o.department_id:
                cur.execute("INSERT INTO dpt_trans (trans_id, department_id) VALUES (%s, %s)",
                            (oe_id, int(o.department_id)))
            support.audittrail(cur, cfg, trans_id=oe_id, tablename="oe", reference=number, formname=o.type,
                               action="saved")
            support.save_recurring(cur, oe_id, o.recurring)
            support.remove_locks(cur, cfg, "oe")
            if source:
                cur.execute("UPDATE oe SET closed = true WHERE id = %s", (source["id"],))
                _link(cur, cfg, "order", source["id"], oe_id)
            o.id = oe_id
        return oe_id

    def _lock_row(self, cur, oe_id: int) -> Optional[Dict[str, Any]]:
        cur.execute("""SELECT id, customer_id, vendor_id, quotation, closed, aa_id, transdate, ordnumber, quonumber,
                              backorder
                       FROM oe WHERE id = %s FOR UPDATE""", (oe_id,))
        return cur.fetchone()

    def _write_lines(self, cur, oe_id: int, o: Order, calc: OrderCalc, cargo: Dict[int, Dict[str, Any]]) -> None:
        for c in calc.lines:
            l = o.lines[c.index]
            p = c.part
            cur.execute(
                """INSERT INTO orderitems (trans_id, parts_id, description, qty, sellprice, discount, unit, reqdate,
                                           project_id, ship, serialnumber, ordernumber, ponumber, itemnotes,
                                           lineitemdetail, cost, vendor, vendor_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (oe_id, p["id"], l.description if l.description is not None else p["description"], c.qty,
                 c.fxsellprice, c.discount, (l.unit if l.unit is not None else (p["unit"] or ""))[:5], l.reqdate,
                 l.project_id or None, _d(l.ship), l.serialnumber, l.ordernumber, l.ponumber, l.itemnotes,
                 bool(l.lineitemdetail), _opt_d(l.cost), l.vendor,
                 int(l.vendor_id) if l.vendor_id is not None else None))
            nid = cur.fetchone()["id"]
            cg = cargo.get(l.id) if l.id else None
            if cg:
                cur.execute("""INSERT INTO cargo (id, trans_id, package, netweight, grossweight, volume)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            (nid, oe_id, cg["package"], cg["netweight"], cg["grossweight"], cg["volume"]))
            l.id = nid

    def _write_shipto(self, cur, oe_id: int, shipto: Dict[str, str]) -> None:
        if any((shipto or {}).get(f) for f in SHIPTO_FIELDS):
            cur.execute("""INSERT INTO shipto (trans_id, shiptoname, shiptoaddress1, shiptoaddress2, shiptocity,
                                               shiptostate, shiptozipcode, shiptocountry, shiptocontact,
                                               shiptophone, shiptofax, shiptoemail, shiptorecurring)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (oe_id, *[shipto.get(f) or None for f in SHIPTO_FIELDS], bool(shipto.get("recurring"))))

    # -------------------------------------------------------------- laden

    def get_order(self, oe_id: int, lock: bool = False) -> Order:
        """Laad een order/offerte zoals het scherm (OE.pm retrieve)."""
        holder = None
        if lock:
            with self.conn.transaction(), _db.cursor(self.conn) as cur:
                support.remove_locks(cur, self.config)
                cur.execute("SELECT 1 FROM oe WHERE id = %s", (oe_id,))
                if cur.fetchone():
                    holder = support.create_lock(cur, self.config, oe_id, "oe")
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT * FROM oe WHERE id = %s", (oe_id,))
            h = cur.fetchone()
            if not h:
                raise NotFoundError(f"oe {oe_id} not found")
            vc = "customer" if h["customer_id"] else "vendor"
            cur.execute("""SELECT o.id, o.parts_id, o.description, o.qty::numeric AS qty, o.sellprice::numeric AS sellprice,
                                  o.discount, o.unit, o.reqdate::text AS reqdate, o.project_id, o.ship::numeric AS ship,
                                  o.serialnumber, o.itemnotes, o.lineitemdetail, o.ordernumber, o.ponumber,
                                  o.cost::numeric AS cost, o.vendor, o.vendor_id
                           FROM orderitems o WHERE o.trans_id = %s ORDER BY o.id""", (oe_id,))
            rows = cur.fetchall()
            cur.execute("SELECT * FROM shipto WHERE trans_id = %s", (oe_id,))
            st = cur.fetchone() or {}
            cur.execute("SELECT printed, emailed, spoolfile, formname FROM status WHERE trans_id = %s", (oe_id,))
            printed, emailed, queued = set(), set(), {}
            for s in cur.fetchall():
                if s["printed"]:
                    printed.add(s["formname"])
                if s["emailed"]:
                    emailed.add(s["formname"])
                if s["spoolfile"]:
                    queued[s["formname"]] = s["spoolfile"]
            recurring = support.get_recurring(cur, oe_id)
            refs = support.all_references(cur, self.config, oe_id)
            invnumber = None
            if h["aa_id"]:
                cur.execute(f"SELECT invnumber FROM {'ar' if vc == 'customer' else 'ap'} WHERE id = %s", (h["aa_id"],))
                r = cur.fetchone()
                invnumber = r["invnumber"] if r else None
            cur.execute("SELECT 1 FROM acc_trans WHERE trans_id = %s LIMIT 1", (oe_id,))
            prepaid = cur.fetchone() is not None
            defaults = _db.get_defaults(cur, "closedto")
            if not lock:
                holder = support.lock_holder(cur, self.config, oe_id)
        closedto = _closedto(defaults, self.config.dateformat)
        from .invoice import _date_or_text
        lines = []
        for r in rows:
            disc = _d(r["discount"]) * 100 if r["discount"] else ZERO
            # Perl slaat de korting als real op (0.1 -> 0.100000001...): terug naar wat ingevoerd is
            disc = Decimal(_plain(round_amount(disc, 4))) if disc else ZERO
            lines.append(OrderLine(
                parts_id=r["parts_id"], qty=_d(r["qty"]), sellprice=_d(r["sellprice"]), discount=disc,
                description=r["description"] if r["description"] is not None else "", unit=r["unit"] or "",
                reqdate=_date_or_text(r["reqdate"]), project_id=r["project_id"], serialnumber=r["serialnumber"],
                ordernumber=r["ordernumber"], ponumber=r["ponumber"], itemnotes=r["itemnotes"],
                lineitemdetail=bool(r["lineitemdetail"]), cost=_opt_d(r["cost"]), vendor=r["vendor"],
                vendor_id=r["vendor_id"], ship=_opt_d(r["ship"]), id=r["id"]))
        shipto = {f: st.get(f"shipto{f}") or "" for f in SHIPTO_FIELDS}
        if st.get("shiptorecurring"):
            shipto["recurring"] = "1"
        return Order(
            type=order_type(vc, bool(h["quotation"])), vc_id=h[f"{vc}_id"], transdate=h["transdate"], lines=lines,
            id=oe_id, ordnumber=h["ordnumber"], quonumber=h["quonumber"], ponumber=h["ponumber"],
            reqdate=h["reqdate"], terms=h["terms"] or 0, description=h["description"], notes=h["notes"],
            intnotes=h["intnotes"], currency=(h["curr"] or "").strip() or None,
            exchangerate=_d(h["exchangerate"]) or ONE, taxincluded=bool(h["taxincluded"]),
            department_id=h["department_id"] or None, employee_id=h["employee_id"],
            language_code=h["language_code"], shippingpoint=h["shippingpoint"], shipvia=h["shipvia"],
            waybill=h["waybill"], warehouse_id=h["warehouse_id"], closed=bool(h["closed"]),
            backorder=bool(h["backorder"]), shipto=shipto, printed=printed, emailed=emailed, queued=queued,
            recurring=recurring,
            references=[Reference(description=r["description"], code=r["code"], archive_id=r["archive_id"],
                                  folder=r["folder"], confidential=bool(r["login"])) for r in refs],
            aa_id=h["aa_id"], invnumber=invnumber, amount=_opt_d(h["amount"]), netamount=_opt_d(h["netamount"]),
            locked=bool(closedto and h["transdate"] and _num(h["transdate"]) <= closedto),
            readonly=bool(holder), haslock=holder, prepaid=prepaid)

    def links(self, oe_id: int) -> Dict[str, List[Dict[str, Any]]]:
        """Koppelingen van een order/offerte: waar komt hij vandaan, wat is eruit ontstaan."""
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT l.kind, l.from_id, l.to_id, l.via_id, o.ordnumber, o.quonumber, o.quotation,
                                  o.customer_id
                           FROM x_oe_link l LEFT JOIN oe o ON (o.id = l.from_id)
                           WHERE l.to_id = %s AND l.kind IN ('order', 'backorder') ORDER BY l.id""", (oe_id,))
            came_from = cur.fetchall()
            cur.execute("""SELECT l.kind, l.from_id, l.to_id, l.via_id, o.ordnumber, o.quonumber, o.quotation,
                                  o.customer_id, COALESCE(a.invnumber, p.invnumber) AS invnumber
                           FROM x_oe_link l LEFT JOIN oe o ON (o.id = l.to_id AND l.kind <> 'invoice')
                           LEFT JOIN ar a ON (a.id = l.to_id AND l.kind = 'invoice')
                           LEFT JOIN ap p ON (p.id = l.to_id AND l.kind = 'invoice')
                           WHERE l.from_id = %s ORDER BY l.id""", (oe_id,))
            went_to = cur.fetchall()
        return {"from": came_from, "to": went_to}

    # -------------------------------------------------------------- verwijderen / heropenen

    def delete_order(self, oe_id: int) -> None:
        """Verwijder een order/offerte (OE.pm delete), zonder voorraadbeweging (O3). Een gefactureerde,
        gesloten of afgesloten order wordt geweigerd (O6, ON1). Kwam hij uit een offerte, dan gaat die weer
        open (zoals een gekoppelde order bij het verwijderen van een factuur, F6)."""
        ensure_schema(self.conn)
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            row = self._lock_row(cur, oe_id)
            if not row:
                raise NotFoundError(f"oe {oe_id} not found")
            closedto = _closedto(_db.get_defaults(cur, "closedto"), cfg.dateformat)
            self._editable(cur, row, closedto, deleting=True)
            otype = order_type("customer" if row["customer_id"] else "vendor", bool(row["quotation"]))
            for tb in ("dpt_trans", "inventory", "status", "orderitems", "shipto", "cargo"):
                cur.execute(f"DELETE FROM {tb} WHERE trans_id = %s", (oe_id,))
            for tb in ("recurring", "recurringemail", "recurringprint"):
                cur.execute(f"DELETE FROM {tb} WHERE id = %s", (oe_id,))
            cur.execute("DELETE FROM oe WHERE id = %s", (oe_id,))
            support.audittrail(cur, cfg, trans_id=oe_id, tablename="oe",
                               reference=row["quonumber"] if row["quotation"] else row["ordnumber"], formname=otype,
                               action="deleted")
            support.delete_references(cur, cfg, oe_id)
            cur.execute("SELECT from_id FROM x_oe_link WHERE kind = 'order' AND to_id = %s", (oe_id,))
            for r in cur.fetchall():
                cur.execute("UPDATE oe SET closed = false WHERE id = %s AND aa_id IS NULL", (r["from_id"],))
            cur.execute("DELETE FROM x_oe_link WHERE (kind <> 'invoice' AND to_id = %s) OR from_id = %s",
                        (oe_id, oe_id))
            support.remove_locks(cur, cfg, "oe")

    def reopen_order(self, oe_id: int) -> None:
        """Heropen een gesloten order/offerte zonder factuur (ON1); afgesloten periode blijft dicht."""
        cfg = self.config
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            row = self._lock_row(cur, oe_id)
            if not row:
                raise NotFoundError(f"oe {oe_id} not found")
            if row["aa_id"]:
                raise LockedTransactionError("Order is invoiced")
            closedto = _closedto(_db.get_defaults(cur, "closedto"), cfg.dateformat)
            if closedto and row["transdate"] and _num(row["transdate"]) <= closedto:
                raise ClosedPeriodError("Order is in a closed period")
            if not row["closed"]:
                return
            # een offerte met een vervolg (order of factuur) blijft dicht, anders kan hij twee keer omgezet worden
            # (F30); het vervolg verwijderen heropent hem vanzelf
            if row["quotation"]:
                cur.execute("SELECT 1 FROM x_oe_link WHERE from_id = %s AND kind IN ('order', 'invoice')", (oe_id,))
                if cur.fetchone():
                    raise LockedTransactionError("Quotation has been converted")
            holder = support.lock_holder(cur, cfg, oe_id)
            if holder:
                raise LockedTransactionError(f"Transaction is locked by {holder}")
            cur.execute("UPDATE oe SET closed = false WHERE id = %s", (oe_id,))
            otype = order_type("customer" if row["customer_id"] else "vendor", bool(row["quotation"]))
            support.audittrail(cur, cfg, trans_id=oe_id, tablename="oe",
                               reference=row["quonumber"] if row["quotation"] else row["ordnumber"], formname=otype,
                               action="saved")

    def last_used(self, type: str) -> Optional[Dict[str, Any]]:
        """Relatie en afdeling van de laatste order resp. offerte van deze soort (Form::lastname_used voor oe)."""
        vc, quotation, _, _ = TYPES[type]
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT {vc}_id AS vc_id, department_id FROM oe
                            WHERE id = (SELECT max(id) FROM oe WHERE quotation = %s AND {vc}_id > 0)""", (quotation,))
            return cur.fetchone()

    # -------------------------------------------------------------- omzetten

    def convert(self, source_id: int, target_type: str, today: Optional[date] = None) -> Order:
        """Nieuwe order/offerte uit een bestaande (oe.pl sales_order/purchase_order/quotation/rfq +
        io.pl create_form): regels en kop overgenomen, datum vandaag, leverdatum = datum + termijn, koers op
        vandaag. Bij offerte -> order wordt de bron pas gesloten als de order opgeslagen wordt (O2)."""
        src = self.get_order(source_id)
        if (src.type, target_type) not in CONVERSIONS:
            raise ValidationError(f"Cannot convert {src.type} to {target_type}")
        closes = CONVERSIONS[(src.type, target_type)]
        if closes and (src.closed or src.aa_id):
            raise ValidationError("De offerte is al gesloten of omgezet")
        today = today or date.today()
        d = self.arap.vc_defaults(src.vc, src.vc_id, today)
        o = replace(src, type=target_type, id=None, transdate=today, closed=False, backorder=False,
                    printed=set(), emailed=set(), queued={}, recurring=None, aa_id=None, invnumber=None,
                    amount=None, netamount=None, locked=False, readonly=False, haslock=None, prepaid=False,
                    source_id=source_id if closes else None, references=list(src.references),
                    shipto=dict(src.shipto),
                    lines=[replace(l, id=None, ship=None) for l in src.lines])
        if TYPES[target_type][1]:
            o.quonumber = None
        else:
            o.ordnumber = None
        # io.pl create_form: termijn en btw inbegrepen van de relatie (btw inbegrepen van de bron als die aan
        # staat), leverdatum = datum + termijn; een offerteaanvraag heeft geen termijn (oe.pl form_header)
        o.terms = 0 if target_type == "request_quotation" else int(d["terms"] or 0)
        o.taxincluded = bool(src.taxincluded or d["taxincluded"])
        o.reqdate = today + timedelta(days=int(o.terms or 0))
        o.exchangerate = self._rate(o.currency, today) or ONE
        return o

    def _rate(self, currency: Optional[str], d: date) -> Optional[Decimal]:
        with _db.cursor(self.conn) as cur:
            defcur = _db.default_currency(cur)
            if not currency or currency == defcur:
                return ONE
            cur.execute("SELECT exchangerate FROM exchangerate WHERE curr = %s AND transdate = %s", (currency, d))
            r = cur.fetchone()
            return _d(r["exchangerate"]) if r and r["exchangerate"] else None

    def to_invoice(self, source_id: int, today: Optional[date] = None):
        """Factuur uit een order of offerte (oe.pl invoice + is.pl/ir.pl prepare_invoice): alle regels
        (deellevering: in het factuurscherm aantallen verlagen, ON2), leverdatum = orderleverdatum,
        afleveradres mee, datum vandaag, koers op vandaag. De order sluit pas bij het boeken (O2)."""
        from .invoice import Invoice, InvoiceLine
        src = self.get_order(source_id)
        if src.closed or src.aa_id:
            raise ValidationError("Order is closed")
        today = today or date.today()
        d = self.arap.vc_defaults(src.vc, src.vc_id, today)
        ARAP = "AR" if src.vc == "customer" else "AP"
        acc = self.arap.accounts(src.vc)
        arap_accno = d["arap_accno"] or (acc[ARAP][0]["accno"] if acc[ARAP] else "")
        bank_accno = d["payment_accno"] or (acc[f"{ARAP}_paid"][0]["accno"] if acc[f"{ARAP}_paid"] else None)
        lines = [InvoiceLine(parts_id=l.parts_id, qty=l.qty, sellprice=l.sellprice, discount=l.discount,
                             description=l.description, unit=l.unit,
                             deliverydate=l.reqdate if isinstance(l.reqdate, date) else None,
                             project_id=l.project_id, serialnumber=l.serialnumber, ordernumber=l.ordernumber,
                             ponumber=l.ponumber, itemnotes=l.itemnotes, lineitemdetail=l.lineitemdetail,
                             cost=l.cost if src.vc == "customer" else None,
                             vendor=l.vendor if src.vc == "customer" else None,
                             vendor_id=l.vendor_id if src.vc == "customer" else None, orderitems_id=l.id)
                 for l in src.lines]
        return Invoice(
            vc=src.vc, vc_id=src.vc_id, transdate=today, arap_accno=arap_accno, lines=lines,
            ordnumber=src.ordnumber, quonumber=src.quonumber, ponumber=src.ponumber,
            terms=src.terms or 0, duedate=today + timedelta(days=int(src.terms or 0)), description=src.description,
            notes=src.notes, intnotes=src.intnotes, currency=src.currency,
            exchangerate=self._rate(src.currency, today) or ONE, taxincluded=src.taxincluded,
            department_id=src.department_id, employee_id=src.employee_id, language_code=src.language_code,
            shippingpoint=src.shippingpoint, shipvia=src.shipvia, waybill=src.waybill, shipto=dict(src.shipto),
            bank_accno=bank_accno, cashdiscount=_d(d["cashdiscount"]) * 100 if d["cashdiscount"] else ZERO,
            discountterms=d["discountterms"] or 0, paymentmethod_id=d["paymentmethod_id"],
            references=list(src.references), order_id=source_id)

    # -------------------------------------------------------------- lijst

    def list_orders(self, type: str, *, open: bool = True, closed: bool = False, vc_id: Optional[int] = None,
                    name: str = "", vcnumber: str = "", number: str = "", ponumber: str = "", description: str = "",
                    notes: str = "", memo: str = "", datefrom: Optional[date] = None, dateto: Optional[date] = None,
                    department_id: Optional[int] = None, employee_id: Optional[int] = None,
                    sort: str = "transdate", desc: bool = False) -> List[Dict[str, Any]]:
        """Orders/offertes zoeken (OE.pm transactions): open/gesloten, relatie, nummer, periode."""
        vc, quotation, numfield, _ = TYPES[type]
        where = ["o.quotation = %s", f"o.{vc}_id > 0"]
        params: List[Any] = [quotation]
        if vc_id:
            where.append(f"o.{vc}_id = %s")
            params.append(vc_id)
        if name:
            where.append("lower(ct.name) LIKE %s")
            params.append(like(name.lower()))
        if vcnumber:
            where.append(f"lower(ct.{vc}number) LIKE %s")
            params.append(like(vcnumber.lower()))
        if number:
            where.append(f"lower(o.{numfield}) LIKE %s")
            params.append(like(number.lower()))
            open = closed = True        # Perl: met een nummer altijd open én gesloten
        for col, val in (("ponumber", ponumber), ("description", description), ("notes", notes)):
            if val:
                where.append(f"lower(o.{col}) LIKE %s")
                params.append(like(val.lower()))
        if memo:
            where.append("o.id IN (SELECT trans_id FROM orderitems WHERE lower(description) LIKE %s)")
            params.append(like(memo.lower()))
        if not open and not closed:
            where.append("false")
        elif not (open and closed):
            where.append("o.closed = %s")
            params.append(not open)
        if datefrom:
            where.append("o.transdate >= %s")
            params.append(datefrom)
        if dateto:
            where.append("o.transdate <= %s")
            params.append(dateto)
        if department_id:
            where.append("o.department_id = %s")
            params.append(department_id)
        if employee_id:
            where.append("o.employee_id = %s")
            params.append(employee_id)
        order = {"transdate": f"o.transdate {'DESC' if desc else ''}, o.{numfield}",
                 "number": f"o.{numfield} {'DESC' if desc else ''}, o.transdate",
                 "name": f"ct.name {'DESC' if desc else ''}, o.transdate",
                 "reqdate": f"o.reqdate {'DESC' if desc else ''}, o.transdate"}.get(sort, "o.transdate")
        arap = "ar" if vc == "customer" else "ap"
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT o.id, o.{numfield} AS number, o.ordnumber, o.quonumber, o.transdate, o.reqdate,
                                   o.amount::numeric AS amount, o.netamount::numeric AS netamount, o.curr,
                                   o.closed, o.backorder, o.aa_id, o.ponumber, o.description, o.notes,
                                   o.shippingpoint, o.shipvia, o.waybill, o.exchangerate,
                                   ct.name, ct.{vc}number AS vcnumber, o.{vc}_id AS vc_id, e.name AS employee,
                                   a.invnumber
                            FROM oe o JOIN {vc} ct ON (ct.id = o.{vc}_id)
                            LEFT JOIN employee e ON (e.id = o.employee_id)
                            LEFT JOIN {arap} a ON (a.id = o.aa_id)
                            WHERE {' AND '.join(where)}
                            ORDER BY {order}, o.id""", params)
            rows = cur.fetchall()
        for r in rows:
            r["amount"] = _d(r["amount"])
            r["netamount"] = _d(r["netamount"])
            r["tax"] = r["amount"] - r["netamount"]
            r["curr"] = (r["curr"] or "").strip()
        return rows


HEAD_TEXT = ("ordnumber", "quonumber", "ponumber", "description", "notes", "intnotes", "shippingpoint", "shipvia",
             "waybill", "language_code")
LINE_TEXT = ("description", "unit", "serialnumber", "itemnotes", "ordernumber", "ponumber", "vendor")


def _keep_blanks(cur, o: Order) -> None:
    """Een leeg tekstveld blijft zoals het opgeslagen was ('' of NULL): het scherm kent het verschil niet, en
    ongewijzigd opslaan moet identiek zijn (O6). Regels via hun oude regel-id."""
    cur.execute(f"SELECT {', '.join(HEAD_TEXT)} FROM oe WHERE id = %s", (o.id,))
    old = cur.fetchone() or {}
    for k in HEAD_TEXT:
        if getattr(o, k) in ("", None) and k in old and old[k] in ("", None):
            setattr(o, k, old[k])
    ids = [l.id for l in o.lines if l.id]
    if not ids:
        return
    cur.execute(f"SELECT id, {', '.join(LINE_TEXT)} FROM orderitems WHERE trans_id = %s AND id = ANY(%s)", (o.id, ids))
    rows = {r["id"]: r for r in cur.fetchall()}
    for l in o.lines:
        r = rows.get(l.id)
        if r:
            for k in LINE_TEXT:
                if getattr(l, k) in ("", None) and r[k] in ("", None):
                    setattr(l, k, r[k])


# ------------------------------------------------------------------ factuur boeken (aangeroepen uit invoice.py)

def fingerprint(cur, oe_id: int) -> str:
    """Inhoud van een order (kop + regels) als vingerafdruk: is een nabestelling nog onaangeroerd (ON3)?"""
    cur.execute("""SELECT transdate, amount::numeric AS amount, netamount::numeric AS netamount, closed, aa_id,
                          ordnumber, curr, taxincluded
                   FROM oe WHERE id = %s""", (oe_id,))
    h = cur.fetchone()
    cur.execute("""SELECT parts_id, description, qty::numeric AS qty, sellprice::numeric AS sellprice, discount,
                          reqdate, project_id, ship::numeric AS ship
                   FROM orderitems WHERE trans_id = %s ORDER BY id""", (oe_id,))
    data = repr((sorted((h or {}).items()), [sorted(r.items()) for r in cur.fetchall()]))
    return hashlib.sha256(data.encode()).hexdigest()


def close_for_invoice(cur, config: UserConfig, order_id: int, inv, trans_id: int) -> None:
    """Een nieuwe factuur uit een order/offerte is geboekt (O2, O4, ON2, ON4): bron sluiten, bij een order
    ``aa_id`` en per regel ``ship`` = gefactureerd aantal, en wat niet gefactureerd is als nabestelling."""
    cur.execute("""SELECT id, customer_id, vendor_id, quotation, closed, aa_id, transdate, ordnumber, quonumber
                   FROM oe WHERE id = %s FOR UPDATE""", (order_id,))
    row = cur.fetchone()
    if not row:
        raise NotFoundError(f"oe {order_id} not found")
    vc = "customer" if row["customer_id"] else "vendor"
    otype = order_type(vc, bool(row["quotation"]))
    if vc != inv.vc:
        raise ValidationError("Order en factuur horen bij een andere soort relatie")
    if inv.type != "invoice":
        raise ValidationError("Een order kan alleen naar een factuur (geen creditnota) omgezet worden")
    if row["closed"] or row["aa_id"]:
        raise ValidationError("Order is closed")
    if row["quotation"]:
        cur.execute("UPDATE oe SET closed = true WHERE id = %s", (order_id,))
        _link(cur, config, "invoice", order_id, trans_id)
        return
    # gefactureerd aantal per orderregel: via de regelkoppeling uit het scherm, anders op artikel (aantallen
    # staan in factuur en order allebei positief, ook bij inkoop)
    cur.execute("""SELECT id, parts_id, qty::numeric AS qty FROM orderitems WHERE trans_id = %s ORDER BY id""",
                (order_id,))
    items = cur.fetchall()
    billed: Dict[int, Decimal] = {}
    loose: Dict[int, Decimal] = {}
    ids = {it["id"] for it in items}
    for l in inv.lines:
        if not l.parts_id or not _d(l.qty):
            continue
        q = _d(l.qty)
        oid = getattr(l, "orderitems_id", None)
        if oid in ids:
            billed[oid] = billed.get(oid, ZERO) + q
        else:
            loose[int(l.parts_id)] = loose.get(int(l.parts_id), ZERO) + q
    remaining: List[tuple] = []
    for it in items:
        qty = _d(it["qty"])
        got = billed.get(it["id"], ZERO)
        if loose.get(it["parts_id"]):
            take = loose[it["parts_id"]]
            need = qty - got
            if need and (need > 0) == (take > 0):
                t = min(abs(need), abs(take)) * (1 if need > 0 else -1)
                got += t
                loose[it["parts_id"]] -= t
        # niet meer dan besteld als geleverd; teken volgt de orderregel
        if qty >= 0:
            ship = max(ZERO, min(got, qty))
        else:
            ship = min(ZERO, max(got, qty))
        cur.execute("UPDATE orderitems SET ship = %s WHERE id = %s", (ship, it["id"]))
        if qty - ship:
            remaining.append((it["id"], qty - ship))
    cur.execute("UPDATE oe SET closed = true, aa_id = %s WHERE id = %s", (trans_id, order_id))
    _link(cur, config, "invoice", order_id, trans_id)
    if remaining:
        bo = _create_backorder(cur, config, order_id, remaining)
        _link(cur, config, "backorder", order_id, bo, via_id=trans_id, fingerprint=fingerprint(cur, bo))


def _link(cur, config: UserConfig, kind: str, from_id: int, to_id: int, via_id: Optional[int] = None,
          fingerprint: Optional[str] = None) -> None:
    cur.execute("""INSERT INTO x_oe_link (kind, from_id, to_id, via_id, fingerprint, login)
                   VALUES (%s, %s, %s, %s, %s, %s)""", (kind, from_id, to_id, via_id, fingerprint, config.login))


def _create_backorder(cur, config: UserConfig, order_id: int, remaining: List[tuple]) -> int:
    """Nabestelling (oe.pl create_backorder): kop van de order (zelfde nummer en datum), regels met het
    restant, verzonden 0, ``backorder`` aan. Herhaalschema, status en referenties gaan niet mee."""
    cur.execute("""INSERT INTO oe (ordnumber, transdate, vendor_id, customer_id, reqdate, taxincluded, shippingpoint,
                                   notes, curr, employee_id, closed, quotation, quonumber, intnotes, department_id,
                                   shipvia, language_code, ponumber, terms, waybill, warehouse_id, description,
                                   exchangerate, backorder)
                   SELECT ordnumber, transdate, vendor_id, customer_id, reqdate, taxincluded, shippingpoint, notes,
                          curr, employee_id, false, false, quonumber, intnotes, department_id, shipvia,
                          language_code, ponumber, terms, waybill, warehouse_id, description, exchangerate, true
                   FROM oe WHERE id = %s RETURNING id""", (order_id,))
    bo = cur.fetchone()["id"]
    cur.execute("SELECT department_id, customer_id, vendor_id, taxincluded, transdate FROM oe WHERE id = %s", (bo,))
    h = cur.fetchone()
    if h["department_id"]:
        cur.execute("INSERT INTO dpt_trans (trans_id, department_id) VALUES (%s, %s)", (bo, h["department_id"]))
    cur.execute("""INSERT INTO shipto (trans_id, shiptoname, shiptoaddress1, shiptoaddress2, shiptocity, shiptostate,
                                       shiptozipcode, shiptocountry, shiptocontact, shiptophone, shiptofax,
                                       shiptoemail, shiptorecurring)
                   SELECT %s, shiptoname, shiptoaddress1, shiptoaddress2, shiptocity, shiptostate, shiptozipcode,
                          shiptocountry, shiptocontact, shiptophone, shiptofax, shiptoemail, shiptorecurring
                   FROM shipto WHERE trans_id = %s""", (bo, order_id))
    rest = dict(remaining)
    cur.execute("SELECT * FROM orderitems WHERE trans_id = %s ORDER BY id", (order_id,))
    for it in cur.fetchall():
        if it["id"] not in rest:
            continue
        cur.execute("""INSERT INTO orderitems (trans_id, parts_id, description, qty, sellprice, discount, unit, reqdate,
                                               project_id, ship, serialnumber, ordernumber, ponumber, itemnotes,
                                               lineitemdetail, cost, vendor, vendor_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (bo, it["parts_id"], it["description"], rest[it["id"]], it["sellprice"], it["discount"],
                     it["unit"], it["reqdate"], it["project_id"], it["serialnumber"], it["ordernumber"],
                     it["ponumber"], it["itemnotes"], it["lineitemdetail"], it["cost"], it["vendor"],
                     it["vendor_id"]))
    # bedragen opnieuw rekenen zoals bij opslaan
    vc = "customer" if h["customer_id"] else "vendor"
    conn = cur.connection
    ol = OrderLedger(conn, config)
    o = Order(type=order_type(vc, False), vc_id=h["customer_id"] or h["vendor_id"], transdate=h["transdate"],
              taxincluded=bool(h["taxincluded"]))
    cur.execute("SELECT parts_id, qty::numeric AS qty, sellprice::numeric AS sellprice, discount FROM orderitems "
                "WHERE trans_id = %s ORDER BY id", (bo,))
    for it in cur.fetchall():
        disc = Decimal(_plain(round_amount(_d(it["discount"]) * 100, 4))) if it["discount"] else ZERO
        o.lines.append(OrderLine(parts_id=it["parts_id"], qty=_d(it["qty"]), sellprice=_d(it["sellprice"]),
                                 discount=disc))
    calc = ol.calculate(o, None, cur)
    cur.execute("UPDATE oe SET amount = %s, netamount = %s WHERE id = %s", (calc.amount, calc.netamount, bo))
    cur.execute("SELECT ordnumber FROM oe WHERE id = %s", (bo,))
    support.audittrail(cur, config, trans_id=bo, tablename="oe", reference=cur.fetchone()["ordnumber"],
                       formname=o.type, action="saved")
    return bo


def reopen_for_deleted_invoice(cur, config: UserConfig, trans_id: int) -> List[str]:
    """De factuur ``trans_id`` wordt verwijderd (F6, ON3): gekoppelde order/offerte weer open (verzonden
    aantallen terug op 0), en een nabestelling die bij deze factuur ontstond verdwijnt als hij nog open en
    onaangeroerd is. Geeft meldingen terug voor wat blijft staan."""
    msgs: List[str] = []
    cur.execute("SELECT id FROM oe WHERE aa_id = %s", (trans_id,))
    for r in cur.fetchall():
        cur.execute("UPDATE orderitems SET ship = 0 WHERE trans_id = %s", (r["id"],))
    cur.execute("UPDATE oe SET aa_id = NULL, closed = false WHERE aa_id = %s", (trans_id,))
    cur.execute("SELECT to_regclass('x_oe_link') AS t")
    if not cur.fetchone()["t"]:
        return msgs
    cur.execute("SELECT from_id FROM x_oe_link WHERE kind = 'invoice' AND to_id = %s", (trans_id,))
    for r in cur.fetchall():
        cur.execute("UPDATE oe SET closed = false WHERE id = %s AND quotation", (r["from_id"],))
    cur.execute("SELECT id, to_id, fingerprint FROM x_oe_link WHERE kind = 'backorder' AND via_id = %s", (trans_id,))
    for link in cur.fetchall():
        bo = link["to_id"]
        cur.execute("""SELECT ordnumber, closed, aa_id,
                              CASE WHEN customer_id > 0 THEN 'sales_order' ELSE 'purchase_order' END AS type
                       FROM oe WHERE id = %s FOR UPDATE""", (bo,))
        b = cur.fetchone()
        if not b:
            continue
        holder = support.lock_holder(cur, config, bo)
        if not b["closed"] and not b["aa_id"] and not holder and fingerprint(cur, bo) == link["fingerprint"]:
            for tb in ("dpt_trans", "inventory", "status", "orderitems", "shipto", "cargo"):
                cur.execute(f"DELETE FROM {tb} WHERE trans_id = %s", (bo,))
            for tb in ("recurring", "recurringemail", "recurringprint"):
                cur.execute(f"DELETE FROM {tb} WHERE id = %s", (bo,))
            support.delete_references(cur, config, bo)
            cur.execute("DELETE FROM oe WHERE id = %s", (bo,))
            cur.execute("DELETE FROM x_oe_link WHERE to_id = %s OR from_id = %s", (bo, bo))
            support.audittrail(cur, config, trans_id=bo, tablename="oe", reference=b["ordnumber"],
                               formname=b["type"], action="deleted")
        else:
            msgs.append(f"Backorder {b['ordnumber']} kept ({bo})")
    cur.execute("DELETE FROM x_oe_link WHERE kind = 'invoice' AND to_id = %s", (trans_id,))
    return msgs
