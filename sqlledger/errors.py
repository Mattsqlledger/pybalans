"""Exceptions van de grootboekbibliotheek.

In SQL-Ledger stoppen fouten via ``$form->error`` / ``$form->dberror`` het
CGI-script. Hier worden het gewone exceptions die de aanroeper kan afvangen.
"""


class LedgerError(Exception):
    """Basisklasse voor alle fouten uit sqlledger."""


class ValidationError(LedgerError):
    """Ongeldige invoer (ontbrekende datum, onbekende rekening, ...)."""


class OutOfBalanceError(ValidationError):
    """Debet en credit zijn niet gelijk (gl.pl: 'Out of balance transaction!')."""


class ClosedPeriodError(ValidationError):
    """Boekdatum valt in een afgesloten periode (defaults.closedto)."""


class LockedTransactionError(ValidationError):
    """Boeking mag niet meer gewijzigd/verwijderd worden (revtrans of afgesloten periode)."""


class NotFoundError(LedgerError):
    """Gevraagde boeking bestaat niet."""
