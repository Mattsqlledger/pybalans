"""Betalingen en ontvangsten op openstaande posten (port van SL/CP.pm en SL/OP.pm
+ de rekenregels uit bin/mozilla/cp.pl).

Eén betaling (ontvangst van een klant, 'receipt', of betaling aan een
leverancier, 'check') wordt verdeeld over openstaande facturen. Per factuur
komen er regels bij in ``acc_trans`` van díe factuur (debiteuren/crediteuren,
bank, koersverschil, koerswinst/-verlies, contante korting met btw-correctie)
en worden ``paid``/``datepaid`` bijgewerkt. Een rest zonder factuur wordt een
vooruitbetaling: een nieuwe ar/ap-boeking met bedrag 0 en ``paid`` = de rest.

Bedragen zijn positief en in de valuta van de betaling, zoals een gebruiker ze
in het betaalscherm invoert. Batches/vouchers worden niet ondersteund.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg

from . import _db, support
from .amounts import round_amount, to_decimal
from .config import UserConfig
from .errors import ClosedPeriodError, NotFoundError, ValidationError
from .gl import _closedto, _num, like
from .numbering import next_number

ZERO = Decimal(0)


def _tables(vc: str):
    if vc not in ("customer", "vendor"):
        raise ValueError("vc moet 'customer' of 'vendor' zijn")
    table = "ar" if vc == "customer" else "ap"
    return table, table.upper()


@dataclass
class OpenInvoice:
    """Openstaande post zoals het betaalscherm hem toont (CP::get_openinvoices).

    amount/paid/netamount staan in de standaardvaluta (zoals in ar/ap);
    due, netamount_fx, discount en paid_suggested in de valuta van de factuur.
    """

    id: int
    invnumber: Optional[str]
    transdate: date
    duedate: Optional[date]
    description: Optional[str]
    vc_id: int
    vcnumber: Optional[str]
    name: Optional[str]
    currency: Optional[str]
    amount: Decimal
    paid: Decimal
    netamount: Decimal
    exchangerate: Decimal
    cashdiscount: Decimal              # fractie, bv. 0.02
    discountterms: int
    calcdiscount: bool                 # korting nog mogelijk op de betaaldatum
    taxincluded: bool
    language_code: Optional[str] = None
    threshold: Decimal = ZERO
    payment_accno_id: Optional[int] = None
    precision: int = 2

    @property
    def due(self) -> Decimal:
        return round_amount((self.amount - self.paid) / self.exchangerate, self.precision)

    @property
    def netamount_fx(self) -> Decimal:
        return round_amount(self.netamount / self.exchangerate, self.precision)

    @property
    def discount(self) -> Decimal:
        """Voorgestelde contante korting (cp.pl update_payment)."""
        if not self.calcdiscount:
            return ZERO
        return round_amount(self.netamount_fx * self.cashdiscount, self.precision)

    @property
    def paid_suggested(self) -> Decimal:
        """Voorgesteld te betalen bedrag: openstaand min korting (cp.pl rekent
        met de onafgeronde korting en rondt daarna af)."""
        raw = self.netamount_fx * self.cashdiscount if self.calcdiscount else ZERO
        return round_amount(self.due - raw, self.precision)


@dataclass
class PaymentLine:
    """Toewijzing van (een deel van) de betaling aan één factuur."""

    trans_id: int
    paid: Decimal
    discount: Decimal = ZERO
    #: openstaand bedrag (valuta van de factuur) zoals het scherm het toonde; is het
    #: bij het boeken anders, dan is de post intussen gewijzigd en wordt er niet geboekt
    expected_due: Optional[Decimal] = None


@dataclass
class Payment:
    """Ontvangst (vc='customer') of betaling (vc='vendor').

    amount: totaalbedrag van de betaling; standaard de som van de regels. Wat
    niet aan facturen is toegewezen wordt een vooruitbetaling.
    source: betaalkenmerk/chequenummer; None = het voorgestelde volgende nummer
    van deze betaalrekening (zoals het scherm het invult).
    arap_accno: debiteuren-/crediteurenrekening voor een vooruitbetaling
    (standaard die van de relatie).
    """

    vc: str
    vc_id: int
    datepaid: date
    payment_accno: str
    lines: List[PaymentLine] = field(default_factory=list)
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    exchangerate: Optional[Decimal] = None
    source: Optional[str] = None
    memo: Optional[str] = None
    discount_accno: Optional[str] = None
    arap_accno: Optional[str] = None
    department_id: Optional[int] = None
    paymentmethod_id: Optional[int] = None
    pending: bool = False

    @property
    def table(self) -> str:
        return _tables(self.vc)[0]


class ChangedError(ValidationError):
    """Een post is na het tonen gewijzigd (betaald, aangepast of verwijderd)."""

    def __init__(self, message: str, trans_id: int):
        super().__init__(message)
        self.trans_id = trans_id


@dataclass
class PaymentResult:
    trans_ids: List[int]                 # facturen waarop betaald is
    overpayment_id: Optional[int]        # nieuwe ar/ap-boeking voor de rest
    source: Optional[str]


class Payments:
    """Openstaande posten zoeken en betalingen boeken."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # --------------------------------------------------------- selecties

    def open_vc(self, vc: str, *, arap_accno: Optional[str] = None, business_id: Optional[int] = None,
                paymentmethod_id: Optional[int] = None, name: Optional[str] = None,
                number: Optional[str] = None, duedatefrom: Optional[date] = None,
                duedateto: Optional[date] = None) -> List[Dict[str, Any]]:
        """Relaties met openstaande posten (CP::get_openvc).

        Afwijking: Perl telde voor de drempel (vendor.threshold) het openstaande
        bedrag van een factuur één keer per journaalregel mee; hier één keer
        per factuur.
        """
        table, ARAP = _tables(vc)
        where = ["a.amount != a.paid", "a.approved = '1'", "a.onhold = '0'",
                 "NOT a.id IN (SELECT id FROM semaphore)"]
        params: List[Any] = []
        if duedatefrom:
            where.append("a.duedate >= %s")
            params.append(duedatefrom)
        if duedateto:
            where.append("a.duedate <= %s")
            params.append(duedateto)
        if arap_accno:
            where.append("EXISTS (SELECT 1 FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id) "
                         "WHERE ac.trans_id = a.id AND c.accno = %s)")
            params.append(arap_accno)
        if vc == "vendor" and business_id:
            where.append("vc.business_id = %s")
            params.append(business_id)
        if paymentmethod_id:
            where.append("a.paymentmethod_id = %s")
            params.append(paymentmethod_id)
        if name:
            where.append("lower(vc.name) LIKE %s")
            params.append(like(name.lower()))
        if number:
            where.append(f"lower(vc.{vc}number) LIKE %s")
            params.append(like(number.lower()))
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            sortorder = f"{vc}number" if _db.get_defaults(cur, "namesbynumber").get("namesbynumber") else "name"
            cur.execute(f"""
                SELECT vc.*, ad.address1, ad.address2, ad.city, ad.state, ad.zipcode, ad.country,
                       a.amount, a.paid, a.exchangerate, a.id AS trans_id,
                       ch.accno AS arap_accno, pa.accno AS payment_accno, pm.description AS paymentmethod
                FROM {vc} vc
                JOIN {table} a ON (a.{vc}_id = vc.id)
                JOIN address ad ON (ad.trans_id = vc.id)
                LEFT JOIN chart ch ON (ch.id = vc.arap_accno_id)
                LEFT JOIN chart pa ON (pa.id = vc.payment_accno_id)
                LEFT JOIN paymentmethod pm ON (pm.id = vc.paymentmethod_id)
                WHERE {' AND '.join(where)}
                ORDER BY vc.{sortorder}, vc.id""", params)
            rows = cur.fetchall()
        due: Dict[int, Decimal] = {}
        for r in rows:
            rate = to_decimal(r["exchangerate"]) or Decimal(1)
            due[r["id"]] = due.get(r["id"], ZERO) + round_amount(
                (to_decimal(r["amount"]) - to_decimal(r["paid"])) / rate, precision)
        result, seen = [], set()
        for r in rows:
            if r["id"] in seen:
                continue
            if vc == "vendor" and to_decimal(r.get("threshold")) > 0 and due[r["id"]] < to_decimal(r["threshold"]):
                continue
            seen.add(r["id"])
            r = dict(r)
            for k in ("amount", "paid", "exchangerate", "trans_id"):
                r.pop(k, None)
            r["curr"] = (r.get("curr") or "").strip() or None
            result.append(r)
        return result

    def open_invoices(self, vc: str, vc_id: Optional[int] = None, *, currency: Optional[str] = None,
                      datepaid: Optional[date] = None, duedatefrom: Optional[date] = None,
                      duedateto: Optional[date] = None, department_id: Optional[int] = None,
                      paymentmethod_id: Optional[int] = None, arap_accno: Optional[str] = None,
                      business_id: Optional[int] = None, lock: bool = False) -> List[OpenInvoice]:
        """Openstaande posten van één relatie, of van alle relaties als vc_id
        None is (CP::get_openinvoices, 'payment' resp. 'payments').

        lock: zet zoals het scherm een lock op elke getoonde post (de eigen
        locks voor deze module worden eerst opgeruimd); post_payment ruimt ze op.
        Zonder lock blijven de eigen locks staan en vallen alleen posten weg die
        een ander gelockt heeft (webscherm: locken pas bij het boeken).
        """
        table, ARAP = _tables(vc)
        cfg = self.config
        where = ["a.amount != a.paid", "a.approved = '1'", "a.onhold = '0'"]
        params: List[Any] = []
        if vc_id is not None:
            where.insert(0, f"a.{vc}_id = %s")
            params.append(vc_id)
        if currency:
            where.append("a.curr = %s")
            params.append(currency)
        if duedatefrom:
            where.append("a.duedate >= %s")
            params.append(duedatefrom)
        if duedateto:
            where.append("a.duedate <= %s")
            params.append(duedateto)
        if department_id:
            where.append("a.department_id = %s")
            params.append(department_id)
        if paymentmethod_id:
            where.append("a.paymentmethod_id = %s")
            params.append(paymentmethod_id)
        if arap_accno:
            where.append("ch.accno = %s")
            params.append(arap_accno)
        if vc == "vendor" and business_id:
            where.append("vc.business_id = %s")
            params.append(business_id)
        if lock:
            where.append("NOT a.id IN (SELECT id FROM semaphore)")
        else:
            where.append("NOT a.id IN (SELECT id FROM semaphore WHERE login <> %s)")
            params.append(cfg.login)
        datepaid = datepaid or date.today()

        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            if lock:
                support.remove_locks(cur, cfg, table)
            defaults = _db.get_defaults(cur, "namesbynumber", "precision")
            precision = int(to_decimal(defaults.get("precision") or 2))
            if vc_id is not None:
                order = "a.transdate, a.invnumber"
            elif defaults.get("namesbynumber"):
                order = f"vc.{vc}number, a.transdate"
            else:
                order = "vc.name, a.transdate"
            cur.execute(f"""
                SELECT DISTINCT a.id, a.invnumber, a.transdate, a.duedate, a.description AS invdescription,
                       a.amount, a.paid, a.curr, vc.{vc}number AS vcnumber, vc.name, vc.language_code,
                       vc.threshold, vc.curr AS vccurrency, vc.payment_accno_id, a.{vc}_id AS vc_id,
                       a.discountterms, a.cashdiscount, a.netamount,
                       %s::date <= a.transdate + a.discountterms AS calcdiscount,
                       a.exchangerate, ex.exchangerate AS vcexch, a.taxincluded
                FROM acc_trans ac
                JOIN {table} a ON (a.id = ac.trans_id)
                JOIN {vc} vc ON (vc.id = a.{vc}_id)
                JOIN chart ch ON (ch.id = ac.chart_id)
                LEFT JOIN exchangerate ex ON (ex.curr = vc.curr AND ex.transdate = a.transdate)
                WHERE {' AND '.join(where)}
                ORDER BY {order}, a.id""", [datepaid] + params)
            rows = cur.fetchall()

            invoices: List[OpenInvoice] = []
            total: Dict[int, Decimal] = {}
            for r in rows:
                calc = bool(r["calcdiscount"]) and bool(to_decimal(r["cashdiscount"]))
                if calc:
                    cur.execute("""SELECT sum(ac.amount) AS s FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                                   WHERE c.link LIKE %s AND ac.approved = '1' AND ac.trans_id = %s""",
                                (f"%{ARAP}_discount%", r["id"]))
                    if to_decimal(cur.fetchone()["s"]):
                        calc = False
                vcexch = to_decimal(r["vcexch"]) or Decimal(1)
                fxdue = round_amount((to_decimal(r["amount"]) - to_decimal(r["paid"])) / vcexch, precision)
                total[r["vc_id"]] = total.get(r["vc_id"], ZERO) + fxdue
                invoices.append(OpenInvoice(
                    id=r["id"], invnumber=r["invnumber"], transdate=r["transdate"], duedate=r["duedate"],
                    description=r["invdescription"], vc_id=r["vc_id"], vcnumber=r["vcnumber"], name=r["name"],
                    currency=(r["curr"] or "").strip() or None, amount=to_decimal(r["amount"]),
                    paid=to_decimal(r["paid"]), netamount=to_decimal(r["netamount"]),
                    exchangerate=to_decimal(r["exchangerate"]) or Decimal(1),
                    cashdiscount=to_decimal(r["cashdiscount"]), discountterms=int(r["discountterms"] or 0),
                    calcdiscount=calc, taxincluded=bool(r["taxincluded"]), language_code=r["language_code"],
                    threshold=to_decimal(r["threshold"]), payment_accno_id=r["payment_accno_id"],
                    precision=precision))
            result = []
            for inv in invoices:
                if vc == "vendor" and inv.threshold > 0 and round_amount(total[inv.vc_id], precision) < inv.threshold:
                    continue
                if lock:
                    support.create_lock(cur, cfg, inv.id, table, add=True)
                result.append(inv)
        return result

    def next_source(self, vc: str, payment_accno: str) -> Optional[str]:
        """Voorgesteld betaalkenmerk voor deze betaalrekening (cp.pl: veld
        receipt_<accno> / check_<accno> in defaults)."""
        kind = "receipt" if vc == "customer" else "check"
        with _db.cursor(self.conn) as cur:
            return _db.get_defaults(cur, f"{kind}_{payment_accno}").get(f"{kind}_{payment_accno}")

    # ------------------------------------------------------------ boeken

    def post_payment(self, p: Payment) -> PaymentResult:
        """Boek een ontvangst/betaling (cp.pl post_payment + CP::post_payment +
        OP::overpayment)."""
        cfg = self.config
        table, ARAP = _tables(p.vc)
        kind = "receipt" if p.vc == "customer" else "check"
        ml = 1 if ARAP == "AR" else -1
        approved = not p.pending
        action = "posted" if approved else "saved"

        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            defaultcurrency = _db.default_currency(cur)
            defaults = _db.get_defaults(cur, "closedto", "precision", "fxgainloss_accno_id", "cdt")
            precision = int(to_decimal(defaults.get("precision") or 2))
            currency = p.currency or defaultcurrency
            foreign = currency != defaultcurrency
            lines = [PaymentLine(l.trans_id, round_amount(to_decimal(l.paid), precision),
                                 round_amount(to_decimal(l.discount), precision), l.expected_due) for l in p.lines]
            lines = [l for l in lines if l.paid or l.discount]
            amount_total = round_amount(to_decimal(p.amount), precision) if p.amount is not None \
                else sum((l.paid for l in lines), ZERO)
            rest = round_amount(amount_total - sum((l.paid for l in lines), ZERO), precision)

            # --- controles (cp.pl check_form + aanvullingen)
            if not p.datepaid:
                raise ValidationError("Date missing!")
            closedto = _closedto(defaults, cfg.dateformat)
            if closedto and _num(p.datepaid) <= closedto:
                raise ClosedPeriodError("Cannot post payment for a closed period!")
            rate = Decimal(1)
            if foreign:
                rate = to_decimal(p.exchangerate)
                if not rate:
                    raise ValidationError("Exchange rate missing!")
            if not lines and not rest:
                raise ValidationError("Nothing to post!")
            # (negatieve bedragen mogen: een creditnota wordt zo verrekend of terugbetaald)
            if rest < 0 and p.amount is not None and sum((l.paid for l in lines), ZERO) > 0:
                raise ValidationError("Amount is less than the total of the allocated payments!")
            if rest and amount_total < 0:
                raise ValidationError("A negative payment cannot leave a prepayment")
            if len({l.trans_id for l in lines}) != len(lines):
                raise ValidationError("Invoice selected more than once")
            cur.execute(f"SELECT id FROM {p.vc} WHERE id = %s", (p.vc_id,))
            if not cur.fetchone():
                raise ValidationError(f"Unknown {p.vc} {p.vc_id}")
            payment_chart = self._chart(cur, p.payment_accno, f"{ARAP}_paid")
            discount_chart = None
            if any(l.discount for l in lines):
                if not p.discount_accno:
                    raise ValidationError("Cash discount account missing!")
                discount_chart = self._chart(cur, p.discount_accno, f"{ARAP}_discount")
            fxgain = defaults.get("fxgainloss_accno_id")
            for l in lines:
                cur.execute(f"""SELECT {p.vc}_id AS vc_id, curr, approved, onhold, invnumber, amount, paid,
                                       exchangerate FROM {table} WHERE id = %s FOR UPDATE""", (l.trans_id,))
                inv = cur.fetchone()
                if not inv:
                    raise NotFoundError(f"{table} {l.trans_id} not found")
                if inv["vc_id"] != p.vc_id:
                    raise ValidationError(f"{table} {l.trans_id} belongs to another {p.vc}")
                if not inv["approved"]:
                    raise ValidationError(f"{table} {l.trans_id} is not approved")
                if ((inv["curr"] or "").strip() or defaultcurrency) != currency:
                    raise ValidationError(f"{table} {l.trans_id} is in another currency")
                due = round_amount((to_decimal(inv["amount"]) - to_decimal(inv["paid"]))
                                   / (to_decimal(inv["exchangerate"]) or Decimal(1)), precision)
                if l.expected_due is not None and due != round_amount(to_decimal(l.expected_due), precision):
                    raise ChangedError(f"Open item {inv['invnumber']} changed meanwhile", l.trans_id)
                # Afwijking: Perl liet een factuur overbetalen (betaald > bedrag); het meerdere hoort
                # een vooruitbetaling te zijn.
                total = l.paid + l.discount
                if total and (not due or (total > 0) != (due > 0) or abs(total) > abs(due)):
                    raise ValidationError(f"Payment exceeds the amount due for {inv['invnumber']}")
                cur.execute("SELECT 1 FROM vr WHERE trans_id = %s", (l.trans_id,))
                if cur.fetchone():
                    raise ValidationError("Transactions in batches are not supported")
                cur.execute("SELECT login FROM semaphore WHERE id = %s AND login <> %s",
                            (l.trans_id, cfg.login))
                if cur.fetchone():
                    raise ValidationError(f"{table} {l.trans_id} is locked by another user")

            # --- koers en betaalkenmerk
            if foreign:
                support.update_exchangerate(cur, currency, p.datepaid, rate, defaultcurrency)
            source = p.source
            if source is None:
                source = _db.get_defaults(cur, f"{kind}_{p.payment_accno}").get(f"{kind}_{p.payment_accno}")
            next_number(cur, f"{kind}_{p.payment_accno}", dateformat=cfg.dateformat, ini=source)

            cur.execute("SELECT id FROM chart WHERE link = %s ORDER BY id LIMIT 1", (ARAP,))
            row = cur.fetchone()
            arapdefault = row["id"] if row else None
            paymentmethod_id = int(p.paymentmethod_id or 0)

            ins = """INSERT INTO acc_trans (trans_id, chart_id, transdate, amount, source, memo,
                                            fx_transaction, cleared, approved, id)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""

            def need_fxgain(amount: Decimal) -> int:
                if amount and not fxgain:
                    raise ValidationError("No account for exchange rate gain/loss (fxgainloss_accno_id)")
                return int(fxgain) if fxgain else None

            for l in lines:
                tid = l.trans_id
                cur.execute("SELECT max(id) AS m FROM payment WHERE trans_id = %s", (tid,))
                paymentid = int(cur.fetchone()["m"] or 0) + 1
                cur.execute(f"""SELECT amount, netamount, paid, transdate, taxincluded, exchangerate
                                FROM {table} WHERE id = %s FOR UPDATE""", (tid,))
                t = cur.fetchone()
                trate = to_decimal(t["exchangerate"]) or Decimal(1)
                cur.execute("""SELECT DISTINCT c.id FROM chart c JOIN acc_trans a ON (a.chart_id = c.id)
                               WHERE c.link = %s AND a.trans_id = %s ORDER BY c.id LIMIT 1""", (ARAP, tid))
                row = cur.fetchone()
                arap = row["id"] if row else arapdefault
                if not arap:
                    raise ValidationError(f"No {ARAP} account for {table} {tid}")

                # debiteuren/crediteuren, bank, betaling
                amount = round_amount(l.paid * trate, precision)
                cur.execute(ins, (tid, arap, p.datepaid, amount * ml, None, None, False, None, approved, None))
                cur.execute(ins, (tid, payment_chart, p.datepaid, l.paid * ml * -1, source, p.memo, False, None,
                                  approved, paymentid))
                cur.execute("""INSERT INTO payment (id, trans_id, exchangerate, paymentmethod_id)
                               VALUES (%s, %s, %s, %s)""", (paymentid, tid, rate, paymentmethod_id))
                # koersverschil en koerswinst/-verlies
                amount = round_amount(l.paid * (rate - 1) * ml * -1, precision)
                if amount:
                    cur.execute(ins, (tid, payment_chart, p.datepaid, amount, source, None, True, None,
                                      approved, None))
                amount = round_amount((round_amount(l.paid * trate, precision)
                                       - round_amount(l.paid * rate, precision)) * ml * -1, precision)
                if amount:
                    cur.execute(ins, (tid, need_fxgain(amount), p.datepaid, amount, None, None, True, None,
                                      approved, None))

                # contante korting, met btw-correctie (defaults.cdt)
                cdt_total = ZERO
                if l.discount:
                    cdt: Dict[str, Decimal] = {}
                    if defaults.get("cdt") and not t["taxincluded"]:
                        rates = self._tax_rates(cur, tid, ARAP, t["transdate"])
                        totalrate = sum(rates.values(), ZERO)
                        cdt_total = round_amount(l.discount * totalrate, precision)
                        diff = ZERO
                        last = None
                        for accno in sorted(rates):
                            last = accno
                            if totalrate:
                                x = cdt_total * rates[accno] / totalrate
                                cdt[accno] = round_amount(x, precision)
                                diff += x - cdt[accno]
                            else:
                                cdt[accno] = ZERO
                        diff = round_amount(diff, precision)
                        if diff and last is not None:
                            cdt[last] -= diff
                        cdt_total = round_amount(cdt_total * trate, precision)
                    cdt[p.discount_accno] = l.discount
                    for accno, value in cdt.items():
                        chart_id = discount_chart if accno == p.discount_accno else self._chart(cur, accno)
                        amount = round_amount(value * trate, precision)
                        cur.execute(ins, (tid, arap, p.datepaid, amount * ml, None, None, False, None,
                                          approved, tid))
                        cur.execute(ins, (tid, chart_id, p.datepaid, value * ml * -1, source, p.memo, False,
                                          None, approved, tid))
                        amount = round_amount(value * (rate - 1) * ml * -1, precision)
                        if amount:
                            cur.execute(ins, (tid, chart_id, p.datepaid, amount, source, None, True, None,
                                              approved, tid))
                            amount = round_amount((round_amount(value * trate, precision)
                                                   - round_amount(value * rate, precision)) * ml * -1, precision)
                            if amount:
                                cur.execute(ins, (tid, need_fxgain(amount), p.datepaid, amount, None, None,
                                                  True, None, approved, tid))

                paid_base = round_amount(l.paid * trate, precision)
                discount_base = round_amount(l.discount * trate, precision)
                newpaid = round_amount(to_decimal(t["paid"]) + paid_base + discount_base, precision)
                newamount = to_decimal(t["amount"])
                if defaults.get("cdt") and not t["taxincluded"]:
                    newamount -= cdt_total
                cur.execute(f"""UPDATE {table} SET amount = %s, paid = %s, datepaid = %s, bank_id = %s,
                                       paymentmethod_id = %s WHERE id = %s""",
                            (newamount, newpaid, p.datepaid, payment_chart, paymentmethod_id, tid))
                support.audittrail(cur, cfg, trans_id=tid, tablename=table, reference=source,
                                   formname=kind, action=action)

            overpayment_id = None
            if rest:
                overpayment_id = self._overpayment(cur, p, rest, rate, currency, source, payment_chart,
                                                   approved, action, ml)
            support.remove_locks(cur, cfg, table)
        return PaymentResult(trans_ids=[l.trans_id for l in lines], overpayment_id=overpayment_id, source=source)

    # ------------------------------------------------- meerdere relaties

    @staticmethod
    def allocate(invoices: List[OpenInvoice], amount: Decimal) -> List[PaymentLine]:
        """Verdeel een bedrag over de openstaande posten van één relatie
        (scherm Ontvangsten/Betalingen, cp.pl post_payments).

        Creditnota's worden eerst verrekend, daarna de oudste posten. Een post
        krijgt zijn contante korting alleen als hij helemaal betaald wordt; een
        gedeeltelijk betaalde post krijgt geen korting. Is het bedrag minstens
        het hele openstaande bedrag (zonder korting), dan is er geen korting
        genomen. Wat overblijft wordt (door post_payment) een vooruitbetaling.

        Afwijking: Perl trok de korting af van het betaalde bedrag maar hield
        het ingevoerde totaal aan, zodat de korting ook nog als
        vooruitbetaling werd geboekt.
        """
        if not invoices:
            return []
        precision = invoices[0].precision
        left = round_amount(to_decimal(amount), precision)
        order = [i for i in invoices if i.due < 0] + [i for i in invoices if i.due >= 0]
        no_discount = left >= sum((i.due for i in invoices), ZERO)
        lines: List[PaymentLine] = []
        for inv in order:
            if inv.due < 0:
                paid, disc = inv.due, ZERO
            else:
                if left <= 0:
                    break
                need = inv.due if no_discount else inv.paid_suggested
                disc = inv.discount if left >= need and not no_discount else ZERO
                paid = need if left >= need else min(left, inv.due)
            left = round_amount(left - paid, precision)
            lines.append(PaymentLine(inv.id, paid, disc, expected_due=inv.due))
        if left < 0:
            raise ValidationError("Amount is less than the credit notes of this customer/vendor")
        return lines

    # -------------------------------------------------- betalingenlijst

    def payment_register(self, vc: str, *, payment_accno: Optional[str] = None, vc_id: Optional[int] = None,
                         name: Optional[str] = None, number: Optional[str] = None,
                         source_from: Optional[str] = None, source_to: Optional[str] = None,
                         date_from: Optional[date] = None, date_to: Optional[date] = None,
                         option: str = "all") -> List[Dict[str, Any]]:
        """Betalingen per betaalrekening voor storneren/opnieuw uitgeven
        (CP::payment_register).

        Eén regel per betaling: kenmerk + betaalrekening + datum + relatie, met
        het totaal, de facturen met een bedrag (trans_ids) en alle betrokken
        facturen (invoices, ook als betaling en storno op dezelfde dag vallen). ``void``: het kenmerk staat op
        deze rekening voor deze relatie netto op nul (gestorneerd).
        option: all, void (alleen gestorneerde), exclude (zonder gestorneerde),
        printed, notprinted.

        Afwijking: Perl gaf een regel per factuur en vergeleek kenmerken als
        getal (een kenmerk met letters viel buiten een van/tot-filter).
        """
        table, ARAP = _tables(vc)
        ml = -1 if ARAP == "AR" else 1
        formname = "check" if ARAP == "AP" else "payment"
        where = ["NOT ac.fx_transaction", "c.link LIKE %s"]
        params: List[Any] = [f"%{ARAP}_paid%"]
        if payment_accno:
            where.append("c.accno = %s")
            params.append(payment_accno)
        if date_from:
            where.append("ac.transdate >= %s")
            params.append(date_from)
        if date_to:
            where.append("ac.transdate <= %s")
            params.append(date_to)
        if vc_id:
            where.append("vc.id = %s")
            params.append(vc_id)
        else:
            if name:
                where.append("lower(vc.name) LIKE %s")
                params.append(like(name.lower()))
            if number:
                where.append(f"lower(vc.{vc}number) LIKE %s")
                params.append(like(number.lower()))
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            cur.execute(f"""
                SELECT c.accno, c.description AS account, ac.source, ac.transdate AS datepaid,
                       vc.id AS vc_id, vc.name, vc.{vc}number AS vcnumber, ac.trans_id,
                       sum(ac.amount)::numeric * {ml} AS amount,
                       bool_or(EXISTS (SELECT 1 FROM status s WHERE s.trans_id = ac.trans_id
                                       AND s.formname = %s AND s.printed)) AS printed
                FROM acc_trans ac
                JOIN chart c ON (c.id = ac.chart_id)
                JOIN {table} a ON (a.id = ac.trans_id)
                JOIN {vc} vc ON (vc.id = a.{vc}_id)
                WHERE {' AND '.join(where)}
                GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
                ORDER BY c.accno, ac.source, ac.transdate, vc.name, ac.trans_id""", [formname] + params)
            rows = cur.fetchall()

        def key_num(src):
            return (0, Decimal(src), "") if src and re.fullmatch(r"\d+(\.\d+)?", src) else (1, ZERO, src or "")

        lo = key_num(source_from) if source_from else None
        hi = key_num(source_to) if source_to else None
        net: Dict[tuple, Decimal] = {}
        for r in rows:
            k = (r["accno"], r["source"], r["vc_id"])
            net[k] = net.get(k, ZERO) + to_decimal(r["amount"])
        result: List[Dict[str, Any]] = []
        index: Dict[tuple, Dict[str, Any]] = {}
        for r in rows:
            if lo and key_num(r["source"]) < lo or hi and key_num(r["source"]) > hi:
                continue
            k = (r["accno"], r["source"], r["datepaid"], r["vc_id"])
            e = index.get(k)
            if e is None:
                e = index[k] = dict(accno=r["accno"], account=r["account"], source=r["source"],
                                    datepaid=r["datepaid"], vc_id=r["vc_id"], name=r["name"],
                                    vcnumber=r["vcnumber"], amount=ZERO, trans_ids=[], invoices=set(), printed=False,
                                    void=bool(r["source"]) and not round_amount(
                                        net[(r["accno"], r["source"], r["vc_id"])], 6))
                result.append(e)
            e["amount"] += to_decimal(r["amount"])
            e["invoices"].add(r["trans_id"])
            if round_amount(to_decimal(r["amount"]), precision):
                e["trans_ids"].append(r["trans_id"])
            e["printed"] = e["printed"] or bool(r["printed"])
        for e in result:
            e["amount"] = round_amount(e["amount"], precision)
            # een stornering zelf (bedrag tegengesteld) heet ook gestorneerd
            e["reversal"] = e["void"] and e["amount"] < 0
        keep = {
            "all": lambda e: True,
            "void": lambda e: e["void"],
            "exclude": lambda e: not e["void"],
            "printed": lambda e: e["printed"] and not e["void"],
            "notprinted": lambda e: not e["printed"] and not e["void"],
        }.get(option or "all")
        if keep is None:
            raise ValidationError(f"Unknown option {option}")
        return [e for e in result if keep(e) and (e["amount"] or e["void"])]

    # ---------------------------------------------------- storneren / heruitgeven

    def void_payment(self, vc: str, payment_accno: str, source: str, *, trans_ids: Optional[List[int]] = None,
                     datepaid: Optional[date] = None, voiddate: Optional[date] = None) -> List[int]:
        """Storneer een betaling (CP::void_payments, maar volledig).

        De betaling wordt herkend aan kenmerk + betaalrekening (+ betaaldatum als
        hetzelfde kenmerk op meerdere dagen voorkomt). Alle regels van de
        betaling worden op ``voiddate`` (standaard vandaag) tegengeboekt: bank,
        koersverschil, debiteuren/crediteuren, koerswinst/-verlies en de
        contante korting met btw-correctie. ``paid`` daalt, het factuurbedrag
        krijgt de btw-correctie terug en ``datepaid`` wordt de laatste
        overgebleven betaaldatum. trans_ids: alleen deze facturen (standaard
        alle facturen met dit kenmerk op deze rekening).

        Afwijking: Perl draaide alleen de bankregel terug (boeking uit balans)
        en verhoogde bij klanten het betaalde bedrag in plaats van het te verlagen.
        """
        cfg = self.config
        table, ARAP = _tables(vc)
        kind = "receipt" if vc == "customer" else "check"
        ml = 1 if ARAP == "AR" else -1
        voiddate = voiddate or date.today()
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            defaults = _db.get_defaults(cur, "closedto", "precision", "fxgainloss_accno_id")
            precision = int(to_decimal(defaults.get("precision") or 2))
            closedto = _closedto(defaults, cfg.dateformat)
            if closedto and _num(voiddate) <= closedto:
                raise ClosedPeriodError("Cannot void payment for a closed period!")
            payment_chart = self._chart(cur, payment_accno, f"{ARAP}_paid")
            done = []
            for tid in self._payment_trans_ids(cur, table, payment_chart, source, trans_ids):
                pl = self._payment_lines(cur, table, ARAP, tid, payment_chart, source, datepaid, precision,
                                         defaults.get("fxgainloss_accno_id"))
                ins = """INSERT INTO acc_trans (trans_id, chart_id, transdate, amount, source, memo, project_id,
                                                fx_transaction, approved, id)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)"""
                cur.execute("SELECT max(id) AS m FROM payment WHERE trans_id = %s", (tid,))
                paymentid = int(cur.fetchone()["m"] or 0) + 1
                cur.execute(ins, (tid, payment_chart, voiddate, -pl["bank"], source, None, None, False, paymentid))
                cur.execute("""INSERT INTO payment (id, trans_id, exchangerate, paymentmethod_id)
                               VALUES (%s, %s, %s, %s)""", (paymentid, tid, pl["rate"], pl["paymentmethod_id"]))
                for r in pl["rows"]:
                    if r["kind"] == "bank":
                        continue
                    cur.execute(ins, (tid, r["chart_id"], voiddate, -to_decimal(r["amount"]), r["source"],
                                      r["memo"], r["project_id"], r["fx_transaction"], r["id"]))
                cur.execute(f"SELECT amount, paid FROM {table} WHERE id = %s FOR UPDATE", (tid,))
                h = cur.fetchone()
                newpaid = round_amount(to_decimal(h["paid"]) - pl["paid_base"], precision)
                newamount = round_amount(to_decimal(h["amount"]) + pl["cdt_base"], precision)
                cur.execute(f"UPDATE {table} SET paid = %s, amount = %s, datepaid = %s WHERE id = %s",
                            (newpaid, newamount, self._last_datepaid(cur, tid, ARAP), tid))
                support.audittrail(cur, cfg, trans_id=tid, tablename=table, reference=source,
                                   formname=kind, action="voided")
                done.append(tid)
            support.remove_locks(cur, cfg, table)
        return done

    def reissue_payment(self, vc: str, payment_accno: str, source: str, new_source: str, *,
                        new_date: Optional[date] = None, trans_ids: Optional[List[int]] = None,
                        datepaid: Optional[date] = None) -> List[int]:
        """Geef een betaling opnieuw uit met een nieuw kenmerk en/of een nieuwe
        datum (CP::reissue_payment). Alle regels van de betaling verhuizen mee
        en ``datepaid`` wordt bijgewerkt; het nieuwe kenmerk wordt het laatst
        gebruikte nummer van de betaalrekening. De koers blijft die van de
        oorspronkelijke betaling.

        Afwijking: Perl verplaatste alleen de bankregel (naar de datum van
        vandaag) en liet debiteuren/crediteuren, koersverschil en korting op de
        oude datum staan.
        """
        cfg = self.config
        table, ARAP = _tables(vc)
        kind = "receipt" if vc == "customer" else "check"
        if not new_source:
            raise ValidationError("New reference (source) missing!")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            defaults = _db.get_defaults(cur, "closedto", "precision", "fxgainloss_accno_id")
            precision = int(to_decimal(defaults.get("precision") or 2))
            closedto = _closedto(defaults, cfg.dateformat)
            payment_chart = self._chart(cur, payment_accno, f"{ARAP}_paid")
            done = []
            for tid in self._payment_trans_ids(cur, table, payment_chart, source, trans_ids):
                pl = self._payment_lines(cur, table, ARAP, tid, payment_chart, source, datepaid, precision,
                                         defaults.get("fxgainloss_accno_id"))
                target = new_date or pl["date"]
                if target != pl["date"] and closedto and (_num(pl["date"]) <= closedto or _num(target) <= closedto):
                    raise ClosedPeriodError("Cannot move a payment into or out of a closed period!")
                for r in pl["rows"]:
                    cur.execute("""UPDATE acc_trans SET transdate = %s,
                                          source = CASE WHEN source IS NULL THEN NULL ELSE %s END
                                   WHERE ctid = %s::tid""", (target, new_source, r["ctid"]))
                cur.execute(f"UPDATE {table} SET datepaid = %s WHERE id = %s",
                            (self._last_datepaid(cur, tid, ARAP), tid))
                support.audittrail(cur, cfg, trans_id=tid, tablename=table, reference=new_source,
                                   formname=kind, action="reissued")
                done.append(tid)
            next_number(cur, f"{kind}_{payment_accno}", dateformat=cfg.dateformat, ini=new_source)
            support.remove_locks(cur, cfg, table)
        return done

    @staticmethod
    def _payment_trans_ids(cur, table: str, payment_chart: int, source: str,
                           trans_ids: Optional[List[int]]) -> List[int]:
        if not source:
            raise ValidationError("Payment reference (source) missing!")
        cur.execute(f"""SELECT ac.trans_id FROM acc_trans ac JOIN {table} a ON (a.id = ac.trans_id)
                        WHERE ac.chart_id = %s AND ac.source = %s AND NOT ac.fx_transaction
                        GROUP BY ac.trans_id HAVING round(sum(ac.amount)::numeric, 6) <> 0
                        ORDER BY ac.trans_id""", (payment_chart, source))
        found = [r["trans_id"] for r in cur.fetchall()]
        if trans_ids is not None:
            missing = sorted(set(trans_ids) - set(found))
            if missing:
                raise NotFoundError(f"No open payment {source} for {table} {missing}")
            found = [t for t in found if t in set(trans_ids)]
        if not found:
            raise NotFoundError(f"Payment {source} not found (or already voided)")
        return found

    @staticmethod
    def _payment_lines(cur, table: str, ARAP: str, tid: int, payment_chart: int, source: str,
                       datepaid: Optional[date], precision: int, fxgain) -> Dict[str, Any]:
        """Alle journaalregels van één betaling op een factuur.

        Regels met het kenmerk (bank, koersverschil, korting, btw-correctie)
        worden direct herkend. De debiteuren-/crediteurenregels en de
        koerswinst/-verliesregels hebben geen kenmerk; die worden gezocht op de
        bedragen die post_payment ervoor boekt. Lukt dat niet eenduidig, dan
        volgt een fout (nooit gokken).
        """
        ml = 1 if ARAP == "AR" else -1
        cur.execute("""SELECT ac.transdate, sum(ac.amount)::numeric AS s FROM acc_trans ac
                       WHERE ac.trans_id = %s AND ac.chart_id = %s AND ac.source = %s AND NOT ac.fx_transaction
                       GROUP BY ac.transdate ORDER BY ac.transdate""", (tid, payment_chart, source))
        groups = [(r["transdate"], round_amount(to_decimal(r["s"]), precision)) for r in cur.fetchall()]
        total = sum((g[1] for g in groups), ZERO)
        dates = [d for d, v in groups if v and (v > 0) == (total > 0)]
        if datepaid:
            if datepaid not in dates:
                raise NotFoundError(f"Payment {source} on {datepaid} not found for {table} {tid}")
            d = datepaid
        elif len(dates) == 1:
            d = dates[0]
        else:
            raise ValidationError(f"Payment {source} occurs on several dates for {table} {tid}; give datepaid")

        cur.execute(f"""SELECT ac.ctid::text AS ctid, ac.*, c.link, a.exchangerate AS trate
                        FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id) JOIN {table} a ON (a.id = ac.trans_id)
                        WHERE ac.trans_id = %s AND ac.transdate = %s""", (tid, d))
        allrows = cur.fetchall()
        trate = to_decimal(allrows[0]["trate"]) if allrows and allrows[0]["trate"] is not None else None

        def links(r):
            return (r["link"] or "").split(":")

        rows: List[Dict[str, Any]] = []
        bank = bankfx = ZERO
        disc: Dict[int, Dict[str, Any]] = {}
        for r in allrows:
            if r["source"] != source:
                continue
            if r["chart_id"] == payment_chart:
                r["kind"] = "bankfx" if r["fx_transaction"] else "bank"
                if r["fx_transaction"]:
                    bankfx += to_decimal(r["amount"])
                else:
                    bank += to_decimal(r["amount"])
            elif f"{ARAP}_discount" in links(r) or f"{ARAP}_tax" in links(r):
                r["kind"] = "disc"
                k = disc.setdefault(r["chart_id"], {"amount": ZERO, "fx": ZERO,
                                                    "tax": f"{ARAP}_tax" in links(r)})
                k["fx" if r["fx_transaction"] else "amount"] += to_decimal(r["amount"])
            else:
                continue
            rows.append(r)
        bank, bankfx = round_amount(bank, precision), round_amount(bankfx, precision)

        # verwachte regels zonder kenmerk
        expect = []          # (soort, id, bedrag)
        paid_fx = -bank * ml
        ar_paid = round_amount(paid_fx * trate, precision) * ml if trate is not None else -(bank + bankfx)
        expect.append(("ar", None, ar_paid))
        expect.append(("gain", None, round_amount(-(ar_paid + bank + bankfx), precision)))
        paid_base = ar_paid * ml
        cdt_base = ZERO
        for chart_id, k in disc.items():
            value = -round_amount(k["amount"], precision) * ml
            ar_k = round_amount(value * (trate if trate is not None else 1), precision) * ml
            expect.append(("ar", tid, ar_k))
            expect.append(("gain", tid, round_amount(-(ar_k + k["amount"] + k["fx"]), precision)))
            if k["tax"]:
                cdt_base += ar_k * ml
            else:
                paid_base += ar_k * ml

        pool = [r for r in allrows if r["source"] is None]
        for what, rid, amount in expect:
            if not amount:
                continue
            for r in pool:
                ok = r["id"] == rid and round_amount(to_decimal(r["amount"]), precision) == amount
                if what == "ar":
                    ok = ok and ARAP in links(r) and not r["fx_transaction"]
                else:
                    ok = ok and fxgain and r["chart_id"] == int(fxgain) and r["fx_transaction"]
                if ok:
                    r["kind"] = what
                    rows.append(r)
                    pool.remove(r)
                    break
            else:
                raise ValidationError(f"Cannot identify the lines of payment {source} on {d} for {table} {tid}")

        rate, pmid = Decimal(1), 0
        bank_ids = [r["id"] for r in rows if r["kind"] == "bank" and r["id"]]
        if bank_ids:
            cur.execute("SELECT exchangerate, paymentmethod_id FROM payment WHERE trans_id = %s AND id = %s",
                        (tid, bank_ids[0]))
            y = cur.fetchone()
            if y:
                rate, pmid = to_decimal(y["exchangerate"]) or Decimal(1), int(y["paymentmethod_id"] or 0)
        return {"date": d, "rows": rows, "bank": bank, "paid_base": paid_base, "cdt_base": cdt_base,
                "rate": rate, "paymentmethod_id": pmid}

    @staticmethod
    def _last_datepaid(cur, tid: int, ARAP: str) -> Optional[date]:
        """Laatste betaaldatum, zonder betalingen die (per kenmerk en rekening)
        netto gestorneerd zijn."""
        cur.execute("""SELECT coalesce(ac.source, '') AS src, ac.chart_id, ac.transdate,
                              sum(ac.amount)::numeric AS s FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id)
                       WHERE ac.trans_id = %s AND c.link LIKE %s AND NOT ac.fx_transaction
                       GROUP BY 1, 2, 3""", (tid, f"%{ARAP}_paid%"))
        net: Dict[tuple, Decimal] = {}
        last: Dict[tuple, date] = {}
        for r in cur.fetchall():
            key, v = (r["src"], r["chart_id"]), to_decimal(r["s"])
            net[key] = net.get(key, ZERO) + v
            # betaling = bank in debet bij klanten (negatief), in credit bij leveranciers
            if v and (v > 0) == (ARAP == "AP") and (key not in last or r["transdate"] > last[key]):
                last[key] = r["transdate"]
        live = [last[k] for k, v in net.items() if round_amount(v, 6) and k in last]
        return max(live) if live else None

    # ---------------------------------------------------------------- intern

    @staticmethod
    def _chart(cur, accno: str, link: Optional[str] = None) -> int:
        cur.execute("SELECT id, link, charttype FROM chart WHERE accno = %s", (accno,))
        row = cur.fetchone()
        if not row or row["charttype"] != "A":
            raise ValidationError(f"Unknown account: {accno}")
        if link and link not in (row["link"] or "").split(":"):
            raise ValidationError(f"Account {accno} is not linked as {link}")
        return row["id"]

    @staticmethod
    def _tax_rates(cur, trans_id: int, ARAP: str, transdate: date) -> Dict[str, Decimal]:
        """Btw-tarief per btw-rekening van de factuur, geldig op de factuurdatum.

        Afwijking: Perl telde voor het totaaltarief álle tarieven op die op of na
        de factuurdatum aflopen (bij een tariefwijziging bv. 19% + 21% = 40%),
        waardoor het factuurbedrag te veel daalde. Per rekening koos Perl wel
        het juiste tarief; hier telt alleen dat tarief mee.
        """
        cur.execute("""SELECT DISTINCT c.accno, t.rate, t.validto FROM chart c
                       JOIN acc_trans ac ON (ac.chart_id = c.id) JOIN tax t ON (t.chart_id = c.id)
                       WHERE c.link LIKE %s AND ac.trans_id = %s AND (t.validto >= %s OR t.validto IS NULL)
                       ORDER BY t.validto DESC""", (f"%{ARAP}_tax%", trans_id, transdate))
        rates: Dict[str, Decimal] = {}
        for r in cur.fetchall():           # NULL eerst, dan aflopend: laatste = geldig op transdate
            rates[r["accno"]] = to_decimal(r["rate"])
        return rates

    def _overpayment(self, cur, p: Payment, amount: Decimal, rate: Decimal, currency: str,
                     source: Optional[str], payment_chart: int, approved: bool, action: str, ml: int) -> int:
        """OP::overpayment: vooruitbetaling als nieuwe ar/ap-boeking zonder bedrag."""
        cfg = self.config
        table, ARAP = _tables(p.vc)
        precision = _db.get_precision(cur)
        fxamount = round_amount(amount * rate, precision)
        arap_accno = p.arap_accno
        if not arap_accno:
            cur.execute(f"""SELECT c.accno FROM {p.vc} vc JOIN chart c ON (c.id = vc.arap_accno_id)
                            WHERE vc.id = %s""", (p.vc_id,))
            row = cur.fetchone()
            arap_accno = row["accno"] if row else None
        if not arap_accno:
            cur.execute("SELECT accno, link FROM chart WHERE closed = '0' ORDER BY accno")
            arap_accno = next((r["accno"] for r in cur.fetchall()
                               if ARAP in (r["link"] or "").split(":")), None)
        if not arap_accno:
            raise ValidationError(f"No {ARAP} account for the prepayment")
        arap_chart = self._chart(cur, arap_accno, ARAP)

        _, employee_id = _db.get_employee(cur, cfg.short_login)
        invnumber = next_number(cur, "sinumber" if table == "ar" else "vinumber", dateformat=cfg.dateformat,
                                transdate=p.datepaid, currency=currency)
        cur.execute(f"""INSERT INTO {table} (invnumber, employee_id, approved, {p.vc}_id, transdate, datepaid,
                                             duedate, netamount, amount, paid, curr, department_id, bank_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s, %s, %s) RETURNING id""",
                    (invnumber, employee_id or None, approved, p.vc_id, p.datepaid, p.datepaid, p.datepaid,
                     fxamount, currency, int(p.department_id or 0), payment_chart))
        uid = cur.fetchone()["id"]
        ins = """INSERT INTO acc_trans (trans_id, chart_id, transdate, amount, source, memo, cleared,
                                        fx_transaction, approved)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
        cur.execute(ins, (uid, arap_chart, p.datepaid, fxamount * ml, None, None, None, False, approved))
        cur.execute(ins, (uid, payment_chart, p.datepaid, amount * ml * -1, source, p.memo, None, False, approved))
        if fxamount != amount:
            # (Perl zet hier cleared = betaaldatum; overgenomen)
            cur.execute(ins, (uid, payment_chart, p.datepaid, (fxamount - amount) * ml * -1, source, None,
                              p.datepaid, True, approved))
        support.audittrail(cur, cfg, trans_id=uid, tablename=table, reference=invnumber,
                           formname="deposit" if table == "ar" else "pre-payment", action=action)
        return uid
