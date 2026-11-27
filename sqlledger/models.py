"""Datastructuren voor invoer en uitvoer.

In Perl gaat alles via één ``$form``-hash met velden als ``debit_3`` en
``"1100--Bank"``. Hier zijn dat getypeerde dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class JournalLine:
    """Eén journaalregel. Vul óf debit óf credit (niet beide)."""

    accno: str
    debit: Decimal = Decimal(0)
    credit: Decimal = Decimal(0)
    source: Optional[str] = None
    memo: Optional[str] = None
    project_id: Optional[int] = None
    cleared: Optional[date] = None
    fx_transaction: bool = False


@dataclass
class Reference:
    """Referentiedocument bij een boeking (tabel ``reference``)."""

    description: str
    code: Optional[str] = None
    archive_id: Optional[int] = None
    folder: Optional[str] = None
    confidential: bool = False
    #: bestandsnaam van het archiefstuk (alleen lezen) of van een nieuw bestand
    filename: Optional[str] = None
    #: nieuw bestand: wordt bij het boeken als archiefstuk opgeslagen (Form::save_reference, tmpfile)
    data: Optional[bytes] = field(default=None, repr=False)


@dataclass
class Recurring:
    """Herhaalschema (tabellen recurring, recurringemail, recurringprint).

    unit: 'days', 'weeks', 'months' of 'years'.
    nextdate: optioneel handmatig gekozen volgende datum.
    email: [(formname, format)], print: [(formname, format, printer)].
    """

    startdate: date
    repeat: int
    unit: str
    howmany: int
    reference: Optional[str] = None
    description: Optional[str] = None
    payment: bool = False
    nextdate: Optional[date] = None
    enddate: Optional[date] = None
    email: List[Tuple[str, str]] = field(default_factory=list)
    print: List[Tuple[str, str, Optional[str]]] = field(default_factory=list)
    message: Optional[str] = None


@dataclass
class GLTransaction:
    """Een memoriaalboeking (tabel ``gl`` met regels in ``acc_trans``).

    id None => nieuwe boeking; anders wordt de bestaande boeking vervangen.
    reference None => volgende ``glnumber`` uit defaults.
    currency None => standaardvaluta. exchangerate geldt voor vreemde valuta.
    """

    transdate: date
    lines: List[JournalLine]
    id: Optional[int] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    department_id: Optional[int] = None
    currency: Optional[str] = None
    exchangerate: Decimal = Decimal(1)
    pending: bool = False
    batch_id: Optional[int] = None
    vouchernumber: Optional[str] = None
    recurring: Optional[Recurring] = None
    references: List[Reference] = field(default_factory=list)


@dataclass
class TransactionSearch:
    """Zoekcriteria voor GeneralLedger.list_transactions (GL::transactions).

    Tekstvelden zoeken met LIKE zoals SQL-Ledger: zonder % of _ wordt er
    '%waarde%' van gemaakt; "tussen aanhalingstekens" zoekt exact.
    category: 'X' = alle, anders A/L/Q/I/E.
    """

    reference: Optional[str] = None
    description: Optional[str] = None
    name: Optional[str] = None
    vcnumber: Optional[str] = None
    department_id: Optional[int] = None
    lineitem: Optional[str] = None
    include_lineitem: bool = False
    source: Optional[str] = None
    memo: Optional[str] = None
    notes: Optional[str] = None
    accno: Optional[str] = None
    gifi_accno: Optional[str] = None
    accnofrom: Optional[str] = None
    accnoto: Optional[str] = None
    category: str = "X"
    datefrom: Optional[date] = None
    dateto: Optional[date] = None
    year: Optional[int] = None
    month: Optional[int] = None
    interval: Optional[int] = None
    amountfrom: Optional[Decimal] = None
    amountto: Optional[Decimal] = None
    splitledger: bool = False
    sort: Optional[str] = None
    direction: str = "ASC"
    l_subtotal: bool = False


@dataclass
class TransactionList:
    """Resultaat van list_transactions."""

    rows: List[Dict[str, Any]]
    datefrom: Optional[date] = None
    dateto: Optional[date] = None
    balance: Optional[Decimal] = None
    info: Dict[str, Any] = field(default_factory=dict)
    display: List[Any] = field(default_factory=list)


@dataclass
class GLTransactionView:
    """Resultaat van get_transaction."""

    header: Dict[str, Any]
    lines: List[Dict[str, Any]]
    fxadj: bool = False
    recurring: Optional[Recurring] = None
    references: List[Dict[str, Any]] = field(default_factory=list)
    defaults: Dict[str, Any] = field(default_factory=dict)
    readonly: bool = False
    haslock: Optional[str] = None

    @property
    def locked(self) -> bool:
        """Mag niet meer gepost/verwijderd worden (revtrans of afgesloten periode)."""
        return bool(self.defaults.get("locked"))


@dataclass
class AccountTransactions:
    """Resultaat van ChartOfAccounts.account_transactions (CA::all_transactions).

    display: regels zoals ca.pl list_transactions ze toont, met waarden
    [debet, credit, saldo]: beginsaldo ('balance', alleen als het niet 0 is),
    mutaties ('row', lopend saldo in weergaveteken), subtotalen en totaal.
    """

    rows: List[Dict[str, Any]]
    balance: Optional[Decimal] = None
    info: Dict[str, Any] = field(default_factory=dict)
    fromdate: Optional[date] = None
    todate: Optional[date] = None
    display: List[Any] = field(default_factory=list)
