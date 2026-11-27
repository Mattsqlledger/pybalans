"""Betalingsspecificatie per betaling per relatie (fase 4 stap 4b, SB7–SB8).

SQL-Ledger (cp.pl ``print_payment``/``print_payments``, sjablonen ``receipt`` en ``check``) drukt de
specificatie af vanuit het betaalscherm, vóór het boeken, met de bedragen zoals ze op het scherm staan;
mailen kan niet en er wordt niets vastgelegd. Hier (SB7, zie FASE4_ANALYSE.md):

* De specificatie wordt gemaakt van een **geboekte** betaling: na het boeken, vanaf het corrigeerscherm
  en vanuit het betalingsregister. Een betaling is relatie + betaalrekening + datum + kenmerk, zoals
  ``Payments.payment_register``; oude betalingen zonder kenmerk op dezelfde dag vallen dus samen. Geen
  eigen betalingstabel.
* Per factuur: nummer, datum, factuurbedrag, betaald en korting in deze betaling; wat niet aan een
  factuur is toegewezen staat als vooruitontvangst/vooruitbetaling op een eigen regel (zoals check_form
  "Deposit"/"Prepayment"). Bedragen in de valuta van de betaling.
* Geen ``status`` (SL heeft er geen); printen/mailen geeft een audittrailregel per factuur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg

from . import _db, support
from .amounts import round_amount, to_decimal
from .config import UserConfig
from .num2text import num2text
from .payments import _tables

ZERO = Decimal(0)


@dataclass
class SpecLine:
    """Een factuur (of de vooruitbetaling) in de betaling; bedragen in de valuta van de betaling."""

    trans_id: int
    invnumber: str
    transdate: Optional[date]
    duedate: Optional[date]
    description: str
    ordnumber: str
    ponumber: str
    amount: Decimal                 # factuurbedrag
    paid: Decimal                   # in deze betaling
    discount: Decimal = ZERO        # contante korting in deze betaling
    prepayment: bool = False        # vooruitontvangst/-betaling (boeking zonder bedrag)


@dataclass
class PaymentSpec:
    """Eén betaling aan of van één relatie."""

    vc: str
    vc_id: int
    name: str
    vcnumber: str
    language_code: str
    accno: str
    account: str
    source: str
    datepaid: date
    memo: str
    currency: str
    lines: List[SpecLine] = field(default_factory=list)
    account_iban: str = ""
    account_bic: str = ""

    @property
    def key(self) -> str:
        """Zelfde sleutel als de regels van het betalingsregister."""
        return spec_key(self.accno, self.source, self.datepaid, self.vc_id)

    @property
    def formname(self) -> str:
        return "receipt" if self.vc == "customer" else "check"

    @property
    def total(self) -> Decimal:
        return sum((l.paid for l in self.lines), ZERO)

    @property
    def total_discount(self) -> Decimal:
        return sum((l.discount for l in self.lines), ZERO)


def spec_key(accno: str, source: Optional[str], datepaid: date, vc_id: int) -> str:
    return "|".join([accno, source or "", datepaid.isoformat(), str(vc_id)])


def parse_key(key: str):
    """(rekening, kenmerk, datum, relatie) uit een sleutel, of None."""
    parts = str(key or "").split("|")
    if len(parts) != 4 or not parts[3].isdigit():
        return None
    try:
        d = date.fromisoformat(parts[2])
    except ValueError:
        return None
    return parts[0], parts[1], d, int(parts[3])


class PaymentSpecs:
    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    def find(self, vc: str, accno: str, source: Optional[str], datepaid: date, vc_id: int) -> Optional[PaymentSpec]:
        """De geboekte betaling, of None (niet gevonden, bedrag 0 of gestorneerd)."""
        table, ARAP = _tables(vc)
        sign = -1 if ARAP == "AR" else 1           # bankregel: ontvangst negatief (AR), betaling positief (AP)
        src = source or ""
        with _db.cursor(self.conn) as cur:
            precision = _db.get_precision(cur)
            cur.execute("SELECT id, description FROM chart WHERE accno = %s AND link LIKE %s",
                        (accno, f"%{ARAP}_paid%"))
            chart = cur.fetchone()
            if not chart:
                return None
            cur.execute(f"SELECT id, name, {vc}number AS vcnumber, language_code FROM {vc} WHERE id = %s",
                        (vc_id,))
            rel = cur.fetchone()
            if not rel:
                return None
            # gestorneerd: het kenmerk staat op deze rekening voor deze relatie netto op nul (zonder kenmerk
            # kan niet gestorneerd worden)
            if src:
                cur.execute(f"""SELECT sum(ac.amount)::numeric AS s FROM acc_trans ac
                                JOIN {table} a ON (a.id = ac.trans_id)
                                WHERE a.{vc}_id = %s AND ac.chart_id = %s AND ac.source = %s
                                  AND NOT ac.fx_transaction""", (vc_id, chart["id"], src))
                if not round_amount(to_decimal((cur.fetchone() or {}).get("s")), 6):
                    return None
            cur.execute(f"""SELECT ac.trans_id, ac.chart_id = %(pc)s AS bank, ac.amount, ac.memo,
                                   a.invnumber, a.transdate, a.duedate, a.description, a.ordnumber, a.ponumber,
                                   a.amount AS invamount, a.netamount, a.curr,
                                   coalesce(nullif(a.exchangerate, 0), 1) AS rate
                            FROM acc_trans ac
                            JOIN chart c ON (c.id = ac.chart_id)
                            JOIN {table} a ON (a.id = ac.trans_id)
                            WHERE a.{vc}_id = %(vc)s AND ac.transdate = %(d)s AND coalesce(ac.source, '') = %(src)s
                              AND NOT ac.fx_transaction
                              AND (ac.chart_id = %(pc)s OR c.link LIKE %(disc)s)
                            ORDER BY a.transdate, a.invnumber, ac.trans_id""",
                        {"pc": chart["id"], "vc": vc_id, "d": datepaid, "src": src, "disc": f"%{ARAP}_discount%"})
            rows = cur.fetchall()
            lines: Dict[int, SpecLine] = {}
            memo, currency = "", ""
            for r in rows:
                if r["trans_id"] not in lines:
                    if not any(x["bank"] for x in rows if x["trans_id"] == r["trans_id"]):
                        continue                    # korting zonder bankregel: hoort bij een andere betaling
                    prepay = not to_decimal(r["invamount"]) and not to_decimal(r["netamount"])
                    lines[r["trans_id"]] = SpecLine(
                        r["trans_id"], r["invnumber"] or "", r["transdate"], r["duedate"], r["description"] or "",
                        r["ordnumber"] or "", r["ponumber"] or "",
                        round_amount(to_decimal(r["invamount"]) / to_decimal(r["rate"]), precision), ZERO, ZERO,
                        prepay)
                line = lines[r["trans_id"]]
                value = to_decimal(r["amount"]) * sign
                if r["bank"]:
                    line.paid += value
                    memo = memo or (r["memo"] or "")
                else:
                    line.discount += value
                currency = currency or (r["curr"] or "")
            for l in lines.values():
                l.paid = round_amount(l.paid, precision)
                l.discount = round_amount(l.discount, precision)
            keep = [l for l in lines.values() if l.paid or l.discount]
            if not keep:
                return None
            keep.sort(key=lambda l: (l.prepayment, l.transdate or date.min, l.invnumber, l.trans_id))
            cur.execute("SELECT iban, bic FROM bank WHERE id = %s", (chart["id"],))
            bank = cur.fetchone() or {}
            return PaymentSpec(vc, vc_id, rel["name"] or "", rel["vcnumber"] or "", rel["language_code"] or "",
                               accno, chart["description"] or "", src, datepaid, memo,
                               currency or _db.default_currency(cur), keep, bank.get("iban") or "",
                               bank.get("bic") or "")

    def find_key(self, vc: str, key: str) -> Optional[PaymentSpec]:
        parts = parse_key(key)
        return self.find(vc, *parts) if parts else None

    def latest(self, vc: str) -> Optional[PaymentSpec]:
        """Laatst geboekte betaling (voorbeeld in Beheer > Sjablonen)."""
        table, ARAP = _tables(vc)
        with _db.cursor(self.conn) as cur:
            cur.execute(f"""SELECT c.accno, ac.source, ac.transdate, a.{vc}_id AS vc_id
                            FROM acc_trans ac JOIN chart c ON (c.id = ac.chart_id) JOIN {table} a ON (a.id = ac.trans_id)
                            WHERE c.link LIKE %s AND NOT ac.fx_transaction AND ac.amount * %s > 0
                            ORDER BY ac.transdate DESC, ac.trans_id DESC LIMIT 20""",
                        (f"%{ARAP}_paid%", -1 if ARAP == "AR" else 1))
            cands = cur.fetchall()
        for r in cands:
            spec = self.find(vc, r["accno"], r["source"], r["transdate"], r["vc_id"])
            if spec:
                return spec
        return None

    def record(self, spec: PaymentSpec, *, emailed: bool = False) -> None:
        """Specificatie verstuurd (definitieve PDF of geslaagde mail): audittrail per factuur, geen status
        (SB8; SL legt niets vast)."""
        table, _ = _tables(spec.vc)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            for l in spec.lines:
                support.audittrail(cur, self.config, trans_id=l.trans_id, tablename=table,
                                   reference=spec.source or l.invnumber, formname=spec.formname,
                                   action="emailed" if emailed else "printed")


# ================================================================== sjabloonvariabelen

def _split(amount: Decimal, precision: int):
    s = f"{round_amount(amount, max(precision, 2)):f}"
    whole, _, dec = s.lstrip("-").partition(".")
    return int(whole or 0), (dec + "00")[:2]


def spec_vars(pd, spec: PaymentSpec) -> Dict[str, Any]:
    """Sjabloonvariabelen van een betalingsspecificatie (``receipt``/``check``).

    Relatie en bedrijf zoals de facturen (``company_details``); ``lines`` voor het eigen sjabloon en de
    lijsten ``invnumber``/``invdate``/``invdescription``/``paid``/``discount`` en het bedrag in woorden
    zoals cp.pl ``check_form``/``print_form`` ze aan een SL-sjabloon geeft."""
    with _db.cursor(pd.conn) as cur:
        v = pd.company_details(cur, spec.vc, spec.vc_id, None)
        p = int(to_decimal(_db.get_defaults(cur, "precision").get("precision") or 2))
    lines = []
    for l in spec.lines:
        lines.append({"id": l.trans_id, "invnumber": l.invnumber, "invdate": pd.date(l.transdate),
                      "duedate": pd.date(l.duedate), "invdescription": l.description, "ordnumber": l.ordnumber,
                      "ponumber": l.ponumber, "amount": pd.amt(l.amount, p, "0") if not l.prepayment else "",
                      "paid": pd.amt(l.paid, p, "0"), "discount": pd.amt(l.discount, p, ""),
                      "prepayment": l.prepayment})
    whole, dec = _split(spec.total, p)
    lang = pd.text_language(spec.language_code)
    invoices = [l.invnumber for l in spec.lines if not l.prepayment]
    v.update({
        "formname": spec.formname, "vc": spec.vc, "language_code": spec.language_code,
        "datepaid": pd.date(spec.datepaid), "source": spec.source, "memo": spec.memo, "currency": spec.currency,
        "account": spec.account, "accno": spec.accno, "account_iban": spec.account_iban,
        "account_bic": spec.account_bic, "paylines": lines,
        "has_discount": any(l.discount for l in spec.lines),
        "amount": pd.amt(spec.total, p, "0"), "total_discount": pd.amt(spec.total_discount, p, "0"),
        "decimal": dec, "text_decimal": num2text(int(dec), lang), "text_amount": num2text(whole, lang),
        "integer_amount": str(whole),
        "yyyy": f"{spec.datepaid.year:04d}", "mm": f"{spec.datepaid.month:02d}", "dd": f"{spec.datepaid.day:02d}",
        # lijsten zoals cp.pl check_form (voor sjablonen in SL-stijl en de vergelijking)
        "invnumbers": [l["invnumber"] for l in lines], "invdates": [l["invdate"] for l in lines],
        "paids": [l["paid"] for l in lines], "discounts": [l["discount"] for l in lines],
        # voor plaatshouders in de mail ({nummer}, {datum}, {bedrag}, {kenmerk})
        "invnumber": ", ".join(invoices), "invdate": pd.date(spec.datepaid), "invtotal": pd.amt(spec.total, p, "0"),
        "duedate": "", "dcn": spec.source,
    })
    return v
