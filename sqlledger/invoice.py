"""Verkoop- en inkoopfacturen met artikelen (port van SL/IS.pm en SL/IR.pm, fase 3 F1–F9, SA7).

Een factuur is een rij in ``ar``/``ap`` met ``invoice = true`` en regels in ``invoice``. Bij
inkoop slaat SQL-Ledger de aantallen negatief op; een creditnota (``credit_invoice`` bij klanten,
``debit_invoice`` bij leveranciers) draait het teken van de aantallen om. Regelbedragen, btw,
koersverschillen en betalingen worden gerekend zoals IS/IR ``post_invoice``; de kostprijs (FIFO)
staat in ``sqlledger.cogs``.

Verschillen met Perl (zie README "Bewuste afwijkingen"):

* Btw-tarieven altijd uit de database (btw van het artikel ∩ btw van de relatie, geldig op de
  factuurdatum), nooit uit het formulier (F2).
* Afgesloten periode op de oude én de nieuwe datum en bij verwijderen (F6); lock van een ander,
  batches en alleen-lezen worden geweigerd.
* Afrondingsverschillen (btw inbegrepen, valuta) gaan naar de grootste omzet-/inkoopregel, nooit
  naar een kostprijsregel; kostprijs en retouren zie ``sqlledger.cogs``.
* Btw inbegrepen + vreemde valuta: de opgeslagen nettoprijs (``invoice.sellprice``) trekt de btw in
  de valuta van de factuur af (Perl trok de btw in standaardvaluta af van de prijs in vreemde valuta).
* Inkoop: de opgeslagen nettoprijs houdt de decimalen van de ingevoerde prijs (minstens de standaard),
  zoals bij verkoop; ir.pl rondde af op 2 decimalen, waardoor de kostprijs (FIFO) fout werd (F3).
* Volledig betaald met btw inbegrepen: betaald = factuurbedrag (Perl liet 0,01 open staan).
* ``lastcost``/``avgcost`` worden binnen de transactie bijgewerkt, ook bij verwijderen (F8); ``bin``
  en gewicht van het artikel worden niet overschreven; een vervallen artikel blijft vervallen bij
  opnieuw opslaan (Perl zette het weer actief).
* Samengestelde artikelen: verkopen zoals Perl (per onderdeel een regel ``assemblyitem`` met FIFO-
  kostprijs); een creditnota zet de onderdelen terug tegen hun oorspronkelijke kostprijs (SA7; Perl
  weigerde in het scherm en rekende in de bibliotheek fout). Inkopen van een samengesteld artikel en
  kits worden geweigerd.
* Alle SQL geparametriseerd.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg

from . import _db, support
from .amounts import _plain, round_amount, to_decimal
from .arap import ArapLedger, ArapPayment, _save_status, format_dcn
from .assembly import Bom
from .cogs import CogsEngine
from .config import UserConfig
from .errors import (ClosedPeriodError, LockedTransactionError, NotFoundError,
                     ValidationError)
from .extensions import ensure_schema
from .gl import _closedto, _num, like
from .models import Recurring, Reference
from .numbering import next_number

ZERO = Decimal(0)
ONE = Decimal(1)
#: maximaal aantal decimalen voor aantallen en prijzen (zoals artikelen)
DECIMALS = 6
SHIPTO_FIELDS = ("name", "address1", "address2", "city", "state", "zipcode", "country", "contact",
                 "phone", "fax", "email")


@dataclass
class InvoiceLine:
    """Factuurregel zoals ingevoerd: aantal en prijs positief (een creditnota draait het teken om;
    een negatief aantal op een gewone factuur is een retourregel)."""

    parts_id: Optional[int]
    qty: Decimal = ZERO
    sellprice: Decimal = ZERO             # prijs per stuk in de valuta van de factuur, vóór korting
    discount: Decimal = ZERO              # korting in procenten
    description: Optional[str] = None     # leeg = omschrijving van het artikel
    unit: Optional[str] = None
    deliverydate: Optional[date] = None
    project_id: Optional[int] = None
    serialnumber: Optional[str] = None
    ordernumber: Optional[str] = None
    ponumber: Optional[str] = None        # inkoopordernummer van de klant (customerponumber)
    itemnotes: Optional[str] = None
    lineitemdetail: bool = False
    cost: Optional[Decimal] = None        # alleen verkoop: kostprijs/leverancier per regel (bewaard, F8)
    vendor: Optional[str] = None
    vendor_id: Optional[int] = None       # Perl: 0 bij een nieuwe regel
    id: Optional[int] = None              # bestaande regel (invoice.id), om wijzigingen te herkennen
    orderitems_id: Optional[int] = None   # omzetten: orderregel waar deze regel uit komt (niet opgeslagen; O4)


@dataclass
class Invoice:
    vc: str
    vc_id: int
    transdate: date
    arap_accno: str
    lines: List[InvoiceLine]
    type: str = "invoice"                 # invoice | credit_invoice (klant) | debit_invoice (leverancier)
    id: Optional[int] = None
    invnumber: Optional[str] = None
    ordnumber: Optional[str] = None
    quonumber: Optional[str] = None
    ponumber: Optional[str] = None
    duedate: Optional[date] = None
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
    payments: List[ArapPayment] = field(default_factory=list)
    discount: Optional[ArapPayment] = None
    cashdiscount: Decimal = ZERO          # percentage
    discountterms: int = 0
    onhold: bool = False
    bank_accno: Optional[str] = None
    paymentmethod_id: Optional[int] = None
    dcn: Optional[str] = None
    shipto: Dict[str, str] = field(default_factory=dict)
    printed: Set[str] = field(default_factory=set)
    emailed: Set[str] = field(default_factory=set)
    queued: Dict[str, str] = field(default_factory=dict)
    recurring: Optional[Recurring] = None
    references: List[Reference] = field(default_factory=list)
    order_id: Optional[int] = None        # order/offerte die met deze nieuwe factuur gefactureerd wordt (O2, ON4)
    #: btw-rekeningen met btw op de opgeslagen factuur: tellen mee ook als de relatie ze niet (meer) heeft
    taxaccounts_extra: Set[str] = field(default_factory=set)
    # alleen lezen (get_invoice)
    oldinvtotal: Optional[Decimal] = None
    taxrates: Dict[str, Decimal] = field(default_factory=dict)
    till: Optional[str] = None
    warehouse_id: Optional[int] = None
    locked: bool = False
    readonly: bool = False
    haslock: Optional[str] = None
    batch: bool = False

    @property
    def table(self) -> str:
        return "ar" if self.vc == "customer" else "ap"

    @property
    def credit(self) -> bool:
        return self.type in ("credit_invoice", "debit_invoice")


@dataclass
class LineCalc:
    """Doorgerekende regel (IS/IR post_invoice)."""

    index: int                            # positie in Invoice.lines
    part: Dict[str, Any]
    qty: Decimal                          # met teken zoals in invoice opgeslagen (inkoop: negatief)
    fxsellprice: Decimal
    discount: Decimal                     # fractie (0.1 = 10%)
    sellprice: Decimal                    # netto stuksprijs in standaardvaluta (invoice.sellprice)
    fxlinetotal: Decimal
    grossamount: Decimal
    amount: Decimal                       # regelbedrag in standaardvaluta, zonder btw (vóór afrondingsverschil)
    taxaccounts: List[str]
    final: Decimal = ZERO                 # geboekt bedrag (incl. afrondingsverschil)
    steady: bool = False                  # bestaande regel, ongewijzigd: toewijzingen blijven staan


@dataclass
class InvoiceCalc:
    lines: List[LineCalc]
    taxes: Dict[str, Decimal]             # btw per rekening, standaardvaluta (zoals geboekt)
    fxtaxes: Dict[str, Decimal]           # btw per rekening in de valuta van de factuur (scherm)
    fxsubtotal: Decimal                   # som regels (valuta)
    fxtotal: Decimal                      # factuurtotaal (valuta) zoals het scherm het toont
    fxpaytotal: Decimal                   # totaal waartegen "volledig betaald" wordt getoetst
    invnetamount: Decimal                 # standaardvaluta
    invamount: Decimal
    fxamount: Decimal
    fxtax: Decimal
    rawtax: Dict[str, Tuple[Decimal, Decimal]]
    warnings: List[str] = field(default_factory=list)


def _d(v: Any) -> Decimal:
    return to_decimal(v)


def _decimals(v: Decimal) -> int:
    s = _plain(v)
    return len(s.split(".", 1)[1]) if "." in s else 0


class InvoiceLedger:
    """Facturen met artikelen: doorrekenen, boeken, laden en verwijderen."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config
        self.arap = ArapLedger(conn, config)

    # ------------------------------------------------------------ artikelen

    def parts_info(self, cur, ids) -> Dict[int, Dict[str, Any]]:
        ids = sorted({int(i) for i in ids if i})
        if not ids:
            return {}
        cur.execute("""SELECT p.id, p.partnumber, p.description, p.unit, p.sellprice::numeric AS sellprice,
                              p.listprice::numeric AS listprice, p.lastcost::numeric AS lastcost,
                              p.avgcost::numeric AS avgcost, p.onhand::numeric AS onhand, p.assembly, p.obsolete,
                              p.checkinventory, p.inventory_accno_id, p.income_accno_id, p.expense_accno_id,
                              p.project_id, p.bin, p.notes
                       FROM parts p WHERE p.id = ANY(%s)""", (ids,))
        out = {r["id"]: r for r in cur.fetchall()}
        cur.execute("""SELECT pt.parts_id, c.accno FROM partstax pt JOIN chart c ON (c.id = pt.chart_id)
                       WHERE pt.parts_id = ANY(%s) ORDER BY c.accno""", (ids,))
        for r in out.values():
            r["taxaccounts"] = []
            for k in ("sellprice", "listprice", "lastcost", "avgcost", "onhand"):
                r[k] = _d(r[k])
        for r in cur.fetchall():
            out[r["parts_id"]]["taxaccounts"].append(r["accno"])
        return out

    def find_items(self, vc: str, text: str = "", *, partnumber: str = "", description: str = "",
                   limit: int = 50) -> List[Dict[str, Any]]:
        """Artikelen voor een factuurregel (IS/IR retrieve_item): niet vervallen, nummer of barcode
        of omschrijving bevat de tekst. Verkoop: alleen met opbrengstrekening (geen loon/overhead,
        geen kits); inkoop: geen samengestelde artikelen of kits."""
        where, params = ["NOT p.obsolete"], []
        if partnumber:
            where.append("(lower(p.partnumber) LIKE %s OR p.barcode LIKE %s)")
            params += [like(partnumber.lower()), like(partnumber)]
        if description:
            where.append("lower(p.description) LIKE %s")
            params.append(like(description.lower()))
        if text:
            where.append("(lower(p.partnumber) LIKE %s OR p.barcode LIKE %s OR lower(p.description) LIKE %s)")
            params += [like(text.lower()), like(text), like(text.lower())]
        if vc == "customer":
            where.append("p.income_accno_id IS NOT NULL")
        else:
            where.append("NOT COALESCE(p.assembly, false)")
            where.append("(p.inventory_accno_id IS NOT NULL OR p.expense_accno_id IS NOT NULL)")
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT p.id FROM parts p WHERE {' AND '.join(where)}
                            ORDER BY {'p.description' if description and not partnumber else 'p.partnumber'}, p.id
                            LIMIT %s""", params + [limit])
            ids = [r["id"] for r in cur.fetchall()]
            info = self.parts_info(cur, ids)
        return [info[i] for i in ids]

    def item_defaults(self, vc: str, parts_id: int, exchangerate: Any = None) -> Dict[str, Any]:
        """Voorinvulling van een gekozen artikel (is.pl/ir.pl update): omschrijving, eenheid, prijs
        (verkoop: verkoopprijs, inkoop: laatste inkoopprijs) en kostprijs omgerekend naar de valuta van
        de factuur (afgerond op de decimalen van de prijs, minstens de standaard), btw-rekeningen."""
        with _db.cursor(self.conn) as cur:
            info = self.parts_info(cur, [parts_id])
            precision = _db.get_precision(cur)
        if parts_id not in info:
            raise NotFoundError(f"part {parts_id} not found")
        p = info[parts_id]
        rate = _d(exchangerate) or ONE

        def fx(v: Decimal) -> Decimal:
            return round_amount(v / rate, max(_decimals(v), precision)) if rate != ONE else v
        price = p["sellprice"] if vc == "customer" else p["lastcost"]
        return {**p, "price": fx(price), "cost": fx(p["lastcost"]) if vc == "customer" else ZERO}

    # ------------------------------------------------------------- rekenen

    def tax_rates(self, inv: Invoice) -> Dict[str, Decimal]:
        """Btw-tarieven van de relatie op de factuurdatum (F2), plus de btw-rekeningen die al op de
        factuur stonden (zoals bij verkoop/inkoop zonder artikelen: Perl liet die bij opnieuw opslaan
        weg als de relatie ze niet meer had, waardoor het factuurbedrag veranderde)."""
        rates = dict(self.arap.vc_defaults(inv.vc, inv.vc_id, inv.transdate)["taxrates"])
        extra = sorted(set(inv.taxaccounts_extra) - set(rates))
        if extra:
            with _db.cursor(self.conn) as cur:
                cur.execute("""SELECT c.accno, t.rate FROM chart c JOIN tax t ON (c.id = t.chart_id)
                               WHERE c.accno = ANY(%s) AND (t.validto >= %s OR t.validto IS NULL)
                               ORDER BY c.accno, t.validto""", (extra, inv.transdate))
                for r in cur.fetchall():
                    rates.setdefault(r["accno"], to_decimal(r["rate"]))
        return rates

    def calculate(self, inv: Invoice, rates: Optional[Dict[str, Decimal]] = None, _cur=None) -> InvoiceCalc:
        """Reken de factuur door zoals IS/IR post_invoice (zonder te boeken): regelbedragen, btw per
        rekening, afrondingsverschil op de grootste regel, totalen."""
        if _cur is None:
            with _db.cursor(self.conn) as cur:
                return self.calculate(inv, rates, cur)
        cur = _cur
        precision = _db.get_precision(cur)
        if rates is None:
            rates = self.tax_rates(inv)
        ar = inv.vc == "customer"
        sw = -1 if inv.credit else 1
        rate = _d(inv.exchangerate) or ONE
        parts = self.parts_info(cur, [l.parts_id for l in inv.lines])
        acc: Dict[str, List[Decimal]] = {}
        out: List[LineCalc] = []
        diff = fxdiff = fxtax = ZERO
        for idx, l in enumerate(inv.lines):
            if not l.parts_id:
                continue
            part = parts.get(int(l.parts_id))
            if part is None:
                raise ValidationError(f"Onbekend artikel {l.parts_id}")
            qty = _d(l.qty) * sw
            if not qty:
                continue
            fxsellprice = _d(l.sellprice)
            # IS: decimalen van de prijs (minstens de standaard); IR rondde korting en opgeslagen prijs af op de
            # standaard, waardoor 0,05316 als 0,05 de kostprijs in ging (F3): de prijs houdt hier zijn decimalen
            decimalplaces = max(_decimals(fxsellprice), precision)
            pct = _d(l.discount)
            discount = round_amount(fxsellprice * pct / 100, decimalplaces if ar else precision)
            sellprice = fxsellprice - discount
            fxlinetotal = round_amount(sellprice * qty, precision)
            amount = fxlinetotal * rate
            linetotal = round_amount(amount, precision)
            fxdiff += round_amount(amount - linetotal, 10)
            taxaccounts = list(part["taxaccounts"])
            tax = ZERO
            for ml in (1, -1):
                taxrate = sum((rates[a] for a in taxaccounts if a in rates and rates[a] * ml > 0), ZERO)
                if inv.taxincluded:
                    amt = linetotal * (taxrate / (1 + taxrate * ml))
                    tax += amt
                    fxamt = fxlinetotal * (taxrate / (1 + taxrate * ml))
                    sellprice -= fxamt / qty            # Perl: base-btw van fx-prijs (fout bij valuta)
                    fxtax += fxamt
                else:
                    amt = linetotal * taxrate
                    tax += amt
                    fxamt = fxlinetotal * taxrate
                    fxtax += fxamt
                for a in taxaccounts:
                    r = rates.get(a)
                    if r is not None and r * ml > 0 and taxrate != 0:
                        slot = acc.setdefault(a, [ZERO, ZERO])
                        slot[0] += amt * r / taxrate
                        slot[1] += fxamt * r / taxrate
            grossamount = round_amount(linetotal, precision)
            if inv.taxincluded:
                amt = round_amount(tax, precision)
                linetotal -= round_amount(tax - diff, precision)
                diff = amt - tax
            out.append(LineCalc(index=idx, part=part, qty=qty if ar else -qty, fxsellprice=fxsellprice,
                                discount=pct / 100, sellprice=round_amount(sellprice * rate, decimalplaces),
                                fxlinetotal=fxlinetotal, grossamount=grossamount,
                                amount=round_amount(linetotal, precision), taxaccounts=taxaccounts))
        invnetamount = sum((c.amount for c in out), ZERO)
        grossamount = sum((c.grossamount for c in out), ZERO)
        fxamount = sum((c.fxlinetotal for c in out), ZERO)
        taxes: Dict[str, Decimal] = {}
        fxtaxes: Dict[str, Decimal] = {}
        for a in sorted(acc):
            v = acc[a][0] if ar else round_amount(acc[a][0], 10)
            taxes[a] = round_amount(v, precision)
            fxtaxes[a] = round_amount(acc[a][1], precision)
        invamount = invnetamount + sum(taxes.values(), ZERO)
        d = round_amount(grossamount - invamount, precision) if inv.taxincluded else ZERO
        invamount += d
        if len(out) == 1:
            fxdiff = ZERO
        fxdiff = round_amount(fxdiff, precision)
        invnetamount += fxdiff
        invamount += fxdiff
        for c in out:
            c.final = c.amount
        if out:
            # Perl: sort { $b->{amount} <=> $a->{amount} } (stabiel); eerste krijgt de verschillen
            first = sorted(out, key=lambda c: -c.amount)[0]
            first.final = first.amount + d + fxdiff
        fxtax = round_amount(fxtax, precision)
        fxtotal = fxamount if inv.taxincluded else fxamount + sum(fxtaxes.values(), ZERO)
        return InvoiceCalc(lines=out, taxes=taxes, fxtaxes=fxtaxes, fxsubtotal=fxamount, fxtotal=fxtotal,
                           fxpaytotal=fxamount if inv.taxincluded else fxamount + fxtax,
                           invnetamount=invnetamount, invamount=invamount, fxamount=fxamount, fxtax=fxtax,
                           rawtax={a: (v[0], v[1]) for a, v in acc.items()})

    # ------------------------------------------------------------ validatie

    def _validate(self, cur, inv: Invoice, defaultcurrency: str) -> Dict[str, Any]:
        cfg = self.config
        if inv.vc not in ("customer", "vendor"):
            raise ValidationError("vc must be 'customer' or 'vendor'")
        valid_types = ("invoice", "credit_invoice") if inv.vc == "customer" else ("invoice", "debit_invoice")
        if inv.type not in valid_types:
            raise ValidationError(f"Unknown invoice type {inv.type}")
        if not inv.transdate:
            raise ValidationError("Invoice Date missing!")
        if not inv.vc_id:
            raise ValidationError("Customer missing!" if inv.vc == "customer" else "Vendor missing!")
        cur.execute(f"SELECT id FROM {inv.vc} WHERE id = %s", (inv.vc_id,))
        if not cur.fetchone():
            raise ValidationError(f"Unknown {inv.vc} {inv.vc_id}")
        defaults = _db.get_defaults(cur, "closedto", "revtrans", "precision", "fx%_accno_id", "cdt")
        closedto = _closedto(defaults, cfg.dateformat)
        if closedto and _num(inv.transdate) <= closedto:
            raise ClosedPeriodError("Cannot post invoice for a closed period!")
        currency = inv.currency or defaultcurrency
        if currency != defaultcurrency and not _d(inv.exchangerate):
            raise ValidationError("Exchange rate missing!")
        if inv.id:
            cur.execute(f"SELECT transdate, till FROM {inv.table} WHERE id = %s", (inv.id,))
            old = cur.fetchone()
            if old:
                if defaults.get("revtrans") not in (None, "", "0"):
                    raise LockedTransactionError("Transaction is locked (revtrans)")
                if closedto and _num(old["transdate"]) <= closedto:
                    raise LockedTransactionError("Cannot change a transaction in a closed period!")
                if inv.table == "ar" and old["till"]:
                    raise ValidationError("Kassabonnen (POS) worden nog niet ondersteund")
            cur.execute("SELECT 1 FROM vr WHERE trans_id = %s", (inv.id,))
            if cur.fetchone():
                raise ValidationError("Transactions in batches are not supported")
            holder = support.lock_holder(cur, cfg, inv.id)
            if holder:
                raise LockedTransactionError(f"Transaction is locked by {holder}")
        precision = int(_d(defaults.get("precision") or 2))
        amounts = [p.amount for p in inv.payments] + ([inv.discount.amount] if inv.discount else [])
        for a in amounts:
            a = _d(a)
            if round_amount(a, precision) != a:
                raise ValidationError(f"Amount has more than {precision} decimals")
        n = 0
        for i, l in enumerate(inv.lines, 1):
            if not l.parts_id:
                continue
            for name, v in (("qty", l.qty), ("sellprice", l.sellprice), ("discount", l.discount)):
                v = _d(v)
                if round_amount(v, DECIMALS) != v:
                    raise ValidationError(f"Regel {i}: meer dan {DECIMALS} decimalen")
            if not 0 <= _d(l.discount) <= 100:
                raise ValidationError(f"Regel {i}: korting moet tussen 0 en 100 liggen")
            if _d(l.qty):
                n += 1
        for p in inv.payments:
            if _d(p.amount):
                if not p.datepaid:
                    raise ValidationError("Payment date missing!")
                if closedto and _num(p.datepaid) <= closedto:
                    raise ClosedPeriodError("Cannot post payment for a closed period!")
                if currency != defaultcurrency and not _d(p.exchangerate) and p.datepaid != inv.transdate:
                    raise ValidationError("Exchange rate for payment missing!")
        if inv.discount and _d(inv.discount.amount):
            d = inv.discount
            if not d.datepaid:
                raise ValidationError("Cash discount date missing!")
            if closedto and _num(d.datepaid) <= closedto:
                raise ClosedPeriodError("Cannot post cash discount for a closed period!")
            if d.datepaid > inv.transdate + timedelta(days=int(inv.discountterms or 0)):
                raise ValidationError("Cash discount date past due!")
        accnos = {inv.arap_accno} | {p.accno for p in inv.payments if _d(p.amount)} | \
            ({inv.discount.accno} if inv.discount and _d(inv.discount.amount) else set())
        if inv.bank_accno:
            accnos.add(inv.bank_accno)
        cur.execute("SELECT accno FROM chart WHERE accno = ANY(%s) AND charttype = 'A'", (sorted(a for a in accnos if a),))
        known = {r["accno"] for r in cur.fetchall()}
        missing = sorted(a for a in accnos if a and a not in known)
        if missing:
            raise ValidationError(f"Unknown account(s): {', '.join(missing)}")
        if not inv.arap_accno:
            raise ValidationError("Debiteuren-/crediteurenrekening ontbreekt")
        return defaults

    def _check_parts(self, inv: Invoice, calc: InvoiceCalc) -> None:
        ar = inv.vc == "customer"
        for c in calc.lines:
            p = c.part
            n = c.index + 1
            kit = not (p["inventory_accno_id"] or p["income_accno_id"] or p["expense_accno_id"])
            if kit:
                raise ValidationError(f"Regel {n}: kits worden (nog) niet ondersteund")
            if ar and not p["income_accno_id"]:
                raise ValidationError(f"Regel {n}: artikel {p['partnumber']} heeft geen opbrengstrekening")
            if not ar and p["assembly"]:
                raise ValidationError(f"Regel {n}: een samengesteld artikel kan niet ingekocht worden")
            if not ar and not (p["inventory_accno_id"] or p["expense_accno_id"]):
                raise ValidationError(f"Regel {n}: artikel {p['partnumber']} heeft geen voorraad- of kostenrekening")

    # -------------------------------------------------------------- boeken

    def post_invoice(self, inv: Invoice) -> int:
        """Boek (of vervang) een factuur met artikelen (IS/IR post_invoice)."""
        ensure_schema(self.conn)
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            defaultcurrency = _db.default_currency(cur)
            defaults = self._validate(cur, inv, defaultcurrency)
            precision = int(_d(defaults.get("precision") or 2))
            closedto = _closedto(defaults, cfg.dateformat)
            ar = inv.vc == "customer"
            table = inv.table
            sw = -1 if inv.credit else 1
            currency = inv.currency or defaultcurrency
            foreign = currency != defaultcurrency
            rate = _d(inv.exchangerate) or ONE
            if not foreign:
                rate = ONE
            rates = self.tax_rates(inv)
            calc = self.calculate(inv, rates, cur)
            self._check_parts(inv, calc)
            bom = Bom(cur)

            # oude factuur: regels en wat er bij hoort
            old_rows: List[Dict[str, Any]] = []
            old = None
            if inv.id:
                cur.execute(f"SELECT id, transdate, {inv.vc}_id AS vc_id FROM {table} WHERE id = %s FOR UPDATE",
                            (inv.id,))
                old = cur.fetchone()
                if old:
                    cur.execute("""SELECT i.id, i.parts_id, i.qty::numeric AS qty, COALESCE(i.allocated,0)::numeric
                                          AS allocated, i.sellprice::numeric AS sellprice, i.project_id,
                                          COALESCE(i.assemblyitem, false) AS assemblyitem
                                   FROM invoice i WHERE i.trans_id = %s ORDER BY i.id""", (inv.id,))
                    old_rows = cur.fetchall()
                    for r in old_rows:
                        for k in ("qty", "allocated", "sellprice"):
                            r[k] = _d(r[k])

            # artikelen locken: oude en nieuwe regels en alle onderdelen
            lock_ids = {r["parts_id"] for r in old_rows} | {c.part["id"] for c in calc.lines}
            explode: Dict[int, List[Tuple[int, Decimal]]] = {}
            for c in calc.lines:
                if c.part["assembly"]:
                    explode[c.index] = [(e["id"], e["qty"]) for e in bom.explode(c.part["id"], c.qty)
                                        if not e["assembly"]]
                    lock_ids |= {pid for pid, _ in explode[c.index]}
            engine = CogsEngine(cur, precision, closedto, inv.id if old else None)
            engine.lock_parts(lock_ids)
            parts = self.parts_info(cur, lock_ids)

            # voorraadcontrole (io.pl validate_items): alleen verkoop met "voorraad controleren"
            if ar and inv.type == "invoice":
                back: Dict[int, Decimal] = {}
                for r in old_rows:
                    if not r["assemblyitem"]:
                        back[r["parts_id"]] = back.get(r["parts_id"], ZERO) + r["qty"]
                for c in calc.lines:
                    p = parts[c.part["id"]]
                    if p["checkinventory"] and c.qty > 0 and (p["inventory_accno_id"] or p["assembly"]):
                        if c.qty > p["onhand"] + back.get(p["id"], ZERO):
                            raise ValidationError(f"Not enough inventory in Row {c.index + 1}")

            # ---- plan: welke oude regels blijven (zelfde regel-id, artikel en teken), en ongewijzigd?
            units = _units(old_rows)                     # gewone regel -> onderdeelrijen
            by_id = {u[0]["id"]: u for u in units}
            keep: Dict[int, Tuple[Dict[str, Any], List[Dict[str, Any]]]] = {}     # calc-index -> (oud, onderdelen)
            unchanged: Dict[int, bool] = {}
            for c in calc.lines:
                lid = inv.lines[c.index].id
                u = by_id.get(lid) if lid else None
                if not u or any(k[0]["id"] == lid for k in keep.values()):
                    continue
                main, comps = u
                if main["parts_id"] != c.part["id"] or (main["qty"] > 0) != (c.qty > 0):
                    continue
                keep[c.index] = (main, comps)
                same = main["qty"] == c.qty
                if c.part["assembly"]:
                    same = same and [(r["parts_id"], r["qty"]) for r in comps] == explode.get(c.index, [])
                elif not ar and c.qty < 0 and c.part["inventory_accno_id"]:
                    # inkoop: ook de kostprijs moet gelijk zijn
                    oldcost = engine.cost(main["id"])
                    if oldcost is not None:
                        same = same and oldcost == (-c.qty, c.final)
                    else:
                        same = same and main["sellprice"] == c.sellprice
                unchanged[c.index] = same
            kept_rows: Set[int] = set()               # oude rijen die (met toewijzingen) blijven
            for idx, (main, comps) in keep.items():
                kept_rows.add(main["id"])
                if unchanged[idx]:
                    kept_rows |= {r["id"] for r in comps}
            steady_rows = {r["id"] for idx, (m, comps) in keep.items() if unchanged[idx] for r in [m] + comps}

            # oude toewijzingen uit Perl: wijzigt een regel met zo'n toewijzing, dan alle oude delen terug
            legacy_rows = [r for r in old_rows if c_legacy(engine, r)]
            legacy_needed = any(r["id"] not in steady_rows for r in legacy_rows)
            realloc_old: Set[int] = set()             # oude rij-id's die opnieuw toegewezen worden
            legacy_keep_rows: List[Dict[str, Any]] = []
            if old:
                if not legacy_needed:
                    legacy_keep_rows = self._legacy_cogs_rows(cur, engine, inv.id, old_rows)
                    for r in legacy_keep_rows:          # Perl-kostprijs op de factuurdatum verhuist mee
                        if r["transdate"] == old["transdate"]:
                            r["transdate"] = inv.transdate
                else:
                    for r in legacy_rows:
                        engine.legacy_reverse(engine.line(r["id"]), inv.transdate)
                        realloc_old.add(r["id"])
                for r in old_rows:                      # verdwenen regels: alles terug
                    if r["id"] not in kept_rows:
                        engine.reverse_all(r["id"])
                        engine.drop_cost(r["id"])
                for idx, (main, comps) in keep.items():  # gewijzigde regels: inkorten tot het nieuwe aantal
                    c = next(x for x in calc.lines if x.index == idx)
                    if unchanged[idx] and main["id"] not in realloc_old:
                        continue
                    realloc_old.add(main["id"])
                    if c.part["assembly"] or not c.part["inventory_accno_id"]:
                        continue
                    if main["qty"] > 0:
                        engine.trim_consumer(main["id"], c.qty)
                    else:
                        engine.trim_supply(main["id"], -c.qty)
                carry = {r["id"]: engine.line(r["id"])["allocated"] for r in old_rows}
                for tb in ("acc_trans", "dpt_trans", "invoice", "shipto", "payment", "inventory"):
                    cur.execute(f"DELETE FROM {tb} WHERE trans_id = %s", (inv.id,))
                trans_id = inv.id
                cur.execute(f"UPDATE {table} SET transdate = %s, {inv.vc}_id = %s WHERE id = %s",
                            (inv.transdate, inv.vc_id, trans_id))
            else:
                carry = {}
                cur.execute(f"""INSERT INTO {table} (invoice, transdate, {inv.vc}_id) VALUES (true, %s, %s)
                                RETURNING id""", (inv.transdate, inv.vc_id))
                trans_id = cur.fetchone()["id"]
                engine.trans_id = trans_id

            department_id = int(inv.department_id or 0)
            if department_id:
                cur.execute("INSERT INTO dpt_trans (trans_id, department_id) VALUES (%s, %s)", (trans_id, department_id))

            # ---- regels wegschrijven (onderdelen van een samengesteld artikel vóór de regel, zoals Perl)
            employee_id = inv.employee_id or _db.get_employee(cur, cfg.short_login)[1]
            onhand: Dict[int, Decimal] = {}
            for r in old_rows:
                p = parts.get(r["parts_id"]) or {}
                if not r["assemblyitem"] and (p.get("inventory_accno_id") or p.get("assembly")):
                    onhand[r["parts_id"]] = onhand.get(r["parts_id"], ZERO) + r["qty"]
            remap: Dict[int, int] = {}
            new_line_ids: Dict[int, int] = {}
            comp_ids: Dict[int, List[int]] = {}
            for c in calc.lines:
                l = inv.lines[c.index]
                p = c.part
                k = keep.get(c.index)
                steady = bool(k) and unchanged[c.index] and k[0]["id"] not in realloc_old
                if p["assembly"]:
                    ids = []
                    old_comps = k[1] if k and unchanged[c.index] else []
                    for j, (cid, cqty) in enumerate(explode.get(c.index, [])):
                        cp = parts[cid]
                        prev = old_comps[j]["id"] if old_comps else None
                        cur.execute("""INSERT INTO invoice (trans_id, description, parts_id, qty, sellprice, fxsellprice,
                                                            allocated, assemblyitem, unit)
                                       VALUES (%s, %s, %s, %s, 0, 0, %s, true, %s) RETURNING id""",
                                    (trans_id, cp["description"], cid, cqty, carry.get(prev, ZERO) if prev else ZERO,
                                     cp["unit"]))
                        nid = cur.fetchone()["id"]
                        ids.append(nid)
                        if prev:
                            remap[prev] = nid
                    comp_ids[c.index] = ids
                cur.execute(
                    """INSERT INTO invoice (trans_id, parts_id, description, qty, sellprice, fxsellprice, discount,
                                            allocated, unit, deliverydate, project_id, serialnumber, ordernumber,
                                            ponumber, itemnotes, lineitemdetail, cost, vendor, vendor_id, assemblyitem)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false)
                       RETURNING id""",
                    (trans_id, p["id"], l.description if l.description is not None else p["description"],
                     c.qty, c.sellprice, c.fxsellprice, c.discount, carry.get(k[0]["id"], ZERO) if k else ZERO,
                     (l.unit if l.unit is not None else (p["unit"] or ""))[:5], l.deliverydate, l.project_id or None,
                     l.serialnumber, l.ordernumber, l.ponumber, l.itemnotes, bool(l.lineitemdetail),
                     (_d(l.cost) if l.cost is not None else None) if ar else None, l.vendor if ar else None,
                     (int(l.vendor_id) if l.vendor_id is not None else None) if ar else None))
                nid = cur.fetchone()["id"]
                new_line_ids[c.index] = nid
                if k:
                    remap[k[0]["id"]] = nid
                if p["inventory_accno_id"] or p["assembly"]:
                    onhand[p["id"]] = onhand.get(p["id"], ZERO) - c.qty
                c.steady = steady
            for old_id, new_id in remap.items():
                cur.execute("UPDATE x_cogs_allocation SET consumer_id = %s WHERE consumer_id = %s", (new_id, old_id))
                cur.execute("UPDATE x_cogs_allocation SET supply_id = %s WHERE supply_id = %s", (new_id, old_id))
                cur.execute("UPDATE x_invoice_cost SET invoice_id = %s WHERE invoice_id = %s", (new_id, old_id))
                cur.execute("UPDATE cargo SET id = %s WHERE id = %s AND trans_id = %s", (new_id, old_id, trans_id))
            for pid, delta in sorted(onhand.items()):
                _db.update_balance(cur, "parts", "onhand", pid, delta)

            # ---- kostprijs toewijzen (regels in volgorde, zoals Perl)
            return_cost_rows: List[Tuple[int, Decimal, Optional[int], int]] = []   # (chart, bedrag, project, id)
            vendor_returns: Set[int] = set()
            legacy_returns: Set[int] = set()

            def supply_return(lid: int, part: Dict[str, Any], steady: bool, project_id, fallback: Decimal,
                              prev: Optional[Tuple[Decimal, Decimal]]) -> None:
                """Retour van een klant: terug in voorraad tegen de oorspronkelijke kostprijs (F5)."""
                row = engine.line(lid)
                q = -row["qty"]
                if steady and engine.cost(lid) is None:
                    legacy_returns.add(lid)
                    return
                if not steady:
                    if prev and prev[0]:
                        amount = round_amount(prev[1] * q / prev[0], precision)
                    else:
                        amount = engine.return_unit_cost(part["id"], inv.vc_id, q, trans_id, fallback)
                    engine.set_cost(lid, q, amount)
                    engine.reprice_supply(lid)
                    engine.allocate_supply(lid)
                cost = engine.cost(lid)[1]
                return_cost_rows.append((part["inventory_accno_id"], -cost, project_id, lid))
                return_cost_rows.append((part["expense_accno_id"], cost, project_id, lid))

            for c in calc.lines:
                l = inv.lines[c.index]
                p = c.part
                project_id = l.project_id or None
                nid = new_line_ids[c.index]
                if p["assembly"]:
                    for cid in comp_ids.get(c.index, []):
                        row = engine.line(cid)
                        cp = parts[row["parts_id"]]
                        if not cp["inventory_accno_id"]:
                            continue
                        if row["qty"] > 0:
                            if c.steady:
                                cur.execute("UPDATE x_cogs_allocation SET project_id = %s WHERE consumer_id = %s",
                                            (project_id, cid))
                            else:
                                engine.allocate_consumer(cid, row["qty"] + row["allocated"], project_id)
                        elif not (c.steady and engine.cost(cid) is None):
                            supply_return(cid, cp, c.steady, project_id, cp["lastcost"], None)
                    continue
                if not p["inventory_accno_id"]:
                    continue
                row = engine.line(nid)
                if row["qty"] > 0:                                   # afnemer: verkoop / retour aan leverancier
                    if c.steady:
                        cur.execute("UPDATE x_cogs_allocation SET project_id = %s WHERE consumer_id = %s",
                                    (project_id, nid))
                    else:
                        need = row["qty"] + row["allocated"]
                        got = engine.allocate_consumer(nid, need, project_id)
                        if not ar:
                            engine.allocate_uncovered(nid, need - got, -c.final / row["qty"], project_id)
                    if not ar and engine.records(consumer=nid):
                        vendor_returns.add(nid)
                elif ar:                                             # retour van klant
                    prev = engine.cost(nid) if keep.get(c.index) else None
                    supply_return(nid, p, c.steady, project_id, c.final / row["qty"] if row["qty"] else ZERO, prev)
                elif not c.steady:                                   # inkoop
                    engine.set_cost(nid, -row["qty"], c.final)
                    engine.reprice_supply(nid)
                    engine.allocate_supply(nid)

            # ---- grootboekregels
            acc_rows: List[Tuple] = []      # (chart_id, amount, transdate, project_id, id, source, memo, fx, cleared)
            ml = 1 if ar else -1
            for c in calc.lines:
                p = c.part
                nid = new_line_ids[c.index]
                project_id = inv.lines[c.index].project_id or None
                if ar:
                    chart = p["income_accno_id"]
                    if nid in legacy_returns:
                        chart = p["inventory_accno_id"]       # retour zoals Perl geboekt (oud)
                else:
                    chart = p["inventory_accno_id"] or p["expense_accno_id"]
                    if nid in vendor_returns:
                        chart = p["expense_accno_id"] or chart
                if c.final:
                    acc_rows.append((chart, c.final * ml, inv.transdate, project_id, nid, None, None, False, None))
            for chart, amount, project_id, lid in return_cost_rows:
                amount = round_amount(amount, precision)
                if amount:
                    acc_rows.append((chart, amount, inv.transdate, project_id, lid, None, None, False, None))
            for row in engine.derived_rows(trans_id):
                acc_rows.append(row + (None, None, False, None))
            for row in legacy_keep_rows:
                acc_rows.append((row["chart_id"], _d(row["amount"]), row["transdate"], row["project_id"], row["id"],
                                 row["source"], row["memo"], bool(row["fx_transaction"]), row["cleared"]))

            invamount = calc.invamount
            invnetamount = calc.invnetamount
            cur.execute("SELECT id FROM chart WHERE accno = %s", (inv.arap_accno,))
            arap_chart = cur.fetchone()["id"]
            if invamount:
                acc_rows.append((arap_chart, invamount * -1 * ml, inv.transdate, None, None, None, None, False, None))

            # betalingen (+ contante korting als laatste betaling, is.pl post)
            pays: List[Tuple[ArapPayment, bool]] = [(p, False) for p in inv.payments if _d(p.amount)]
            if inv.discount and _d(inv.discount.amount):
                pays.append((inv.discount, True))
            for p, _ in pays:
                if not foreign:
                    p.exchangerate = ONE
                elif p.datepaid == inv.transdate and not _d(p.exchangerate):
                    p.exchangerate = rate
            paid_fx = sum((_d(p.amount) * sw for p, _ in pays), ZERO)
            datepaid = None
            for p, _ in pays:
                datepaid = p.datepaid or inv.transdate

            cd_tax = ZERO
            disc = next(((p, d) for p, d in pays if d), None)
            if disc and defaults.get("cdt"):
                p = disc[0]
                prate = _d(p.exchangerate) or ONE
                support.update_exchangerate(cur, currency, p.datepaid, prate, defaultcurrency)
                # aandeel van de korting in het factuurbedrag (inclusief btw); Perl deelde door het bedrag
                # zonder btw (IS.pm r1342, IR.pm r1149) en corrigeerde zo te veel btw
                gross = calc.fxamount if inv.taxincluded else calc.fxamount + calc.fxtax
                share = (_d(p.amount) * sw / gross) if gross else ZERO
                dd = ZERO
                taxrows = []
                for a in sorted(calc.rawtax):
                    fxt = round_amount(calc.rawtax[a][1] * share, precision)
                    amt = fxt * prate
                    rnd = round_amount(amt, precision)
                    cd_tax += rnd
                    dd += amt - rnd
                    taxrows.append([a, rnd if ar else -rnd])
                dd = round_amount(dd, precision)
                if dd and taxrows:
                    taxrows[-1][1] -= dd
                for a, amt in taxrows:
                    amt = round_amount(amt, precision)
                    if amt:
                        acc_rows.append((self._chart_id(cur, a), amt * -1, p.datepaid, None, trans_id, None, None,
                                         False, None))
                arapamt = round_amount(-cd_tax if ar else cd_tax, precision)
                if arapamt:
                    acc_rows.append((arap_chart, arapamt * -1, p.datepaid, None, None, None, None, False, None))
            for a in sorted(calc.taxes):
                amt = calc.taxes[a]
                if amt:
                    acc_rows.append((self._chart_id(cur, a), amt * ml, inv.transdate, None, None, None, None, False, None))

            fxgain = defaults.get("fxgainloss_accno_id")
            paymentid = 1
            payment_rows = []
            for p, _ in pays:
                pdate = p.datepaid or inv.transdate
                prate = _d(p.exchangerate) or ONE
                support.update_exchangerate(cur, currency, pdate, prate, defaultcurrency)
                paid = _d(p.amount) * sw
                cleared = p.cleared if old else None
                pchart = self._chart_id(cur, p.accno)
                acc_rows.append((arap_chart, round_amount(paid * rate, precision) * ml, pdate, None, None, None, None,
                                 False, None))
                acc_rows.append((pchart, paid * -1 * ml, pdate, None, paymentid, p.source, p.memo, False, cleared))
                payment_rows.append((paymentid, prate, int(p.paymentmethod_id or 0)))
                paymentid += 1
                fxamt = round_amount(round_amount(paid * prate - paid, precision) * -1 * ml, precision)
                if fxamt:
                    acc_rows.append((pchart, fxamt, pdate, None, None, p.source, None, True, cleared))
                gain = round_amount((round_amount(paid * rate, precision) - round_amount(paid * prate, precision))
                                    * -1 * ml, precision)
                if gain:
                    if not fxgain:
                        raise ValidationError("No account for exchange rate gain/loss (fxgainloss_accno_id)")
                    acc_rows.append((int(fxgain), gain, pdate, None, None, None, None, True, cleared))

            for (chart, amount, d, project_id, rid, source, memo, fx, cleared) in acc_rows:
                amount = round_amount(amount, precision)
                if not amount:
                    continue
                cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, project_id, id, source,
                                                      memo, fx_transaction, cleared, approved)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)""",
                            (trans_id, chart, amount, d, project_id, rid, source, memo, fx, cleared))
            for pid, prate, pm in payment_rows:
                cur.execute("INSERT INTO payment (id, trans_id, exchangerate, paymentmethod_id) VALUES (%s, %s, %s, %s)",
                            (pid, trans_id, prate, pm))

            # ---- kop
            if round_amount(paid_fx - calc.fxpaytotal, precision) == 0:
                paid = invamount
            else:
                paid = round_amount(paid_fx * rate, precision)
            if defaults.get("cdt") and disc and not inv.taxincluded:
                invamount -= cd_tax
            invnumber = inv.invnumber or next_number(
                cur, "sinumber" if ar else "vinumber", dateformat=cfg.dateformat, transdate=inv.transdate,
                currency=currency, fields={"description": inv.description})
            duedate = inv.duedate or inv.transdate
            if ar:
                total = calc.fxtotal
                whole, _, dec = _plain(total).partition(".")
                fields = {"integer_amount": whole, "decimal": (dec + "00")[:2], "invnumber": invnumber,
                          "ordnumber": inv.ordnumber}
                cur.execute("SELECT customernumber FROM customer WHERE id = %s", (inv.vc_id,))
                fields["customernumber"] = (cur.fetchone() or {}).get("customernumber")
                cur.execute("""SELECT bk.membernumber, bk.dcn FROM bank bk JOIN chart c ON (c.id = bk.id)
                               WHERE c.accno = %s""", (inv.bank_accno,))
                bk = cur.fetchone() or {}
                fields["membernumber"] = bk.get("membernumber")
                dcn = format_dcn(bk.get("dcn"), fields) or inv.dcn
            else:
                dcn = inv.dcn
            # ir.pl post verliest de standaard betaalrekening (lusvariabele buiten de lus): alleen de
            # rekening van de laatste betaling wordt bewaard (zoals Perl; alleen gebruikt als voorinvulling)
            # (hier: rekening van de laatste betaling, anders de gekozen standaardrekening)
            bank_accno = inv.bank_accno if ar else (pays[-1][0].accno if pays else inv.bank_accno)
            cur.execute(
                f"""UPDATE {table} SET invnumber = %s, description = %s, ordnumber = %s, quonumber = %s,
                        transdate = %s, {inv.vc}_id = %s, amount = %s, netamount = %s, paid = %s, datepaid = %s,
                        duedate = %s, invoice = true, shippingpoint = %s, shipvia = %s, waybill = %s, terms = %s,
                        notes = %s, intnotes = %s, taxincluded = %s, curr = %s, department_id = %s,
                        employee_id = %s, language_code = %s, ponumber = %s, cashdiscount = %s, discountterms = %s,
                        onhold = %s, warehouse_id = 0, exchangerate = %s, dcn = %s,
                        bank_id = (SELECT id FROM chart WHERE accno = %s), paymentmethod_id = %s
                    WHERE id = %s""",
                (invnumber, inv.description, inv.ordnumber, inv.quonumber, inv.transdate, inv.vc_id, invamount,
                 invnetamount, paid, datepaid if pays else None, duedate, inv.shippingpoint, inv.shipvia,
                 inv.waybill, int(inv.terms or 0), inv.notes, inv.intnotes, bool(inv.taxincluded), currency,
                 department_id, employee_id, inv.language_code or "", inv.ponumber,
                 _d(inv.cashdiscount) / 100, int(inv.discountterms or 0), bool(inv.onhold), rate, dcn,
                 bank_accno, int(inv.paymentmethod_id or 0), trans_id))
            support.update_exchangerate(cur, currency, inv.transdate, rate, defaultcurrency)
            if any((inv.shipto or {}).get(f) for f in SHIPTO_FIELDS):
                s = inv.shipto
                cur.execute("""INSERT INTO shipto (trans_id, shiptoname, shiptoaddress1, shiptoaddress2, shiptocity,
                                                   shiptostate, shiptozipcode, shiptocountry, shiptocontact,
                                                   shiptophone, shiptofax, shiptoemail, shiptorecurring)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (trans_id, *[s.get(f) or None for f in SHIPTO_FIELDS], bool(s.get("recurring"))))
            _save_status(cur, trans_id, inv.printed, inv.emailed, inv.queued)
            support.save_references(cur, cfg, trans_id, f"{table}_{inv.type}", inv.references)
            if inv.order_id and not old:
                from .order import close_for_invoice
                close_for_invoice(cur, cfg, int(inv.order_id), inv, trans_id)
            support.audittrail(cur, cfg, trans_id=trans_id, tablename=table, reference=invnumber,
                               formname=inv.type, action="posted")
            cur.execute("DELETE FROM recurring WHERE id = %s", (trans_id,))
            support.save_recurring(cur, trans_id, inv.recurring)
            support.remove_locks(cur, cfg, table)

            engine.rebook_others()
            if not ar:
                self._update_costs(cur, {r["parts_id"] for r in old_rows} | {c.part["id"] for c in calc.lines
                                                                             if c.part["inventory_accno_id"]},
                                   precision)
        return trans_id

    def _chart_id(self, cur, accno: str) -> int:
        cur.execute("SELECT id FROM chart WHERE accno = %s", (accno,))
        r = cur.fetchone()
        if not r:
            raise ValidationError(f"Unknown account {accno}")
        return r["id"]

    def _cogs_accounts(self, cur, line_ids: List[int]) -> Set[int]:
        if not line_ids:
            return set()
        cur.execute("""SELECT DISTINCT p.inventory_accno_id, p.expense_accno_id FROM invoice i
                       JOIN parts p ON (p.id = i.parts_id) WHERE i.id = ANY(%s)""", (line_ids,))
        out: Set[int] = set()
        for r in cur.fetchall():
            out |= {x for x in (r["inventory_accno_id"], r["expense_accno_id"]) if x}
        return out

    def _legacy_cogs_rows(self, cur, engine: CogsEngine, trans_id: int, old_rows) -> List[Dict[str, Any]]:
        """Kostprijsregels in deze factuur die niet uit vastgelegde toewijzingen komen (oude Perl-
        regels, tegenboekingen van latere inkopen): ze blijven staan zolang de regels niet wijzigen."""
        line_ids = [r["id"] for r in old_rows]
        cogs = self._cogs_accounts(cur, line_ids)
        if not cogs:
            return []
        cur.execute("SELECT transdate FROM ar WHERE id = %s UNION ALL SELECT transdate FROM ap WHERE id = %s",
                    (trans_id, trans_id))
        tdate = cur.fetchone()["transdate"]
        derived = {}
        for row in engine.derived_rows(trans_id):
            derived[row] = derived.get(row, 0) + 1
        prec = engine.prec
        cur.execute("""SELECT ac.chart_id, ac.amount, ac.transdate, ac.project_id, ac.id, ac.source, ac.memo,
                              ac.fx_transaction, ac.cleared
                       FROM acc_trans ac WHERE ac.trans_id = %s AND ac.chart_id = ANY(%s)""",
                    (trans_id, sorted(cogs)))
        out = []
        ids = set(line_ids)
        for r in cur.fetchall():
            key = (r["chart_id"], round_amount(r["amount"], prec), r["transdate"], r["project_id"], r["id"])
            if derived.get(key):
                derived[key] -= 1
                continue
            if r["id"] in ids and r["transdate"] == tdate:
                continue                       # eigen regel van de factuur (wordt opnieuw geschreven)
            out.append(r)
        return out

    def _update_costs(self, cur, part_ids, precision: int) -> None:
        """Laatste en gemiddelde inkoopprijs (IR.pm na post_invoice), binnen de transactie (F8)."""
        for pid in sorted(i for i in part_ids if i):
            cur.execute("""SELECT SUM(i.sellprice::numeric * i.qty::numeric) AS cost, SUM(i.qty::numeric) AS qty
                           FROM invoice i JOIN ap a ON (a.id = i.trans_id) WHERE i.parts_id = %s""", (pid,))
            r = cur.fetchone()
            avg = round_amount(_d(r["cost"]) / _d(r["qty"]), precision) if _d(r["qty"]) else ZERO
            cur.execute("""SELECT i.sellprice FROM invoice i JOIN ap a ON (a.id = i.trans_id) WHERE i.parts_id = %s
                           ORDER BY a.transdate DESC, a.id DESC LIMIT 1""", (pid,))
            last = cur.fetchone()
            cur.execute("SELECT inventory_accno_id FROM parts WHERE id = %s", (pid,))
            p = cur.fetchone()
            if not p or not p["inventory_accno_id"]:
                continue
            cur.execute("UPDATE parts SET avgcost = %s, lastcost = %s WHERE id = %s",
                        (avg, _d(last["sellprice"]) if last else ZERO, pid))

    # -------------------------------------------------------------- laden

    def get_invoice(self, trans_id: int, vc: str, lock: bool = False) -> Invoice:
        """Laad een factuur met artikelen zoals het scherm (IS/IR retrieve_invoice + create_links)."""
        table = "ar" if vc == "customer" else "ap"
        with _db.cursor(self.conn) as cur:
            cur.execute(f"SELECT * FROM {table} WHERE id = %s", (trans_id,))
            h = cur.fetchone()
            if not h:
                raise NotFoundError(f"{table} {trans_id} not found")
            # leverdatum als tekst: administratie A heeft datums als 20121-04-25 (buiten het bereik van Python)
            cur.execute("""SELECT i.id, i.parts_id, i.description, i.fxsellprice, i.discount, i.unit,
                                  i.deliverydate::text AS deliverydate, i.project_id, i.serialnumber, i.ordernumber,
                                  i.ponumber, i.itemnotes, i.lineitemdetail, i.cost, i.vendor, i.vendor_id,
                                  i.qty::numeric AS nqty, COALESCE(i.assemblyitem, false) AS isasm
                           FROM invoice i WHERE i.trans_id = %s ORDER BY i.id""", (trans_id,))
            rows = cur.fetchall()
            line_ids = [r["id"] for r in rows]
            cogs = self._cogs_accounts(cur, line_ids)
            cur.execute("SELECT * FROM shipto WHERE trans_id = %s", (trans_id,))
            st = cur.fetchone() or {}
            cur.execute("""SELECT p.id, p.income_accno_id, p.inventory_accno_id, p.expense_accno_id FROM parts p
                            WHERE p.id = ANY(%s)""", ([r["parts_id"] for r in rows],))
            pacc = {r["id"]: r for r in cur.fetchall()}
            cur.execute("SELECT id, accno FROM chart")
            accno_of = {r["id"]: r["accno"] for r in cur.fetchall()}
        cogs_accnos = {accno_of[c] for c in cogs}
        line_charts = {}
        for r in rows:
            a = pacc.get(r["parts_id"]) or {}
            line_charts[r["id"]] = {accno_of.get(a.get(k)) for k in ("income_accno_id", "inventory_accno_id",
                                                                    "expense_accno_id") if a.get(k)}
        ARAP = table.upper()

        def exclude(e) -> bool:
            keys = [k for k in (e["link"] or "").split(":") if ARAP in k]
            if any(k in (ARAP, f"{ARAP}_paid", f"{ARAP}_tax", f"{ARAP}_discount") for k in keys):
                return False
            if e["accno"] in cogs_accnos:
                return True
            return e["id"] in line_charts and e["transdate"] == h["transdate"] and e["accno"] in line_charts[e["id"]]

        t = self.arap.get_transaction(trans_id, vc, lock=lock, exclude=exclude)
        credit = _d(h["amount"]) < 0
        itype = ("credit_invoice" if vc == "customer" else "debit_invoice") if credit else "invoice"
        sw = -1 if credit else 1
        lines = []
        for r in rows:
            if r["isasm"]:
                continue
            q = _d(r["nqty"]) * sw * (1 if vc == "customer" else -1)
            lines.append(InvoiceLine(
                parts_id=r["parts_id"], qty=q, sellprice=_d(r["fxsellprice"]), discount=_d(r["discount"]) * 100,
                description=r["description"] or "", unit=r["unit"] or "", deliverydate=_date_or_text(r["deliverydate"]),
                project_id=r["project_id"], serialnumber=r["serialnumber"], ordernumber=r["ordernumber"],
                ponumber=r["ponumber"], itemnotes=r["itemnotes"],
                lineitemdetail=bool(r["lineitemdetail"]),
                cost=_d(r["cost"]) if r.get("cost") is not None else None, vendor=r.get("vendor"),
                vendor_id=r.get("vendor_id"), id=r["id"]))
        # Perl slaat de korting als real op (0.1 -> 0.100000001...): terug naar wat ingevoerd is
        for l in lines:
            l.discount = Decimal(_plain(round_amount(l.discount, 4))) if l.discount else ZERO
        shipto = {f: st.get(f"shipto{f}") or "" for f in SHIPTO_FIELDS}
        if st.get("shiptorecurring"):
            shipto["recurring"] = "1"
        arap_accno = t.arap_accno
        if not arap_accno:          # factuur van 0 zonder debiteuren-/crediteurenregel: eerste rekening (Perl)
            acc = self.arap.accounts(vc)[ARAP]
            arap_accno = acc[0]["accno"] if acc else None
        return Invoice(
            vc=vc, vc_id=h[f"{vc}_id"], transdate=h["transdate"], arap_accno=arap_accno, lines=lines,
            type=itype, id=trans_id, invnumber=h["invnumber"], ordnumber=h["ordnumber"], quonumber=h["quonumber"],
            ponumber=h["ponumber"], duedate=h["duedate"], terms=h["terms"] or 0, description=h["description"],
            notes=h["notes"], intnotes=h["intnotes"], currency=t.currency, exchangerate=t.exchangerate,
            taxincluded=bool(h["taxincluded"]), department_id=h["department_id"] or None,
            employee_id=h["employee_id"], language_code=h["language_code"], shippingpoint=h["shippingpoint"],
            shipvia=h["shipvia"], waybill=h["waybill"], payments=t.payments, discount=t.discount,
            cashdiscount=t.cashdiscount, discountterms=t.discountterms, onhold=t.onhold, bank_accno=t.bank_accno,
            paymentmethod_id=t.paymentmethod_id, dcn=h["dcn"], shipto=shipto, printed=t.printed,
            emailed=t.emailed, queued=t.queued, recurring=t.recurring, references=t.references,
            oldinvtotal=t.oldinvtotal, taxrates=t.taxrates, till=h.get("till"),
            taxaccounts_extra={a for a, v in t.taxes.items() if v},
            warehouse_id=h.get("warehouse_id") or None, locked=t.locked, readonly=t.readonly, haslock=t.haslock,
            batch=t.batch)

    # -------------------------------------------------------------- verwijderen

    def delete_invoice(self, trans_id: int, vc: str) -> List[str]:
        """Verwijder een factuur (IS/IR delete_invoice): voorraad en kostprijs precies terug, een
        gekoppelde order/offerte weer open (F6) en een nabestelling die bij deze factuur ontstond weg als hij
        onaangeroerd is (ON3). Geeft meldingen terug (nabestelling blijft staan)."""
        ensure_schema(self.conn)
        cfg = self.config
        table = "ar" if vc == "customer" else "ap"
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute(f"SELECT invnumber, transdate, amount, till FROM {table} WHERE id = %s FOR UPDATE",
                        (trans_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError(f"{table} {trans_id} not found")
            defaults = _db.get_defaults(cur, "closedto", "revtrans", "precision")
            if defaults.get("revtrans") not in (None, "", "0"):
                raise LockedTransactionError("Transaction is locked (revtrans)")
            closedto = _closedto(defaults, cfg.dateformat)
            if closedto and _num(row["transdate"]) <= closedto:
                raise ClosedPeriodError("Cannot delete a transaction in a closed period!")
            cur.execute("SELECT 1 FROM vr WHERE trans_id = %s", (trans_id,))
            if cur.fetchone():
                raise ValidationError("Transactions in batches are not supported")
            holder = support.lock_holder(cur, cfg, trans_id)
            if holder:
                raise LockedTransactionError(f"Transaction is locked by {holder}")
            precision = int(_d(defaults.get("precision") or 2))
            cur.execute("""SELECT i.id, i.parts_id, i.qty::numeric AS qty, COALESCE(i.assemblyitem, false) AS asm,
                                  p.inventory_accno_id, p.assembly
                           FROM invoice i JOIN parts p ON (p.id = i.parts_id) WHERE i.trans_id = %s ORDER BY i.id""",
                        (trans_id,))
            rows = cur.fetchall()
            engine = CogsEngine(cur, precision, closedto, trans_id)
            engine.lock_parts(r["parts_id"] for r in rows)
            if any(c_legacy(engine, r) for r in rows):
                for r in rows:
                    engine.legacy_reverse(engine.line(r["id"]), row["transdate"])
            for r in rows:
                engine.reverse_all(r["id"])
                engine.drop_cost(r["id"])
                if not r["asm"] and (r["inventory_accno_id"] or r["assembly"]):
                    _db.update_balance(cur, "parts", "onhand", r["parts_id"], _d(r["qty"]))
            support.audittrail(cur, cfg, trans_id=trans_id, tablename=table, reference=row["invnumber"],
                               formname=("credit_invoice" if vc == "customer" else "debit_invoice")
                               if _d(row["amount"]) < 0 else "invoice", action="deleted")
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (trans_id,))
            for tb in ("acc_trans", "dpt_trans", "invoice", "inventory", "cargo", "shipto", "payment", "status"):
                cur.execute(f"DELETE FROM {tb} WHERE trans_id = %s", (trans_id,))
            for tb in ("recurring", "recurringemail", "recurringprint"):
                cur.execute(f"DELETE FROM {tb} WHERE id = %s", (trans_id,))
            from .order import reopen_for_deleted_invoice
            msgs = reopen_for_deleted_invoice(cur, cfg, trans_id)
            support.delete_references(cur, cfg, trans_id)
            support.remove_locks(cur, cfg, table)
            engine.rebook_others()
            if vc == "vendor":
                self._update_costs(cur, {r["parts_id"] for r in rows}, precision)
        return msgs


def _date_or_text(v: Optional[str]):
    """Datum uit de database; een datum buiten het bereik van Python blijft tekst (wordt zo teruggeschreven)."""
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return v


def c_legacy(engine: CogsEngine, r: Dict[str, Any]) -> bool:
    """Heeft deze factuurregel een toewijzing uit Perl (zonder vastlegging)?"""
    return engine.legacy_qty(engine.line(r["id"])) != 0


def _units(rows: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """Gewone factuurregels met hun onderdeelregels (Perl schrijft de onderdelen van een samengesteld
    artikel direct vóór de regel van het artikel)."""
    out = []
    pending: List[Dict[str, Any]] = []
    for r in rows:
        if r["assemblyitem"]:
            pending.append(r)
        else:
            out.append((r, pending))
            pending = []
    return out
