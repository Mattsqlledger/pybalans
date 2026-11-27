"""Bankafschriften inlezen: CAMT.053 (XML) en MT940 (SWIFT-tekst).

Pure Python zonder database en alleen met de standaardbibliotheek. Het
resultaat is een lijst :class:`Statement`-objecten met :class:`StatementLine`-
regels; koppelen en afletteren gebeurt elders.

Gebruik::

    from sqlledger.bankstatement import parse
    for st in parse(open("afschrift.xml", "rb").read(), "afschrift.xml"):
        verschil = st.check_balance()
        for regel in st.lines:
            print(regel.booking_date, regel.amount, regel.description)

Tekenconventie: ``amount`` is positief bij een bijschrijving en negatief bij
een afschrijving, vanuit de rekeninghouder gezien.

Storno's (bewuste keuze, zie ook de tests):

* CAMT.053: ``CdtDbtInd`` geeft volgens ISO 20022 altijd de werkelijke
  richting van de boeking op de rekening aan, óók bij ``RvslInd=true``
  ("CRDT + reversal: de oorspronkelijke boeking was een debet"). Het teken
  volgt daarom ``CdtDbtInd`` en wordt niet nogmaals omgedraaid; alleen zo
  sluit beginsaldo + regels aan op het eindsaldo. Bij een storno wordt de
  tegenpartij wél gezocht aan de kant van de oorspronkelijke boeking
  (een gestorneerde incasso: bij, maar tegenpartij is de ``Cdtr``).
* MT940: ``RC`` (reversal of credit) is een afschrijving (negatief) en
  ``RD`` (reversal of debit) een bijschrijving (positief).

In beide gevallen staat ``is_reversal`` op True.

Veiligheid: XML met een DOCTYPE- of ENTITY-declaratie wordt geweigerd
(bescherming tegen 'billion laughs' en externe entiteiten).
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

from .errors import ValidationError

__all__ = [
    "BankStatementError",
    "Statement",
    "StatementLine",
    "parse",
    "detect_format",
    "is_valid_iban",
    "nl_iban",
    "card_merchant",
]


class BankStatementError(ValidationError):
    """Bankafschrift kan niet worden ingelezen (onbekend formaat, ongeldige inhoud)."""


# ---------------------------------------------------------------- datamodel


@dataclass(frozen=True)
class StatementLine:
    """Eén mutatie op een bankafschrift.

    sequence: volgnummer binnen het afschrift (1, 2, ...).
    amount: met teken; positief = bij, negatief = af.
    description: ongestructureerde omschrijving (samengevoegd).
    payment_reference: gestructureerd betalingskenmerk (CdtrRefInf/Ref, /CRF/,
        /REMI/STRD/CUR/... of 'betalingskenmerk' in een vrije :86:).
    bank_reference: AcctSvcrRef (CAMT) of de bankreferentie na '//' (MT940 :61:).
    transaction_code: 'Domn/Fmly/SubFmly' of Prtry-code (CAMT), 'Nxxx' (MT940).
    account: rekening van het afschrift (IBAN of rekeningnummer).
    occurrence: volgnummer binnen volledig gelijke regels in hetzelfde
        afschrift (0 voor de eerste); onderdeel van :attr:`dedup_key`.
    """

    sequence: int
    booking_date: date
    value_date: Optional[date]
    amount: Decimal
    currency: Optional[str] = None
    counterparty_name: Optional[str] = None
    counterparty_iban: Optional[str] = None
    counterparty_bic: Optional[str] = None
    description: Optional[str] = None
    payment_reference: Optional[str] = None
    end_to_end_id: Optional[str] = None
    bank_reference: Optional[str] = None
    transaction_code: Optional[str] = None
    is_reversal: bool = False
    account: Optional[str] = None
    occurrence: int = 0

    def _base_key(self) -> str:
        return "|".join(
            [
                self.account or "",
                self.booking_date.isoformat(),
                _amount_key(self.amount),
                self.bank_reference or "",
                self.end_to_end_id or "",
                self.description or "",
            ]
        )

    @property
    def dedup_key(self) -> str:
        """Stabiele sleutel om dubbel inlezen te herkennen (sha256, hex).

        Over rekening, boekdatum, bedrag, bankreferentie, end-to-end-id,
        omschrijving en het volgnummer binnen gelijke regels. Twee identieke
        regels in één afschrift krijgen zo toch een verschillende sleutel,
        terwijl hetzelfde afschrift nogmaals inlezen dezelfde sleutels geeft.
        """
        raw = f"{self._base_key()}|{self.occurrence}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Statement:
    """Eén bankafschrift (CAMT ``Stmt`` of één MT940-bericht).

    format: 'camt053' of 'mt940'.
    account: IBAN, of rekeningnummer als er geen IBAN is.
    statement_number: ElctrncSeqNb/LglSeqNb (CAMT) of :28C: (MT940).
    id: Stmt/Id (CAMT) of :20: (MT940).
    created: aanmaakdatum (CAMT CreDtTm; MT940: None).
    available_balance: MT940 :64: (informatief, niet in de saldocontrole).
    bic: BIC van de bank uit de kop van het MT940-bestand (ABN AMRO: ``ABNANL2A``).
    """

    format: str
    account: Optional[str]
    currency: Optional[str]
    statement_number: Optional[str] = None
    id: Optional[str] = None
    created: Optional[date] = None
    opening_balance: Optional[Decimal] = None
    opening_date: Optional[date] = None
    closing_balance: Optional[Decimal] = None
    closing_date: Optional[date] = None
    lines: Tuple[StatementLine, ...] = field(default_factory=tuple)
    available_balance: Optional[Decimal] = None
    version: Optional[str] = None
    bic: Optional[str] = None

    @property
    def iban(self) -> Optional[str]:
        """Het rekeningnummer als het een geldige IBAN is, anders None. Een kaal Nederlands rekeningnummer (ABN AMRO
        zet in MT940 ``:25:417164300``) wordt met de bankcode uit de BIC omgerekend tot IBAN."""
        if self.account and is_valid_iban(self.account):
            return self.account
        digits = re.sub(r"[.\s]", "", self.account or "")
        if self.bic and self.bic[4:6] == "NL" and re.fullmatch(r"\d{7,10}", digits):
            return nl_iban(self.bic[:4], digits)
        return None

    @property
    def total(self) -> Decimal:
        """Som van alle regels (met teken)."""
        return sum((ln.amount for ln in self.lines), Decimal(0))

    def check_balance(self) -> Optional[Decimal]:
        """Saldocontrole: eindsaldo - (beginsaldo + som regels).

        Geeft Decimal(0) als het klopt, anders het verschil. None als begin-
        of eindsaldo onbekend is.
        """
        if self.opening_balance is None or self.closing_balance is None:
            return None
        return self.closing_balance - (self.opening_balance + self.total)


# ---------------------------------------------------------------- hulpfuncties


def _amount_key(amount: Decimal) -> str:
    """Bedrag als vaste tekst ('10', '10.0' en '10.00' gelijk)."""
    s = format(amount.normalize(), "f")
    return "0" if s in ("-0", "0") else s


def _clean(value: Optional[str]) -> Optional[str]:
    """Witruimte samenvoegen; lege tekst wordt None."""
    if value is None:
        return None
    s = re.sub(r"\s+", " ", value).strip()
    return s or None


def nl_iban(bankcode: str, account: str) -> str:
    """Nederlands IBAN uit bankcode (4 letters) en rekeningnummer (tot 10 cijfers): NLkk BBBB 0000000000."""
    bban = bankcode.upper() + account.zfill(10)
    digits = "".join(str(int(c, 36)) for c in bban + "NL00")
    return f"NL{98 - int(digits) % 97:02d}{bban}"


#: pinbetaling/geldopname in een vrije :86: (ABN AMRO): "BEA, BETAALPAS <winkel>,PAS492 NR:..." of
#: "BEA, BETAALPAS   720 HORNBACH NIEUWERKE NR:CT404723, ..."; een kassa-voorvoegsel (BCK*, CCV*) valt weg
_CARD = re.compile(r"^(?:BEA|GEA|eCom)\s*,\s*(?:BETAALPAS|Apple Pay|Google Pay)?\s*(.+?)\s*(?:,PAS\d+|\s+NR:)", re.I)
_CARD_PREFIX = re.compile(r"^(?:\d{1,4}\s+)?(?:[A-Z]{2,7}[ _]?\*\s*)?", re.I)


def card_merchant(text: Optional[str]) -> Optional[str]:
    """Winkelnaam van een pinbetaling (BEA/GEA) uit de omschrijving, anders None."""
    m = _CARD.match((text or "").strip())
    if not m:
        return None
    name = _CARD_PREFIX.sub("", m.group(1)).strip(" ,")
    return name or None


def is_valid_iban(value: str) -> bool:
    """Controleer een IBAN (lengte, tekens en modulus 97)."""
    s = value.replace(" ", "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", s):
        return False
    digits = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(digits) % 97 == 1


def _finish_lines(account: Optional[str], rows: Iterable[dict]) -> Tuple[StatementLine, ...]:
    """Maak regels definitief: volgnummer, rekening en occurrence invullen."""
    seen: Dict[str, int] = {}
    out: List[StatementLine] = []
    for i, row in enumerate(rows, start=1):
        line = StatementLine(sequence=i, account=account, **row)
        base = line._base_key()
        n = seen.get(base, 0)
        seen[base] = n + 1
        if n:
            line = replace(line, occurrence=n)
        out.append(line)
    return tuple(out)


# ---------------------------------------------------------------- herkenning

_CAMT053_NS = re.compile(r"camt\.053\.001\.(\d{2})")
_MT940_START = re.compile(r"(?m)^\s*:20:")
_MT940_BAL = re.compile(r"(?m)^\s*:60[FM]:")


def _decode_text(data: bytes) -> str:
    """Tekst decoderen: utf-8 (met of zonder BOM), anders latin-1."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _looks_like_xml(data: bytes) -> bool:
    head = data.lstrip(b"\xef\xbb\xbf").lstrip()
    return head.startswith(b"<") or data.startswith((b"\xff\xfe", b"\xfe\xff"))


def detect_format(data: bytes) -> Optional[str]:
    """Geef 'camt053', 'mt940' of None (onbekend)."""
    if _looks_like_xml(data):
        if b"camt.053" in data or b"BkToCstmrStmt" in data:
            return "camt053"
        return None
    text = _decode_text(data)
    if _MT940_START.search(text) and _MT940_BAL.search(text):
        return "mt940"
    return None


def parse(data: bytes, filename: Optional[str] = None) -> List[Statement]:
    """Lees een bankafschriftbestand in; het formaat wordt zelf herkend.

    Eén bestand kan meerdere afschriften bevatten. ``filename`` wordt alleen
    in foutmeldingen gebruikt.
    """
    where = f" ({filename})" if filename else ""
    if not data or not data.strip():
        raise BankStatementError(f"leeg bestand{where}")
    if _looks_like_xml(data):
        fmt = detect_format(data)
        if fmt != "camt053":
            raise BankStatementError(
                f"XML-bestand is geen CAMT.053-bankafschrift{where}"
            )
        statements = _parse_camt(data, where)
    elif detect_format(data) == "mt940":
        statements = _parse_mt940(_decode_text(data), where)
    else:
        raise BankStatementError(
            f"onbekend formaat{where}: verwacht CAMT.053 (XML) of MT940"
        )
    if not statements:
        raise BankStatementError(f"geen afschriften gevonden{where}")
    return statements


# ---------------------------------------------------------------- CAMT.053

_FORBIDDEN_XML = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _check_xml_safe(data: bytes, where: str) -> None:
    probe = data
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        probe = data.decode("utf-16").encode("utf-8")
    if _FORBIDDEN_XML.search(probe):
        raise BankStatementError(
            f"XML met DOCTYPE/ENTITY-declaratie geweigerd{where}"
        )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el: Optional[ET.Element], name: str) -> List[ET.Element]:
    if el is None:
        return []
    return [c for c in el if _local(c.tag) == name]


def _find(el: Optional[ET.Element], *path: str) -> Optional[ET.Element]:
    """Zoek een pad van lokale namen (namespace-onafhankelijk)."""
    cur = el
    for name in path:
        if cur is None:
            return None
        cur = next((c for c in cur if _local(c.tag) == name), None)
    return cur


def _text(el: Optional[ET.Element], *path: str) -> Optional[str]:
    node = _find(el, *path)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _iter_local(el: ET.Element, name: str) -> Iterable[ET.Element]:
    for node in el.iter():
        if _local(node.tag) == name:
            yield node


def _camt_date(el: Optional[ET.Element]) -> Optional[date]:
    """Datum uit een element met Dt of DtTm (of zelf een datum/tijd)."""
    if el is None:
        return None
    s = _text(el, "Dt") or _text(el, "DtTm") or (el.text or "").strip()
    return _iso_date(s)


def _iso_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        raise BankStatementError(f"ongeldige datum in CAMT: {s!r}") from None


def _camt_amount(el: Optional[ET.Element]) -> Tuple[Optional[Decimal], Optional[str]]:
    if el is None or el.text is None:
        return None, None
    try:
        return Decimal(el.text.strip()), el.get("Ccy")
    except InvalidOperation:
        raise BankStatementError(f"ongeldig bedrag in CAMT: {el.text!r}") from None


def _camt_account(acct: Optional[ET.Element]) -> Optional[str]:
    return _text(acct, "Id", "IBAN") or _text(acct, "Id", "Othr", "Id")


def _camt_status(ntry: ET.Element) -> Optional[str]:
    sts = _find(ntry, "Sts")
    if sts is None:
        return None
    return (_text(sts, "Cd") or _text(sts, "Prtry") or (sts.text or "").strip()).upper() or None


def _camt_txcode(el: Optional[ET.Element]) -> Optional[str]:
    bk = _find(el, "BkTxCd")
    if bk is None:
        return None
    domn = _text(bk, "Domn", "Cd")
    if domn:
        parts = [domn, _text(bk, "Domn", "Fmly", "Cd"), _text(bk, "Domn", "Fmly", "SubFmlyCd")]
        return "/".join(p for p in parts if p)
    return _text(bk, "Prtry", "Cd")


def _camt_party_name(rltd: Optional[ET.Element], role: str) -> Optional[str]:
    # .02-.07: RltdPties/Dbtr/Nm; .08+: RltdPties/Dbtr/Pty/Nm
    return _text(rltd, role, "Nm") or _text(rltd, role, "Pty", "Nm")


def _camt_bic(agts: Optional[ET.Element], role: str) -> Optional[str]:
    return _text(agts, role, "FinInstnId", "BIC") or _text(agts, role, "FinInstnId", "BICFI")


def _parse_camt(data: bytes, where: str) -> List[Statement]:
    _check_xml_safe(data, where)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise BankStatementError(f"ongeldige XML{where}: {exc}") from None
    m = _CAMT053_NS.search(root.tag)
    version = m.group(1) if m else None
    if version is not None and not 2 <= int(version) <= 13:
        raise BankStatementError(
            f"CAMT.053-versie 001.{version} wordt niet ondersteund{where}"
        )
    stmts = list(_iter_local(root, "Stmt"))
    return [_camt_statement(st, version) for st in stmts]


def _camt_statement(st: ET.Element, version: Optional[str]) -> Statement:
    acct = _find(st, "Acct")
    account = _camt_account(acct)
    currency = _text(acct, "Ccy")

    opening: Dict[str, Tuple[Decimal, Optional[date]]] = {}
    closing = None
    for bal in _children(st, "Bal"):
        code = (_text(bal, "Tp", "CdOrPrtry", "Cd") or _text(bal, "Tp", "CdOrPrtry", "Prtry") or "").upper()
        amt, ccy = _camt_amount(_find(bal, "Amt"))
        if amt is None:
            continue
        currency = currency or ccy
        if (_text(bal, "CdtDbtInd") or "CRDT").upper() == "DBIT":
            amt = -amt
        when = _camt_date(_find(bal, "Dt"))
        if code in ("OPBD", "PRCD"):
            opening[code] = (amt, when)
        elif code == "CLBD":
            closing = (amt, when)
    open_bal = opening.get("OPBD") or opening.get("PRCD")

    rows: List[dict] = []
    for ntry in _children(st, "Ntry"):
        rows.extend(_camt_entry(ntry, currency))

    return Statement(
        format="camt053",
        account=account,
        currency=currency,
        statement_number=_text(st, "ElctrncSeqNb") or _text(st, "LglSeqNb"),
        id=_text(st, "Id"),
        created=_iso_date(_text(st, "CreDtTm")),
        opening_balance=open_bal[0] if open_bal else None,
        opening_date=open_bal[1] if open_bal else None,
        closing_balance=closing[0] if closing else None,
        closing_date=closing[1] if closing else None,
        lines=_finish_lines(account, rows),
        version=f"001.{version}" if version else None,
    )


def _camt_entry(ntry: ET.Element, stmt_ccy: Optional[str]) -> List[dict]:
    """Eén Ntry omzetten naar één of meer regels (batch: per TxDtls)."""
    status = _camt_status(ntry)
    if status is not None and status not in ("BOOK", "BOOKED"):
        return []  # PDNG/INFO e.d.: (nog) niet geboekt

    amount, ccy = _camt_amount(_find(ntry, "Amt"))
    if amount is None:
        raise BankStatementError("CAMT-boeking zonder bedrag")
    credit = (_text(ntry, "CdtDbtInd") or "").upper() == "CRDT"
    reversal = (_text(ntry, "RvslInd") or "").lower() in ("true", "1")
    booking = _camt_date(_find(ntry, "BookgDt"))
    value = _camt_date(_find(ntry, "ValDt"))
    if booking is None:
        booking = value
    if booking is None:
        raise BankStatementError("CAMT-boeking zonder boekdatum")
    base = dict(
        booking_date=booking,
        value_date=value,
        currency=ccy or stmt_ccy,
        bank_reference=_text(ntry, "AcctSvcrRef"),
        transaction_code=_camt_txcode(ntry),
        is_reversal=reversal,
    )
    entry_info = _clean(_text(ntry, "AddtlNtryInf"))

    txs = [tx for nd in _children(ntry, "NtryDtls") for tx in _children(nd, "TxDtls")]
    signed = amount if credit else -amount

    if len(txs) > 1:
        parts = []
        for tx in txs:
            tx_amt, _ = _camt_amount(_find(tx, "AmtDtls", "TxAmt", "Amt"))
            if tx_amt is None:
                tx_amt, _ = _camt_amount(_find(tx, "Amt"))
            if tx_amt is None:
                break
            tx_ind = (_text(tx, "CdtDbtInd") or "").upper()
            tx_credit = credit if not tx_ind else tx_ind == "CRDT"
            parts.append((tx, tx_amt if tx_credit else -tx_amt, tx_credit))
        if len(parts) == len(txs) and sum(p[1] for p in parts) == signed:
            return [
                _camt_tx_row(base, tx, amt, tx_credit, reversal, entry_info)
                for tx, amt, tx_credit in parts
            ]
        # bedragen ontbreken of tellen niet op: één regel met het Ntry-bedrag
    tx = txs[0] if txs else None
    return [_camt_tx_row(base, tx, signed, credit, reversal, entry_info)]


def _camt_tx_row(
    base: dict,
    tx: Optional[ET.Element],
    amount: Decimal,
    credit: bool,
    reversal: bool,
    entry_info: Optional[str],
) -> dict:
    row = dict(base, amount=amount)
    if tx is None:
        row["description"] = entry_info
        return row

    # tegenpartij aan de kant van de oorspronkelijke boeking (zie moduledoc)
    orig_credit = credit != reversal
    rltd = _find(tx, "RltdPties")
    agts = _find(tx, "RltdAgts")
    sides = [("Dbtr", "DbtrAcct", "DbtrAgt"), ("Cdtr", "CdtrAcct", "CdtrAgt")]
    if not orig_credit:
        sides.reverse()
    for party, acct, agt in sides:
        name = _camt_party_name(rltd, party)
        iban = _camt_account(_find(rltd, acct))
        if name or iban:
            row["counterparty_name"] = _clean(name)
            row["counterparty_iban"] = iban
            row["counterparty_bic"] = _camt_bic(agts, agt)
            break

    rmt = _find(tx, "RmtInf")
    ustrd = [(u.text or "").strip() for u in _children(rmt, "Ustrd")]
    refs = [
        r
        for strd in _children(rmt, "Strd")
        for r in [_text(strd, "CdtrRefInf", "Ref")]
        if r
    ]
    row["description"] = (
        _clean(" ".join(u for u in ustrd if u))
        or _clean(_text(tx, "AddtlTxInf"))
        or entry_info
    )
    row["payment_reference"] = refs[0] if refs else None
    e2e = _text(tx, "Refs", "EndToEndId")
    row["end_to_end_id"] = None if e2e in (None, "NOTPROVIDED") else e2e
    row["bank_reference"] = _text(tx, "Refs", "AcctSvcrRef") or base["bank_reference"]
    row["transaction_code"] = _camt_txcode(tx) or base["transaction_code"]
    return row


# ---------------------------------------------------------------- MT940

_TAG = re.compile(r"^:(\d{2}[A-Z]?):(.*)$")
_HEADER_LINE = re.compile(r"^(940|[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?)$")
_BALANCE = re.compile(r"^([CD])(\d{6})([A-Z]{3})(\d+(?:,\d*)?)")
_LINE61 = re.compile(
    r"^(\d{6})(\d{4})?(RC|RD|C|D)([A-Z])?(\d+,\d*)([NFS][A-Z0-9]{3})(.*)$"
)
_STRUCT_CODES = (
    "EREF|NAME|IBAN|BIC|REMI|CSID|MARF|TRCD|CRF|CNTP|ORDP|BENM|ID|ADDR|PREF|IREF|"
    "KREF|PURP|ULTC|ULTD|ULTB|SVCL|RTRN|TRTP|ISDT|OCMT|ATOS|CHGS|EXCH|BUSP|"
    "ACCW|SUM"
)
_STRUCT_TOKEN = re.compile(r"/(" + _STRUCT_CODES + r")/")
_STRUCT_DETECT = re.compile(r"^/(" + _STRUCT_CODES + r")/")


def _mt_amount(s: str) -> Decimal:
    s = s.replace(",", ".")
    if s.endswith("."):
        s += "0"
    try:
        return Decimal(s)
    except InvalidOperation:
        raise BankStatementError(f"ongeldig MT940-bedrag: {s!r}") from None


def _mt_date(s: str) -> date:
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    try:
        return date(2000 + yy if yy < 80 else 1900 + yy, mm, dd)
    except ValueError:
        raise BankStatementError(f"ongeldige MT940-datum: {s!r}") from None


def _mt_booking_date(mmdd: str, value_date: date) -> date:
    """Boekdatum (MMDD) bij een valutadatum: kies het jaar dat het dichtst bij ligt.

    Zo wordt valuta 2023-12-31 met boekdatum 0102 de 2e januari 2024, en
    valuta 2024-01-01 met boekdatum 1229 de 29e december 2023.
    """
    mm, dd = int(mmdd[:2]), int(mmdd[2:])
    best = None
    for year in (value_date.year - 1, value_date.year, value_date.year + 1):
        try:
            cand = date(year, mm, dd)
        except ValueError:
            continue
        if best is None or abs((cand - value_date).days) < abs((best - value_date).days):
            best = cand
    if best is None:
        raise BankStatementError(f"ongeldige MT940-boekdatum: {mmdd!r}")
    return best


def _mt_balance(value: str) -> Tuple[Decimal, date, str]:
    m = _BALANCE.match(value.strip())
    if not m:
        raise BankStatementError(f"ongeldig MT940-saldo: {value!r}")
    amt = _mt_amount(m.group(4))
    return (-amt if m.group(1) == "D" else amt), _mt_date(m.group(2)), m.group(3)


def _mt_account(value: str) -> Tuple[str, Optional[str]]:
    """:25: -> (rekening, valuta). 'NL..INGB..EUR', 'INGBNL2A/NL..', '123456789'."""
    s = value.strip().replace(" ", "")
    if "/" in s:
        s = s.rsplit("/", 1)[-1] or s.split("/", 1)[0]
    ccy = None
    if not is_valid_iban(s) and re.search(r"[A-Z]{3}$", s) and is_valid_iban(s[:-3]):
        s, ccy = s[:-3], s[-3:]
    elif re.fullmatch(r"\d+[A-Z]{3}", s):
        s, ccy = s[:-3], s[-3:]
    return s, ccy


def _split_mt940(text: str) -> List[List[Tuple[str, str]]]:
    """Tekst opdelen in berichten, elk een lijst (tag, waarde)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # SWIFT-blokken {1:...}{2:...}{3:...} en de opening van {4: weghalen
    text = re.sub(r"\{[1-3]:(\{[^}]*\}|[^{}])*\}", "", text)
    text = text.replace("{4:", "\n")
    text = re.sub(r"\{5:(\{[^}]*\}|[^{}])*\}", "", text)

    messages: List[List[Tuple[str, str]]] = []
    current: Optional[List[List[str]]] = None

    def close() -> None:
        nonlocal current
        if current:
            messages.append([(t, "\n".join(v)) for t, *v in current])
        current = None

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if stripped in ("-", "-}", "}"):
            close()
            continue
        m = _TAG.match(stripped)
        if m:
            tag, value = m.group(1), m.group(2)
            if tag == "20":
                if current:
                    # ABN-kop (ABNANL2A / 940) aan het eind van het vorige veld weg
                    last = current[-1]
                    while len(last) > 2 and _HEADER_LINE.match(last[-1].strip()):
                        last.pop()
                close()
                current = []
            if current is None:
                continue  # tag buiten een bericht
            current.append([tag, value])
        elif current:
            current[-1].append(line)
        # regels vóór de eerste tag (kopregels) worden genegeerd
    close()
    return messages


_HEADER_BIC = re.compile(r"(?m)^\s*([A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\s*$")


def _parse_mt940(text: str, where: str) -> List[Statement]:
    statements = []
    m = _HEADER_BIC.search(text)          # kopregel van het bestand (ABN AMRO), nodig voor een kaal rekeningnummer
    for fields in _split_mt940(text):
        statements.append(replace(_mt940_statement(fields, where), bic=m.group(1) if m else None))
    return statements


def _mt940_statement(fields: List[Tuple[str, str]], where: str) -> Statement:
    ref = account = number = currency = None
    opening = closing = None
    available = None
    rows: List[dict] = []
    prev_tag = None
    for tag, value in fields:
        if tag == "20":
            ref = value.strip() or None
        elif tag == "25":
            account, ccy = _mt_account(value)
            currency = currency or ccy
        elif tag in ("28", "28C"):
            number = value.strip() or None
        elif tag in ("60F", "60M"):
            if opening is None:
                amt, d, ccy = _mt_balance(value)
                opening = (amt, d)
                currency = ccy
        elif tag in ("62F", "62M"):
            amt, d, ccy = _mt_balance(value)
            closing = (amt, d)
            currency = currency or ccy
        elif tag == "64":
            available = _mt_balance(value)[0]
        elif tag == "61":
            rows.append(_mt940_line(value, currency))
        elif tag == "86" and prev_tag == "61" and rows:
            rows[-1].update(_mt940_info(value, rows[-1]))
        prev_tag = tag
    if opening is None and closing is None and not rows:
        raise BankStatementError(f"MT940-bericht zonder saldi of mutaties{where}")
    return Statement(
        format="mt940",
        account=account,
        currency=currency,
        statement_number=number,
        id=ref,
        opening_balance=opening[0] if opening else None,
        opening_date=opening[1] if opening else None,
        closing_balance=closing[0] if closing else None,
        closing_date=closing[1] if closing else None,
        lines=_finish_lines(account, rows),
        available_balance=available,
    )


def _mt940_line(value: str, currency: Optional[str]) -> dict:
    first, _, rest = value.partition("\n")
    m = _LINE61.match(first.strip())
    if not m:
        raise BankStatementError(f"ongeldige MT940 :61:-regel: {first!r}")
    vdate = _mt_date(m.group(1))
    bdate = _mt_booking_date(m.group(2), vdate) if m.group(2) else vdate
    mark = m.group(3)
    amount = _mt_amount(m.group(5))
    if mark in ("D", "RC"):
        amount = -amount
    refs = m.group(7)
    bank_ref = None
    if "//" in refs:
        bank_ref = refs.split("//", 1)[1].strip() or None
    row = dict(
        booking_date=bdate,
        value_date=vdate,
        amount=amount,
        currency=currency,
        bank_reference=bank_ref,
        transaction_code=m.group(6),
        is_reversal=mark.startswith("R"),
    )
    # aanvullende gegevens (2e regel): bij Rabobank o.a. de tegenrekening
    extra = rest.strip().split()
    if extra and is_valid_iban(extra[0]):
        row["counterparty_iban"] = extra[0].upper()
    return row


def _struct_fields(text: str) -> List[Tuple[str, str]]:
    """'/CODE/waarde/CODE/waarde' -> [(CODE, waarde)] voor bekende codes."""
    out: List[Tuple[str, str]] = []
    matches = list(_STRUCT_TOKEN.finditer(text))
    for i, m in enumerate(matches):
        # een code direct na een andere (b.v. /ORDP//NAME/) geeft een lege waarde
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1), text[m.end() : end]))
    return out


def _non_empty(v: Optional[str]) -> Optional[str]:
    v = _clean(v.strip("/")) if v else None
    return None if v in (None, "NOTPROVIDED", "NONREF") else v


def _mt940_info(value: str, row: dict) -> dict:
    """:86: ontleden; gestructureerd (/CODE/waarde/) of vrije tekst."""
    lines = [ln.strip() for ln in value.split("\n")]
    joined = "".join(lines)
    info: dict = {}
    if _STRUCT_DETECT.match(joined):
        for code, val in _struct_fields(joined):
            if code == "EREF":
                info.setdefault("end_to_end_id", _non_empty(val))
            elif code == "NAME":
                info.setdefault("counterparty_name", _non_empty(val))
            elif code == "IBAN":
                info.setdefault("counterparty_iban", _non_empty(val))
            elif code == "BIC":
                info.setdefault("counterparty_bic", _non_empty(val))
            elif code == "CNTP":
                parts = val.split("/") + ["", "", ""]
                info.setdefault("counterparty_iban", _non_empty(parts[0]))
                info.setdefault("counterparty_bic", _non_empty(parts[1]))
                info.setdefault("counterparty_name", _non_empty(parts[2]))
            elif code == "CRF":
                info.setdefault("payment_reference", _non_empty(val))
            elif code == "REMI":
                v = val.rstrip("/")
                if v.startswith("USTD/"):
                    info["description"] = _non_empty(v[5:].lstrip("/"))
                elif v.startswith("STRD/"):
                    ref = v.rstrip("/").rsplit("/", 1)[-1]
                    info.setdefault("payment_reference", _non_empty(ref))
                else:
                    info["description"] = _non_empty(v)
        info = {k: v for k, v in info.items() if v is not None}
        if not info:
            # niets herkenbaars: dan toch de hele tekst als omschrijving
            info["description"] = _clean(" ".join(lines))
        return info

    # vrije tekst: alles als omschrijving, plus herkenbare velden
    text = _clean(" ".join(lines)) or ""
    info["description"] = text or None
    m = re.search(r"(?i)\bIBAN:\s*([A-Z]{2}\d{2}[A-Z0-9]{10,30})", text)
    if m:
        info["counterparty_iban"] = m.group(1).upper()
    m = re.search(r"(?i)\bBIC:\s*([A-Z0-9]{8,11})\b", text)
    if m:
        info["counterparty_bic"] = m.group(1).upper()
    m = re.search(
        r"(?i)\bNAAM:\s*(.+?)(?=\s+(?:OMSCHRIJVING|KENMERK|MACHTIGING|IBAN|BIC|ID BEGUNSTIGDE|BETALINGSKENM\.?):|$)",
        text,
    )
    if m:
        info["counterparty_name"] = _clean(m.group(1))
    m = re.search(r"(?i)\bbetalingskenm(?:erk)?\.?\s*:?\s*(\d[\d ]{6,}\d)", text)
    if m:
        info["payment_reference"] = m.group(1).replace(" ", "")
    m = re.search(r"(?i)\bKENMERK:\s*(\S+)", text)
    if m and m.group(1).upper() != "NOTPROVIDED":
        info["end_to_end_id"] = m.group(1)
    if "counterparty_name" not in info:
        # pinbetaling: de winkel is de tegenpartij; kosten/rente van de bank zelf: de bank
        shop = card_merchant(text)
        if shop:
            info["counterparty_name"] = shop
        elif re.match(r"(?i)ABN AMRO BANK N\.?V\.?\b", text):
            info["counterparty_name"] = "ABN AMRO BANK N.V."
    return info
