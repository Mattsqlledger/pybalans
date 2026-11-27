"""Bankafschriften inlezen, koppelen en boeken (fase 2, beslispunten I1–I10).

Nieuw ten opzichte van SQL-Ledger (dat kan alleen een CSV met betalingen
inlezen, im.pl). Het ontleden zit in :mod:`sqlledger.bankstatement`; deze
module doet de rest:

* :meth:`BankImport.import_file` — afschrift(en) uit een bestand opslaan in
  ``x_bank_statement``/``x_bank_line``. De bankrekening wordt gevonden via de
  IBAN in ``bank`` (``bank.id`` = ``chart.id`` van een betaalrekening, I4); is
  die onbekend, dan volgt :class:`UnknownAccountError` met de kiesbare
  betaalrekeningen en kan de aanroeper opnieuw inlezen met ``chart_accno``
  (de IBAN wordt dan bij die rekening opgeslagen). Valuta moet die van de
  rekening zijn. Saldocontrole begin + regels = eind is verplicht; aansluiting
  op het vorige afschrift en op het grootboeksaldo geven een waarschuwing (I5).
  Al bekende regels (zelfde ``dedup_key`` op dezelfde rekening) worden
  overgeslagen en gemeld (I3).
* :meth:`BankImport.proposals` — per open regel een voorstel (I6), in deze
  volgorde: (a) betalingskenmerk = ``dcn`` van een open post (NL-kenmerk van 16
  cijfers met controlegetal of RF-kenmerk, ook in de omschrijving), (b)
  factuurnummer in omschrijving/end-to-end-id, (c) IBAN van de tegenpartij →
  relatie(s) → één open post met precies dat bedrag of alle open posten samen,
  (d) bestaande niet-afgeletterde boeking op de bankrekening met hetzelfde
  bedrag en een datum binnen 7 dagen (alleen koppelen), (e) vaste regel
  (``x_bank_rule``); anders onbekend. Richting uit het teken: bij = klant
  (of creditnota van een leverancier), af = leverancier (of creditnota van
  een klant); creditnota's die bij (a)/(b) genoemd worden gaan mee. Staat er bij
  een betaalvoorstel ook al een niet-afgeletterde bankboeking met hetzelfde bedrag,
  dan krijgt het een waarschuwing (``doubt``) en is het niet "zeker".
  Storno's worden alleen gesignaleerd (I9).
  Bankproef 2026-11: (d) ook samen afletteren (2–3 regels of boekingen, BT4) en "zeker" bij de enige kandidaat
  met hetzelfde bedrag binnen 2 dagen (BT5); zonder IBAN van de relatie daarna (f) naam en bedrag op een open
  post en (g) "zoals eerder" uit de historie (oude boekingen + afgeletterde bankregels), als laatste een
  deelbetaling op naam (VZ1/VZ6). Die zijn nooit "zeker"; regels zonder rekeningnummer van de tegenpartij (pin)
  ook nooit (VZ2/VZ3). Afletteren tegen een inkoop/verkoop geeft het IBAN aan de relatie (BT2, niet bij een
  verzamelrelatie); na verwerken biedt het scherm aan het IBAN op te slaan of te vervangen (VZ4).
* :meth:`BankImport.process` — één regel verwerken: betaling boeken (via
  :meth:`Payments.post_payment`, dus precies zoals het betaalscherm),
  memoriaalboeking (tegenrekening, optioneel btw), koppelen aan een bestaande
  boeking of negeren. Alles wat geboekt of gekoppeld wordt krijgt meteen de
  afletterdatum = boekdatum van de bank op de bankregel(s) (I8). De regel wordt
  met ``FOR UPDATE`` vastgezet en moet nog open zijn, zodat twee gebruikers (of
  een dubbele klik) nooit twee keer boeken.

Contante korting wordt niet voorgesteld (alleen volledige of gedeeltelijke
betaling); wie korting wil geven gebruikt het betaalscherm of geeft
``discount`` per regel mee in :class:`PayAction`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import psycopg

from . import _db
from .amounts import format_amount, round_amount, to_decimal
from .bankstatement import Statement, card_merchant, parse
from .dates import format_date
from .config import UserConfig
from .errors import NotFoundError, ValidationError
from .extensions import ensure_schema
from .gl import GeneralLedger
from .models import GLTransaction, JournalLine
from .payments import OpenInvoice, Payment, PaymentLine, Payments

__all__ = [
    "BankImport", "BankImportError", "UnknownAccountError", "ImportResult", "ImportedStatement",
    "Proposal", "Allocation", "PayAction", "GLAction", "MatchAction", "IgnoreAction", "ProcessResult",
    "Rule", "is_valid_nl_reference", "is_valid_rf_reference", "find_references",
]

ZERO = Decimal(0)
#: datumvenster voor koppelen aan een bestaande boeking (I6 d)
MATCH_DAYS = 7
#: "zeker" afletteren tegen een bestaande boeking: hoogstens zoveel dagen verschil (BT5)
SURE_MATCH_DAYS = 2
#: samen afletteren (BT4): hoogstens zoveel regels/boekingen, binnen zoveel dagen
GROUP_MAX, GROUP_DAYS = 3, 3
#: een relatie met zoveel verschillende rekeningnummers is een verzamelrelatie ("Diverse crediteuren", BT2)
COLLECTIVE_IBANS = 3
#: historie voor voorstellen zonder IBAN (VZ1): zoveel dagen terug
HISTORY_DAYS = 730
#: factuurnummers korter dan dit worden niet in een omschrijving gezocht
MIN_INVNUMBER = 3


class BankImportError(ValidationError):
    """Afschrift of bankregel kan niet worden verwerkt."""


class UnknownAccountError(BankImportError):
    """De IBAN van het afschrift hoort (nog) bij geen enkele betaalrekening.

    ``accounts``: kiesbare betaalrekeningen (accno, description, iban).
    """

    def __init__(self, message: str, iban: Optional[str], accounts: List[Dict[str, Any]]):
        super().__init__(message)
        self.iban = iban
        self.accounts = accounts


# ------------------------------------------------------------------ kenmerken

_NL_WEIGHTS = (2, 4, 8, 5, 10, 9, 7, 3, 6, 1)


def norm_iban(value: Optional[str]) -> Optional[str]:
    s = re.sub(r"\s+", "", value or "").upper()
    return s or None


def _same_account(stored: Optional[str], key: str) -> bool:
    """Opgeslagen rekeningnummer = dat van het afschrift; ook een eerder opgeslagen kaal Nederlands nummer
    (417164300) tegen de IBAN (NL91ABNA0417164300)."""
    if not stored:
        return False
    if stored == key:
        return True
    return bool(re.fullmatch(r"NL\d{2}[A-Z]{4}\d{10}", key) and stored.isdigit()
                and stored.lstrip("0") == key[8:].lstrip("0"))


def norm_ref(value: Optional[str]) -> str:
    return re.sub(r"[\s.\-/]", "", value or "").upper()


def is_valid_nl_reference(value: str) -> bool:
    """NL-betalingskenmerk: 16 cijfers, eerste cijfer = controlegetal (modulus 11,
    gewichten 2,4,8,5,10,9,7,3,6,1 van rechts naar links)."""
    s = norm_ref(value)
    if not re.fullmatch(r"\d{16}", s):
        return False
    total = sum(int(d) * _NL_WEIGHTS[i % 10] for i, d in enumerate(reversed(s[1:])))
    check = 11 - total % 11
    check = {10: 1, 11: 0}.get(check, check)
    return check == int(s[0])


def is_valid_rf_reference(value: str) -> bool:
    """RF-kenmerk (ISO 11649): 'RF' + 2 controlecijfers + max. 21 tekens, modulus 97."""
    s = norm_ref(value)
    if not re.fullmatch(r"RF\d{2}[A-Z0-9]{1,21}", s):
        return False
    digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


_NL_IN_TEXT = re.compile(r"(?<![0-9])(\d{4}[ .]?\d{4}[ .]?\d{4}[ .]?\d{4})(?![0-9])")
_RF_IN_TEXT = re.compile(r"(?<![A-Z0-9])(RF\d{2}(?: ?[A-Z0-9]{1,4}){1,6})")


def find_references(text: Optional[str]) -> List[str]:
    """Geldige NL- en RF-betalingskenmerken in een vrije tekst (genormaliseerd)."""
    found: List[str] = []
    if not text:
        return found
    for m in _NL_IN_TEXT.finditer(text):
        s = norm_ref(m.group(1))
        if is_valid_nl_reference(s):
            found.append(s)
    for m in _RF_IN_TEXT.finditer(text.upper()):
        groups = m.group(1).split(" ")
        # spaties kunnen ook achter het kenmerk staan: probeer van lang naar kort
        for n in range(len(groups), 0, -1):
            s = norm_ref("".join(groups[:n]))
            if is_valid_rf_reference(s):
                found.append(s)
                break
    return list(dict.fromkeys(found))


# ------------------------------------------------------------------ datamodel

@dataclass
class ImportedStatement:
    """Uitslag van één ingelezen afschrift. ``id`` is None als alle regels al
    bekend waren (het afschrift is dan niet nog eens opgeslagen)."""

    id: Optional[int]
    chart_accno: str
    account: Optional[str]
    currency: Optional[str]
    format: str
    statement_ref: Optional[str]
    begindate: Optional[date]
    enddate: Optional[date]
    beginbalance: Optional[Decimal]
    endbalance: Optional[Decimal]
    lines_total: int
    lines_new: int
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    iban_saved: bool = False
    ledger_diff: Optional[str] = None

    @property
    def lines_skipped(self) -> int:
        return len(self.skipped)


@dataclass
class ImportResult:
    statements: List[ImportedStatement]

    @property
    def lines_new(self) -> int:
        return sum(s.lines_new for s in self.statements)

    @property
    def lines_skipped(self) -> int:
        return sum(s.lines_skipped for s in self.statements)


@dataclass
class Allocation:
    """Voorgesteld bedrag op één open post (valuta van de post, teken zoals in
    het betaalscherm: positief = betalen, negatief = creditnota verrekenen)."""

    trans_id: int
    invnumber: Optional[str]
    transdate: date
    due: Decimal
    paid: Decimal


@dataclass
class Proposal:
    """Voorstel voor één bankregel.

    category: 'a' kenmerk, 'b' factuurnummer, 'c' IBAN relatie, 'd' bestaande
    boeking, 'e' vaste regel, 'f' naam en bedrag (zonder IBAN), 'g' zoals eerder
    (historie), None = onbekend.
    kind: 'payment', 'gl', 'match' of None.
    exact: de posten sluiten precies aan op het bankbedrag.
    matches: bij samen afletteren (BT4) alle boekingen; ``match`` is de eerste.
    together: andere bankregels die samen met deze regel tegen ``match`` worden afgeletterd.
    unique: bij 'd' de enige kandidaat (één boeking en één bankregel met dit bedrag binnen het venster).
    """

    line: Dict[str, Any]
    category: Optional[str] = None
    kind: Optional[str] = None
    score: int = 0
    reason: str = ""
    vc: Optional[str] = None
    vc_id: Optional[int] = None
    vc_name: Optional[str] = None
    allocations: List[Allocation] = field(default_factory=list)
    exact: bool = False
    prepayment: Decimal = ZERO
    match: Optional[Dict[str, Any]] = None
    rule: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: er staat al een niet-afgeletterde bankboeking met hetzelfde bedrag (mogelijk dubbel)
    doubt: bool = False
    alternative: Optional[Dict[str, Any]] = None   # die bestaande boeking (om te koppelen)
    matches: List[Dict[str, Any]] = field(default_factory=list)
    together: List[Dict[str, Any]] = field(default_factory=list)
    unique: bool = False

    @property
    def card(self) -> bool:
        """Pinbetaling of andere regel zonder rekeningnummer van de tegenpartij: altijd met de hand (VZ2/VZ3)."""
        return not self.line.get("iban")

    @property
    def sure(self) -> bool:
        """Zeker genoeg voor "alle zekere voorstellen boeken": kenmerk of factuurnummer met precies het bedrag, of
        afletteren tegen een bestaande boeking met hetzelfde bedrag en (bijna) dezelfde datum als enige kandidaat
        (BT5). Nooit bij een storno, twijfel, een regel zonder rekeningnummer (pin, VZ2/VZ3), een voorstel op naam
        of uit de historie (f, g) of samen afletteren."""
        if self.line.get("is_reversal") or self.doubt:
            return False
        if self.kind == "payment":
            return self.category in ("a", "b") and self.exact
        if self.kind == "match" and self.category == "d" and self.unique and not self.together \
                and len(self.matches) <= 1 and not self.card:
            return abs((self.match["transdate"] - self.line["bookdate"]).days) <= SURE_MATCH_DAYS
        return False

    def action(self) -> Optional["Action"]:
        """De verwerking die bij dit voorstel hoort (None bij onbekend)."""
        if self.kind == "payment":
            return PayAction(vc=self.vc, vc_id=self.vc_id,
                             lines=[PaymentLine(a.trans_id, a.paid, expected_due=a.due) for a in self.allocations],
                             allow_prepayment=self.prepayment > 0, category=self.category)
        if self.kind == "match":
            extra = [(g["trans_id"], g["transdate"], g["source"]) for g in self.matches[1:]]
            return MatchAction(self.match["trans_id"], self.match["transdate"], self.match["source"],
                               category=self.category, more=extra, with_lines=[l["id"] for l in self.together])
        if self.kind == "gl":
            r = self.rule
            return GLAction(accno=r["accno"], tax_accno=r.get("tax_accno"), description=r.get("description"),
                            department_id=r.get("department_id"), project_id=r.get("project_id"),
                            rule_id=r.get("id"), category=self.category)
        return None


@dataclass
class PayAction:
    """Betaling boeken op open posten van één relatie.

    lines: bedragen per post zoals in het betaalscherm (positief = betalen; bij
    een leverancier is een afschrijving dus positief). Wat het bankbedrag meer
    is dan de som wordt alleen met ``allow_prepayment`` een vooruitbetaling.
    """

    vc: str
    vc_id: int
    lines: List[PaymentLine] = field(default_factory=list)
    allow_prepayment: bool = False
    memo: Optional[str] = None
    exchangerate: Optional[Decimal] = None
    discount_accno: Optional[str] = None
    arap_accno: Optional[str] = None
    category: Optional[str] = None


@dataclass
class GLAction:
    """Memoriaalboeking: bankrekening tegen ``accno``; met ``tax_accno`` wordt het
    bankbedrag als bedrag inclusief btw gesplitst (tarief op de boekdatum)."""

    accno: str
    tax_accno: Optional[str] = None
    description: Optional[str] = None
    department_id: Optional[int] = None
    project_id: Optional[int] = None
    exchangerate: Optional[Decimal] = None
    rule_id: Optional[int] = None
    category: Optional[str] = None


@dataclass
class MatchAction:
    """Koppelen aan een bestaande boeking op de bankrekening (alleen afletteren). Samen afletteren (BT4): ``more``
    zijn extra boekingen (trans_id, transdate, source), ``with_lines`` extra open bankregels van dezelfde rekening;
    de som van de boekingen moet gelijk zijn aan de som van de bankregels."""

    trans_id: int
    transdate: date
    source: Optional[str] = None
    category: Optional[str] = None
    more: List[Tuple[int, date, Optional[str]]] = field(default_factory=list)
    with_lines: List[int] = field(default_factory=list)


@dataclass
class IgnoreAction:
    note: Optional[str] = None


Action = Union[PayAction, GLAction, MatchAction, IgnoreAction]


@dataclass
class ProcessResult:
    line_id: int
    status: str                       # booked | matched | ignored
    kind: str                         # payment | gl | match | ignore
    trans_ids: List[int] = field(default_factory=list)
    source: Optional[str] = None


@dataclass
class Rule:
    """Vaste regel (I7): IBAN en/of trefwoord → tegenrekening (+ btw)."""

    accno: str
    iban: Optional[str] = None
    keyword: Optional[str] = None
    direction: Optional[str] = None   # '+' bij, '-' af, None beide
    chart_accno: Optional[str] = None  # bankrekening; None = alle
    tax_accno: Optional[str] = None
    description: Optional[str] = None
    department_id: Optional[int] = None
    project_id: Optional[int] = None
    id: Optional[int] = None


# ------------------------------------------------------------------ hoofdklasse

class BankImport:
    """Bankafschriften inlezen, voorstellen maken en verwerken."""

    def __init__(self, conn: psycopg.Connection, config: UserConfig):
        self.conn = conn
        self.config = config

    # ------------------------------------------------------------ rekeningen


    def _amt(self, value) -> str:
        """Bedrag in de notatie van de gebruiker (voor meldingen)."""
        return format_amount(to_decimal(value), self.config.numberformat, 2)

    def _date(self, value) -> str:
        return format_date(value, self.config.dateformat) if value else ""
    def payment_accounts(self) -> List[Dict[str, Any]]:
        """Betaalrekeningen (link AR_paid/AP_paid) met hun IBAN uit ``bank``."""
        with _db.cursor(self.conn) as cur:
            return self._payment_accounts(cur)

    @staticmethod
    def _payment_accounts(cur) -> List[Dict[str, Any]]:
        cur.execute("""SELECT c.id, c.accno, c.description, c.link, b.iban
                       FROM chart c LEFT JOIN bank b ON (b.id = c.id)
                       WHERE c.charttype = 'A' AND NOT coalesce(c.closed, false)
                         AND (c.link LIKE '%%AR_paid%%' OR c.link LIKE '%%AP_paid%%')
                       ORDER BY c.accno""")
        return [dict(r, iban=norm_iban(r["iban"])) for r in cur.fetchall()]

    def _account(self, cur, accno: str) -> Dict[str, Any]:
        acc = next((a for a in self._payment_accounts(cur) if a["accno"] == accno), None)
        if not acc:
            raise BankImportError(f"Rekening {accno} is geen betaalrekening.")
        return acc

    @staticmethod
    def account_currency(cur, chart_id: int, defaultcurrency: Optional[str]) -> Optional[str]:
        """Valuta van een bankrekening. SQL-Ledger legt die nergens vast; hier:
        de valuta van eerder ingelezen afschriften, anders de meest gebruikte
        valuta van de laatste 200 boekingen op de rekening, anders de
        standaardvaluta."""
        cur.execute("""SELECT curr FROM x_bank_statement WHERE chart_id = %s AND curr IS NOT NULL
                       ORDER BY enddate DESC NULLS LAST, id DESC LIMIT 1""", (chart_id,))
        r = cur.fetchone()
        if r and r["curr"]:
            return r["curr"].strip()
        cur.execute("""SELECT curr, count(*) AS n FROM (
                           SELECT trim(coalesce(ar.curr, ap.curr, gl.curr)) AS curr
                           FROM acc_trans ac
                           LEFT JOIN ar ON (ar.id = ac.trans_id) LEFT JOIN ap ON (ap.id = ac.trans_id)
                           LEFT JOIN gl ON (gl.id = ac.trans_id)
                           WHERE ac.chart_id = %s AND NOT ac.fx_transaction
                           ORDER BY ac.transdate DESC LIMIT 200) x
                       WHERE curr IS NOT NULL AND curr <> '' GROUP BY curr ORDER BY n DESC, curr LIMIT 1""",
                    (chart_id,))
        r = cur.fetchone()
        return r["curr"] if r else defaultcurrency

    # ------------------------------------------------------------ inlezen

    def import_file(self, data: bytes, filename: Optional[str] = None,
                    chart_accno: Optional[str] = None) -> ImportResult:
        """Lees een CAMT.053- of MT940-bestand in (alles of niets).

        chart_accno: betaalrekening voor afschriften waarvan de IBAN nog niet
        bekend is; de IBAN wordt dan bij die rekening opgeslagen.
        """
        statements = parse(data, filename)
        ensure_schema(self.conn)
        results: List[ImportedStatement] = []
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            accounts = self._payment_accounts(cur)
            defaultcurrency = _db.default_currency(cur)
            known = set(_db.currencies(cur))
            ledger: Dict[int, List[ImportedStatement]] = {}
            for st in statements:
                acc, saved = self._resolve_account(cur, st, chart_accno, accounts)
                res = self._store_statement(cur, st, acc, saved, filename, defaultcurrency, known)
                results.append(res)
                if res.ledger_diff:
                    ledger.setdefault(acc["id"], []).append(res)
            self._summarize_ledger(cur, ledger)
        return ImportResult(results)

    def _summarize_ledger(self, cur, ledger: Dict[int, List[ImportedStatement]]) -> None:
        """BT6: wijkt het grootboeksaldo bij meerdere afschriften af, dan alleen bij het eerste afschrift de
        waarschuwing, met het aantal volgende afschriften en de laatste boekdatum op de bankrekening."""
        for chart_id, items in ledger.items():
            first, rest = items[0], items[1:]
            for r in rest:
                r.warnings = [w for w in r.warnings if w != r.ledger_diff]
                cur.execute("UPDATE x_bank_statement SET warnings = %s WHERE id = %s",
                            ("\n".join(r.warnings) or None, r.id))
            if not rest or first.id is None:
                continue
            cur.execute("SELECT max(transdate) AS d FROM acc_trans WHERE chart_id = %s AND approved", (chart_id,))
            last = cur.fetchone()["d"]
            msg = (f"Ook bij {len(rest)} volgende afschrift(en) sluit het grootboeksaldo niet aan op het beginsaldo; "
                   f"laatste boeking op rekening {first.chart_accno}: {self._date(last) if last else '-'}.")
            first.warnings.append(msg)
            cur.execute("UPDATE x_bank_statement SET warnings = %s WHERE id = %s",
                        ("\n".join(first.warnings), first.id))

    def _resolve_account(self, cur, st: Statement, chart_accno: Optional[str],
                         accounts: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bool]:
        key = norm_iban(st.iban or st.account)
        by_iban = [a for a in accounts if key and _same_account(a["iban"], key)]
        for a in by_iban:
            if a["iban"] != key:          # eerder als kaal rekeningnummer opgeslagen: nu de IBAN
                cur.execute("UPDATE bank SET iban = %s WHERE id = %s", (key, a["id"]))
                a["iban"] = key
        if chart_accno:
            acc = next((a for a in accounts if a["accno"] == chart_accno), None)
            if not acc:
                raise BankImportError(f"Rekening {chart_accno} is geen betaalrekening.")
            if by_iban and by_iban[0]["id"] != acc["id"]:
                raise BankImportError(f"Rekeningnummer {key} hoort bij rekening {by_iban[0]['accno']}, "
                                      f"niet bij {acc['accno']}.")
            if by_iban or not key:
                return acc, False
            if acc["iban"] and acc["iban"] != key:
                raise BankImportError(f"Rekening {acc['accno']} heeft al rekeningnummer {acc['iban']}; "
                                      f"het afschrift is van {key}.")
            cur.execute("SELECT 1 FROM bank WHERE id = %s", (acc["id"],))
            if cur.fetchone():
                cur.execute("UPDATE bank SET iban = %s WHERE id = %s", (key, acc["id"]))
            else:
                cur.execute("INSERT INTO bank (id, iban) VALUES (%s, %s)", (acc["id"], key))
            acc["iban"] = key
            return acc, True
        if len(by_iban) == 1:
            return by_iban[0], False
        if len(by_iban) > 1:
            raise BankImportError(f"Rekeningnummer {key} staat bij meer dan één betaalrekening: "
                                  + ", ".join(a["accno"] for a in by_iban) + ".")
        choices = [dict(accno=a["accno"], description=a["description"], iban=a["iban"]) for a in accounts]
        raise UnknownAccountError(
            f"Rekeningnummer {key or '(onbekend)'} van het afschrift hoort bij geen enkele betaalrekening; "
            f"kies de rekening.", key, choices)

    def _store_statement(self, cur, st: Statement, acc: Dict[str, Any], saved: bool, filename: Optional[str],
                         defaultcurrency: Optional[str], known: set) -> ImportedStatement:
        chart_id = acc["id"]
        what = f"afschrift {st.statement_number or st.id or ''}".strip()
        currency = (st.currency or defaultcurrency or "").strip() or None
        if currency and currency not in known:
            raise BankImportError(f"Valuta {currency} van {what} is niet bekend in deze administratie.")
        acc_curr = self.account_currency(cur, chart_id, defaultcurrency)
        if currency != acc_curr:
            raise BankImportError(f"{what.capitalize()} is in {currency}, maar rekening {acc['accno']} "
                                  f"is in {acc_curr}.")
        for ln in st.lines:
            if ln.currency and ln.currency != currency:
                raise BankImportError(f"Regel {ln.sequence} van {what} is in {ln.currency}, "
                                      f"het afschrift in {currency}.")

        warnings: List[str] = []
        ledger_diff: Optional[str] = None
        diff = st.check_balance()
        if diff is None:
            warnings.append(f"{what.capitalize()}: begin- of eindsaldo ontbreekt, saldocontrole niet mogelijk.")
        elif diff:
            raise BankImportError(
                f"Saldocontrole {what} klopt niet: beginsaldo {st.opening_balance} + mutaties {st.total} "
                f"= {st.opening_balance + st.total}, eindsaldo {st.closing_balance} (verschil {diff}).")

        dates = [ln.booking_date for ln in st.lines]
        begindate = min(dates) if dates else (st.opening_date or st.closing_date)
        enddate = max(dates + [st.closing_date] if st.closing_date else dates) if dates else st.closing_date

        if st.opening_balance is not None and begindate:
            # aansluiting op het vorige afschrift: het laatste dat vóór of op de begindatum eindigt; eindigt dat op
            # dezelfde dag (twee afschriften op één dag, B6), dan telt ook het afschrift daarvóór. Bij een
            # overlappende export (regels al bekend) is er geen zinnige vergelijking: overslaan.
            cur.execute("SELECT 1 FROM x_bank_line WHERE chart_id = %s AND dedup_key = ANY(%s) LIMIT 1",
                        (chart_id, [ln.dedup_key for ln in st.lines]))
            overlap = cur.fetchone() is not None
            cur.execute("""SELECT endbalance, enddate FROM x_bank_statement
                           WHERE chart_id = %s AND enddate <= %s AND endbalance IS NOT NULL
                           ORDER BY enddate DESC, id DESC LIMIT 20""", (chart_id, begindate))
            prevs = cur.fetchall()
            prev = prevs[0] if prevs and not overlap else None
            if prev:
                cands = [p for p in prevs if p["enddate"] == prev["enddate"]]
                if prev["enddate"] == begindate:
                    before = [p for p in prevs if p["enddate"] < begindate]
                    cands += [p for p in before if p["enddate"] == before[0]["enddate"]] if before else []
                if any(to_decimal(p["endbalance"]) == st.opening_balance for p in cands):
                    prev = None
            if prev:
                warnings.append(f"Beginsaldo {self._amt(st.opening_balance)} sluit niet aan op het eindsaldo "
                                f"{self._amt(prev['endbalance'])} van het vorige afschrift "
                                f"({self._date(prev['enddate'])}).")
            ledger = self._ledger_balance(cur, chart_id, begindate, acc_curr != defaultcurrency)
            if ledger != st.opening_balance:
                ledger_diff = (f"Grootboeksaldo rekening {acc['accno']} vóór {self._date(begindate)}: "
                               f"{self._amt(ledger)}; beginsaldo afschrift: {self._amt(st.opening_balance)} "
                               f"(verschil {self._amt(st.opening_balance - ledger)}).")
                warnings.append(ledger_diff)

        cur.execute("""INSERT INTO x_bank_statement (chart_id, format, filename, iban, curr, statement_ref,
                           sequence, createdate, begindate, beginbalance, enddate, endbalance, lines_total,
                           login, warnings)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                    (chart_id, st.format, (filename or "")[:200] or None, norm_iban(st.iban or st.account), currency,
                     st.id, st.statement_number, st.created, begindate, st.opening_balance, enddate,
                     st.closing_balance, len(st.lines), self.config.login, "\n".join(warnings) or None))
        sid = cur.fetchone()["id"]
        skipped: List[Dict[str, Any]] = []
        for ln in st.lines:
            cur.execute("""INSERT INTO x_bank_line (statement_id, chart_id, dedup_key, seq, bookdate, valuedate,
                               amount, curr, name, iban, bic, description, reference, end_to_end, bank_ref,
                               txcode, is_reversal)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (chart_id, dedup_key) DO NOTHING RETURNING id""",
                        (sid, chart_id, ln.dedup_key, ln.sequence, ln.booking_date, ln.value_date, ln.amount,
                         ln.currency or currency, ln.counterparty_name, norm_iban(ln.counterparty_iban),
                         ln.counterparty_bic, ln.description, ln.payment_reference, ln.end_to_end_id,
                         ln.bank_reference, ln.transaction_code, ln.is_reversal))
            if not cur.fetchone():
                skipped.append(dict(seq=ln.sequence, bookdate=ln.booking_date, amount=ln.amount,
                                    name=ln.counterparty_name, description=ln.description))
        new = len(st.lines) - len(skipped)
        if st.lines and not new:
            cur.execute("DELETE FROM x_bank_statement WHERE id = %s", (sid,))
            sid = None
            warnings = [f"{what.capitalize()} was al helemaal ingelezen; er is niets toegevoegd."]
        else:
            cur.execute("UPDATE x_bank_statement SET lines_skipped = %s WHERE id = %s", (len(skipped), sid))
            if skipped:
                warnings.append(f"{len(skipped)} regel(s) van {what} waren al eerder ingelezen en zijn "
                                f"overgeslagen.")
        return ImportedStatement(
            id=sid, chart_accno=acc["accno"], account=st.account, currency=currency, format=st.format,
            statement_ref=st.statement_number or st.id, begindate=begindate, enddate=enddate,
            beginbalance=st.opening_balance, endbalance=st.closing_balance, lines_total=len(st.lines),
            lines_new=new, skipped=skipped, warnings=warnings, iban_saved=saved,
            ledger_diff=ledger_diff if sid else None)

    @staticmethod
    def _ledger_balance(cur, chart_id: int, before: date, foreign: bool) -> Decimal:
        """Saldo van de bankrekening vóór een datum (activa positief). Bij een
        rekening in vreemde valuta zonder de koersverschilregels."""
        cur.execute(f"""SELECT coalesce(sum(amount)::numeric, 0) AS s FROM acc_trans
                        WHERE chart_id = %s AND transdate < %s AND approved
                        {'AND NOT fx_transaction' if foreign else ''}""", (chart_id, before))
        return round_amount(-to_decimal(cur.fetchone()["s"]), 2)

    # ------------------------------------------------------------ lijsten

    def statements(self, chart_accno: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT s.*, c.accno, c.description AS account,
                                  (SELECT count(*) FROM x_bank_line l WHERE l.statement_id = s.id
                                     AND l.status = 'open') AS lines_open
                           FROM x_bank_statement s JOIN chart c ON (c.id = s.chart_id)
                           WHERE (%s::text IS NULL OR c.accno = %s)
                           ORDER BY s.enddate DESC NULLS LAST, s.id DESC LIMIT %s""",
                        (chart_accno, chart_accno, limit))
            return cur.fetchall()

    def lines(self, chart_accno: Optional[str] = None, status: Optional[str] = "open",
              statement_id: Optional[int] = None, line_ids: Optional[Sequence[int]] = None) -> List[Dict[str, Any]]:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            return self._lines(cur, chart_accno, status, statement_id, line_ids)

    @staticmethod
    def _lines(cur, chart_accno, status, statement_id=None, line_ids=None) -> List[Dict[str, Any]]:
        where, params = [], []
        if chart_accno:
            where.append("c.accno = %s")
            params.append(chart_accno)
        if status:
            where.append("l.status = %s")
            params.append(status)
        if statement_id:
            where.append("l.statement_id = %s")
            params.append(statement_id)
        if line_ids is not None:
            where.append("l.id = ANY(%s)")
            params.append(list(line_ids))
        cur.execute(f"""SELECT l.*, c.accno FROM x_bank_line l JOIN chart c ON (c.id = l.chart_id)
                        {'WHERE ' + ' AND '.join(where) if where else ''}
                        ORDER BY l.bookdate, l.statement_id, l.seq, l.id""", params)
        out = []
        for r in cur.fetchall():
            r = dict(r)
            r["amount"] = to_decimal(r["amount"])
            r["curr"] = (r["curr"] or "").strip() or None
            out.append(r)
        return out

    def open_counts(self) -> Dict[str, int]:
        """Aantal open regels per bankrekening (accno)."""
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, count(*) AS n FROM x_bank_line l JOIN chart c ON (c.id = l.chart_id)
                           WHERE l.status = 'open' GROUP BY c.accno ORDER BY c.accno""")
            return {r["accno"]: r["n"] for r in cur.fetchall()}

    # ------------------------------------------------------------ voorstellen

    def proposals(self, chart_accno: str, line_ids: Optional[Sequence[int]] = None) -> List[Proposal]:
        """Voorstel per open bankregel van deze rekening (op datum). Er wordt
        niets geboekt. Een open post of bestaande boeking wordt maar aan één
        regel voorgesteld (de eerste)."""
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            acc = self._account(cur, chart_accno)
            lines = self._lines(cur, chart_accno, "open", None, line_ids)
            if not lines:
                return []
            ctx = _MatchContext.load(self, cur, acc, lines)
        return [ctx.propose(line) for line in lines]

    def proposal(self, line_id: int) -> Proposal:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT c.accno, l.status FROM x_bank_line l JOIN chart c ON (c.id = l.chart_id) "
                        "WHERE l.id = %s", (line_id,))
            r = cur.fetchone()
        if not r:
            raise NotFoundError(f"Bankregel {line_id} bestaat niet.")
        if r["status"] != "open":
            line = self.lines(status=None, line_ids=[line_id])[0]
            return Proposal(line=line, reason="al verwerkt")
        return self.proposals(r["accno"], [line_id])[0]

    def match_candidates(self, line_id: int, days: int = 31) -> List[Dict[str, Any]]:
        """Niet-afgeletterde boekingen op de bankrekening met hetzelfde bedrag als de
        bankregel, binnen ``days`` dagen (dichtstbijzijnde eerst), om te koppelen."""
        [line] = self.lines(status=None, line_ids=[line_id]) or [None]
        if not line:
            raise NotFoundError(f"Bankregel {line_id} bestaat niet.")
        with _db.cursor(self.conn) as cur:
            foreign = (line["curr"] or _db.default_currency(cur)) != _db.default_currency(cur)
            groups = _open_groups(cur, line["chart_id"], line["bookdate"] - timedelta(days=days),
                                  line["bookdate"] + timedelta(days=days), foreign)
        hits = [g for g in groups if g["amount"] == line["amount"]]
        return sorted(hits, key=lambda g: (abs((g["transdate"] - line["bookdate"]).days), g["trans_id"]))

    def open_bookings(self, line_id: int, days: int = 31, limit: int = 40) -> List[Dict[str, Any]]:
        """BT4 (met de hand samen afletteren): niet-afgeletterde boekingen op de bankrekening binnen ``days`` dagen,
        dichtstbijzijnde eerst, om er meerdere samen tegen de bankregel af te letteren."""
        [line] = self.lines(status=None, line_ids=[line_id]) or [None]
        if not line:
            raise NotFoundError(f"Bankregel {line_id} bestaat niet.")
        with _db.cursor(self.conn) as cur:
            foreign = (line["curr"] or _db.default_currency(cur)) != _db.default_currency(cur)
            groups = _open_groups(cur, line["chart_id"], line["bookdate"] - timedelta(days=days),
                                  line["bookdate"] + timedelta(days=days), foreign)
        groups.sort(key=lambda g: (abs((g["transdate"] - line["bookdate"]).days), g["trans_id"]))
        return groups[:limit]

    def open_invoices(self, line: Dict[str, Any], vc: str, vc_id: int) -> List[OpenInvoice]:
        """Open posten van een relatie in de valuta van de bankregel (voor het
        handmatig kiezen van facturen)."""
        with _db.cursor(self.conn) as cur:
            defaultcurrency = _db.default_currency(cur)
        return Payments(self.conn, self.config).open_invoices(
            vc, vc_id, currency=line.get("curr") or defaultcurrency, datepaid=line["bookdate"])

    # ------------------------------------------------------------ verwerken

    def process(self, line_id: int, action: Action) -> ProcessResult:
        """Verwerk één bankregel (alles of niets)."""
        ensure_schema(self.conn)
        cfg = self.config
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("""SELECT l.*, c.accno FROM x_bank_line l JOIN chart c ON (c.id = l.chart_id)
                           WHERE l.id = %s FOR UPDATE OF l""", (line_id,))
            line = cur.fetchone()
            if not line:
                raise NotFoundError(f"Bankregel {line_id} bestaat niet.")
            if line["status"] != "open":
                raise BankImportError(f"Bankregel {line_id} is al verwerkt ({line['status']}).")
            line = dict(line, amount=to_decimal(line["amount"]), curr=(line["curr"] or "").strip() or None)
            upd: Dict[str, Any] = dict(vc=None, vc_id=None, trans_id=None, trans_ids=None, source=None,
                                       transdate=None, category=getattr(action, "category", None),
                                       rule_id=None, note=None)
            if isinstance(action, PayAction):
                ids, source = self._pay(cur, line, action)
                res = ProcessResult(line_id, "booked", "payment", ids, source)
                upd.update(vc=action.vc, vc_id=action.vc_id, source=source, transdate=line["bookdate"])
            elif isinstance(action, GLAction):
                gl_id, source = self._gl(cur, line, action)
                res = ProcessResult(line_id, "booked", "gl", [gl_id], source)
                upd.update(source=source, transdate=line["bookdate"], rule_id=action.rule_id)
            elif isinstance(action, MatchAction):
                others = self._match(cur, line, action)
                ids = list(dict.fromkeys([action.trans_id] + [m[0] for m in action.more]))
                res = ProcessResult(line_id, "matched", "match", ids, action.source)
                upd.update(source=action.source, transdate=action.transdate)
                rel = self._relation_of(cur, ids)
                if rel:
                    upd.update(vc=rel[0], vc_id=rel[1])
                    self._learn_iban(cur, rel[0], rel[1], line)          # BT2
                for o in others:
                    cur.execute("""UPDATE x_bank_line SET status = 'matched', kind = 'match', trans_id = %s,
                                          trans_ids = %s, source = %s, transdate = %s, category = %s, vc = %s,
                                          vc_id = %s, login = %s, processed = now() WHERE id = %s""",
                                (ids[0], ids, action.source, action.transdate, upd["category"], upd["vc"],
                                 upd["vc_id"], cfg.login, o["id"]))
            elif isinstance(action, IgnoreAction):
                res = ProcessResult(line_id, "ignored", "ignore")
                upd.update(note=(action.note or "")[:500] or None)
            else:
                raise TypeError("onbekende actie")
            cur.execute("""UPDATE x_bank_line SET status = %s, kind = %s, trans_id = %s, trans_ids = %s,
                                  source = %s, vc = %s, vc_id = %s, transdate = %s, category = %s, rule_id = %s,
                                  note = %s, login = %s, processed = now()
                           WHERE id = %s""",
                        (res.status, res.kind, res.trans_ids[0] if res.trans_ids else None,
                         res.trans_ids or None, upd["source"], upd["vc"], upd["vc_id"], upd["transdate"],
                         upd["category"], upd["rule_id"], upd["note"], cfg.login, line_id))
        return res

    def reopen(self, line_id: int) -> None:
        """Een genegeerde regel weer open zetten (geboekte regels: eerst de
        boeking corrigeren)."""
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT status FROM x_bank_line WHERE id = %s FOR UPDATE", (line_id,))
            r = cur.fetchone()
            if not r:
                raise NotFoundError(f"Bankregel {line_id} bestaat niet.")
            if r["status"] != "ignored":
                raise BankImportError("Alleen een genegeerde regel kan weer open gezet worden.")
            cur.execute("""UPDATE x_bank_line SET status = 'open', kind = NULL, note = NULL, login = NULL,
                                  processed = NULL WHERE id = %s""", (line_id,))

    def _source(self, cur, line: Dict[str, Any]) -> str:
        """Uniek kenmerk op deze bankrekening: de bankreferentie, anders BANK-<regel>."""
        ref = re.sub(r"\s+", " ", line.get("bank_ref") or "").strip()[:60]
        for cand in ([ref, f"{ref}/{line['id']}"] if ref and ref.upper() != "NOTPROVIDED" else []) \
                + [f"BANK-{line['id']}"]:
            cur.execute("SELECT 1 FROM acc_trans WHERE chart_id = %s AND source = %s LIMIT 1",
                        (line["chart_id"], cand))
            if not cur.fetchone():
                return cand
        raise BankImportError("Geen uniek kenmerk voor deze bankregel te maken.")

    def _rate(self, cur, line: Dict[str, Any], given: Optional[Decimal]) -> Tuple[str, Optional[Decimal]]:
        defaultcurrency = _db.default_currency(cur)
        curr = line["curr"] or defaultcurrency
        if curr == defaultcurrency:
            return curr, None
        rate = to_decimal(given) if given else GeneralLedger(self.conn, self.config).exchange_rate(
            curr, line["bookdate"])
        if not rate:
            raise BankImportError(f"Koers {curr} op {line['bookdate']} ontbreekt; vul de koers in.")
        return curr, rate

    def _pay(self, cur, line: Dict[str, Any], a: PayAction) -> Tuple[List[int], str]:
        if a.vc not in ("customer", "vendor"):
            raise ValueError("vc moet 'customer' of 'vendor' zijn")
        precision = _db.get_precision(cur)
        total = line["amount"] if a.vc == "customer" else -line["amount"]
        lines = [PaymentLine(l.trans_id, round_amount(to_decimal(l.paid), precision),
                             round_amount(to_decimal(l.discount), precision), l.expected_due) for l in a.lines]
        lines = [l for l in lines if l.paid or l.discount]
        allocated = sum((l.paid for l in lines), ZERO)
        rest = round_amount(total - allocated, precision)
        if rest and not (rest > 0 and total > 0 and a.allow_prepayment):
            raise BankImportError(f"Het totaal van de posten ({allocated}) sluit niet aan op het bankbedrag "
                                  f"({total}); verschil {rest}.")
        curr, rate = self._rate(cur, line, a.exchangerate)
        source = self._source(cur, line)
        kind = "receipt" if a.vc == "customer" else "check"
        counter = f"{kind}_{line['accno']}"
        old = _db.get_defaults(cur, counter)
        res = Payments(self.conn, self.config).post_payment(Payment(
            vc=a.vc, vc_id=a.vc_id, datepaid=line["bookdate"], payment_accno=line["accno"], lines=lines,
            amount=total, currency=curr, exchangerate=rate, source=source,
            memo=a.memo if a.memo is not None else ((line.get("name") or "")[:100] or None),
            discount_accno=a.discount_accno, arap_accno=a.arap_accno))
        # het kenmerk is de bankreferentie, niet het volgende nummer: teller van het
        # betaalscherm terugzetten zoals hij was
        if counter in old:
            cur.execute("UPDATE defaults SET fldvalue = %s WHERE fldname = %s", (old[counter], counter))
        else:
            cur.execute("DELETE FROM defaults WHERE fldname = %s", (counter,))
        ids = list(res.trans_ids) + ([res.overpayment_id] if res.overpayment_id else [])
        cur.execute("""UPDATE acc_trans SET cleared = %s WHERE chart_id = %s AND trans_id = ANY(%s)
                       AND transdate = %s AND source = %s AND cleared IS NULL""",
                    (line["bookdate"], line["chart_id"], ids, line["bookdate"], source))
        return ids, source

    def _gl(self, cur, line: Dict[str, Any], a: GLAction) -> Tuple[int, Optional[str]]:
        precision = _db.get_precision(cur)
        gross = abs(line["amount"])
        if not gross:
            raise BankImportError("Een bankregel van 0 kan niet geboekt worden; negeer hem.")
        if a.accno == line["accno"]:
            raise BankImportError("De tegenrekening kan niet de bankrekening zelf zijn.")
        tax = ZERO
        if a.tax_accno:
            cur.execute("""SELECT t.rate FROM tax t JOIN chart c ON (c.id = t.chart_id)
                           WHERE c.accno = %s AND (t.validto IS NULL OR t.validto >= %s)
                           ORDER BY t.validto ASC NULLS LAST LIMIT 1""", (a.tax_accno, line["bookdate"]))
            r = cur.fetchone()
            if not r:
                raise BankImportError(f"Rekening {a.tax_accno} heeft geen btw-tarief op {line['bookdate']}.")
            rate = to_decimal(r["rate"])
            tax = round_amount(gross * rate / (1 + rate), precision)
        net = gross - tax
        bij = line["amount"] > 0
        source = (line.get("bank_ref") or "")[:60] or None

        def jl(accno, amount, **kw):
            return JournalLine(accno, debit=amount, **kw) if bij == (accno == line["accno"]) \
                else JournalLine(accno, credit=amount, **kw)
        memo = (line.get("name") or "")[:100] or None
        jlines = [jl(line["accno"], gross, source=source, memo=memo),
                  jl(a.accno, net, project_id=a.project_id, memo=memo)]
        if tax:
            jlines.append(jl(a.tax_accno, tax, memo=memo))
        curr, rate = self._rate(cur, line, a.exchangerate)
        description = a.description or " ".join(x for x in (line.get("name"), line.get("description")) if x)[:250] \
            or None
        defaultcurrency = _db.default_currency(cur)
        gl_id = GeneralLedger(self.conn, self.config).post_transaction(GLTransaction(
            transdate=line["bookdate"], lines=jlines, description=description, department_id=a.department_id,
            currency=curr if curr != defaultcurrency else None, exchangerate=rate or Decimal(1)))
        cur.execute("UPDATE acc_trans SET cleared = %s WHERE trans_id = %s AND chart_id = %s AND cleared IS NULL",
                    (line["bookdate"], gl_id, line["chart_id"]))
        return gl_id, source

    def _match(self, cur, line: Dict[str, Any], a: MatchAction) -> List[Dict[str, Any]]:
        """Afletteren tegen een of meer bestaande boekingen; geeft de extra bankregels (samen afletteren, BT4)."""
        defaultcurrency = _db.default_currency(cur)
        foreign = (line["curr"] or defaultcurrency) != defaultcurrency
        others: List[Dict[str, Any]] = []
        for lid in dict.fromkeys(a.with_lines):
            if lid == line["id"]:
                continue
            cur.execute("SELECT id, chart_id, status, amount FROM x_bank_line WHERE id = %s FOR UPDATE", (lid,))
            o = cur.fetchone()
            if not o or o["chart_id"] != line["chart_id"] or o["status"] != "open":
                raise BankImportError(f"Bankregel {lid} staat niet (meer) open op deze bankrekening.")
            others.append(dict(o, amount=to_decimal(o["amount"])))
        groups = list(dict.fromkeys([(a.trans_id, a.transdate, a.source)] + list(a.more)))
        total = ZERO
        for tid, tdate, src in groups:
            cur.execute(f"""SELECT count(*) AS n, sum({'CASE WHEN fx_transaction THEN 0 ELSE amount END'
                                                         if foreign else 'amount'})::numeric AS s
                            FROM acc_trans WHERE chart_id = %s AND trans_id = %s AND transdate = %s
                              AND source IS NOT DISTINCT FROM %s AND cleared IS NULL AND approved""",
                        (line["chart_id"], tid, tdate, src))
            r = cur.fetchone()
            if not r["n"]:
                raise BankImportError("De boeking staat niet (meer) open op deze bankrekening.")
            total += round_amount(-to_decimal(r["s"]), 2)
        bank = line["amount"] + sum((o["amount"] for o in others), ZERO)
        if total != bank:
            raise BankImportError(f"Het bedrag van de boeking ({total}) is niet gelijk aan het bankbedrag ({bank}).")
        for tid, tdate, src in groups:
            cur.execute("""UPDATE acc_trans SET cleared = %s WHERE chart_id = %s AND trans_id = %s AND transdate = %s
                             AND source IS NOT DISTINCT FROM %s AND cleared IS NULL AND approved""",
                        (line["bookdate"], line["chart_id"], tid, tdate, src))
        return others

    @staticmethod
    def _relation_of(cur, trans_ids: List[int]) -> Optional[Tuple[str, int]]:
        """De klant of leverancier van de afgeletterde boeking(en), als het er precies één is."""
        cur.execute("""SELECT DISTINCT 'customer' AS vc, customer_id AS id FROM ar WHERE id = ANY(%(i)s)
                       UNION SELECT DISTINCT 'vendor', vendor_id FROM ap WHERE id = ANY(%(i)s)""", {"i": trans_ids})
        rows = cur.fetchall()
        return (rows[0]["vc"], rows[0]["id"]) if len(rows) == 1 and rows[0]["id"] else None

    @staticmethod
    def _collective(cur, vc: str, vc_id: int, iban: Optional[str]) -> bool:
        """Verzamelrelatie (BT2, "Diverse crediteuren"): de facturen noemen
        COLLECTIVE_IBANS of meer verschillende namen ("nummer | naam" in de omschrijving, zoals SQL-Ledger-
        gebruikers dat doen), of de bankregels van deze relatie hebben al zoveel verschillende rekeningnummers
        (met dit nieuwe erbij)."""
        table = "ar" if vc == "customer" else "ap"
        col = "customer_id" if vc == "customer" else "vendor_id"
        cur.execute(f"""SELECT count(DISTINCT lower(btrim(split_part(description, '|', 2)))) AS n FROM {table}
                        WHERE {col} = %s AND description LIKE '%%|%%'
                          AND btrim(split_part(description, '|', 2)) <> ''""", (vc_id,))
        if cur.fetchone()["n"] >= COLLECTIVE_IBANS:
            return True
        cur.execute(f"""SELECT count(DISTINCT iban) AS n FROM (
                           SELECT l.iban FROM x_bank_line l
                           WHERE l.iban IS NOT NULL AND (
                                 (l.vc = %s AND l.vc_id = %s)
                              OR EXISTS (SELECT 1 FROM {table} t WHERE t.{col} = %s
                                         AND t.id = ANY(coalesce(l.trans_ids, ARRAY[l.trans_id]))))
                           UNION SELECT %s) x WHERE iban IS NOT NULL""", (vc, vc_id, vc_id, iban))
        return cur.fetchone()["n"] >= COLLECTIVE_IBANS

    def _learn_iban(self, cur, vc: str, vc_id: int, line: Dict[str, Any]) -> bool:
        """BT2: bij afletteren het rekeningnummer (en de BIC) van de bankregel in de bestaande velden van de relatie
        zetten (tabel ``bank``, zoals SQL-Ledger) — alleen als daar nog niets staat en het geen verzamelrelatie is."""
        iban = norm_iban(line.get("iban"))
        if not iban or vc not in ("customer", "vendor"):
            return False
        cur.execute("SELECT iban FROM bank WHERE id = %s FOR UPDATE", (vc_id,))
        r = cur.fetchone()
        if r and (r["iban"] or "").strip():
            return False
        if self._collective(cur, vc, vc_id, iban):
            return False
        bic = (line.get("bic") or "").strip()[:11] or None
        if r:
            cur.execute("UPDATE bank SET iban = %s, bic = coalesce(nullif(bic, ''), %s) WHERE id = %s",
                        (iban, bic, vc_id))
        else:
            cur.execute("INSERT INTO bank (id, iban, bic) VALUES (%s, %s, %s)", (vc_id, iban, bic))
        return True

    # ------------------------------------------------------------ vaste regels

    def rules(self, chart_accno: Optional[str] = None) -> List[Dict[str, Any]]:
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            return self._rules(cur, chart_accno)

    @staticmethod
    def _rules(cur, chart_accno: Optional[str] = None, chart_id: Optional[int] = None) -> List[Dict[str, Any]]:
        cur.execute("""SELECT r.*, b.accno AS chart_accno, c.description AS account
                       FROM x_bank_rule r LEFT JOIN chart b ON (b.id = r.chart_id)
                       LEFT JOIN chart c ON (c.accno = r.accno)
                       WHERE (%s::text IS NULL OR r.chart_id IS NULL OR b.accno = %s)
                         AND (%s::int IS NULL OR r.chart_id IS NULL OR r.chart_id = %s)
                       ORDER BY (r.iban IS NULL), r.id""", (chart_accno, chart_accno, chart_id, chart_id))
        return cur.fetchall()

    def save_rule(self, rule: Rule) -> int:
        """Vaste regel opslaan (nieuw of wijzigen als ``rule.id`` gezet is)."""
        ensure_schema(self.conn)
        iban = norm_iban(rule.iban)
        keyword = (rule.keyword or "").strip() or None
        if not iban and not keyword:
            raise BankImportError("Een vaste regel heeft een rekeningnummer of een trefwoord nodig.")
        if keyword and len(keyword) < 3:
            raise BankImportError("Het trefwoord moet minstens 3 tekens hebben.")
        if rule.direction not in (None, "+", "-"):
            raise BankImportError("Richting moet '+', '-' of leeg zijn.")
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            chart_id = self._account(cur, rule.chart_accno)["id"] if rule.chart_accno else None
            cur.execute("SELECT charttype, closed, link FROM chart WHERE accno = %s", (rule.accno,))
            r = cur.fetchone()
            if not r or r["charttype"] != "A" or r["closed"]:
                raise BankImportError(f"Rekening {rule.accno} bestaat niet of is niet boekbaar.")
            if rule.tax_accno:
                cur.execute("SELECT 1 FROM tax t JOIN chart c ON (c.id = t.chart_id) WHERE c.accno = %s",
                            (rule.tax_accno,))
                if not cur.fetchone():
                    raise BankImportError(f"Rekening {rule.tax_accno} is geen btw-rekening.")
            values = (chart_id, iban, keyword, rule.direction, rule.accno, rule.tax_accno or None,
                      (rule.description or "").strip()[:250] or None, rule.department_id or None,
                      rule.project_id or None, self.config.login)
            if rule.id:
                cur.execute("""UPDATE x_bank_rule SET chart_id = %s, iban = %s, keyword = %s, direction = %s,
                                      accno = %s, tax_accno = %s, description = %s, department_id = %s,
                                      project_id = %s, login = %s WHERE id = %s RETURNING id""",
                            values + (rule.id,))
                if not cur.fetchone():
                    raise NotFoundError(f"Vaste regel {rule.id} bestaat niet.")
                return rule.id
            cur.execute("""INSERT INTO x_bank_rule (chart_id, iban, keyword, direction, accno, tax_accno,
                               description, department_id, project_id, login)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""", values)
            return cur.fetchone()["id"]

    def rule_from_line(self, line_id: int, accno: str, *, tax_accno: Optional[str] = None,
                       keyword: Optional[str] = None, use_iban: bool = True, all_accounts: bool = False,
                       description: Optional[str] = None) -> int:
        """Vaste regel maken vanuit een bankregel: IBAN van de tegenpartij (en/of
        een trefwoord) en de richting van de regel."""
        line = self.lines(status=None, line_ids=[line_id])
        if not line:
            raise NotFoundError(f"Bankregel {line_id} bestaat niet.")
        line = line[0]
        iban = line["iban"] if use_iban else None
        if use_iban and not iban and not keyword:
            raise BankImportError("Deze regel heeft geen rekeningnummer van de tegenpartij; geef een trefwoord.")
        return self.save_rule(Rule(accno=accno, iban=iban, keyword=keyword,
                                   direction="+" if line["amount"] > 0 else "-",
                                   chart_accno=None if all_accounts else line["accno"], tax_accno=tax_accno,
                                   description=description))

    # ------------------------------------------------------------ tegenrekening en "voortaan zo boeken" (v1.0.1)

    def recent_contra_accounts(self, chart_accno: Optional[str], limit: int = 10,
                               days: int = 730) -> List[str]:
        """PB26: tegenrekeningen die in memoriaalboekingen op deze bankrekening (de laatste ``days`` dagen) het
        vaakst gebruikt zijn, bij gelijk aantal de laatst gebruikte eerst; btw-rekeningen tellen niet mee.
        Bron: ``acc_trans`` van ``gl``-boekingen met een regel op de bankrekening (ook boekingen uit
        SQL-Ledger en uit bankregels; goedkoop via de index op ``chart_id``)."""
        if not chart_accno:
            return []
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, count(*) AS n, max(a.transdate) AS last
                           FROM acc_trans b
                           JOIN chart bc ON (bc.id = b.chart_id AND bc.accno = %s)
                           JOIN gl ON (gl.id = b.trans_id)
                           JOIN acc_trans a ON (a.trans_id = b.trans_id AND a.chart_id <> b.chart_id)
                           JOIN chart c ON (c.id = a.chart_id)
                           WHERE b.transdate >= current_date - %s AND c.charttype = 'A' AND c.closed = '0'
                             AND coalesce(c.link, '') NOT LIKE '%%tax%%'
                           GROUP BY c.accno ORDER BY n DESC, last DESC, c.accno LIMIT %s""",
                        (chart_accno, days, limit))
            return [r["accno"] for r in cur.fetchall()]

    def account_categories(self) -> Dict[str, str]:
        """Soort per rekening (A/L/Q/I/E), voor de volgorde in de zoeklijst (PB26)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT accno, category FROM chart WHERE charttype = 'A'")
            return {r["accno"]: r["category"] or "" for r in cur.fetchall()}

    def rule_suggestion(self, line_id: int) -> Optional[Dict[str, Any]]:
        """PB27: na het boeken van een bankregel een vaste regel voorstellen ("Voortaan zo boeken?"):
        rekeningnummer van de tegenpartij, anders een kenmerkend stuk van naam/omschrijving als trefwoord, en de
        tegenrekening en btw van de memoriaalboeking. None als de regel geen memoriaalboeking is of al via een
        vaste regel ging, of als er al een vaste regel is die hem zou vinden."""
        ensure_schema(self.conn)
        found = self.lines(status=None, line_ids=[line_id])
        if not found:
            return None
        line = found[0]
        if line.get("status") != "booked" or line.get("kind") != "gl" or line.get("rule_id") or not line.get("trans_id"):
            return None
        with _db.cursor(self.conn) as cur:
            cur.execute("""SELECT c.accno, coalesce(c.link, '') AS link FROM acc_trans a JOIN chart c ON (c.id = a.chart_id)
                           WHERE a.trans_id = %s AND c.accno <> %s ORDER BY abs(a.amount) DESC""",
                        (line["trans_id"], line["accno"]))
            rows = cur.fetchall()
            accno = next((r["accno"] for r in rows if "tax" not in r["link"]), None)
            tax = next((r["accno"] for r in rows if "tax" in r["link"]), None)
            cur.execute("SELECT description FROM gl WHERE id = %s", (line["trans_id"],))
            g = cur.fetchone()
            rules = self._rules(cur, chart_id=line["chart_id"])
        if not accno:
            return None
        direction = "+" if line["amount"] > 0 else "-"
        text = " ".join(x for x in (line.get("name"), line.get("description")) if x).lower()
        for r in rules:                               # er is al een regel die deze regel vindt
            if (not r["direction"] or r["direction"] == direction) and (not r["iban"] or r["iban"] == line.get("iban")) \
                    and (not r["keyword"] or r["keyword"].lower() in text):
                return None
        own = " ".join(x for x in (line.get("name"), line.get("description")) if x)
        own_desc = (g or {}).get("description") or ""
        return dict(line=line, iban=line.get("iban"), keyword=suggest_keyword(line.get("name"), line.get("description")),
                    accno=accno, tax_accno=tax, direction=direction,
                    description=own_desc if own_desc and own_desc != own[:250] else None)

    def matching_open_lines(self, rule_id: int) -> int:
        """Hoeveel open regels van de bankrekening van deze vaste regel krijgen hem nu als voorstel (PB27)."""
        with _db.cursor(self.conn) as cur:
            cur.execute("SELECT c.accno FROM x_bank_rule r JOIN chart c ON (c.id = r.chart_id) WHERE r.id = %s",
                        (rule_id,))
            r = cur.fetchone()
        accnos = [r["accno"]] if r else list(self.open_counts())
        return sum(1 for a in accnos for p in self.proposals(a) if p.rule and p.rule["id"] == rule_id)

    def save_vc_iban(self, line_id: int, replace: bool = False) -> bool:
        """PB27/VZ4: het rekeningnummer (en de BIC) van de tegenpartij opslaan in de bestaande velden van de relatie
        (tabel ``bank``, zoals SQL-Ledger), zodat volgende regels van die relatie via het rekeningnummer gevonden
        worden. Heeft de relatie al een ander rekeningnummer, dan alleen met ``replace``. Nooit bij een
        verzamelrelatie. Geeft True als het is opgeslagen."""
        offer = self.vc_iban_offer(line_id)
        if not offer or (offer["current"] and not replace):
            return False
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("SELECT 1 FROM bank WHERE id = %s FOR UPDATE", (offer["vc_id"],))
            if cur.fetchone():
                cur.execute("UPDATE bank SET iban = %s, bic = %s WHERE id = %s",
                            (offer["iban"], offer["bic"], offer["vc_id"]))
            else:
                cur.execute("INSERT INTO bank (id, iban, bic) VALUES (%s, %s, %s)",
                            (offer["vc_id"], offer["iban"], offer["bic"]))
        return True

    def vc_iban_offer(self, line_id: int) -> Optional[Dict[str, Any]]:
        """VZ4: na het boeken of afletteren van een regel op een relatie vragen of het rekeningnummer van de regel
        het standaardrekeningnummer van die relatie wordt: relatie, rekeningnummer, BIC en het huidige nummer (leeg =
        nog geen; anders "vervangen?"), of None (geen rekeningnummer, geen relatie, al gelijk, verzamelrelatie)."""
        found = self.lines(status=None, line_ids=[line_id])
        if not found:
            return None
        line = found[0]
        iban, vc, vc_id = norm_iban(line.get("iban")), line.get("vc"), line.get("vc_id")
        if line.get("status") not in ("booked", "matched") or not iban or vc not in ("customer", "vendor") or not vc_id:
            return None
        with _db.cursor(self.conn) as cur:
            cur.execute(f"SELECT v.name, b.iban FROM {vc} v LEFT JOIN bank b ON (b.id = v.id) WHERE v.id = %s",
                        (vc_id,))
            r = cur.fetchone()
            if not r:
                return None
            current = norm_iban(r["iban"]) or None
            if current == iban or self._collective(cur, vc, vc_id, iban):
                return None
        return dict(line=line, iban=iban, bic=(line.get("bic") or "").strip()[:11] or None, vc=vc, vc_id=vc_id,
                    name=r["name"], current=current)

    def vc_iban_missing(self, line_id: int) -> Optional[Dict[str, Any]]:
        """PB27 (bestaand): als :meth:`vc_iban_offer`, maar alleen als de relatie nog geen rekeningnummer heeft."""
        offer = self.vc_iban_offer(line_id)
        return offer if offer and not offer["current"] else None

    def rule_proposals(self, chart_accno: str, minimum: int = 2) -> List[Dict[str, Any]]:
        """BT3: vaste regels die uit de geboekte en afgeletterde memoriaalregels van deze bankrekening volgen: per
        rekeningnummer van de tegenpartij (anders een trefwoord uit de naam) en richting één tegenrekening (+ btw),
        minstens ``minimum`` keer. Zijn het verschillende tegenrekeningen, dan ``varies`` (geen regel, alleen de
        melding "verschilt per keer"). Pinbetalingen (geen rekeningnummer) nooit (VZ2). Regels die al door een vaste
        regel gevonden worden vallen weg."""
        ensure_schema(self.conn)
        with _db.cursor(self.conn) as cur:
            acc = self._account(cur, chart_accno)
            cur.execute("""SELECT l.id, l.iban, l.name, l.description, l.amount, l.bookdate, l.trans_id
                           FROM x_bank_line l JOIN gl ON (gl.id = l.trans_id)
                           WHERE l.chart_id = %s AND l.status IN ('matched', 'booked') AND l.rule_id IS NULL
                           ORDER BY l.bookdate""", (acc["id"],))
            lines = cur.fetchall()
            ids = [l["trans_id"] for l in lines]
            contra: Dict[int, List[Optional[str]]] = {}
            if ids:
                cur.execute("""SELECT a.trans_id, c.accno, coalesce(c.link, '') AS link FROM acc_trans a
                               JOIN chart c ON (c.id = a.chart_id) WHERE a.trans_id = ANY(%s) AND a.chart_id <> %s
                               ORDER BY abs(a.amount) DESC""", (ids, acc["id"]))
                for r in cur.fetchall():
                    slot = contra.setdefault(r["trans_id"], [None, None])
                    i = 1 if "tax" in r["link"] else 0
                    if slot[i] is None:
                        slot[i] = r["accno"]
            rules = self._rules(cur, chart_id=acc["id"])
        groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
        for l in lines:
            if not l["iban"]:
                continue                                   # pin e.d.: altijd met de hand (VZ2)
            direction = "+" if to_decimal(l["amount"]) > 0 else "-"
            groups.setdefault(("iban", norm_iban(l["iban"]), direction), []).append(l)
        out = []
        for (by, key, direction), ls in groups.items():
            pairs = [tuple(contra.get(l["trans_id"], [None, None])) for l in ls]
            pairs = [p for p in pairs if p[0]]
            if len(pairs) < minimum:
                continue
            example = ls[-1]
            text = " ".join(x for x in (example["name"], example["description"]) if x).lower()
            if any((not r["direction"] or r["direction"] == direction) and (not r["iban"] or r["iban"] == key)
                   and (not r["keyword"] or r["keyword"].lower() in text) for r in rules):
                continue
            kinds = sorted(set(pairs), key=lambda p: -pairs.count(p))
            out.append(dict(iban=key, keyword=None, direction=direction, name=example["name"] or "",
                            count=len(pairs), last=example["bookdate"], line_id=example["id"],
                            accno=kinds[0][0], tax_accno=kinds[0][1], varies=len(kinds) > 1,
                            choices=[dict(accno=a, tax_accno=t, n=pairs.count((a, t))) for a, t in kinds]))
        out.sort(key=lambda r: (r["varies"], -r["count"], r["name"]))
        return out

    def delete_rule(self, rule_id: int) -> None:
        ensure_schema(self.conn)
        with self.conn.transaction(), _db.cursor(self.conn) as cur:
            cur.execute("DELETE FROM x_bank_rule WHERE id = %s RETURNING id", (rule_id,))
            if not cur.fetchone():
                raise NotFoundError(f"Vaste regel {rule_id} bestaat niet.")


#: woorden die geen kenmerk zijn (PB27)
_KEYWORD_STOP = {"van", "voor", "naar", "het", "een", "der", "den", "des", "de", "en", "bv", "b.v.", "nv", "n.v.",
                 "via", "betaling", "overboeking", "incasso", "sepa", "europese", "periodieke", "kenmerk", "omschrijving",
                 "iban", "bic", "naam", "machtiging", "id", "datum", "transactie", "ref", "referentie"}


def suggest_keyword(name: Optional[str], description: Optional[str]) -> str:
    """Kenmerkend stuk van omschrijving of naam als trefwoord voor een vaste regel (PB27): de eerste twee
    aaneengesloten woorden van minstens drie letters zonder cijfers (geen bedragen, datums of kenmerken), eerst uit
    de omschrijving (die onderscheidt bv. coupon en dividend van dezelfde bank; de tegenpartij zelf vindt de regel
    al via het rekeningnummer), anders uit de naam. "Coupon 4% Staatslening 2037" → "coupon"; leeg als er niets is."""
    for text in (description, name):
        low = " ".join((text or "").lower().split())
        words: List[str] = []
        for w in low.split(" "):
            w = w.strip(".,:;()/-'\"")
            if len(w) >= 3 and not re.search(r"\d", w) and w.replace(".", "") not in _KEYWORD_STOP:
                words.append(w)
                if len(words) == 2:
                    break
            elif words:
                break                 # alleen aaneengesloten woorden: het trefwoord moet zo in de tekst staan
        if words:
            both = " ".join(words)
            return both if both in low else words[0]
    return ""


# ------------------------------------------------------------------ koppelen

#: woorden die niets zeggen over de tegenpartij (VZ1)
_TOKEN_STOP = set("""SEPA INCASSO ALGEMEEN DOORLOPEND BEDRIJVEN OVERBOEKING IBAN BIC NAAM NAME OMSCHRIJVING KENMERK
MACHTIGING INCASSANT REMI EREF MARF CSID TRTP NOTPROVIDED BEA GEA BETAALPAS KAARTNUMMER IDEAL WERO VIA ONLINE
BANKIEREN EUR HOLDING GROEP GROUP THE DER VAN DEN DEN HET EEN TE NR DHR MEVR STICHTING NEDERLAND SERVICES REF REFNR
PERIODE FACTUUR FACTUURNR FAKTNR DEBNR KLNR KLTNR MEER INFO WWW COM INZAKE REKENING BETREFFENDE UVW VOOR NAAR
PRIVE BETAAL INT OVERB BANK PAYMENT PAYMENTS LTD GMBH INVOICE FOR YOU AND VOF""".split())
_KENTEKEN = re.compile(r"\b([A-Z0-9]{1,3}-[A-Z0-9]{2,3}-[A-Z0-9]{1,2})\b")
_IBAN_TOKEN = re.compile(r"[A-Z]{2}\d{2}[A-Z]{4}\d{7,}")


def _tokens(text: Optional[str]) -> Dict[str, float]:
    """Woord -> gewicht voor het vergelijken van namen (VZ1): kenteken en rekeningnummer zwaar, cijfers licht,
    buurwoorden ook samen ("YOU SURE" vindt "YouSure"); rechtsvormen en bankwoorden tellen niet."""
    up = (text or "").upper()
    out: Dict[str, float] = {}
    for k in _KENTEKEN.findall(up):
        out[k.replace("-", "")] = 5.0
    words = [w for w in re.split(r"[^A-Z0-9]+", up) if w]
    for w in words:
        if w in _TOKEN_STOP or len(w) < 3:
            continue
        if _IBAN_TOKEN.fullmatch(w):
            out[w] = 3.0
            continue
        out.setdefault(w, 0.3 if w.isdigit() else 1.0)
    for a, b in zip(words, words[1:]):
        if a.isalpha() and b.isalpha() and a not in _TOKEN_STOP and b not in _TOKEN_STOP:
            out.setdefault(a + b, 1.0)
    return out


def _overlap(a: Dict[str, float], b: Dict[str, float], idf: Optional[Dict[str, float]] = None) -> float:
    """Gewogen overeenkomst; met ``idf`` tellen woorden die in veel boekingen staan (plaatsnamen) minder."""
    if idf is None:
        return sum(min(a[k], b[k]) for k in a.keys() & b.keys())
    return sum(min(a[k], b[k]) * idf.get(k, 1.0) for k in a.keys() & b.keys())


@dataclass
class _Hist:
    key: tuple            # ('gl', tegenrekening, btw-rekening) of (vc, vc_id)
    label: str
    transdate: date
    amount: Decimal       # bankteken
    toks: Dict[str, float]


def _history(cur, chart_id: int, lo: date, hi: date) -> List[_Hist]:
    """VZ1: boekingen op de bankrekening (ook uit SQL-Ledger) met hun omschrijving of relatie; is een boeking ook
    door een afgeletterde/geboekte bankregel gedekt, dan tellen naam, omschrijving en rekeningnummer van die
    bankregel mee (zo hoort "BELASTINGDIENST" bij "OB november")."""
    cur.execute("""SELECT t.trans_id, string_agg(coalesce(l.name, '') || ' ' || coalesce(l.description, '') || ' '
                                                 || coalesce(l.iban, ''), ' ') AS t
                   FROM x_bank_line l CROSS JOIN LATERAL unnest(coalesce(l.trans_ids, ARRAY[l.trans_id])) AS t(trans_id)
                   WHERE l.chart_id = %s AND l.status IN ('matched', 'booked') AND t.trans_id IS NOT NULL
                   GROUP BY t.trans_id""", (chart_id,))
    bank = {r["trans_id"]: r["t"] for r in cur.fetchall()}
    cur.execute("""SELECT ac.trans_id, ac.transdate, sum(ac.amount)::numeric AS amt,
                          max(gl.id) AS gl_id, max(gl.description) AS gld, max(gl.reference) AS glr,
                          max(ap.vendor_id) AS vid, max(ve.name) AS vname, max(ap.description) AS apd,
                          max(ar.customer_id) AS cid, max(cu.name) AS cname, max(ar.description) AS ard
                   FROM acc_trans ac LEFT JOIN gl ON (gl.id = ac.trans_id)
                   LEFT JOIN ap ON (ap.id = ac.trans_id) LEFT JOIN vendor ve ON (ve.id = ap.vendor_id)
                   LEFT JOIN ar ON (ar.id = ac.trans_id) LEFT JOIN customer cu ON (cu.id = ar.customer_id)
                   WHERE ac.chart_id = %s AND ac.transdate BETWEEN %s AND %s AND ac.approved
                   GROUP BY ac.trans_id, ac.transdate, ac.source""", (chart_id, lo, hi))
    rows = [r for r in cur.fetchall() if round_amount(to_decimal(r["amt"]), 2)]
    gl_ids = [r["trans_id"] for r in rows if r["gl_id"]]
    contra: Dict[int, List[Optional[str]]] = {}
    if gl_ids:
        cur.execute("""SELECT a.trans_id, c.accno, coalesce(c.link, '') AS link FROM acc_trans a
                       JOIN chart c ON (c.id = a.chart_id)
                       WHERE a.trans_id = ANY(%s) AND a.chart_id <> %s ORDER BY abs(a.amount) DESC""",
                    (gl_ids, chart_id))
        for r in cur.fetchall():
            slot = contra.setdefault(r["trans_id"], [None, None])
            i = 1 if "tax" in r["link"] else 0
            if slot[i] is None:
                slot[i] = r["accno"]
    out: List[_Hist] = []
    for r in rows:
        amt = round_amount(-to_decimal(r["amt"]), 2)
        extra = bank.get(r["trans_id"], "")
        if r["gl_id"]:
            acc, tax = contra.get(r["trans_id"], [None, None])
            if acc:
                text = f"{r['gld'] or ''} {r['glr'] or ''}"
                out.append(_Hist(("gl", acc, tax), (r["gld"] or r["glr"] or "").strip(), r["transdate"], amt,
                                 _tokens(f"{text} {extra}")))
        elif r["vid"] or r["cid"]:
            vc = "vendor" if r["vid"] else "customer"
            name, desc = (r["vname"], r["apd"]) if r["vid"] else (r["cname"], r["ard"])
            # bij een verzamelrelatie staat de echte naam in de omschrijving ("nummer | naam")
            desc = (desc or "").split("|")[-1]
            out.append(_Hist((vc, r["vid"] or r["cid"]), name or "", r["transdate"], amt,
                             _tokens(f"{name or ''} {desc} {extra}")))
    return out


def _open_groups(cur, chart_id: int, lo: date, hi: date, foreign: bool) -> List[Dict[str, Any]]:
    """Niet-afgeletterde boekingen op de bankrekening, per (boeking, datum, kenmerk)
    zoals rc.pl ze samenneemt; bedrag in bankteken (+ = bij)."""
    cur.execute(f"""SELECT ac.trans_id, ac.transdate, ac.source,
                           sum({'CASE WHEN ac.fx_transaction THEN 0 ELSE ac.amount END' if foreign
                                else 'ac.amount'})::numeric AS amount,
                           coalesce(max(gl.reference), max(ar.invnumber), max(ap.invnumber)) AS reference,
                           coalesce(max(gl.description), max(cu.name), max(ve.name)) AS description,
                           CASE WHEN max(gl.id) IS NOT NULL THEN 'gl' WHEN max(ar.id) IS NOT NULL THEN 'ar'
                                WHEN max(ap.id) IS NOT NULL THEN 'ap' END AS module
                    FROM acc_trans ac
                    LEFT JOIN gl ON (gl.id = ac.trans_id)
                    LEFT JOIN ar ON (ar.id = ac.trans_id) LEFT JOIN customer cu ON (cu.id = ar.customer_id)
                    LEFT JOIN ap ON (ap.id = ac.trans_id) LEFT JOIN vendor ve ON (ve.id = ap.vendor_id)
                    WHERE ac.chart_id = %s AND ac.cleared IS NULL AND ac.approved
                      AND ac.transdate BETWEEN %s AND %s
                    GROUP BY ac.trans_id, ac.transdate, ac.source
                    ORDER BY ac.transdate, ac.trans_id""", (chart_id, lo, hi))
    out = []
    for r in cur.fetchall():
        r = dict(r)
        r["amount"] = round_amount(-to_decimal(r["amount"]), 2)
        if r["amount"]:
            out.append(r)
    return out


def _vc_total(vc: str, amount: Decimal) -> Decimal:
    """Bedrag zoals het betaalscherm het boekt: klant = bankbedrag, leverancier = omgekeerd."""
    return amount if vc == "customer" else -amount


class _MatchContext:
    """Alles wat nodig is om voorstellen te maken, één keer geladen."""

    def __init__(self):
        self.invoices: List[Tuple[str, OpenInvoice]] = []
        self.by_dcn: Dict[str, List[Tuple[str, OpenInvoice]]] = {}
        self.by_vc: Dict[Tuple[str, int], List[OpenInvoice]] = {}
        self.names: Dict[Tuple[str, int], str] = {}
        self.ibans: Dict[str, List[Tuple[str, int]]] = {}
        self.groups: List[Dict[str, Any]] = []
        self.rules: List[Dict[str, Any]] = []
        self.claimed_inv: set = set()
        self.claimed_grp: set = set()
        self.open_lines: List[Dict[str, Any]] = []
        self.claimed_line: Dict[int, Dict[str, Any]] = {}   # bankregel -> de regel waarmee hij samen gaat (BT4)
        self.history: List[_Hist] = []
        self.idf: Dict[str, float] = {}
        self.collective: set = set()
        self.defaultcurrency: Optional[str] = None
        self.precision = 2

    @classmethod
    def load(cls, bi: BankImport, cur, acc: Dict[str, Any], lines: List[Dict[str, Any]]) -> "_MatchContext":
        self = cls()
        self.defaultcurrency = _db.default_currency(cur)
        self.precision = _db.get_precision(cur)
        pm = Payments(bi.conn, bi.config)
        dcn: Dict[Tuple[str, int], str] = {}
        for vc, table in (("customer", "ar"), ("vendor", "ap")):
            cur.execute(f"SELECT id, dcn FROM {table} WHERE amount <> paid AND coalesce(dcn, '') <> ''")
            for r in cur.fetchall():
                dcn[(vc, r["id"])] = norm_ref(r["dcn"])
            for inv in pm.open_invoices(vc):
                if not inv.due:
                    continue
                self.invoices.append((vc, inv))
                self.by_vc.setdefault((vc, inv.vc_id), []).append(inv)
                self.names[(vc, inv.vc_id)] = inv.name or ""
                d = dcn.get((vc, inv.id))
                if d:
                    self.by_dcn.setdefault(d, []).append((vc, inv))
        for vc in ("customer", "vendor"):
            cur.execute(f"""SELECT b.id, b.iban, v.name FROM bank b JOIN {vc} v ON (v.id = b.id)
                            WHERE coalesce(b.iban, '') <> ''""")
            for r in cur.fetchall():
                self.ibans.setdefault(norm_iban(r["iban"]), []).append((vc, r["id"]))
                self.names.setdefault((vc, r["id"]), r["name"] or "")
        lo = min(l["bookdate"] for l in lines) - timedelta(days=MATCH_DAYS)
        hi = max(l["bookdate"] for l in lines) + timedelta(days=MATCH_DAYS)
        foreign = (lines[0]["curr"] or self.defaultcurrency) != self.defaultcurrency
        self.groups = _open_groups(cur, acc["id"], lo, hi, foreign)
        self.rules = BankImport._rules(cur, chart_id=acc["id"])
        self.open_lines = [l for l in BankImport._lines(cur, acc["accno"], "open") if lo <= l["bookdate"] <= hi]
        self.history = _history(cur, acc["id"], lo - timedelta(days=HISTORY_DAYS), hi)
        # woorden die bij veel verschillende boekingen/relaties staan wegen minder (bv. "GOUDA")
        df: Dict[str, set] = {}
        for h in self.history:
            for k in h.toks:
                df.setdefault(k, set()).add(h.key)
        self.idf = {k: min(1.0, 3 / len(v)) for k, v in df.items() if len(v) > 3}
        vcs = {key for vc, inv in self.invoices for key in [(vc, inv.vc_id)]}
        self.collective = {k for k in vcs if BankImport._collective(cur, k[0], k[1], None)}
        return self

    # --- hulpfuncties

    def _fits(self, vc: str, inv: OpenInvoice, line: Dict[str, Any], any_sign: bool = False) -> bool:
        """Past de post bij de richting en valuta van de bankregel? any_sign: ook een
        post in de andere richting (creditnota die met de factuur verrekend is)."""
        if inv.id in self.claimed_inv:
            return False
        total = _vc_total(vc, line["amount"])
        if not total or not any_sign and (inv.due > 0) != (total > 0):
            return False
        return (inv.currency or self.defaultcurrency) == (line["curr"] or self.defaultcurrency)

    @staticmethod
    def _with_main(hits, line):
        """Genoemde posten (ook creditnota's), mits er minstens één in de richting van de bankregel is."""
        return hits if any((inv.due > 0) == (_vc_total(vc, line["amount"]) > 0) for vc, inv in hits) else []

    def _allocate(self, invs: List[OpenInvoice], total: Decimal) -> Tuple[List[Allocation], Decimal]:
        """Verdeel ``total`` (teken als in het betaalscherm) over de posten:
        creditnota's (andere richting) helemaal, daarna de oudste eerst."""
        s = 1 if total > 0 else -1
        left = total * s
        out: List[Allocation] = []
        order = sorted(invs, key=lambda i: (i.due * s > 0, i.transdate, i.id))
        for inv in order:
            due = inv.due * s
            if due > 0 and left <= 0:
                break
            take = due if due < 0 else min(left, due)
            left -= take
            out.append(Allocation(inv.id, inv.invnumber, inv.transdate, inv.due, take * s))
        return out, round_amount(left * s, self.precision) if left > 0 else ZERO

    def _payment(self, line, cat: str, vc: str, invs: List[OpenInvoice], reason: str,
                 exact_score: int, other_score: int) -> Proposal:
        total = _vc_total(vc, line["amount"])
        due = sum((i.due for i in invs), ZERO)
        exact = due == total
        allocs, rest = self._allocate(invs, total)
        vc_id = invs[0].vc_id
        p = Proposal(line=line, category=cat, kind="payment", vc=vc, vc_id=vc_id,
                     vc_name=self.names.get((vc, vc_id)), allocations=allocs, exact=exact, prepayment=rest,
                     score=exact_score if exact else other_score, reason=reason)
        if not exact:
            if rest:
                p.warnings.append(f"Bankbedrag is {rest} hoger dan openstaand: het meerdere wordt een "
                                  f"vooruitbetaling.")
            else:
                p.warnings.append(f"Bankbedrag is lager dan openstaand ({due}): gedeeltelijke betaling.")
        for a in allocs:
            self.claimed_inv.add(a.trans_id)
        return p

    def _best_group(self, hits: List[Tuple[str, OpenInvoice]], line) -> Tuple[str, List[OpenInvoice]]:
        """Bij posten van meerdere relaties: de relatie waarvan de posten precies
        aansluiten, anders die met de meeste posten."""
        groups: Dict[Tuple[str, int], List[OpenInvoice]] = {}
        for vc, inv in hits:
            groups.setdefault((vc, inv.vc_id), []).append(inv)
        best = None
        for (vc, vc_id), invs in groups.items():
            exact = sum((i.due for i in invs), ZERO) == _vc_total(vc, line["amount"])
            key = (exact, len(invs))
            if best is None or key > best[0]:
                best = (key, vc, invs)
        return best[1], best[2]

    # --- de koppelregels

    def propose(self, line: Dict[str, Any]) -> Proposal:
        if line["is_reversal"]:
            p = self._existing(line)
            if p:
                p.warnings.append("Storno van een eerdere boeking.")
                return p
            return Proposal(line=line, reason="Storno van een eerdere boeking: niet automatisch gekoppeld; "
                                              "verwerk de regel zelf (boeking corrigeren, koppelen of memoriaal).",
                            warnings=["Storno"])
        first = self.claimed_line.get(line["id"])
        if first:
            return Proposal(line=line, reason=f"Hoort bij het voorstel van de bankregel van {first['bookdate']} "
                                              f"({first['amount']}): samen afletteren.")
        for step in (self._by_reference, self._by_invnumber, self._by_iban_exact, self._existing,
                     self._existing_together, self._by_rule, self._by_iban_other, self._by_name, self._by_history,
                     self._by_name_partial):
            p = step(line)
            if p:
                if p.kind == "payment":
                    g = self._existing_group(line)
                    if g:
                        # de factuur staat open, maar er staat ook al een boeking met dit bedrag op de bank
                        # (bv. als memoriaal geboekt): niet blind boeken, anders staat het geld er twee keer
                        p.doubt, p.alternative = True, g
                        p.warnings.append(f"Let op: er staat al een niet-afgeletterde boeking met hetzelfde bedrag "
                                          f"op de bank ({g['reference'] or g['trans_id']}, {g['transdate']}); "
                                          f"mogelijk is dit al geboekt.")
                return p
        cands = [dict(vc=vc, vc_id=i, name=self.names.get((vc, i))) for vc, i in self.ibans.get(line["iban"], [])]
        return Proposal(line=line, reason="Geen koppeling gevonden.", candidates=cands)

    def _by_reference(self, line) -> Optional[Proposal]:
        refs = []
        if line.get("reference"):
            refs.append(norm_ref(line["reference"]))
        refs += find_references(" ".join(x for x in (line.get("description"), line.get("end_to_end")) if x))
        hits = []
        for ref in dict.fromkeys(refs):
            hits += [(vc, inv) for vc, inv in self.by_dcn.get(ref, []) if self._fits(vc, inv, line, True)]
        hits = self._with_main(hits, line)
        if not hits:
            return None
        vc, invs = self._best_group(hits, line)
        return self._payment(line, "a", vc, invs, "Betalingskenmerk van de factuur: "
                             + ", ".join(i.invnumber or str(i.id) for i in invs), 100, 80)

    def _by_invnumber(self, line) -> Optional[Proposal]:
        text = " ".join(x for x in (line.get("description"), line.get("end_to_end"), line.get("reference")) if x)
        if not text:
            return None
        up = text.upper()
        cands = [(vc, inv) for vc, inv in self.invoices
                 if inv.invnumber and len(inv.invnumber.strip()) >= MIN_INVNUMBER
                 and inv.invnumber.strip().upper() in up and self._fits(vc, inv, line, True)]
        hits = []
        taken: List[Tuple[int, int]] = []
        for vc, inv in sorted(cands, key=lambda x: -len(x[1].invnumber.strip())):
            num = re.escape(inv.invnumber.strip().upper())
            for m in re.finditer(rf"(?<![A-Z0-9]){num}(?![A-Z0-9]|[.,]\d)", up):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue          # deel van een langer, al gevonden nummer
                taken.append((m.start(), m.end()))
                hits.append((vc, inv))
                break
        hits = self._with_main(hits, line)
        if not hits:
            return None
        vc, invs = self._best_group(hits, line)
        return self._payment(line, "b", vc, invs, "Factuurnummer in de omschrijving: "
                             + ", ".join(i.invnumber for i in invs), 95, 70)

    def _relations(self, line) -> List[Tuple[str, int]]:
        rel = self.ibans.get(line.get("iban") or "", [])
        first = "customer" if line["amount"] > 0 else "vendor"
        return sorted(rel, key=lambda r: (r[0] != first, r[1]))

    def _by_iban_exact(self, line) -> Optional[Proposal]:
        for vc, vc_id in self._relations(line):
            total = _vc_total(vc, line["amount"])
            invs = [i for i in self.by_vc.get((vc, vc_id), []) if self._fits(vc, i, line)]
            one = [i for i in invs if i.due == total]
            if one:
                inv = sorted(one, key=lambda i: (i.transdate, i.id))[0]
                p = self._payment(line, "c", vc, [inv], f"Rekeningnummer van {self.names.get((vc, vc_id))}; "
                                  f"open post {inv.invnumber} met hetzelfde bedrag", 85, 85)
                self._same_amount_doubt(p, one)
                return p
            every = [i for i in self.by_vc.get((vc, vc_id), []) if self._fits(vc, i, line, True)]
            if invs and len(every) > 1 and sum((i.due for i in every), ZERO) == total:
                return self._payment(line, "c", vc, every, f"Rekeningnummer van {self.names.get((vc, vc_id))}; "
                                     f"alle open posten samen", 80, 80)
        return None

    def _by_iban_other(self, line) -> Optional[Proposal]:
        for vc, vc_id in self._relations(line):
            invs = [i for i in self.by_vc.get((vc, vc_id), []) if self._fits(vc, i, line)]
            if invs:
                return self._payment(line, "c", vc, invs, f"Rekeningnummer van {self.names.get((vc, vc_id))}; "
                                     f"bedrag wijkt af van de open posten", 40, 40)
        return None

    def _existing_group(self, line):
        best = None
        for g in self.groups:
            key = (g["trans_id"], g["transdate"], g["source"])
            if key in self.claimed_grp or g["amount"] != line["amount"]:
                continue
            days = abs((g["transdate"] - line["bookdate"]).days)
            if days > MATCH_DAYS:
                continue
            if best is None or days < best[0]:
                best = (days, g, key)
        return best[1] if best else None

    @staticmethod
    def _gkey(g) -> Tuple[int, date, Optional[str]]:
        return (g["trans_id"], g["transdate"], g["source"])

    def _same_amount_doubt(self, p: Proposal, posts: List[OpenInvoice]) -> None:
        """BT10/VZ5: meer open posten van dezelfde relatie met precies dit bedrag: de oudste, maar met twijfel."""
        if len(posts) > 1:
            p.doubt = True
            p.warnings.append(f"{len(posts)} open posten met hetzelfde bedrag bij {p.vc_name}; de oudste is gekozen, "
                              f"kies zo nodig een andere.")

    def _existing(self, line) -> Optional[Proposal]:
        g = self._existing_group(line)
        if not g:
            return self._existing_multi(line)
        key = self._gkey(g)
        days = abs((g["transdate"] - line["bookdate"]).days)
        # BT5: enige kandidaat = één open boeking met dit bedrag binnen het venster, en geen andere open bankregel
        # met dit bedrag die er ook bij zou passen
        groups = [x for x in self.groups if x["amount"] == line["amount"] and self._gkey(x) not in self.claimed_grp
                  and abs((x["transdate"] - line["bookdate"]).days) <= MATCH_DAYS]
        rivals = [l for l in self.open_lines if l["id"] != line["id"] and l["amount"] == line["amount"]
                  and abs((l["bookdate"] - g["transdate"]).days) <= MATCH_DAYS]
        self.claimed_grp.add(key)
        what = {"gl": "memoriaal", "ar": "verkoop", "ap": "inkoop"}.get(g["module"], "boeking")
        return Proposal(line=line, category="d", kind="match", score=75 - 3 * days, exact=True, match=g,
                        matches=[g], unique=len(groups) == 1 and not rivals,
                        reason=f"Bestaande boeking ({what} {g['reference'] or g['trans_id']}, {g['transdate']}"
                               f"{', kenmerk ' + g['source'] if g['source'] else ''}) met hetzelfde bedrag: "
                               f"alleen afletteren")

    def _existing_multi(self, line) -> Optional[Proposal]:
        """BT4: één bankregel tegen 2–3 bestaande boekingen (zelfde omschrijving/relatie, binnen GROUP_DAYS) die
        samen precies het bedrag zijn; bv. een betaling van een factuur min een creditnota."""
        near = [g for g in self.groups if self._gkey(g) not in self.claimed_grp
                and abs((g["transdate"] - line["bookdate"]).days) <= GROUP_DAYS and g["amount"] != line["amount"]]
        near.sort(key=lambda g: (abs((g["transdate"] - line["bookdate"]).days), g["trans_id"]))
        near = near[:12]
        from itertools import combinations
        for n in range(2, GROUP_MAX + 1):
            for combo in combinations(near, n):
                if sum((g["amount"] for g in combo), ZERO) != line["amount"]:
                    continue
                if len({(g["module"], g["description"]) for g in combo}) != 1:
                    continue
                for g in combo:
                    self.claimed_grp.add(self._gkey(g))
                combo = sorted(combo, key=lambda g: (g["transdate"], g["trans_id"]))
                refs = ", ".join(str(g["reference"] or g["trans_id"]) for g in combo)
                return Proposal(line=line, category="d", kind="match", score=50, exact=True, match=combo[0],
                                matches=list(combo),
                                reason=f"{n} bestaande boekingen ({combo[0]['description'] or ''}: {refs}) zijn samen "
                                       f"het bedrag: samen afletteren")
        return None

    def _existing_together(self, line) -> Optional[Proposal]:
        """BT4: 2–3 bankregels (zelfde tegenpartij of rekeningnummer, binnen GROUP_DAYS) tegen één bestaande
        boeking; bv. twee pinbetalingen die als één boeking zijn ingevoerd."""
        def same(l):
            return (line.get("iban") and l.get("iban") == line.get("iban")) or \
                (not line.get("iban") and line.get("name") and l.get("name") == line.get("name"))
        others = [l for l in self.open_lines if l["id"] != line["id"] and l["id"] not in self.claimed_line
                  and same(l) and abs((l["bookdate"] - line["bookdate"]).days) <= GROUP_DAYS
                  and (l["amount"] > 0) == (line["amount"] > 0)]
        if not others:
            return None
        from itertools import combinations
        for n in range(1, GROUP_MAX):
            for combo in combinations(others, n):
                total = line["amount"] + sum((l["amount"] for l in combo), ZERO)
                for g in self.groups:
                    if g["amount"] != total or self._gkey(g) in self.claimed_grp:
                        continue
                    dates = [line["bookdate"]] + [l["bookdate"] for l in combo]
                    if max(abs((g["transdate"] - d).days) for d in dates) > GROUP_DAYS:
                        continue
                    self.claimed_grp.add(self._gkey(g))
                    for l in combo:
                        self.claimed_line[l["id"]] = line
                    what = {"gl": "memoriaal", "ar": "verkoop", "ap": "inkoop"}.get(g["module"], "boeking")
                    return Proposal(line=line, category="d", kind="match", score=50, exact=True, match=g, matches=[g],
                                    together=list(combo),
                                    reason=f"Bestaande boeking ({what} {g['reference'] or g['trans_id']}, "
                                           f"{g['transdate']}) is samen met {n} andere bankregel(s) het bedrag: "
                                           f"samen afletteren")
        return None

    # --- zonder IBAN van de relatie (VZ1): naam en bedrag, dan de historie; nooit "zeker"

    def _who(self, line) -> Dict[str, float]:
        return _tokens(f"{line.get('name') or card_merchant(line.get('description')) or ''} {line.get('iban') or ''}")

    def _by_name(self, line) -> Optional[Proposal]:
        """VZ1: open post met precies het bedrag van een relatie waarvan de naam (of de omschrijving van de post, zo
        heeft een verzamelrelatie de echte naam) lijkt op de tegenpartij; anders een deelbetaling (VZ6)."""
        who = self._who(line)
        if not who:
            return None
        scored: Dict[Tuple[str, int], Tuple[float, List[OpenInvoice]]] = {}
        for vc, inv in self.invoices:
            if not self._fits(vc, inv, line):
                continue
            sc = _overlap(who, _tokens(f"{inv.name or ''} {(inv.description or '').split('|')[-1]}"), self.idf)
            if sc < 1:
                continue
            best, invs = scored.get((vc, inv.vc_id), (0.0, []))
            scored[(vc, inv.vc_id)] = (max(best, sc), invs + [inv])
        if not scored:
            return None
        hits = []
        for (vc, vc_id), (sc, invs) in scored.items():
            total = _vc_total(vc, line["amount"])
            exact = [i for i in invs if i.due == total]
            if exact:
                hits.append((sc, vc, exact))
        if hits:
            sc, vc, exact = max(hits, key=lambda h: (h[0], -min(i.transdate for i in h[2]).toordinal()))
            inv = sorted(exact, key=lambda i: (i.transdate, i.id))[0]
            p = self._payment(line, "f", vc, [inv], f"Naam lijkt op {self.names.get((vc, inv.vc_id))}; open post "
                              f"{inv.invnumber} met hetzelfde bedrag (geen rekeningnummer bij de relatie)", 60, 60)
            self._same_amount_doubt(p, exact)
            return p
        return None

    def _by_name_partial(self, line) -> Optional[Proposal]:
        """VZ6, als laatste: één open post van een relatie (geen verzamelrelatie) met een naam die erop lijkt en
        een hoger bedrag: deelbetaling."""
        who = self._who(line)
        if not who:
            return None
        best = None
        for vc, inv in self.invoices:
            if not self._fits(vc, inv, line) or (vc, inv.vc_id) in self.collective:
                continue
            sc = _overlap(who, _tokens(f"{inv.name or ''} {(inv.description or '').split('|')[-1]}"), self.idf)
            if sc >= 1 and inv.due > _vc_total(vc, line["amount"]) and (best is None or (sc, -inv.transdate.toordinal())
                                                                         > (best[0], -best[2].transdate.toordinal())):
                best = (sc, vc, inv)
        if not best:
            return None
        __, vc, inv = best
        return self._payment(line, "f", vc, [inv], f"Naam lijkt op {self.names.get((vc, inv.vc_id))}; bedrag wijkt af "
                             f"van de open posten (geen rekeningnummer bij de relatie)", 35, 35)

    def _by_history(self, line) -> Optional[Proposal]:
        """VZ1: wat is er eerder geboekt bij een bankregel/boeking met dezelfde naam (of winkelnaam, kenteken,
        rekeningnummer van de tegenpartij)? Memoriaal met die tegenrekening en btw, of de relatie."""
        who = self._who(line)
        allt = _tokens(" ".join(x for x in (line.get("name"), card_merchant(line.get("description")),
                                            line.get("description"), line.get("iban")) if x))
        strong = {k for k, w in allt.items() if w >= 3}
        if not who and not strong:
            return None
        score: Dict[tuple, float] = {}
        last: Dict[tuple, _Hist] = {}
        for h in self.history:
            if h.transdate > line["bookdate"] or (h.amount > 0) != (line["amount"] > 0):
                continue
            sn = _overlap(who, h.toks, self.idf) + sum(allt[k] for k in strong & h.toks.keys())
            if sn < 1:
                continue
            sc = 2 * sn + _overlap(allt, h.toks, self.idf)
            if h.amount == line["amount"]:
                sc *= 1.5
            sc /= 1 + (line["bookdate"] - h.transdate).days / 180
            score[h.key] = score.get(h.key, 0.0) + sc
            if h.key not in last or h.transdate > last[h.key].transdate:
                last[h.key] = h
        if not score:
            return None
        key, best = max(score.items(), key=lambda kv: kv[1])
        h = last[key]
        if key[0] == "gl":
            __, accno, tax = key
            return Proposal(line=line, category="g", kind="gl", score=30, exact=True,
                            rule=dict(id=None, accno=accno, tax_accno=tax, description=None),
                            reason=f"Zoals eerder: “{h.label}” ({h.transdate}) → {accno}"
                                   f"{' (btw ' + tax + ')' if tax else ''}")
        vc, vc_id = key
        name = self.names.get((vc, vc_id)) or h.label
        invs = [i for i in self.by_vc.get((vc, vc_id), []) if self._fits(vc, i, line)]
        if (vc, vc_id) in self.collective:
            # verzamelrelatie: alleen posten waarvan de omschrijving de naam noemt, en geen deelbetaling
            invs = [i for i in invs if _overlap(who, _tokens((i.description or "").split("|")[-1]), self.idf) >= 1]
            invs = [i for i in invs if i.due == _vc_total(vc, line["amount"])]
        total = _vc_total(vc, line["amount"])
        exact = [i for i in invs if i.due == total]
        if exact:
            inv = sorted(exact, key=lambda i: (i.transdate, i.id))[0]
            p = self._payment(line, "g", vc, [inv], f"Zoals eerder: {name} ({h.transdate}); open post "
                              f"{inv.invnumber} met hetzelfde bedrag", 30, 30)
            self._same_amount_doubt(p, exact)
            return p
        if invs and sum((i.due for i in invs), ZERO) > total:
            return self._payment(line, "g", vc, invs, f"Zoals eerder: {name} ({h.transdate}); bedrag wijkt af van "
                                 f"de open posten", 25, 25)
        return Proposal(line=line, category="g", reason=f"Zoals eerder: {name} ({h.transdate}); er staat geen open "
                                                        f"post met dit bedrag: voer eerst de factuur in, of boek een "
                                                        f"memoriaal.",
                        candidates=[dict(vc=vc, vc_id=vc_id, name=name)])

    def _by_rule(self, line) -> Optional[Proposal]:
        direction = "+" if line["amount"] > 0 else "-"
        text = " ".join(x for x in (line.get("name"), line.get("description")) if x).lower()
        for r in self.rules:
            if r["direction"] and r["direction"] != direction:
                continue
            if r["iban"] and r["iban"] != line.get("iban"):
                continue
            if r["keyword"] and r["keyword"].lower() not in text:
                continue
            return Proposal(line=line, category="e", kind="gl", score=90, exact=True, rule=r,
                            reason=f"Vaste regel: {'rekeningnummer ' + r['iban'] if r['iban'] else ''}"
                                   f"{' en ' if r['iban'] and r['keyword'] else ''}"
                                   f"{'trefwoord “' + r['keyword'] + '”' if r['keyword'] else ''}"
                                   f" → {r['accno']}{' (btw ' + r['tax_accno'] + ')' if r['tax_accno'] else ''}")
        return None
