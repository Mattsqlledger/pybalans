"""sqlledger -- bibliotheek van PyBalans: Python-port van de grootboekkern van SQL-Ledger 3.2.12.

Werkt op het bestaande SQL-Ledger PostgreSQL-schema.

    from sqlledger import connect, UserConfig, GeneralLedger, GLTransaction, JournalLine

    conn = connect("dbname=mijnbedrijf")
    gl = GeneralLedger(conn, UserConfig(login="admin", numberformat="1.000,00"))
    trans_id = gl.post_transaction(GLTransaction(
        transdate=date(2026, 3, 1), description="Kasopname",
        lines=[JournalLine("1000", debit=Decimal("50")),
               JournalLine("1100", credit=Decimal("50"))]))

Dit is afgeleid werk van SQL-Ledger (c) DWS Systems Inc. en valt onder de GPL.
"""

from ._db import connect
from .admin import Account, Admin
from .amounts import format_amount, parse_amount, round_amount
from .arap import ArapLedger, ArapLine, ArapPayment, ArapTransaction
from .arap_list import ArapList, ArapSearch
from .ca import ChartOfAccounts
from .config import UserConfig
from .contacts import Contacts, ContactSearch, Relation
from .errors import (ClosedPeriodError, LedgerError, LockedTransactionError,
                     NotFoundError, OutOfBalanceError, ValidationError)
from .gl import GeneralLedger
from .masterdata import MasterData, Project
from .parts import Part, Parts, PartSearch
from .payments import OpenInvoice, Payment, PaymentLine, PaymentResult, Payments
from .recurring import ProcessItem, ProcessResult, RecurringRow, RecurringTransactions
from .reconcile import ReconcileResult, Reconciliation, Statement, StatementLine
from .reports import (DisplayRow, FinancialStatement, Reports, StatementPeriod,
                      TrialBalance)
from .reports_arap import (AgingReport, PaymentReport, ReminderReport, TaxReport,
                           YearendPreview)
from .models import (AccountTransactions, GLTransaction, GLTransactionView,
                     JournalLine, Recurring, Reference, TransactionList,
                     TransactionSearch)

__version__ = "1.4.0"

__all__ = [
    "connect", "UserConfig", "GeneralLedger", "ChartOfAccounts", "Reports",
    "ArapLedger", "ArapTransaction", "ArapLine", "ArapPayment", "ArapSearch", "ArapList",
    "Payments", "Payment", "PaymentLine", "PaymentResult", "OpenInvoice",
    "RecurringTransactions", "RecurringRow", "ProcessItem", "ProcessResult",
    "Reconciliation", "Statement", "StatementLine", "ReconcileResult",
    "Contacts", "ContactSearch", "Relation",
    "TrialBalance", "FinancialStatement", "StatementPeriod", "DisplayRow",
    "AgingReport", "ReminderReport", "TaxReport", "PaymentReport", "YearendPreview",
    "GLTransaction", "JournalLine", "Recurring", "Reference", "TransactionSearch",
    "GLTransactionView", "TransactionList", "AccountTransactions",
    "parse_amount", "format_amount", "round_amount",
    "LedgerError", "ValidationError", "OutOfBalanceError", "ClosedPeriodError",
    "LockedTransactionError", "NotFoundError",
    "Admin", "Account", "MasterData", "Project",
    "Parts", "Part", "PartSearch",
]
