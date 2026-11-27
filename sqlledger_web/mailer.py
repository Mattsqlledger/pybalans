"""Documenten mailen (fase 4, D9): SMTP per administratie, standaardteksten, mapmodus voor testen.

Zoals SQL-Ledger (io.pl ``e_mail``/``print_form``, ``Mailer.pm``): aan/cc/bcc uit de relatie, onderwerp,
bericht met de handtekening van de gebruiker, het document als PDF-bijlage. Anders dan Perl:

* Verzenden via SMTP met STARTTLS of SSL (Perl: het programma ``sendmail`` op de server), met
  afzender per administratie in plaats van naam/e-mail van de gebruiker; de gebruiker kan als
  antwoordadres (Reply-To) dienen.
* Adressen worden gecontroleerd (geen regeleinden of losse tekst in kopregels); Perl haalde alleen
  ``< > \\`` weg.
* Modus "map": de mail komt als ``.eml`` in ``<data_dir>/mail/<administratie-id>/`` en wordt niet
  verstuurd (ontwikkelen en testen).

Het SMTP-wachtwoord staat versleuteld in de app-database (Fernet); de sleutel komt uit de instelling
``secret_key`` of uit ``<data_dir>/secret.key``.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import psycopg

from sqlledger import __version__

from .i18n import N_, gettext as _

MODES = ("off", "smtp", "folder")
SECURITIES = ("starttls", "ssl", "none")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
MAX_RECIPIENTS = 20
TIMEOUT = 30

ADDR_RE = re.compile(r"^[^@\s<>()\[\]\\,;:\"]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$|^\[?[0-9A-Fa-f:]+\]?$")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class MailError(Exception):
    """Fout bij instellen of verzenden: Nederlandse tekst (met ``N_`` gemarkeerd) + waarden voor de
    plaatshouders; ``text()`` vertaalt naar de taal van de gebruiker."""

    def __init__(self, template: str, **values: Any):
        super().__init__(template.format(**values) if values else template)
        self.template, self.values = template, values

    def text(self) -> str:
        return _(self.template).format(**self.values) if self.values else _(self.template)


# ------------------------------------------------------------------ standaardteksten (D9)

#: onderwerp en bericht per documentsoort en documenttaal; plaatshouders zie ``placeholders``
DEFAULT_TEXTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "nl": {
        "invoice": {"subject": "Factuur {nummer} van {bedrijf}",
                    "body": "Beste {relatie},\n\nIn de bijlage vindt u factuur {nummer} van {datum} over "
                            "{valuta} {bedrag}. Wij verzoeken u dit bedrag vóór {vervaldatum} te betalen.\n\n"
                            "Met vriendelijke groet,\n{bedrijf}"},
        "credit_invoice": {"subject": "Creditfactuur {nummer} van {bedrijf}",
                           "body": "Beste {relatie},\n\nIn de bijlage vindt u creditfactuur {nummer} van {datum} "
                                   "over {valuta} {bedrag}.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "vendor_invoice": {"subject": "Uw factuur {nummer}",
                           "body": "Beste {relatie},\n\nIn de bijlage vindt u uw factuur {nummer} van {datum} over "
                                   "{valuta} {bedrag} zoals wij die hebben geboekt.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "debit_invoice": {"subject": "Debetnota {nummer} van {bedrijf}",
                          "body": "Beste {relatie},\n\nIn de bijlage vindt u debetnota {nummer} van {datum} over "
                                  "{valuta} {bedrag}.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "packing_list": {"subject": "Pakbon {nummer} van {bedrijf}",
                         "body": "Beste {relatie},\n\nIn de bijlage vindt u de pakbon bij {nummer}.\n\n"
                                 "Met vriendelijke groet,\n{bedrijf}"},
        "pick_list": {"subject": "Picklijst {nummer}",
                      "body": "In de bijlage de picklijst bij {nummer}.\n\n{bedrijf}"},
        "sales_order": {"subject": "Orderbevestiging {nummer} van {bedrijf}",
                        "body": "Beste {relatie},\n\nHartelijk dank voor uw bestelling. In de bijlage vindt u "
                                "orderbevestiging {nummer} van {datum}.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "purchase_order": {"subject": "Bestelling {nummer} van {bedrijf}",
                           "body": "Beste {relatie},\n\nIn de bijlage vindt u onze bestelling {nummer} van {datum}. "
                                   "Graag leveren vóór {vervaldatum}.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "sales_quotation": {"subject": "Offerte {nummer} van {bedrijf}",
                            "body": "Beste {relatie},\n\nIn de bijlage vindt u offerte {nummer} van {datum}, "
                                    "geldig tot {vervaldatum}.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "request_quotation": {"subject": "Offerteaanvraag {nummer} van {bedrijf}",
                              "body": "Beste {relatie},\n\nGraag ontvangen wij uw offerte voor de artikelen in "
                                      "de bijlage (aanvraag {nummer}).\n\nMet vriendelijke groet,\n{bedrijf}"},
        "ar_transaction": {"subject": "Factuur {nummer} van {bedrijf}",
                           "body": "Beste {relatie},\n\nIn de bijlage vindt u factuur {nummer} van {datum} over "
                                   "{valuta} {bedrag}. Wij verzoeken u dit bedrag vóór {vervaldatum} te betalen.\n\n"
                                   "Met vriendelijke groet,\n{bedrijf}"},
        "credit_note": {"subject": "Creditnota {nummer} van {bedrijf}",
                        "body": "Beste {relatie},\n\nIn de bijlage vindt u creditnota {nummer} van {datum} over "
                                "{valuta} {bedrag}.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "ap_transaction": {"subject": "Uw factuur {nummer}",
                           "body": "Beste {relatie},\n\nIn de bijlage vindt u uw factuur {nummer} van {datum} over "
                                   "{valuta} {bedrag} zoals wij die hebben geboekt.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "debit_note": {"subject": "Debetnota {nummer} van {bedrijf}",
                       "body": "Beste {relatie},\n\nIn de bijlage vindt u debetnota {nummer} van {datum} over "
                               "{valuta} {bedrag}.\n\nMet vriendelijke groet,\n{bedrijf}"},
        "reminder1": {"subject": 'Herinnering openstaande facturen {bedrijf}',
                     "body": 'Beste {relatie},\n\nVolgens onze administratie staan de facturen {nummer} nog open; in de bijlage vindt u het overzicht. Wij verzoeken u het openstaande bedrag van {valuta} {bedrag} binnen 14 dagen te voldoen. Heeft u inmiddels betaald, dan kunt u deze mail als niet verzonden beschouwen.\n\nMet vriendelijke groet,\n{bedrijf}'},
        "reminder2": {"subject": 'Tweede herinnering openstaande facturen {bedrijf}',
                     "body": 'Beste {relatie},\n\nOndanks onze herinnering hebben wij de betaling van de facturen {nummer} nog niet ontvangen. In de bijlage vindt u het overzicht. Wij verzoeken u het openstaande bedrag van {valuta} {bedrag} binnen 7 dagen te voldoen.\n\nMet vriendelijke groet,\n{bedrijf}'},
        "reminder3": {"subject": 'Aanmaning openstaande facturen {bedrijf}',
                     "body": 'Beste {relatie},\n\nOndanks eerdere herinneringen staan de facturen {nummer} nog steeds open. In de bijlage vindt u de aanmaning. Wij verzoeken u dringend het openstaande bedrag van {valuta} {bedrag} binnen 7 dagen te voldoen.\n\nMet vriendelijke groet,\n{bedrijf}'},
        "statement": {"subject": 'Rekeningoverzicht {bedrijf} per {datum}',
                     "body": 'Beste {relatie},\n\nIn de bijlage vindt u het overzicht van uw openstaande posten per {datum}, totaal {valuta} {bedrag}.\n\nMet vriendelijke groet,\n{bedrijf}'},
        "receipt": {"subject": "Betalingsspecificatie {bedrijf} {datum}",
                    "body": "Beste {relatie},\n\nWij hebben uw betaling van {datum} van {valuta} {bedrag} ontvangen. In de bijlage vindt u hoe wij deze hebben verwerkt (facturen {nummer}).\n\nMet vriendelijke groet,\n{bedrijf}"},
        "check": {"subject": "Betalingsspecificatie {bedrijf} {datum}",
                    "body": "Beste {relatie},\n\nWij hebben op {datum} {valuta} {bedrag} aan u overgemaakt (kenmerk {kenmerk}). In de bijlage vindt u de specificatie per factuur ({nummer}).\n\nMet vriendelijke groet,\n{bedrijf}"},
    },
    "en": {
        "invoice": {"subject": "Invoice {number} from {company}",
                    "body": "Dear {name},\n\nPlease find attached invoice {number} of {date} for {currency} "
                            "{amount}. Kindly pay this amount before {duedate}.\n\nKind regards,\n{company}"},
        "credit_invoice": {"subject": "Credit invoice {number} from {company}",
                           "body": "Dear {name},\n\nPlease find attached credit invoice {number} of {date} for "
                                   "{currency} {amount}.\n\nKind regards,\n{company}"},
        "vendor_invoice": {"subject": "Your invoice {number}",
                           "body": "Dear {name},\n\nPlease find attached your invoice {number} of {date} for "
                                   "{currency} {amount} as we have recorded it.\n\nKind regards,\n{company}"},
        "debit_invoice": {"subject": "Debit note {number} from {company}",
                          "body": "Dear {name},\n\nPlease find attached debit note {number} of {date} for "
                                  "{currency} {amount}.\n\nKind regards,\n{company}"},
        "packing_list": {"subject": "Packing list {number} from {company}",
                         "body": "Dear {name},\n\nPlease find attached the packing list for {number}.\n\n"
                                 "Kind regards,\n{company}"},
        "pick_list": {"subject": "Pick list {number}",
                      "body": "Attached is the pick list for {number}.\n\n{company}"},
        "sales_order": {"subject": "Order confirmation {number} from {company}",
                        "body": "Dear {name},\n\nThank you for your order. Please find attached order "
                                "confirmation {number} of {date}.\n\nKind regards,\n{company}"},
        "purchase_order": {"subject": "Purchase order {number} from {company}",
                           "body": "Dear {name},\n\nPlease find attached our purchase order {number} of {date}. "
                                   "Kindly deliver before {duedate}.\n\nKind regards,\n{company}"},
        "sales_quotation": {"subject": "Quotation {number} from {company}",
                            "body": "Dear {name},\n\nPlease find attached quotation {number} of {date}, valid "
                                    "until {duedate}.\n\nKind regards,\n{company}"},
        "request_quotation": {"subject": "Request for quotation {number} from {company}",
                              "body": "Dear {name},\n\nWe would appreciate your quotation for the items in the "
                                      "attachment (request {number}).\n\nKind regards,\n{company}"},
        "ar_transaction": {"subject": "Invoice {number} from {company}",
                           "body": "Dear {name},\n\nPlease find attached invoice {number} of {date} for {currency} "
                                   "{amount}. Kindly pay this amount before {duedate}.\n\nKind regards,\n{company}"},
        "credit_note": {"subject": "Credit note {number} from {company}",
                        "body": "Dear {name},\n\nPlease find attached credit note {number} of {date} for "
                                "{currency} {amount}.\n\nKind regards,\n{company}"},
        "ap_transaction": {"subject": "Your invoice {number}",
                           "body": "Dear {name},\n\nPlease find attached your invoice {number} of {date} for "
                                   "{currency} {amount} as we have recorded it.\n\nKind regards,\n{company}"},
        "debit_note": {"subject": "Debit note {number} from {company}",
                       "body": "Dear {name},\n\nPlease find attached debit note {number} of {date} for "
                               "{currency} {amount}.\n\nKind regards,\n{company}"},
        "reminder1": {"subject": 'Payment reminder from {company}',
                     "body": 'Dear {name},\n\nAccording to our records, invoices {number} are still outstanding; please find the overview attached. Kindly pay the outstanding amount of {currency} {amount} within 14 days. If you have already paid, please disregard this message.\n\nKind regards,\n{company}'},
        "reminder2": {"subject": 'Second payment reminder from {company}',
                     "body": 'Dear {name},\n\nDespite our reminder, we have not yet received payment of invoices {number}. Please find the overview attached. Kindly pay the outstanding amount of {currency} {amount} within 7 days.\n\nKind regards,\n{company}'},
        "reminder3": {"subject": 'Final reminder from {company}',
                     "body": 'Dear {name},\n\nDespite previous reminders, invoices {number} are still outstanding. Please find the final reminder attached. We urgently request you to pay the outstanding amount of {currency} {amount} within 7 days.\n\nKind regards,\n{company}'},
        "statement": {"subject": 'Statement of account from {company} as of {date}',
                     "body": 'Dear {name},\n\nPlease find attached the statement of your outstanding items as of {date}, total {currency} {amount}.\n\nKind regards,\n{company}'},
        "receipt": {"subject": "Payment received by {company} {date}",
                    "body": "Dear {name},\n\nWe have received your payment of {currency} {amount} on {date}. Please find attached how we have allocated it (invoices {number}).\n\nKind regards,\n{company}"},
        "check": {"subject": "Remittance advice from {company} {date}",
                    "body": "Dear {name},\n\nOn {date} we transferred {currency} {amount} to you (reference {reference}). Please find attached the specification per invoice ({number}).\n\nKind regards,\n{company}"},
    },
    "de": {
        "invoice": {"subject": "Rechnung {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie die Rechnung {number} vom {date} über {currency} {amount}. Bitte überweisen Sie den Betrag bis zum {duedate}.\n\nMit freundlichen Grüßen\n{company}"},
        "credit_invoice": {"subject": "Rechnungskorrektur {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie die Rechnungskorrektur {number} vom {date} über {currency} {amount}.\n\nMit freundlichen Grüßen\n{company}"},
        "vendor_invoice": {"subject": "Ihre Rechnung {number}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie Ihre Rechnung {number} vom {date} über {currency} {amount}, wie wir sie gebucht haben.\n\nMit freundlichen Grüßen\n{company}"},
        "debit_invoice": {"subject": "Belastungsanzeige {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie die Belastungsanzeige {number} vom {date} über {currency} {amount}.\n\nMit freundlichen Grüßen\n{company}"},
        "packing_list": {"subject": "Lieferschein {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie den Lieferschein zu {number}.\n\nMit freundlichen Grüßen\n{company}"},
        "pick_list": {"subject": "Kommissionierliste {number}",
            "body": "Anbei die Kommissionierliste zu {number}.\n\n{company}"},
        "sales_order": {"subject": "Auftragsbestätigung {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nvielen Dank für Ihre Bestellung. Anbei erhalten Sie die Auftragsbestätigung {number} vom {date}.\n\nMit freundlichen Grüßen\n{company}"},
        "purchase_order": {"subject": "Bestellung {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie unsere Bestellung {number} vom {date}. Bitte liefern Sie bis zum {duedate}.\n\nMit freundlichen Grüßen\n{company}"},
        "sales_quotation": {"subject": "Angebot {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie unser Angebot {number} vom {date}, gültig bis {duedate}.\n\nMit freundlichen Grüßen\n{company}"},
        "request_quotation": {"subject": "Angebotsanfrage {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nbitte senden Sie uns ein Angebot für die Artikel im Anhang (Anfrage {number}).\n\nMit freundlichen Grüßen\n{company}"},
        "ar_transaction": {"subject": "Rechnung {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie die Rechnung {number} vom {date} über {currency} {amount}. Bitte überweisen Sie den Betrag bis zum {duedate}.\n\nMit freundlichen Grüßen\n{company}"},
        "credit_note": {"subject": "Rechnungskorrektur {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie die Rechnungskorrektur {number} vom {date} über {currency} {amount}.\n\nMit freundlichen Grüßen\n{company}"},
        "ap_transaction": {"subject": "Ihre Rechnung {number}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie Ihre Rechnung {number} vom {date} über {currency} {amount}, wie wir sie gebucht haben.\n\nMit freundlichen Grüßen\n{company}"},
        "debit_note": {"subject": "Belastungsanzeige {number} von {company}",
            "body": "Sehr geehrte Damen und Herren,\n\nanbei erhalten Sie die Belastungsanzeige {number} vom {date} über {currency} {amount}.\n\nMit freundlichen Grüßen\n{company}"},
        "reminder1": {"subject": 'Zahlungserinnerung von {company}',
                     "body": 'Sehr geehrte Damen und Herren,\n\nlaut unserer Buchhaltung sind die Rechnungen {number} noch offen; die Übersicht finden Sie im Anhang. Bitte begleichen Sie den offenen Betrag von {currency} {amount} innerhalb von 14 Tagen. Sollten Sie inzwischen bezahlt haben, betrachten Sie diese Nachricht bitte als gegenstandslos.\n\nMit freundlichen Grüßen\n{company}'},
        "reminder2": {"subject": 'Zweite Zahlungserinnerung von {company}',
                     "body": 'Sehr geehrte Damen und Herren,\n\ntrotz unserer Zahlungserinnerung haben wir die Zahlung der Rechnungen {number} noch nicht erhalten. Die Übersicht finden Sie im Anhang. Bitte begleichen Sie den offenen Betrag von {currency} {amount} innerhalb von 7 Tagen.\n\nMit freundlichen Grüßen\n{company}'},
        "reminder3": {"subject": 'Mahnung von {company}',
                     "body": 'Sehr geehrte Damen und Herren,\n\ntrotz früherer Erinnerungen sind die Rechnungen {number} noch immer offen. Die Mahnung finden Sie im Anhang. Wir fordern Sie dringend auf, den offenen Betrag von {currency} {amount} innerhalb von 7 Tagen zu begleichen.\n\nMit freundlichen Grüßen\n{company}'},
        "statement": {"subject": 'Kontoauszug von {company} zum {date}',
                     "body": 'Sehr geehrte Damen und Herren,\n\nim Anhang finden Sie die Übersicht Ihrer offenen Posten zum {date}, insgesamt {currency} {amount}.\n\nMit freundlichen Grüßen\n{company}'},
        "receipt": {"subject": "Zahlungseingang bei {company} {date}",
                    "body": "Sehr geehrte Damen und Herren,\n\nwir haben Ihre Zahlung über {currency} {amount} vom {date} erhalten. Im Anhang finden Sie, wie wir sie verbucht haben (Rechnungen {number}).\n\nMit freundlichen Grüßen\n{company}"},
        "check": {"subject": "Zahlungsavis von {company} {date}",
                    "body": "Sehr geehrte Damen und Herren,\n\nam {date} haben wir Ihnen {currency} {amount} überwiesen (Referenz {reference}). Im Anhang finden Sie die Aufstellung je Rechnung ({number}).\n\nMit freundlichen Grüßen\n{company}"},
    },
    "fr": {
        "invoice": {"subject": "Facture {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint la facture {number} du {date} d'un montant de {currency} {amount}. Merci de régler ce montant avant le {duedate}.\n\nCordialement,\n{company}"},
        "credit_invoice": {"subject": "Avoir {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint l'avoir {number} du {date} d'un montant de {currency} {amount}.\n\nCordialement,\n{company}"},
        "vendor_invoice": {"subject": "Votre facture {number}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint votre facture {number} du {date} d'un montant de {currency} {amount}, telle que nous l'avons enregistrée.\n\nCordialement,\n{company}"},
        "debit_invoice": {"subject": "Note de débit {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint la note de débit {number} du {date} d'un montant de {currency} {amount}.\n\nCordialement,\n{company}"},
        "packing_list": {"subject": "Bon de livraison {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint le bon de livraison pour {number}.\n\nCordialement,\n{company}"},
        "pick_list": {"subject": "Liste de préparation {number}",
            "body": "Ci-joint la liste de préparation pour {number}.\n\n{company}"},
        "sales_order": {"subject": "Confirmation de commande {number} de {company}",
            "body": "Madame, Monsieur,\n\nNous vous remercions de votre commande. Veuillez trouver ci-joint la confirmation de commande {number} du {date}.\n\nCordialement,\n{company}"},
        "purchase_order": {"subject": "Bon de commande {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint notre bon de commande {number} du {date}. Merci de livrer avant le {duedate}.\n\nCordialement,\n{company}"},
        "sales_quotation": {"subject": "Devis {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint notre devis {number} du {date}, valable jusqu'au {duedate}.\n\nCordialement,\n{company}"},
        "request_quotation": {"subject": "Demande de prix {number} de {company}",
            "body": "Madame, Monsieur,\n\nMerci de nous faire parvenir votre offre pour les articles en pièce jointe (demande {number}).\n\nCordialement,\n{company}"},
        "ar_transaction": {"subject": "Facture {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint la facture {number} du {date} d'un montant de {currency} {amount}. Merci de régler ce montant avant le {duedate}.\n\nCordialement,\n{company}"},
        "credit_note": {"subject": "Avoir {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint l'avoir {number} du {date} d'un montant de {currency} {amount}.\n\nCordialement,\n{company}"},
        "ap_transaction": {"subject": "Votre facture {number}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint votre facture {number} du {date} d'un montant de {currency} {amount}, telle que nous l'avons enregistrée.\n\nCordialement,\n{company}"},
        "debit_note": {"subject": "Note de débit {number} de {company}",
            "body": "Madame, Monsieur,\n\nVeuillez trouver ci-joint la note de débit {number} du {date} d'un montant de {currency} {amount}.\n\nCordialement,\n{company}"},
        "reminder1": {"subject": 'Rappel de paiement de {company}',
                     "body": "Madame, Monsieur,\n\nD'après notre comptabilité, les factures {number} restent impayées ; vous trouverez le détail en pièce jointe. Nous vous prions de régler le montant de {currency} {amount} sous 14 jours. Si vous avez déjà effectué le paiement, merci de ne pas tenir compte de ce message.\n\nCordialement,\n{company}"},
        "reminder2": {"subject": 'Deuxième rappel de paiement de {company}',
                     "body": "Madame, Monsieur,\n\nMalgré notre rappel, nous n'avons pas encore reçu le règlement des factures {number}. Vous trouverez le détail en pièce jointe. Nous vous prions de régler le montant de {currency} {amount} sous 7 jours.\n\nCordialement,\n{company}"},
        "reminder3": {"subject": 'Mise en demeure de {company}',
                     "body": 'Madame, Monsieur,\n\nMalgré nos rappels précédents, les factures {number} restent impayées. Vous trouverez la mise en demeure en pièce jointe. Nous vous demandons instamment de régler le montant de {currency} {amount} sous 7 jours.\n\nCordialement,\n{company}'},
        "statement": {"subject": 'Relevé de compte de {company} au {date}',
                     "body": 'Madame, Monsieur,\n\nVous trouverez en pièce jointe le relevé de vos postes ouverts au {date}, pour un total de {currency} {amount}.\n\nCordialement,\n{company}'},
        "receipt": {"subject": "Paiement reçu par {company} {date}",
                    "body": "Madame, Monsieur,\n\nNous avons bien reçu votre paiement de {currency} {amount} du {date}. Vous trouverez en pièce jointe son imputation (factures {number}).\n\nCordialement,\n{company}"},
        "check": {"subject": "Avis de paiement de {company} {date}",
                    "body": "Madame, Monsieur,\n\nLe {date}, nous vous avons viré {currency} {amount} (référence {reference}). Vous trouverez en pièce jointe le détail par facture ({number}).\n\nCordialement,\n{company}"},
    },
}

#: plaatshouder -> sjabloonvariabele (Nederlandse en Engelse naam)
PLACEHOLDERS = {
    "nummer": "invnumber", "number": "invnumber",
    "datum": "invdate", "date": "invdate",
    "vervaldatum": "duedate", "duedate": "duedate",
    "bedrag": "invtotal", "amount": "invtotal",
    "valuta": "currency", "currency": "currency",
    "relatie": "name", "name": "name",
    "bedrijf": "company", "company": "company",
    "kenmerk": "dcn", "reference": "dcn",
    "ordernummer": "ordnumber", "ordnumber": "ordnumber",
    "referentie": "ponumber", "ponumber": "ponumber",
}


def fill(text: str, v: Mapping[str, Any]) -> str:
    """Bekende plaatshouders vervangen; onbekende blijven staan (geen ``str.format``: veilig)."""
    def repl(m):
        key = PLACEHOLDERS.get(m.group(1))
        return str(v.get(key) or "") if key else m.group(0)
    return PLACEHOLDER_RE.sub(repl, text or "")


# ------------------------------------------------------------------ geheimen

def _fernet(settings):
    from cryptography.fernet import Fernet

    key = (settings.secret_key or "").strip()
    if not key:
        path = Path(settings.data_dir).expanduser() / "secret.key"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(Fernet.generate_key())
        key = path.read_text().strip()
    try:
        return Fernet(key.encode())
    except ValueError:
        raise MailError(N_("De instelling secret_key is geen geldige sleutel."))


def encrypt(settings, plain: str) -> str:
    return _fernet(settings).encrypt(plain.encode()).decode() if plain else ""


def decrypt(settings, token: str) -> str:
    from cryptography.fernet import InvalidToken

    if not token:
        return ""
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken:
        raise MailError(N_("Het opgeslagen SMTP-wachtwoord kan niet gelezen worden (andere sleutel?). "
                        "Vul het opnieuw in bij Beheer > E-mail."))


# ------------------------------------------------------------------ instellingen per administratie

@dataclass
class MailSettings:
    company_id: int
    mode: str = "off"
    host: str = ""
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password_enc: str = ""
    from_name: str = ""
    from_address: str = ""
    reply_to: str = ""
    texts: Dict[str, Dict[str, Dict[str, str]]] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.mode in ("smtp", "folder") and bool(self.from_address)

    @property
    def has_password(self) -> bool:
        return bool(self.password_enc)

    def text(self, lang: str, formname: str, key: str) -> str:
        """Eigen tekst van de administratie, anders de standaardtekst."""
        own = ((self.texts.get(lang) or {}).get(formname) or {}).get(key)
        if own:
            return own
        return DEFAULT_TEXTS.get(lang, DEFAULT_TEXTS["nl"]).get(formname, {}).get(key, "")


COLUMNS = ("mode", "host", "port", "security", "username", "password_enc", "from_name", "from_address",
           "reply_to", "texts")


def load(conn: psycopg.Connection, company_id: int) -> MailSettings:
    row = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM company_mail WHERE company_id = %s",
                       (company_id,)).fetchone()
    if not row:
        return MailSettings(company_id)
    row = dict(row) if not isinstance(row, dict) else row
    return MailSettings(company_id, **{k: row[k] for k in COLUMNS})


def save(conn: psycopg.Connection, ms: MailSettings) -> None:
    from psycopg.types.json import Jsonb

    vals = {k: getattr(ms, k) for k in COLUMNS}
    vals["texts"] = Jsonb(ms.texts)
    conn.execute(
        f"INSERT INTO company_mail (company_id, {', '.join(COLUMNS)}) "
        f"VALUES (%(company_id)s, {', '.join('%(' + k + ')s' for k in COLUMNS)}) "
        f"ON CONFLICT (company_id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in COLUMNS)}, "
        f"updated = now()", {"company_id": ms.company_id, **vals})


def validate(ms: MailSettings) -> None:
    if ms.mode not in MODES:
        raise MailError(N_("Ongeldige keuze."))
    if ms.security not in SECURITIES:
        raise MailError(N_("Ongeldige keuze."))
    if ms.mode == "off":
        return
    if not ms.from_address:
        raise MailError(N_("Vul het afzenderadres in."))
    check_address(ms.from_address)
    if ms.reply_to:
        check_address(ms.reply_to)
    no_newline(ms.from_name, ms.username)
    if ms.mode == "smtp":
        if not ms.host or not HOST_RE.match(ms.host):
            raise MailError(N_("Vul een geldige SMTP-server in."))
        if not 1 <= int(ms.port) <= 65535:
            raise MailError(N_("Ongeldige poort."))
        if ms.security == "none" and ms.username and ms.host.lower() not in LOCAL_HOSTS:
            raise MailError(N_("Inloggen zonder versleuteling kan alleen bij een server op deze computer; "
                            "kies STARTTLS of SSL."))


# ------------------------------------------------------------------ adressen en bericht

def no_newline(*values: str) -> None:
    for v in values:
        if v and re.search(r"[\r\n\x00]", v):
            raise MailError(N_("Regeleinden zijn niet toegestaan in adressen en het onderwerp."))


def check_address(addr: str) -> str:
    addr = (addr or "").strip()
    if not ADDR_RE.match(addr):
        raise MailError(N_("Ongeldig e-mailadres: {adres}"), adres=addr[:100])
    return addr


def parse_addresses(text: str) -> List[Tuple[str, str]]:
    """Lijst "Naam <a@b.nl>, c@d.nl" (komma of puntkomma) -> [(naam, adres)]; fout bij ongeldig."""
    text = (text or "").strip()
    if not text:
        return []
    no_newline(text)
    out = []
    for piece in text.replace(";", ",").split(","):
        piece = piece.strip()
        if not piece:
            continue
        name, addr = parseaddr(piece)
        # alleen "adres" of "Naam <adres>": geen losse tekst om het adres heen
        if not addr or not (piece == addr or piece.endswith(f"<{addr}>")):
            raise MailError(N_("Ongeldig e-mailadres: {adres}"), adres=piece[:100])
        out.append((name.strip(), check_address(addr)))
    return out


@dataclass
class Outgoing:
    to: List[Tuple[str, str]]
    cc: List[Tuple[str, str]]
    bcc: List[Tuple[str, str]]
    subject: str
    body: str
    attachments: List[Tuple[str, bytes, str]] = field(default_factory=list)   # (bestandsnaam, inhoud, mime)

    @property
    def recipients(self) -> List[str]:
        return [a for __, a in self.to + self.cc + self.bcc]


def compose(to: str, cc: str, bcc: str, subject: str, body: str) -> Outgoing:
    out = Outgoing(parse_addresses(to), parse_addresses(cc), parse_addresses(bcc), (subject or "").strip(),
                   (body or "").replace("\r\n", "\n"))
    if not out.to:
        raise MailError(N_("Vul een e-mailadres in bij Aan."))
    if len(out.recipients) > MAX_RECIPIENTS:
        raise MailError(N_("Te veel ontvangers."))
    no_newline(out.subject)
    if not out.subject:
        raise MailError(N_("Vul een onderwerp in."))
    return out


def build_message(ms: MailSettings, out: Outgoing, reply_to: str = "") -> EmailMessage:
    if not ms.enabled:
        raise MailError(N_("E-mail is niet ingesteld voor deze administratie (Beheer > E-mail)."))
    msg = EmailMessage()
    msg["From"] = formataddr((ms.from_name, ms.from_address))
    msg["To"] = ", ".join(formataddr(a) for a in out.to)
    if out.cc:
        msg["Cc"] = ", ".join(formataddr(a) for a in out.cc)
    if out.bcc:
        msg["Bcc"] = ", ".join(formataddr(a) for a in out.bcc)
    reply = ms.reply_to or reply_to
    if reply:
        msg["Reply-To"] = check_address(reply)
    msg["Subject"] = out.subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=ms.from_address.rsplit("@", 1)[1])
    msg["X-Mailer"] = f"PyBalans {__version__}"
    msg.set_content(out.body)
    for name, data, mime in out.attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


# ------------------------------------------------------------------ verzenden

def send(settings, ms: MailSettings, msg: EmailMessage, recipients: List[str]) -> str:
    """Verstuur (of zet in de map); geeft een korte omschrijving terug voor het logboek."""
    if not ms.enabled:
        raise MailError(N_("E-mail is niet ingesteld voor deze administratie (Beheer > E-mail)."))
    if ms.mode == "folder":
        folder = Path(settings.data_dir).expanduser() / "mail" / str(int(ms.company_id))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.eml"
        path.write_bytes(bytes(msg))
        return f"folder:{path.name}"
    password = decrypt(settings, ms.password_enc)
    context = ssl.create_default_context()
    try:
        if ms.security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(ms.host, ms.port, timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(ms.host, ms.port, timeout=TIMEOUT)
        with server:
            server.ehlo()
            if ms.security == "starttls":
                server.starttls(context=context)
                server.ehlo()
            if ms.username:
                server.login(ms.username, password)
            refused = server.send_message(msg, from_addr=ms.from_address, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError:
        raise MailError(N_("De SMTP-server weigert de gebruikersnaam of het wachtwoord."))
    except smtplib.SMTPRecipientsRefused as exc:
        raise MailError(N_("De SMTP-server weigert de ontvanger(s): {adressen}"), adressen=", ".join(exc.recipients))
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        raise MailError(N_("Verzenden mislukt: {fout}"), fout=(str(exc) or exc.__class__.__name__)[:300])
    if refused:
        raise MailError(N_("De SMTP-server weigert de ontvanger(s): {adressen}"), adressen=", ".join(refused))
    return f"smtp:{ms.host}"
