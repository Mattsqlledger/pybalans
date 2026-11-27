"""Verkoop- en inkoopboekingen zonder artikelen (port van SL/AA.pm + regels uit
bin/mozilla/aa.pl en arap.pl).

Teken-conventies (ongewijzigd t.o.v. SQL-Ledger): in ``acc_trans`` staat de
debiteurenregel (AR) negatief en de opbrengst positief; bij crediteuren (AP)
andersom. Een creditnota ('credit_note' bij klanten, 'debit_note' bij
leveranciers) draait alle tekens om. In deze module zijn bedragen altijd
positief in de valuta van de boeking, zoals een gebruiker ze invoert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Set

import psycopg

from . import _db, support
from .amounts import _plain, round_amount, to_decimal
from .config import UserConfig
from .dates import add_date, parse_date
from .errors import (ClosedPeriodError, LockedTransactionError, NotFoundError,
                     ValidationError)
from .gl import _closedto, _num, like
from .models import Recurring, Reference
from .numbering import next_number

ZERO = Decimal(0)


@dataclass
class ArapLine:
    """Opbrengst- of kostenregel."""

    accno: str
    amount: Decimal
    description: Optional[str] = None
    project_id: Optional[int] = None
    cleared: Optional[date] = None


@dataclass
class ArapPayment:
    """Betaling direct bij de boeking (of de contante korting)."""

    accno: str
    amount: Decimal
    datepaid: Optional[date] = None
    source: Optional[str] = None
    memo: Optional[str] = None
    exchangerate: Optional[Decimal] = None
    cleared: Optional[date] = None
    paymentmethod_id: Optional[int] = None


@dataclass
class ArapTransaction:
    """Verkoopboeking (vc='customer', tabel ar) of inkoopboeking (vc='vendor', tabel ap).

    taxes: btw per rekening in de valuta van de boeking (bereken ze met
    ArapLedger.calculate_taxes). Bij taxincluded zijn de regelbedragen inclusief btw.
    bank_accno: standaard betaalrekening van de boeking (ar/ap.bank_id).
    """

    vc: str
    vc_id: int
    transdate: date
    arap_accno: str
    lines: List[ArapLine]
    taxes: Dict[str, Decimal] = field(default_factory=dict)
    id: Optional[int] = None
    type: str = "transaction"
    invnumber: Optional[str] = None
    duedate: Optional[date] = None
    terms: int = 0
    ordnumber: Optional[str] = None
    ponumber: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = None
    exchangerate: Decimal = Decimal(1)
    taxincluded: bool = False
    department_id: Optional[int] = None
    employee_id: Optional[int] = None
    language_code: Optional[str] = None
    payments: List[ArapPayment] = field(default_factory=list)
    discount: Optional[ArapPayment] = None
    cashdiscount: Decimal = ZERO          # percentage
    discountterms: int = 0
    onhold: bool = False
    pending: bool = False
    bank_accno: Optional[str] = None
    paymentmethod_id: Optional[int] = None
    dcn: Optional[str] = None             # betaalkenmerk; bij klanten opnieuw berekend
    printed: Set[str] = field(default_factory=set)
    emailed: Set[str] = field(default_factory=set)
    queued: Dict[str, str] = field(default_factory=dict)
    recurring: Optional[Recurring] = None
    references: List[Reference] = field(default_factory=list)
    # gevuld door get_transaction / vc_defaults
    calctax: Set[str] = field(default_factory=set)
    taxrates: Dict[str, Decimal] = field(default_factory=dict)
    oldinvtotal: Optional[Decimal] = None
    # alleen gevuld door get_transaction (om te tonen; boeken negeert ze)
    invoice: bool = False                 # factuur met artikelen (is.pl/ir.pl)
    batch: bool = False                   # hoort bij een batch (vr)
    locked: bool = False                  # afgesloten periode of revtrans
    readonly: bool = False                # gelockt door een ander (haslock)
    haslock: Optional[str] = None

    @property
    def table(self) -> str:
        return "ar" if self.vc == "customer" else "ap"

    @property
    def ARAP(self) -> str:
        return self.table.upper()


def format_dcn(template: Optional[str], fields: Dict[str, Any]) -> Optional[str]:
    """Betaalkenmerk volgens sjabloon (Form::format_dcn): <%veld lengte%> en
    controlecijfers <%modulo10%>, <%modulo11_..._...%> e.d."""
    if not template or "<%" not in template:
        return template
    s = template
    mtab = [0, 9, 4, 6, 8, 2, 7, 1, 3, 5]
    m = {}
    rot = list(mtab)
    for k in range(10):
        m[k] = list(rot)
        rot = rot[1:] + rot[:1]
    while True:
        hit = re.search(r"<%(.+?)%>", s)
        if not hit:
            break
        param = hit.group(1)
        parts = param.split(" ")
        var = parts[0]
        padl = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if var == "membernumber":
            val = re.sub(r"\W", "", str(fields.get(var) or ""))
            if padl:
                val = ("0" * padl + val)[-padl:]
        elif "modulo" in var:
            val = f"\x01{param}\x01"
        else:
            raw = str(fields.get(var) or "")
            counter = [0]

            def repl(_m):
                counter[0] += 1
                return str(counter[0])
            val = re.sub(r"\D", repl, raw)
            if padl:
                val = ("0" * padl + val)[-padl:]
        s = s.replace(f"<%{param}%>", val, 1)
    while True:
        hit = re.search("\x01(modulo.+?)\x01", s)
        if not hit:
            break
        param = hit.group(1)
        pre = re.match("(.+?)\x01modulo", s)
        digits = [int(c) for c in (pre.group(1) if pre else "") if c.isdigit()]
        val = ""
        if param == "modulo10":
            e = 0
            for n in digits:
                e = m[e][n]
            val = str(10 - e)[-1]
        mm = re.match(r"modulo(1\d+)_(\d+)_?(\w*)", param)
        if mm:
            mod, w, lr = int(mm.group(1)), mm.group(2), mm.group(3)
            e = list(reversed(digits)) if lr == "right" else digits
            weights = [int(c) for c in w]
            cd = 0
            for i, d in enumerate(e):
                x = d * weights[i % 2]
                cd += sum(int(c) for c in str(x)) if w in ("12", "21") and x > 9 else x
            r = cd % mod
            if mod == 10 and r > 0:
                r = mod - r
            val = str(r)
        s = s.replace(f"\x01{param}\x01", val, 1)
    return s


class ArapLedger:
    """Verkoop- en inkoopboekingen: standaarden per relatie, btw berekenen,
    boeken, laden en verwijderen."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------ relatie

    def vc_defaults(self, vc: str, vc_id: int, transdate: Optional[date] = None) -> Dict[str, Any]:
        """Standaardwaarden van een klant/leverancier (AA::get_name): btw-
        rekeningen met tarief op transdate, termijn, vervaldatum, valuta,
        standaard debiteuren/crediteuren-, betaal- en kortingsrekening en de
        korting (fractie) voor nieuwe factuur-/orderregels."""
        if vc not in ("customer", "vendor"):
            raise ValueError("vc moet 'customer' of 'vendor' zijn")
        cc = self.config.countrycode
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""
                SELECT c.name, c.{vc}number AS number, c.terms, c.taxincluded, c.curr AS currency,
                       c.language_code, c.cashdiscount, c.discountterms, c.paymentmethod_id,
                       e.id AS employee_id, c1.accno AS arap_accno, c2.accno AS payment_accno,
                       c3.accno AS discount_accno, c.creditlimit, c.discount
                FROM {vc} c
                LEFT JOIN employee e ON (e.id = c.employee_id)
                LEFT JOIN chart c1 ON (c1.id = c.arap_accno_id)
                LEFT JOIN chart c2 ON (c2.id = c.payment_accno_id)
                LEFT JOIN chart c3 ON (c3.id = c.discount_accno_id)
                WHERE c.id = %s""", (vc_id,))
            ref = cur.fetchone()
            if not ref:
                raise NotFoundError(f"{vc} {vc_id} not found")
            ref["currency"] = (ref["currency"] or "").strip() or _db.default_currency(cur)
            ref["duedate"] = (transdate or date.today()) + _days(ref["terms"])
            ARAP = "AR" if vc == "customer" else "AP"
            cur.execute(f"""SELECT c.accno FROM chart c JOIN {vc}tax ct ON (ct.chart_id = c.id)
                            WHERE ct.{vc}_id = %s""", (vc_id,))
            linked = {r["accno"] for r in cur.fetchall()}
            if transdate:
                cur.execute("""SELECT c.accno, t.rate FROM chart c JOIN tax t ON (c.id = t.chart_id)
                               WHERE c.link LIKE %s AND c.closed = '0'
                                 AND (t.validto >= %s OR t.validto IS NULL)
                               ORDER BY c.accno, t.validto""", (f"%{ARAP}_tax%", transdate))
            else:
                cur.execute("""SELECT c.accno, t.rate FROM chart c JOIN tax t ON (c.id = t.chart_id)
                               WHERE c.link LIKE %s AND c.closed = '0' AND t.validto IS NULL
                               ORDER BY c.accno, t.validto""", (f"%{ARAP}_tax%",))
            rates: Dict[str, Decimal] = {}
            for r in cur.fetchall():
                if r["accno"] in linked and r["accno"] not in rates:
                    rates[r["accno"]] = to_decimal(r["rate"])
            ref["taxrates"] = rates
        return ref

    # ------------------------------------------------------------- btw

    def calculate_taxes(self, trans: ArapTransaction, rates: Optional[Dict[str, Decimal]] = None,
                        only: Optional[Set[str]] = None, checktax: bool = False) -> Dict[str, Decimal]:
        """Bereken de btw zoals het boekingsscherm (aa.pl update).

        rates: btw-tarief per rekening (standaard trans.taxrates of die van de
        relatie). only: alleen deze rekeningen herberekenen; de rest houdt het
        bedrag uit trans.taxes (zoals 'calctax' in het scherm). checktax: bij
        btw inbegrepen ook het tarief meetellen van niet-herberekende rekeningen
        met een bedrag (het scherm doet dat vanaf de tweede herberekening).
        Btw-regels op rekeningen die niet (meer) bij de relatie horen blijven
        staan (Perl liet ze bij opnieuw opslaan stilzwijgend weg).
        """
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            cdt = _db.get_defaults(cur, "cdt").get("cdt")
        if rates is None:
            rates = trans.taxrates or self.vc_defaults(trans.vc, trans.vc_id, trans.transdate)["taxrates"]
        accounts = list(rates)
        recalc = set(accounts) if only is None else set(only)
        invtotal = sum((to_decimal(l.amount) for l in trans.lines), ZERO)
        discount_paid = to_decimal(trans.discount.amount) if trans.discount else ZERO
        x0 = invtotal - discount_paid if cdt else invtotal
        taxes = {a: to_decimal(v) for a, v in trans.taxes.items()}
        for a in accounts:
            taxes.setdefault(a, ZERO)

        if trans.taxincluded:
            diff = ZERO
            for ml in (1, -1):
                taxrate = ZERO
                diff = ZERO
                for a in accounts:
                    if rates[a] * ml > 0:
                        if a in recalc or (checktax and taxes.get(a)):
                            taxrate += rates[a]
                taxrate *= ml
                tax = ZERO
                for a in accounts:
                    if rates[a] * ml > 0:
                        if taxrate:
                            x = x0 * rates[a] / (1 + taxrate)
                            y = round_amount(x, precision)
                            tax = round_amount(x - diff, precision)
                            diff = y - (x - diff)
                        if a in recalc:
                            taxes[a] = tax
        else:
            for a in accounts:
                if a in recalc:
                    taxes[a] = round_amount(x0 * rates[a], precision)
        return taxes

    # ------------------------------------------------------------ validatie

    def _validate(self, cur, t: ArapTransaction, defaultcurrency: str) -> Dict[str, Any]:
        cfg = self.config
        if t.vc not in ("customer", "vendor"):
            raise ValidationError("vc must be 'customer' or 'vendor'")
        if not t.transdate:
            raise ValidationError("Invoice Date missing!")
        if not t.vc_id:
            raise ValidationError("Customer missing!" if t.vc == "customer" else "Vendor missing!")
        cur.execute(f"SELECT id FROM {t.vc} WHERE id = %s", (t.vc_id,))
        if not cur.fetchone():
            raise ValidationError(f"Unknown {t.vc} {t.vc_id}")
        defaults = _db.get_defaults(cur, "closedto", "revtrans", "precision", "fxgainloss_accno_id",
                                    "cdt", "roundchange", "cashovershort_accno_id")
        closedto = _closedto(defaults, cfg.dateformat)
        if closedto and _num(t.transdate) <= closedto:
            raise ClosedPeriodError("Cannot post transaction for a closed period!")
        currency = t.currency or defaultcurrency
        if currency != defaultcurrency and not to_decimal(t.exchangerate):
            raise ValidationError("Exchange rate missing!")
        if t.id:
            cur.execute(f"SELECT transdate FROM {t.table} WHERE id = %s", (t.id,))
            old = cur.fetchone()
            if old:
                if defaults.get("revtrans") not in (None, "", "0"):
                    raise LockedTransactionError("Transaction is locked (revtrans)")
                if closedto and _num(old["transdate"]) <= closedto:
                    raise LockedTransactionError("Cannot change a transaction in a closed period!")
            cur.execute("SELECT 1 FROM vr WHERE trans_id = %s", (t.id,))
            if cur.fetchone():
                raise ValidationError("Transactions in batches are not supported")
            holder = support.lock_holder(cur, cfg, t.id)
            if holder:
                raise LockedTransactionError(f"Transaction is locked by {holder}")
        # Perl sloeg bv. 0.004 ongewijzigd op in acc_trans (zoals bij het memoriaal, README 40)
        precision = int(to_decimal(defaults.get("precision") or 2))
        amounts = [l.amount for l in t.lines] + list(t.taxes.values()) + [p.amount for p in t.payments]
        if t.discount:
            amounts.append(t.discount.amount)
        for a in amounts:
            a = to_decimal(a)
            if round_amount(a, precision) != a:
                raise ValidationError(f"Amount has more than {precision} decimals")
        for p in t.payments:
            if to_decimal(p.amount):
                if not p.datepaid:
                    raise ValidationError("Payment date missing!")
                if closedto and _num(p.datepaid) <= closedto:
                    raise ClosedPeriodError("Cannot post payment for a closed period!")
        if t.discount and to_decimal(t.discount.amount):
            d = t.discount
            if not d.datepaid:
                raise ValidationError("Cash Discount date missing!")
            if closedto and _num(d.datepaid) <= closedto:
                raise ClosedPeriodError("Cannot post cash discount for a closed period!")
            if d.datepaid > t.transdate + _days(t.discountterms):
                raise ValidationError("Date for cash discount past due!")
            if t.oldinvtotal is not None and to_decimal(d.amount) > t.oldinvtotal * to_decimal(t.cashdiscount) / 100:
                raise ValidationError("Cash discount exceeds available discount!")
        accnos = {t.arap_accno} | {l.accno for l in t.lines} | set(t.taxes) | \
            {p.accno for p in t.payments} | ({t.discount.accno} if t.discount else set())
        if t.bank_accno:
            accnos.add(t.bank_accno)
        cur.execute("SELECT accno FROM chart WHERE accno = ANY(%s) AND charttype = 'A'", (sorted(accnos),))
        known = {r["accno"] for r in cur.fetchall()}
        missing = sorted(a for a in accnos if a and a not in known)
        if missing:
            raise ValidationError(f"Unknown account(s): {', '.join(missing)}")
        return defaults

    # -------------------------------------------------------------- boeken

    def post_transaction(self, t: ArapTransaction) -> int:
        """Boek (of vervang) een verkoop-/inkoopboeking (aa.pl post + AA::post_transaction)."""
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            defaultcurrency = _db.default_currency(cur)
            defaults = self._validate(cur, t, defaultcurrency)
            precision = int(to_decimal(defaults.get("precision") or 2))
            currency = t.currency or defaultcurrency
            foreign = currency != defaultcurrency
            ml = -1 if t.vc == "vendor" else 1
            arapml = -1 if t.type.endswith("_note") else 1
            approved = not t.pending
            action = "posted" if approved else "saved"
            rate = to_decimal(t.exchangerate) or Decimal(1)
            keepcleared = bool(t.id)

            # betalingen zoals aa.pl post ze klaarzet (korting = extra betaalregel)
            pays: List[ArapPayment] = [p for p in t.payments if to_decimal(p.amount)]
            if t.discount and to_decimal(t.discount.amount):
                pays.append(t.discount)
            for p in pays:
                # aa.pl post: een betaling op de boekdatum krijgt de koers van de boeking
                if foreign and p.datepaid == t.transdate:
                    p.exchangerate = rate
                if foreign and not to_decimal(p.exchangerate):
                    raise ValidationError("Exchange rate for payment missing!")

            # btw
            acc_taxes = []
            tax = fxtax = diff = ZERO
            for accno in sorted(t.taxes):
                fx = to_decimal(t.taxes[accno])
                fxtax += fx
                tax += fx
                acc_taxes.append((accno, fx, False))
                amount = fx * rate
                taxamount = round_amount(amount - diff, precision)
                diff = taxamount - (amount - diff)
                amount = taxamount - fx
                tax += amount
                if foreign:
                    acc_taxes.append((accno, amount, True))

            # regels
            fxinvamount = sum((to_decimal(l.amount) for l in t.lines), ZERO)
            lineitems = []
            diff = fxdiff = invnetamount = ZERO
            idx = 0
            for l in t.lines:
                fx = to_decimal(l.amount)
                if not fx:
                    continue
                idx += 1
                if t.taxincluded:
                    amount = fxtax * fx / fxinvamount if fxinvamount else ZERO
                    taxamount = round_amount(amount - fxdiff, precision)
                    fx -= taxamount
                    fxdiff = round_amount(taxamount - (amount - fxdiff), 10)
                amount = fx * rate
                base = round_amount(amount - diff, precision)
                diff = base - (amount - diff)
                cleared = l.cleared if keepcleared else None
                lineitems.append((l, fx, False, cleared, idx))
                if foreign:
                    lineitems.append((l, base - fx, True, cleared, idx))
                invnetamount += base
            invamount = invnetamount + tax

            # betaalbedragen
            paid = ZERO
            diff = ZERO
            paidamt = []
            datepaid = None
            for p in pays:
                fx = to_decimal(p.amount)
                paid += fx
                pa = fx * rate
                amount = round_amount(pa - diff, precision)
                diff = amount - (pa - diff)
                datepaid = p.datepaid
                paidamt.append((p, fx, amount))

            if not t.taxincluded:
                fxinvamount += fxtax
            fxinvamount = round_amount(fxinvamount, precision)
            invamount = round_amount(invamount, precision)
            paid = round_amount(paid, precision)
            paid = invamount if fxinvamount == paid else round_amount(paid * rate, precision)

            employee_id = t.employee_id or _db.get_employee(cur, cfg.short_login)[1]
            table = t.table
            trans_id = None
            if t.id:
                cur.execute(f"SELECT id FROM {table} WHERE id = %s", (t.id,))
                row = cur.fetchone()
                if row:
                    trans_id = row["id"]
                    for tb in ("acc_trans", "dpt_trans", "payment"):
                        cur.execute(f"DELETE FROM {tb} WHERE trans_id = %s", (trans_id,))
            if not trans_id:
                cur.execute(f"INSERT INTO {table} (approved) VALUES (%s) RETURNING id", (approved,))
                trans_id = cur.fetchone()["id"]

            department_id = int(t.department_id or 0)
            if department_id:
                cur.execute("INSERT INTO dpt_trans (trans_id, department_id) VALUES (%s, %s)",
                            (trans_id, department_id))

            datepaid = (datepaid or t.transdate) if paid else None
            invnumber = t.invnumber or next_number(
                cur, "sinumber" if t.vc == "customer" else "vinumber", dateformat=cfg.dateformat,
                transdate=t.transdate, currency=t.currency, fields={"description": t.description})
            duedate = t.duedate or t.transdate

            paymentaccno = t.bank_accno
            dcn = None
            if t.vc == "customer":
                whole, _, dec = _plain(fxinvamount).partition(".")
                fields = {"integer_amount": whole, "decimal": (dec + "00")[:2], "invnumber": invnumber,
                          "customernumber": None, "ordnumber": t.ordnumber}
                cur.execute("SELECT customernumber FROM customer WHERE id = %s", (t.vc_id,))
                fields["customernumber"] = (cur.fetchone() or {}).get("customernumber")
                cur.execute("""SELECT bk.membernumber, bk.dcn, bk.rvc FROM bank bk
                               JOIN chart c ON (c.id = bk.id) WHERE c.accno = %s""", (paymentaccno,))
                bk = cur.fetchone() or {}
                fields["membernumber"] = bk.get("membernumber")
                # zonder sjabloon bij de bankrekening blijft een bestaand kenmerk staan; Perl
                # maakte het dan leeg, terwijl het al op de factuur stond (README 48)
                dcn = format_dcn(bk.get("dcn"), fields) or t.dcn
            else:
                dcn = t.dcn          # Perl rekent dcn alleen voor klanten opnieuw uit

            cur.execute(
                f"""UPDATE {table} SET invnumber = %s, description = %s, ordnumber = %s,
                        transdate = %s, {t.vc}_id = %s, taxincluded = %s, amount = %s, duedate = %s,
                        paid = %s, datepaid = %s, netamount = %s, terms = %s, curr = %s, notes = %s,
                        department_id = %s, employee_id = %s, ponumber = %s, cashdiscount = %s,
                        discountterms = %s, onhold = %s, exchangerate = %s, dcn = %s,
                        bank_id = (SELECT id FROM chart WHERE accno = %s), paymentmethod_id = %s,
                        language_code = %s
                    WHERE id = %s""",
                (invnumber, t.description, t.ordnumber, t.transdate, t.vc_id, bool(t.taxincluded),
                 invamount * arapml, duedate, paid * arapml, datepaid, invnetamount * arapml,
                 int(t.terms or 0), currency, t.notes, department_id, employee_id, t.ponumber,
                 to_decimal(t.cashdiscount) / 100, int(t.discountterms or 0), bool(t.onhold), rate,
                 dcn, paymentaccno, int(t.paymentmethod_id or 0), t.language_code or "", trans_id))

            support.update_exchangerate(cur, currency, t.transdate, rate, defaultcurrency)

            ins = """INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, project_id, memo,
                                            fx_transaction, cleared, approved, id)
                     VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s, %s, %s, %s, %s, %s)"""
            for l, amount, fxflag, cleared, idx in lineitems:
                amount = round_amount(amount, precision)
                if amount:
                    cur.execute(ins, (trans_id, l.accno, amount * ml * arapml, t.transdate, l.project_id,
                                      l.description, fxflag, cleared, approved, idx))
            for accno, amount, fxflag in acc_taxes:
                amount = round_amount(amount, precision)
                if amount:
                    cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate,
                                                          fx_transaction, approved)
                                   VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s, %s)""",
                                (trans_id, accno, amount * ml * arapml, t.transdate, fxflag, approved))

            arap: Decimal = invamount
            if invamount:
                cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                               VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s)""",
                            (trans_id, t.arap_accno, invamount * -1 * ml * arapml, t.transdate, approved))
            if fxinvamount == 0:
                arap = Decimal(1)

            fxgain = defaults.get("fxgainloss_accno_id")
            if foreign and pays and not fxgain:
                raise ValidationError("No account for exchange rate gain/loss (fxgainloss_accno_id)")
            paymentid = 1
            for p, fx, amount_base in paidamt:
                pdate = p.datepaid or t.transdate
                prate = to_decimal(p.exchangerate) or Decimal(1)
                if fxinvamount == 0:
                    rate = prate
                if arap:
                    cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, approved)
                                   VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s)""",
                                (trans_id, t.arap_accno, amount_base * ml * arapml, pdate, approved))
                arap = amount_base
                cleared = p.cleared if keepcleared else None
                cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate, source, memo,
                                                      cleared, approved, id)
                               VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, %s, %s, %s, %s, %s)""",
                            (trans_id, p.accno, fx * -1 * ml * arapml, pdate, p.source, p.memo, cleared,
                             approved, paymentid))
                cur.execute("""INSERT INTO payment (id, trans_id, exchangerate, paymentmethod_id)
                               VALUES (%s, %s, %s, %s)""",
                            (paymentid, trans_id, prate, int(p.paymentmethod_id or 0)))
                paymentid += 1
                if foreign:
                    gain = round_amount((round_amount(fx * rate, precision) - round_amount(fx * prate, precision))
                                        * -1, precision)
                    if gain:
                        cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate,
                                                              fx_transaction, cleared, approved)
                                       VALUES (%s, %s, %s, %s, TRUE, %s, %s)""",
                                    (trans_id, fxgain, gain * ml * arapml, pdate, cleared, approved))
                    fxdiff_amount = round_amount(amount_base - fx + gain, precision)
                    if fxdiff_amount:
                        cur.execute("""INSERT INTO acc_trans (trans_id, chart_id, amount, transdate,
                                                              fx_transaction, cleared, source, approved)
                                       VALUES (%s, (SELECT id FROM chart WHERE accno = %s), %s, %s, TRUE,
                                               %s, %s, %s)""",
                                    (trans_id, p.accno, fxdiff_amount * -1 * ml * arapml, pdate, cleared,
                                     p.source, approved))

            _save_status(cur, trans_id, t.printed, t.emailed, t.queued)
            support.save_references(cur, cfg, trans_id, f"{table}_{t.type}", t.references)
            support.audittrail(cur, cfg, trans_id=trans_id, tablename=table, reference=invnumber,
                               formname="transaction", action=action)
            support.save_recurring(cur, trans_id, t.recurring)
            support.remove_locks(cur, cfg, table)
        return trans_id

    # -------------------------------------------------------------- laden

    def get_transaction(self, trans_id: int, vc: str, lock: bool = False,
                        exclude=None) -> ArapTransaction:
        """Laad een bestaande boeking zoals het scherm dat doet (Form::create_links
        + aa.pl create_links), klaar om te wijzigen en opnieuw te boeken.

        Met lock=True wordt de boeking gelockt voor deze gebruiker (eerdere locks
        van deze gebruiker vervallen); heeft een ander hem al open, dan is
        readonly True en haslock de naam. locked: afgesloten periode of revtrans."""
        table = "ar" if vc == "customer" else "ap"
        ARAP = table.upper()
        holder = None
        if lock:
            with self.conn.transaction(), _db.cursor(self.conn) as cur:
                support.remove_locks(cur, self.config)
                cur.execute(f"SELECT 1 FROM {table} WHERE id = %s", (trans_id,))
                if cur.fetchone():
                    holder = support.create_lock(cur, self.config, trans_id, table)
        with _db.cursor(self.conn) as cur:
            defaults = _db.get_defaults(cur, "precision", "cdt", "closedto", "revtrans")
            precision = int(to_decimal(defaults.get("precision") or 2))
            cur.execute(f"""SELECT a.*, a.curr AS currency, ch.accno AS bank_accno
                            FROM {table} a LEFT JOIN chart ch ON (ch.id = a.bank_id)
                            WHERE a.id = %s""", (trans_id,))
            h = cur.fetchone()
            if not h:
                raise NotFoundError(f"{table} {trans_id} not found")
            rate = to_decimal(h["exchangerate"]) or Decimal(1)
            oldinvtotal = round_amount(to_decimal(h["amount"]) / rate, precision)
            ttype = "transaction"
            if oldinvtotal < 0:
                ttype = "credit_note" if vc == "customer" else "debit_note"
                oldinvtotal = -oldinvtotal
            ml = (1 if vc == "customer" else -1) * (-1 if ttype.endswith("_note") else 1)

            cur.execute("SELECT printed, emailed, spoolfile, formname FROM status WHERE trans_id = %s",
                        (trans_id,))
            printed, emailed, queued = set(), set(), {}
            for s in cur.fetchall():
                if s["printed"]:
                    printed.add(s["formname"])
                if s["emailed"]:
                    emailed.add(s["formname"])
                if s["spoolfile"]:
                    queued[s["formname"]] = s["spoolfile"]

            cur.execute("""SELECT c.accno, c.link, ac.source, ac.amount, ac.memo, ac.transdate,
                                  ac.cleared, ac.project_id, ac.id, y.exchangerate, y.paymentmethod_id
                           FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                           LEFT JOIN payment y ON (y.trans_id = ac.trans_id AND ac.id = y.id)
                           WHERE ac.trans_id = %s AND ac.fx_transaction = '0'
                           ORDER BY ac.transdate, ac.id""", (trans_id,))
            # (Perl sorteert alleen op datum; de volgorde binnen een datum is dan
            # willekeurig. Hier blijft de oorspronkelijke regelvolgorde behouden.)
            entries = cur.fetchall()
            if exclude is not None:     # facturen met artikelen: regel- en kostprijsregels (invoice.py)
                entries = [e for e in entries if not exclude(e)]

            vcd = self.vc_defaults(vc, h[f"{vc}_id"], h["transdate"])
            rates = vcd["taxrates"]
            recurring = support.get_recurring(cur, trans_id)
            references = support.all_references(cur, self.config, trans_id)
            cur.execute("SELECT 1 FROM vr WHERE trans_id = %s", (trans_id,))
            batch = cur.fetchone() is not None
        closedto = _closedto(defaults, self.config.dateformat)
        locked = defaults.get("revtrans") not in (None, "", "0") or bool(
            closedto and _num(h["transdate"]) <= closedto)

        lines: List[ArapLine] = []
        taxes: Dict[str, Decimal] = {}
        payments: List[ArapPayment] = []
        discount = None
        arap_accno = None
        unlinked: List[Dict[str, Any]] = []     # regels op rekeningen zonder AR/AP-koppeling
        paidcost: List[Dict[str, Any]] = []
        for e in entries:
            link = e["link"] or ""
            keys = [k for k in link.split(":") if ARAP in k]
            key = keys[-1] if keys else None
            amount = to_decimal(e["amount"])
            if key == f"{ARAP}_paid" and e["transdate"] == h["transdate"] and amount * ml > 0 \
                    and not e["source"] and e["id"] is None:
                paidcost.append(e)       # mogelijk kostenregel op een betaalrekening (zie onder)
            elif key == f"{ARAP}_paid":
                payments.append(ArapPayment(accno=e["accno"], amount=amount * -1 * ml, datepaid=e["transdate"],
                                            source=e["source"], memo=e["memo"],
                                            exchangerate=to_decimal(e["exchangerate"]) or Decimal(1),
                                            cleared=e["cleared"], paymentmethod_id=e["paymentmethod_id"]))
            elif key == f"{ARAP}_discount":
                if discount is None:
                    discount = ArapPayment(accno=e["accno"], amount=amount * -1 * ml, datepaid=e["transdate"],
                                           source=e["source"], memo=e["memo"],
                                           exchangerate=to_decimal(e["exchangerate"]) or Decimal(1),
                                           cleared=e["cleared"], paymentmethod_id=e["paymentmethod_id"])
            elif key == f"{ARAP}_tax":
                if not e["id"]:
                    taxes[e["accno"]] = taxes.get(e["accno"], ZERO) + amount * ml
            elif key == f"{ARAP}_amount":
                lines.append(ArapLine(accno=e["accno"], amount=amount * ml, description=e["memo"],
                                      project_id=e["project_id"], cleared=e["cleared"]))
            elif key == ARAP:
                arap_accno = arap_accno or e["accno"]
            elif key is None:
                unlinked.append(e)

        # Regels op rekeningen zonder AR/AP-koppeling (bv. een oude bankrekening
        # waarvan de koppeling later is weggehaald). Perl liet ze bij opnieuw
        # opslaan vallen (betalingen verdwenen, factuur weer open) of boekte ze
        # zonder rekening; hier blijven ze behouden:
        # - een betaling: regel met een eigen rij in payment, op een andere datum dan
        #   de boekdatum, of op een rekening die in de administratie overwegend met
        #   het teken van een betaling voorkomt;
        # - zonder gekoppelde debiteuren-/crediteurenregel is een andere
        #   ongekoppelde rekening op de boekdatum de debiteuren-/crediteurenrekening;
        # - de rest is kosten/opbrengst.
        paylike = {a: self._payment_like(a, table) for a in {e["accno"] for e in unlinked}}
        if not arap_accno:
            arap_accno = next((e["accno"] for e in unlinked if not paylike[e["accno"]]
                               and e["transdate"] == h["transdate"] and to_decimal(e["amount"]) * ml < 0), None)
        for e in unlinked:
            if e["accno"] == arap_accno:
                continue
            amount = to_decimal(e["amount"])
            is_payment = e["exchangerate"] is not None or e["transdate"] != h["transdate"] or paylike[e["accno"]]
            if not is_payment:
                lines.append(ArapLine(accno=e["accno"], amount=amount * ml, description=e["memo"],
                                      project_id=e["project_id"], cleared=e["cleared"]))
            elif amount:
                payments.append(ArapPayment(accno=e["accno"], amount=amount * -1 * ml, datepaid=e["transdate"],
                                            source=e["source"], memo=e["memo"],
                                            exchangerate=to_decimal(e["exchangerate"]) or Decimal(1),
                                            cleared=e["cleared"], paymentmethod_id=e["paymentmethod_id"]))

        # Kostenregel op een als betaalrekening gekoppelde rekening (bv. een
        # tussenrekening): alleen als regel behouden als het nettobedrag van de
        # factuur dat bevestigt; anders is het een (negatieve) betaling. Perl telde
        # hem altijd als betaling, waardoor het factuurbedrag daalde.
        if paidcost:
            net_header = round_amount(abs(to_decimal(h["netamount"])) / rate, precision)
            extra = [ArapLine(accno=e["accno"], amount=to_decimal(e["amount"]) * ml, description=e["memo"],
                              project_id=e["project_id"], cleared=e["cleared"]) for e in paidcost]
            if round_amount(sum((l.amount for l in lines + extra), ZERO), precision) == net_header:
                lines.extend(extra)
            else:
                for e in paidcost:
                    payments.append(ArapPayment(accno=e["accno"], amount=to_decimal(e["amount"]) * -1 * ml,
                                                datepaid=e["transdate"], source=e["source"], memo=e["memo"],
                                                exchangerate=to_decimal(e["exchangerate"]) or Decimal(1),
                                                cleared=e["cleared"], paymentmethod_id=e["paymentmethod_id"]))

        netamount = sum((l.amount for l in lines), ZERO)
        tax = oldinvtotal - netamount
        if h["taxincluded"] and netamount:
            for l in lines:
                l.amount = round_amount(l.amount * (1 + tax / netamount), precision)

        calctax = set()
        cdt_base = netamount
        if defaults.get("cdt") and discount:
            cdt_base = netamount - discount.amount
        for a, r in rates.items():
            x = round_amount(cdt_base * r, precision)
            if x and to_decimal(taxes.get(a)) == x:
                calctax.add(a)

        return ArapTransaction(
            vc=vc, vc_id=h[f"{vc}_id"], transdate=h["transdate"], arap_accno=arap_accno or vcd["arap_accno"],
            lines=lines, taxes=taxes, id=trans_id, type=ttype, invnumber=h["invnumber"], duedate=h["duedate"],
            terms=h["terms"] or 0, ordnumber=h["ordnumber"], ponumber=h["ponumber"],
            description=h["description"], notes=h["notes"], currency=(h["currency"] or "").strip() or None,
            exchangerate=rate, taxincluded=bool(h["taxincluded"]), department_id=h["department_id"] or None,
            employee_id=h["employee_id"], language_code=h["language_code"], payments=payments,
            discount=discount, cashdiscount=to_decimal(h["cashdiscount"]) * 100,
            discountterms=h["discountterms"] or 0, onhold=bool(h["onhold"]), pending=not h["approved"],
            bank_accno=h["bank_accno"], paymentmethod_id=h["paymentmethod_id"] or None,
            printed=printed, emailed=emailed, queued=queued, calctax=calctax, taxrates=rates,
            oldinvtotal=oldinvtotal, dcn=h["dcn"], recurring=recurring,
            invoice=bool(h["invoice"]), batch=batch, locked=locked, readonly=holder is not None, haslock=holder,
            references=[Reference(description=r["description"], code=r["code"],
                                  archive_id=r["archive_id"], folder=r["folder"],
                                  confidential=bool(r["confidential"]), filename=r["filename"])
                        for r in references])

    def _payment_like(self, accno: str, table: str) -> bool:
        """Wordt deze (ongekoppelde) rekening in ar/ap overwegend gebruikt met het
        teken van een betaling (bij klanten debet, bij leveranciers credit)?"""
        sign = -1 if table == "ar" else 1
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT count(*) FILTER (WHERE ac.amount * %s * sign(a.amount) > 0) AS pay,
                                   count(*) AS n
                            FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id) JOIN {table} a ON (a.id = ac.trans_id)
                            WHERE c.accno = %s AND NOT ac.fx_transaction""", (sign, accno))
            r = cur.fetchone()
        return bool(r["n"]) and r["pay"] * 2 > r["n"]

    # ------------------------------------------------- hulpmiddelen voor het scherm

    def accounts(self, vc: str) -> Dict[str, List[Dict[str, Any]]]:
        """Rekeningen per koppeling voor het boekingsscherm (Form::create_links):
        sleutels AR/AP, AR_amount, AR_tax, AR_paid, AR_discount (of AP_...);
        alleen open rekeningen, omschrijving vertaald naar de taal van de gebruiker."""
        ARAP = "AR" if vc == "customer" else "AP"
        out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in
                                               (ARAP, f"{ARAP}_amount", f"{ARAP}_tax", f"{ARAP}_paid", f"{ARAP}_discount")}
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, COALESCE(l.description, c.description) AS description, c.link
                           FROM chart c
                           LEFT JOIN translation l ON (l.trans_id = c.id AND l.language_code = %s)
                           WHERE c.link LIKE %s AND c.closed = '0' AND c.charttype = 'A'
                           ORDER BY c.accno""", (self.config.countrycode, f"%{ARAP}%"))
            for r in cur.fetchall():
                for key in (r["link"] or "").split(":"):
                    if key in out:
                        out[key].append({"accno": r["accno"], "description": r["description"]})
        return out

    def vc_info(self, vc: str, vc_id: int, exclude_id: Optional[int] = None) -> Dict[str, Any]:
        """Adres, notities, kredietlimiet en wat daarvan resteert (AA::get_name):
        limiet min openstaande posten en open orders. exclude_id: deze boeking
        niet meetellen (het scherm rekent het bedrag op het formulier zelf mee)."""
        table = "ar" if vc == "customer" else "ap"
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT c.name, c.{vc}number AS number, c.notes, c.creditlimit,
                                   ad.address1, ad.address2, ad.city, ad.state, ad.zipcode, ad.country
                            FROM {vc} c LEFT JOIN address ad ON (ad.trans_id = c.id) WHERE c.id = %s""", (vc_id,))
            ref = cur.fetchone()
            if not ref:
                raise NotFoundError(f"{vc} {vc_id} not found")
            cur.execute(f"""SELECT COALESCE(SUM(a.amount::numeric - a.paid::numeric), 0) AS open FROM {table} a
                            WHERE a.amount != a.paid AND a.{vc}_id = %s AND a.id <> %s""", (vc_id, int(exclude_id or 0)))
            open_items = to_decimal(cur.fetchone()["open"])
            cur.execute(f"""SELECT COALESCE(SUM(a.amount::numeric), 0) AS open FROM oe a
                            WHERE a.quotation = '0' AND a.closed = '0' AND a.{vc}_id = %s""", (vc_id,))
            orders = to_decimal(cur.fetchone()["open"])
        ref["creditlimit"] = to_decimal(ref["creditlimit"])
        ref["creditremaining"] = ref["creditlimit"] - open_items - orders
        return ref

    def last_lines(self, vc: str, vc_id: int) -> List[ArapLine]:
        """Rekeningen (en projecten) van de laatste boeking van deze relatie, zonder
        bedrag: het scherm vult die voor bij een nieuwe boeking (AA::get_name)."""
        table = "ar" if vc == "customer" else "ap"
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT c.accno, c.link, ac.project_id FROM chart c
                            JOIN acc_trans ac ON (ac.chart_id = c.id)
                            WHERE ac.trans_id = (SELECT max(id) FROM {table} WHERE {vc}_id = %s)
                            ORDER BY ac.transdate, ac.id NULLS LAST""", (vc_id,))
            return [ArapLine(accno=r["accno"], amount=ZERO, project_id=r["project_id"])
                    for r in cur.fetchall() if "_amount" in (r["link"] or "")]

    def last_used(self, vc: str) -> Optional[Dict[str, Any]]:
        """Relatie en afdeling van de laatste boeking (Form::lastname_used): een nieuwe
        boeking begint daarmee."""
        table = "ar" if vc == "customer" else "ap"
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT a.{vc}_id AS vc_id, a.department_id FROM {table} a
                            WHERE a.id = (SELECT max(id) FROM {table} WHERE {vc}_id > 0)""")
            return cur.fetchone()

    def next_invnumber(self, vc: str, transdate: Optional[date] = None, currency: Optional[str] = None,
                       description: Optional[str] = None) -> str:
        """Volgend factuurnummer nu al uitgeven (knop 'Nieuw nummer'; telt de teller op)."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            return next_number(cur, "sinumber" if vc == "customer" else "vinumber",
                               dateformat=self.config.dateformat, transdate=transdate, currency=currency,
                               fields={"description": description})

    def same_invnumber(self, vc: str, vc_id: int, invnumber: str, exclude_id: Optional[int] = None) -> List[int]:
        """Andere boekingen van deze relatie met hetzelfde factuurnummer (om voor
        dubbel invoeren te waarschuwen; Perl controleerde dit niet)."""
        table = "ar" if vc == "customer" else "ap"
        if not invnumber:
            return []
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT id FROM {table} WHERE {vc}_id = %s AND lower(invnumber) = lower(%s)
                            AND id <> %s ORDER BY id""", (vc_id, invnumber, int(exclude_id or 0)))
            return [r["id"] for r in cur.fetchall()]

    def employees(self, transdate: Optional[date] = None, include_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Verkopers/medewerkers voor de keuzelijst (Form::all_employees met sales):
        in dienst op transdate; include_id (die van de boeking) staat er altijd in."""
        where, params = "enddate IS NULL", []
        if transdate:
            where = "(startdate IS NULL OR startdate <= %s) AND (enddate IS NULL OR enddate >= %s)"
            params = [transdate, transdate]
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT id, name FROM employee WHERE {where} AND sales = '1'
                            UNION SELECT id, name FROM employee WHERE id = %s
                            ORDER BY 2, 1""", params + [int(include_id or 0)])
            return cur.fetchall()

    def payment_methods(self) -> List[Dict[str, Any]]:
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT id, description, roundchange FROM paymentmethod ORDER BY rn")
            return cur.fetchall()

    def unlock(self, vc: str) -> None:
        """Geef de locks van deze gebruiker op verkoop- of inkoopboekingen vrij."""
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            support.remove_locks(cur, self.config, "ar" if vc == "customer" else "ap")

    # --------------------------------------------------------- relatie zoeken

    def find_names(self, vc: str, *, name: Optional[str] = None, number: Optional[str] = None,
                   transdate: Optional[date] = None) -> List[Dict[str, Any]]:
        """Klanten/leveranciers zoeken op naam of nummer (Form::get_name): met
        transdate alleen relaties die op die datum actief zijn (start-/einddatum),
        zonder alleen relaties zonder einddatum. Sortering op naam, of op nummer
        als defaults.namesbynumber aan staat. Is een nummer opgegeven, dan wordt
        op nummer gezocht (zoals het scherm doet als het nummerveld gewijzigd is).
        """
        if vc not in ("customer", "vendor"):
            raise ValueError("vc moet 'customer' of 'vendor' zijn")
        where, params = ["1=1"], []
        if transdate:
            where.append("(ct.startdate IS NULL OR ct.startdate <= %s) AND (ct.enddate IS NULL OR ct.enddate >= %s)")
            params += [transdate, transdate]
        else:
            where.append("ct.enddate IS NULL")
        if number:
            where.append(f"lower(ct.{vc}number) LIKE %s")
            params.append(like(number.lower()))
        else:
            where.append("lower(ct.name) LIKE %s")
            params.append(like((name or "").lower()))
        with _db.cursor(self.conn) as cur:
            byno = _db.get_defaults(cur, "namesbynumber").get("namesbynumber")
            order = f"ct.{vc}number" if (byno or number) else "ct.name"
            cur.execute(f"""SELECT ct.*, ct.{vc}number AS number, ad.address1, ad.address2, ad.city, ad.state,
                                   ad.zipcode, ad.country
                            FROM {vc} ct JOIN address ad ON (ad.trans_id = ct.id)
                            WHERE {' AND '.join(where)} ORDER BY {order}, ct.id""", params)
            rows = cur.fetchall()
        for r in rows:
            r["curr"] = (r.get("curr") or "").strip() or None
        return rows

    def all_names(self, vc: str, *, transdate: Optional[date] = None, open_invoices: bool = False,
                  include_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Keuzelijst van relaties (Form::all_vc): actief op transdate, of alleen
        relaties met openstaande posten; include_id staat er altijd in (de
        relatie van de boeking zelf)."""
        if vc not in ("customer", "vendor"):
            raise ValueError("vc moet 'customer' of 'vendor' zijn")
        table = "ar" if vc == "customer" else "ap"
        join, where, params = "", ["1 = 1"], []
        if open_invoices:
            join = f"JOIN {table} a ON (a.{vc}_id = vc.id)"
            where.append("a.amount != a.paid")
        if transdate:
            where.append("(vc.startdate IS NULL OR vc.startdate <= %s) AND (vc.enddate IS NULL OR vc.enddate >= %s)")
            params += [transdate, transdate]
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT vc.id, vc.name FROM {vc} vc {join} WHERE {' AND '.join(where)}
                            UNION SELECT vc.id, vc.name FROM {vc} vc WHERE vc.id = %s
                            ORDER BY 2, 1""", params + [int(include_id or 0)])
            return cur.fetchall()

    # ------------------------------------------------------------- overzicht

    def list_transactions(self, vc: str, search: "Optional[ArapSearch]" = None) -> "ArapList":
        """Overzicht/openstaande posten (AA::transactions + aa.pl); zie arap_list."""
        from .arap_list import list_transactions
        return list_transactions(self.conn, self.config, vc, search)

    # ---------------------------------------------------------- verwijderen

    def delete_transaction(self, trans_id: int, vc: str) -> None:
        """Verwijder een verkoop-/inkoopboeking (AA::delete_transaction)."""
        cfg = self.config
        table = "ar" if vc == "customer" else "ap"
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute(f"SELECT invnumber, transdate FROM {table} WHERE id = %s", (trans_id,))
            row = cur.fetchone()
            if not row:
                raise NotFoundError(f"{table} {trans_id} not found")
            defaults = _db.get_defaults(cur, "closedto", "revtrans")
            if defaults.get("revtrans") not in (None, "", "0"):
                raise LockedTransactionError("Transaction is locked (revtrans)")
            closedto = _closedto(defaults, cfg.dateformat)
            if closedto and _num(row["transdate"]) <= closedto:
                raise ClosedPeriodError("Cannot delete a transaction in a closed period!")
            cur.execute("SELECT 1 FROM vr WHERE trans_id = %s", (trans_id,))
            if cur.fetchone():
                raise ValidationError("Transactions in batches are not supported")
            support.audittrail(cur, cfg, trans_id=trans_id, tablename=table, reference=row["invnumber"],
                               formname="transaction", action="deleted")
            cur.execute(f"DELETE FROM {table} WHERE id = %s", (trans_id,))
            cur.execute("SELECT glid FROM pay_trans WHERE trans_id = %s", (trans_id,))
            g = cur.fetchone()
            for tid in (trans_id, g["glid"] if g else None):
                if not tid:
                    continue
                for tb in ("acc_trans", "dpt_trans", "payment", "status", "pay_trans"):
                    cur.execute(f"DELETE FROM {tb} WHERE trans_id = %s", (tid,))
                for tb in ("recurring", "recurringemail", "recurringprint"):
                    cur.execute(f"DELETE FROM {tb} WHERE id = %s", (tid,))
            support.delete_references(cur, cfg, trans_id)
            support.remove_locks(cur, cfg, table)


def _days(n) -> "timedelta":
    from datetime import timedelta
    return timedelta(days=int(n or 0))


def _save_status(cur, trans_id: int, printed: Set[str], emailed: Set[str], queued: Dict[str, str]) -> None:
    """Form::save_status: printed/emailed/spoolfile per formulier."""
    cur.execute("DELETE FROM status WHERE trans_id = %s", (trans_id,))
    printed, emailed = set(printed), set(emailed)
    for formname, spool in queued.items():
        if spool:
            cur.execute("""INSERT INTO status (trans_id, printed, emailed, spoolfile, formname)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (trans_id, formname in printed, formname in emailed, spool, formname))
        printed.discard(formname)
        emailed.discard(formname)
    for formname in sorted(printed | emailed):
        cur.execute("""INSERT INTO status (trans_id, printed, emailed, formname)
                       VALUES (%s, %s, %s, %s)""", (trans_id, formname in printed, formname in emailed, formname))
