"""Meldingen uit de bibliotheek (``sqlledger``) in de taal van de gebruiker.

De bibliotheek maakt haar meldingen met f-strings (deels Nederlands, deels de
Engelse Perl-teksten). Hier staat per melding het sjabloon zoals de bibliotheek
het opbouwt, met benoemde plaatshouders, en de Nederlandse en Engelse tekst.
``lib_message`` herkent een melding aan het sjabloon en zet haar om; de
ingevulde waarden blijven staan (vaste termen zoals "customer" of "afschrift 3"
worden ook vertaald).

``tests/web/test_libmsg.py`` controleert dat elke f-string-melding in
``sqlledger/`` (raise, waarschuwing, reden) hier een sjabloon heeft.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Dict, Tuple, Union

from .i18n import CATALOGS, LIBMSG, OVERRIDES, lang

#: sjabloon zoals in sqlledger -> (Nederlands, Engels)
TEMPLATES: Dict[str, Tuple[str, str]] = {
    # ---- sepa.py / routes_sepa.py: betaalbestand (bouwstap 5)
    'Betaalrekening {accno} heeft geen geldige IBAN.': ('Betaalrekening {accno} heeft geen geldige IBAN.', 'Payment account {accno} has no valid IBAN.'),
    'Betaalrekening {accno} heeft een IBAN van een land buiten SEPA.': ('Betaalrekening {accno} heeft een IBAN van een land buiten SEPA.', 'Payment account {accno} has an IBAN from a country outside SEPA.'),
    'Factuur {number} van {name} kan niet in het betaalbestand.': ('Factuur {number} van {name} kan niet in het betaalbestand.', 'Invoice {number} from {name} cannot be included in the payment file.'),
    'Factuur {number} van {name}: het bedrag moet groter zijn dan 0.': ('Factuur {number} van {name}: het bedrag moet groter zijn dan 0.', 'Invoice {number} from {name}: the amount must be greater than 0.'),
    'Factuur {number} van {name}: het bedrag is hoger dan wat openstaat ({due}).': ('Factuur {number} van {name}: het bedrag is hoger dan wat openstaat ({due}).', 'Invoice {number} from {name}: the amount is more than what is outstanding ({due}).'),
    'Factuur {number} van {name}: het bedrag is te hoog.': ('Factuur {number} van {name}: het bedrag is te hoog.', 'Invoice {number} from {name}: the amount is too high.'),
    'Het betaalbestand voldoet niet aan de XSD: {reason} ({path}).': ('Het betaalbestand voldoet niet aan de XSD: {reason} ({path}).', 'The payment file does not conform to the XSD: {reason} ({path}).'),
    # ---- importer.py importen facturen/orders/betalingen (bouwstap 4)
    'Klant {number} bestaat niet.': ('Klant {number} bestaat niet.', 'Customer {number} does not exist.'),
    'Leverancier {number} bestaat niet.': ('Leverancier {number} bestaat niet.', 'Vendor {number} does not exist.'),
    'Relatienummer {number} komt meer dan eens voor in de administratie.': ('Relatienummer {number} komt meer dan eens voor in de administratie.', 'Number {number} occurs more than once in the company.'),
    'Rij {row}: artikel {number} bestaat niet.': ('Rij {row}: artikel {number} bestaat niet.', 'Row {row}: part {number} does not exist.'),
    'Rij {row}: artikelnummer {number} komt meer dan eens voor.': ('Rij {row}: artikelnummer {number} komt meer dan eens voor.', 'Row {row}: part number {number} occurs more than once.'),
    'Rij {row}: artikelnummer ontbreekt.': ('Rij {row}: artikelnummer ontbreekt.', 'Row {row}: part number missing.'),
    'Koers ontbreekt voor {currency} op {date}.': ('Koers ontbreekt voor {currency} op {date}.', 'Exchange rate missing for {currency} on {date}.'),
    'Factuurnummer {number} bestaat al.': ('Factuurnummer {number} bestaat al.', 'Invoice number {number} already exists.'),
    'Ordernummer {number} bestaat al.': ('Ordernummer {number} bestaat al.', 'Order number {number} already exists.'),
    'De kolom {label} ontbreekt.': ('De kolom {label} ontbreekt.', 'The column {label} is missing.'),
    'Geen factuur gevonden voor {ref} bij {number}.': ('Geen factuur gevonden voor {ref} bij {number}.', 'No invoice found for {ref} at {number}.'),
    'Geen factuur gevonden voor {ref}.': ('Geen factuur gevonden voor {ref}.', 'No invoice found for {ref}.'),
    'Meer dan één factuur gevonden voor {ref}; vul het klant- of leveranciersnummer in.': ('Meer dan één factuur gevonden voor {ref}; vul het klant- of leveranciersnummer in.', 'More than one invoice found for {ref}; enter the customer or vendor number.'),
    'Factuur {number} staat niet open.': ('Factuur {number} staat niet open.', 'Invoice {number} is not open.'),
    'Het bedrag {amount} is meer dan wat openstaat ({due}).': ('Het bedrag {amount} is meer dan wat openstaat ({due}).', 'The amount {amount} is more than what is outstanding ({due}).'),
    # ---- admin.py GIFI (bouwstap 3)
    'GIFI-code {gifi} staat niet in de GIFI-indeling.': ('GIFI-code {gifi} staat niet in de GIFI-indeling.', 'GIFI code {gifi} is not in the GIFI codes.'),
    'GIFI-code {code} bestaat al.': ('GIFI-code {code} bestaat al.', 'GIFI code {code} already exists.'),
    'GIFI-code {code} is in gebruik bij rekening {used}.': ('GIFI-code {code} is in gebruik bij rekening {used}.', 'GIFI code {code} is in use by account {used}.'),
    # ---- slbackup.py / slupgrade.py (fase 6a)
    'Regel {line}: SET {name} is niet toegestaan.': (
        'Regel {line}: SET {name} is niet toegestaan.', 'Line {line}: SET {name} is not allowed.'),
    'Regel {line}: dit hoort niet in een back-up: {text}': (
        'Regel {line}: dit hoort niet in een back-up: {text}', 'Line {line}: this does not belong in a backup: {text}'),
    'Regel {line}: een functie mag alleen in plpgsql of sql.': (
        'Regel {line}: een functie mag alleen in plpgsql of sql.', 'Line {line}: a function may only be in plpgsql or sql.'),
    'Regel {line}: psql-opdracht {cmd} is niet toegestaan.': (
        'Regel {line}: psql-opdracht {cmd} is niet toegestaan.', 'Line {line}: psql command {cmd} is not allowed.'),
    'Regel {line}: commentaar zonder einde.': (
        'Regel {line}: commentaar zonder einde.', 'Line {line}: comment without an end.'),
    'Regel {line}: naam zonder afsluitend aanhalingsteken.': (
        'Regel {line}: naam zonder afsluitend aanhalingsteken.', 'Line {line}: name without a closing quote.'),
    'Regel {line}: tekst zonder afsluitend aanhalingsteken.': (
        'Regel {line}: tekst zonder afsluitend aanhalingsteken.', 'Line {line}: text without a closing quote.'),
    'Regel {line}: {tag}-blok zonder einde.': (
        'Regel {line}: {tag}-blok zonder einde.', 'Line {line}: {tag} block without an end.'),
    'Regel {line}: COPY zonder einde (\\.).': (
        'Regel {line}: COPY zonder einde (\\.).', 'Line {line}: COPY without an end (\\.).'),
    'Dit is een database van Run my Accounts (versie {version}); die kan deze app nog niet inlezen.': (
        'Dit is een database van Run my Accounts (versie {version}); die kan deze app nog niet inlezen.', 'This is a Run my Accounts database (version {version}); this app cannot import it yet.'),
    # ---- rmaimport.py (RMA_IMPORT_ANALYSE.md BK4)
    'Extra tabel met een naam die PyBalans zelf gebruikt: {tables}.': (
        'Extra tabel met een naam die PyBalans zelf gebruikt: {tables}.', 'Extra table with a name that PyBalans uses itself: {tables}.'),
    'Deze database heeft versie {version}, nieuwer dan {target}; die kan deze app niet openen.': (
        'Deze database heeft versie {version}, nieuwer dan {target}; die kan deze app niet openen.', 'This database has version {version}, newer than {target}; this app cannot open it.'),
    # ---- timecards.py (fase Uren U-3)
    'Toewijzing {assignment_id} niet gevonden.': (
        'Toewijzing {assignment_id} niet gevonden.', 'Assignment {assignment_id} not found.'),
    'De uren op {day}-{month}-{year} kunnen hier niet gewijzigd worden.': (
        'De uren op {day}-{month}-{year} kunnen hier niet gewijzigd worden.', 'The hours on {day}-{month}-{year} cannot be changed here.'),
    # ---- timecards.py (fase Uren U-1)
    'Tijdkaart {card_id} niet gevonden.': (
        'Tijdkaart {card_id} niet gevonden.', 'Time card {card_id} not found.'),
    'Er staan al {allocated} uren op een verkooporder; het aantal kan niet lager.': (
        'Er staan al {allocated} uren op een verkooporder; het aantal kan niet lager.', '{allocated} hours are already on a sales order; the number cannot be lower.'),
    # ---- importer.py (stap 7 import)
    'Kolom {label}: {value} is geen getal.': (
        'Kolom {label}: {value} is geen getal.', 'Column {label}: {value} is not a number.'),
    'Kolom {label}: {value} is geen geheel getal.': (
        'Kolom {label}: {value} is geen geheel getal.', 'Column {label}: {value} is not a whole number.'),
    'Kolom {label}: {value} is geen datum.': (
        'Kolom {label}: {value} is geen datum.', 'Column {label}: {value} is not a date.'),
    'Kolom {label}: {value} is geen ja of nee.': (
        'Kolom {label}: {value} is geen ja of nee.', 'Column {label}: {value} is not yes or no.'),
    'Kolom {label}: valuta {value} bestaat niet.': (
        'Kolom {label}: valuta {value} bestaat niet.', 'Column {label}: currency {value} does not exist.'),
    'Kolom {label}: {value} bestaat niet.': (
        'Kolom {label}: {value} bestaat niet.', 'Column {label}: {value} does not exist.'),
    'Op rij {row} ontbreekt de rekening.': (
        'Op rij {row} ontbreekt de rekening.', 'Row {row} has no account.'),
    'Te veel rijen ({rows}); hoogstens {max} per keer.': (
        'Te veel rijen ({rows}); hoogstens {max} per keer.', 'Too many rows ({rows}); at most {max} at a time.'),
    '{what} {key} komt meer dan eens voor in de administratie.': (
        '{what} {key} komt meer dan eens voor in de administratie.', '{what} {key} occurs more than once in the company.'),
    'Artikelgroep {name} komt meer dan eens voor in de administratie.': (
        'Artikelgroep {name} komt meer dan eens voor in de administratie.', 'Group {name} occurs more than once in the company.'),
    'Komt ook voor in rij {row}.': (
        'Komt ook voor in rij {row}.', 'Also occurs in row {row}.'),
    'Nieuwe artikelgroep: {names}': (
        'Nieuwe artikelgroep: {names}', 'New group: {names}'),
    'Rij {row}: {error}': (
        'Rij {row}: {error}', 'Row {row}: {error}'),
    # ---- icp.py (ICP-opgaaf)
    'Een relatie in rubriek {box} moet een btw-nummer hebben (met landcode, bv. DE123456789).': (
        'Een relatie in rubriek {box} moet een btw-nummer hebben (met landcode, bv. DE123456789).', 'A contact in box {box} must have a VAT number (with country code, e.g. DE123456789).'),
    'Een relatie in rubriek {box} moet een btw-nummer uit een ander EU-land hebben, geen Nederlands nummer.': (
        'Een relatie in rubriek {box} moet een btw-nummer uit een ander EU-land hebben, geen Nederlands nummer.', 'A contact in box {box} must have a VAT number from another EU country, not a Dutch number.'),
    'Het btw-nummer {number} is geen geldig EU-btw-nummer (landcode + nummer, bv. DE123456789).': (
        'Het btw-nummer {number} is geen geldig EU-btw-nummer (landcode + nummer, bv. DE123456789).', 'VAT number {number} is not a valid EU VAT number (country code + number, e.g. DE123456789).'),
    # ---- gl.py / arap.py / reports_arap.py
    "Transaction is locked by {holder}": (
        "Deze boeking is in gebruik door {holder}.", "This transaction is in use by {holder}."),
    "Amount has more than {precision} decimals": (
        "Een bedrag heeft meer dan {precision} decimalen.", "An amount has more than {precision} decimals."),
    "Unknown account(s): {accounts}": (
        "Onbekende rekening: {accounts}.", "Unknown account: {accounts}."),
    "Account(s) not postable (heading or closed): {accounts}": (
        "Op deze rekening kan niet geboekt worden (kop of gesloten): {accounts}.",
        "Cannot post to this account (heading or closed): {accounts}."),
    "GL transaction {trans_id} not found": (
        "Boeking {trans_id} niet gevonden.", "Transaction {trans_id} not found."),
    "{table} {trans_id} not found": (
        "Boeking {trans_id} niet gevonden.", "Transaction {trans_id} not found."),
    "{vc} {vc_id} not found": (
        "Relatie {vc_id} niet gevonden.", "Contact {vc_id} not found."),
    "Unknown {vc} {vc_id}": (
        "Onbekende {vc} ({vc_id}).", "Unknown {vc} ({vc_id})."),
    "Not a retained earnings account: {accno}": (
        "Geen rekening voor eigen vermogen: {accno}.", "Not a retained earnings account: {accno}."),
    "vc moet 'customer' of 'vendor' zijn": (
        "Interne fout: soort relatie moet klant of leverancier zijn.",
        "Internal error: type must be customer or vendor."),
    "vc must be 'customer' or 'vendor'": (
        "Interne fout: soort relatie moet klant of leverancier zijn.",
        "Internal error: type must be customer or vendor."),
    "arap moet 'ar' of 'ap' zijn": (
        "Interne fout: soort moet verkoop of inkoop zijn.", "Internal error: type must be AR or AP."),
    "onbekende sorteerkolom: {column}": (
        "Onbekende sorteerkolom: {column}.", "Unknown sort column: {column}."),
    # ---- admin.py (btw, BT1)
    'Het tarief van {rate}% van rekening {accno} is al gebruikt in boekingen tot en met {date}. Voeg een nieuw tarief toe met een ingangsdatum.': (
        'Het tarief van {rate}% van rekening {accno} is al gebruikt in boekingen tot en met {date}. Voeg een nieuw tarief toe met een ingangsdatum.',
        "The rate of {rate}% on account {accno} has been used in transactions up to and including {date}. "
        "Add a new rate with a start date."),
    # ---- payments.py
    "{table} {trans_id} belongs to another {vc}": (
        "Post {trans_id} hoort bij een andere {vc}.", "Item {trans_id} belongs to another {vc}."),
    "{table} {trans_id} is not approved": (
        "Post {trans_id} is nog niet goedgekeurd.", "Item {trans_id} is not approved."),
    "{table} {trans_id} is in another currency": (
        "Post {trans_id} is in een andere valuta.", "Item {trans_id} is in another currency."),
    "Open item {invnumber} changed meanwhile": (
        "Post {invnumber} is intussen gewijzigd; controleer de bedragen en boek opnieuw.",
        "Item {invnumber} has changed meanwhile; check the amounts and post again."),
    "Payment exceeds the amount due for {invnumber}": (
        "Het bedrag bij post {invnumber} is hoger dan wat openstaat; boek het meerdere als vooruitbetaling.",
        "The amount for item {invnumber} exceeds the amount due; post the excess as a prepayment."),
    "{table} {trans_id} is locked by another user": (
        "Een van de posten is in gebruik door een andere gebruiker.",
        "One of the items is in use by another user."),
    "No {arap} account for {table} {trans_id}": (
        "Post {trans_id} heeft geen {arap}-rekening.", "Item {trans_id} has no {arap} account."),
    "Unknown option {option}": (
        "Onbekende keuze: {option}.", "Unknown option: {option}."),
    "No open payment {source} for {table} {trans_id}": (
        "Geen openstaande betaling {source} bij boeking {trans_id}.",
        "No open payment {source} for transaction {trans_id}."),
    "Payment {source} not found (or already voided)": (
        "Betaling {source} niet gevonden (of al gestorneerd).", "Payment {source} not found (or already voided)."),
    "Payment {source} on {date} not found for {table} {trans_id}": (
        "Betaling {source} op {date} niet gevonden bij boeking {trans_id}.",
        "Payment {source} on {date} not found for transaction {trans_id}."),
    "Payment {source} occurs on several dates for {table} {trans_id}; give datepaid": (
        "Kenmerk {source} komt op meerdere datums voor.", "Source {source} occurs on several dates."),
    "Cannot identify the lines of payment {source} on {date} for {table} {trans_id}": (
        "De regels van betaling {source} zijn niet eenduidig te herkennen; corrigeer deze met een "
        "memoriaalboeking.",
        "The lines of payment {source} cannot be identified unambiguously; correct it with a GL transaction."),
    "Unknown account: {accno}": (
        "Onbekende rekening: {accno}.", "Unknown account: {accno}."),
    "Account {accno} is not linked as {link}": (
        "Rekening {accno} is niet gekoppeld als {link}.", "Account {accno} is not linked as {link}."),
    "No {arap} account for the prepayment": (
        "Er is geen {arap}-rekening voor de vooruitbetaling.", "There is no {arap} account for the prepayment."),
    "Invoice selected more than once": (
        "Een post is meer dan eens gekozen.", "An item was selected more than once."),
    # ---- reconcile.py
    "Invalid line key: {key}": (
        "Ongeldige regel in het formulier: {key}", "Invalid line in the form: {key}"),
    "Too many decimals: {amount}": (
        "Het afschriftsaldo heeft meer decimalen dan de administratie gebruikt.",
        "The statement balance has more decimals than the company uses."),
    "Opening balance {opening} differs from the statement balance {previous} of the previous reconciliation "
    "({date})": (
        "Het beginsaldo {opening} wijkt af van het afschriftsaldo {previous} van de vorige aflettering ({date}).",
        "Opening balance {opening} differs from the statement balance {previous} of the previous reconciliation "
        "({date})."),
    "Account {accno} not found": (
        "Onbekende rekening: {accno}.", "Unknown account: {accno}."),
    # ---- recurring.py / support.py / dates.py / config.py
    "Unknown unit: {unit}": (
        "Kies dagen, weken, maanden of jaren.", "Choose days, weeks, months or years."),
    "Unknown form: {form}": (
        "Onbekende documentsoort: {form}.", "Unknown document type: {form}."),
    "onbekende eenheid voor herhaling: {unit}": (
        "Kies dagen, weken, maanden of jaren.", "Choose days, weeks, months or years."),
    "onbekende eenheid: {unit}": (
        "Kies dagen, weken, maanden of jaren.", "Choose days, weeks, months or years."),
    "Transaction {trans_id} not found": (
        "Boeking {trans_id} niet gevonden.", "Transaction {trans_id} not found."),
    "Attachment {archive_id} not found": (
        "Bijlage {archive_id} niet gevonden.", "Attachment {archive_id} not found."),
    "Exchange rate missing for {curr} on {date}": (
        "Koers {curr} op {date} ontbreekt; voer die eerst in (overgeslagen).",
        "Exchange rate {curr} on {date} is missing; enter it first (skipped)."),
    "ongeldige datum: {value}": (
        "Ongeldige datum: {value}", "Invalid date: {value}"),
    "onbekend numberformat: {value}": (
        "Onbekende getalnotatie: {value}", "Unknown number format: {value}"),
    "onbekend dateformat: {value}": (
        "Onbekende datumnotatie: {value}", "Unknown date format: {value}"),
    # ---- contacts.py
    "Onbekende rekening bij {label}: {accno}": (
        "Onbekende rekening bij {label}: {accno}", "Unknown account for {label}: {accno}"),
    "Nummer {number} bestaat al ({name}).": (
        "Nummer {number} bestaat al ({name}).", "Number {number} already exists ({name})."),
    "Ongeldig aantal dagen: {value}": (
        "Ongeldig aantal dagen: {value}", "Invalid number of days: {value}"),
    "IBAN {iban} is niet geldig (controle mislukt).": (
        "IBAN {iban} is niet geldig (controle mislukt).", "IBAN {iban} is not valid (check failed)."),
    "BIC {bic} heeft niet de juiste vorm (8 of 11 tekens).": (
        "BIC {bic} heeft niet de juiste vorm (8 of 11 tekens).",
        "BIC {bic} does not have the right form (8 or 11 characters)."),
    "Onbekende valuta: {value}": (
        "Onbekende valuta: {value}", "Unknown currency: {value}"),
    "Onbekende taal: {value}": (
        "Onbekende taal: {value}", "Unknown language: {value}"),
    "Onbekende keuze ({field}): {value}": (
        "Onbekende keuze ({field}): {value}", "Unknown choice ({field}): {value}"),
    "{label} is te lang (maximaal {n} tekens).": (
        "{label} is te lang (maximaal {n} tekens).", "{label} is too long (at most {n} characters)."),
    "Voornaam en achternaam samen zijn te lang (maximaal {n} tekens).": (
        "Voornaam en achternaam samen zijn te lang (maximaal {n} tekens).",
        "First and last name together are too long (at most {n} characters)."),
    "{label} {name} wordt gebruikt in boekingen of orders en kan niet verwijderd worden; maak de relatie "
    "inactief.": (
        "{label} {name} wordt gebruikt in boekingen of orders en kan niet verwijderd worden; maak de relatie "
        "inactief.",
        "{label} {name} is used in transactions or orders and cannot be deleted; make the contact inactive."),
    # ---- admin.py
    "Rekening {chart_id} niet gevonden.": (
        "Rekening {chart_id} niet gevonden.", "Account {chart_id} not found."),
    "Onbekende koppeling: {links}.": (
        "Onbekende koppeling: {links}.", "Unknown link: {links}."),
    "Een hoofdrekening ({accno}) kan geen andere koppelingen hebben.": (
        "Een hoofdrekening ({accno}) kan geen andere koppelingen hebben.",
        "A control account ({accno}) cannot have other links."),
    "Kies bij {side} maar één van omzet/kosten, betaling, korting of btw.": (
        "Kies bij {side} maar één van omzet/kosten, betaling, korting of btw.",
        "For {side} choose only one of income/expense, payment, discount or VAT."),
    "Rekeningnummer {accno} bestaat al.": (
        "Rekeningnummer {accno} bestaat al.", "Account number {accno} already exists."),
    "Dit is een standaardrekening ({keys}) en kan niet verwijderd worden.": (
        "Dit is een standaardrekening ({keys}) en kan niet verwijderd worden.",
        "This is a default account ({keys}) and cannot be deleted."),
    # ---- masterdata.py
    "Valuta {curr} bestaat al.": ("Valuta {curr} bestaat al.", "Currency {curr} already exists."),
    "Valuta {curr} niet gevonden.": ("Valuta {curr} niet gevonden.", "Currency {curr} not found."),
    "Valuta {curr} is in gebruik: {used}.": (
        "Valuta {curr} is in gebruik: {used}.", "Currency {curr} is in use: {used}."),
    "{curr} is geen vreemde valuta van deze administratie.": (
        "{curr} is geen vreemde valuta van deze administratie.",
        "{curr} is not a foreign currency of this company."),
    "Deze afdeling is in gebruik: {used}.": (
        "Deze afdeling is in gebruik: {used}.", "This department is in use: {used}."),
    "Projectnummer {number} bestaat al.": (
        "Projectnummer {number} bestaat al.", "Project number {number} already exists."),
    "Dit project is in gebruik: {used}.": (
        "Dit project is in gebruik: {used}.", "This project is in use: {used}."),
    "Taalcode {code} is in gebruik ({used}) en kan niet gewijzigd worden.": (
        "Taalcode {code} is in gebruik ({used}) en kan niet gewijzigd worden.",
        "Language code {code} is in use ({used}) and cannot be changed."),
    "Taal {code} bestaat al.": ("Taal {code} bestaat al.", "Language {code} already exists."),
    "Taal {code} is in gebruik: {used}.": ("Taal {code} is in gebruik: {used}.", "Language {code} is in use: {used}."),
    "Deze branche is in gebruik: {used}.": (
        "Deze branche is in gebruik: {used}.", "This type of business is in use: {used}."),
    "Deze betaalmethode is in gebruik: {used}.": (
        "Deze betaalmethode is in gebruik: {used}.", "This payment method is in use: {used}."),
    # ---- bankstatement.py
    "leeg bestand{where}": ("Leeg bestand{where}.", "Empty file{where}."),
    "XML-bestand is geen CAMT.053-bankafschrift{where}": (
        "Het XML-bestand is geen CAMT.053-bankafschrift{where}.",
        "The XML file is not a CAMT.053 bank statement{where}."),
    "onbekend formaat{where}: verwacht CAMT.053 (XML) of MT940": (
        "Onbekend formaat{where}: verwacht CAMT.053 (XML) of MT940.",
        "Unknown format{where}: expected CAMT.053 (XML) or MT940."),
    "geen afschriften gevonden{where}": ("Geen afschriften gevonden{where}.", "No statements found{where}."),
    "XML met DOCTYPE/ENTITY-declaratie geweigerd{where}": (
        "XML met DOCTYPE/ENTITY-declaratie geweigerd{where}.",
        "XML with a DOCTYPE/ENTITY declaration refused{where}."),
    "ongeldige datum in CAMT: {value}": ("Ongeldige datum in CAMT: {value}", "Invalid date in CAMT: {value}"),
    "ongeldig bedrag in CAMT: {value}": ("Ongeldig bedrag in CAMT: {value}", "Invalid amount in CAMT: {value}"),
    "ongeldige XML{where}: {error}": ("Ongeldige XML{where}: {error}", "Invalid XML{where}: {error}"),
    "CAMT.053-versie 001.{version} wordt niet ondersteund{where}": (
        "CAMT.053-versie 001.{version} wordt niet ondersteund{where}.",
        "CAMT.053 version 001.{version} is not supported{where}."),
    "ongeldig MT940-bedrag: {value}": ("Ongeldig MT940-bedrag: {value}", "Invalid MT940 amount: {value}"),
    "ongeldige MT940-datum: {value}": ("Ongeldige MT940-datum: {value}", "Invalid MT940 date: {value}"),
    "ongeldige MT940-boekdatum: {value}": (
        "Ongeldige MT940-boekdatum: {value}", "Invalid MT940 booking date: {value}"),
    "ongeldig MT940-saldo: {value}": ("Ongeldig MT940-saldo: {value}", "Invalid MT940 balance: {value}"),
    "MT940-bericht zonder saldi of mutaties{where}": (
        "MT940-bericht zonder saldi of mutaties{where}.", "MT940 message without balances or entries{where}."),
    "ongeldige MT940 :61:-regel: {value}": (
        "Ongeldige MT940 :61:-regel: {value}", "Invalid MT940 :61: line: {value}"),
    # ---- bankimport.py
    "Rekening {accno} is geen betaalrekening.": (
        "Rekening {accno} is geen betaalrekening.", "Account {accno} is not a payment account."),
    "Rekeningnummer {iban} hoort bij rekening {accno}, niet bij {other}.": (
        "Rekeningnummer {iban} hoort bij rekening {accno}, niet bij {other}.",
        "Account number {iban} belongs to account {accno}, not to {other}."),
    "Rekening {accno} heeft al rekeningnummer {iban}; het afschrift is van {other}.": (
        "Rekening {accno} heeft al rekeningnummer {iban}; het afschrift is van {other}.",
        "Account {accno} already has account number {iban}; the statement is for {other}."),
    "Rekeningnummer {iban} van het afschrift hoort bij geen enkele betaalrekening; kies de rekening.": (
        "Rekeningnummer {iban} van het afschrift hoort bij geen enkele betaalrekening; kies de rekening.",
        "Account number {iban} of the statement does not belong to any payment account; choose the account."),
    "Rekeningnummer {iban} staat bij meer dan één betaalrekening: {accounts}.": (
        "Rekeningnummer {iban} staat bij meer dan één betaalrekening: {accounts}.",
        "Account number {iban} is set on more than one payment account: {accounts}."),
    "Valuta {curr} van {what} is niet bekend in deze administratie.": (
        "Valuta {curr} van {what} is niet bekend in deze administratie.",
        "Currency {curr} of {what} is not known in this company."),
    "{What} is in {curr}, maar rekening {accno} is in {acc_curr}.": (
        "{What} is in {curr}, maar rekening {accno} is in {acc_curr}.",
        "{What} is in {curr}, but account {accno} is in {acc_curr}."),
    "Regel {seq} van {what} is in {curr}, het afschrift in {stmt_curr}.": (
        "Regel {seq} van {what} is in {curr}, het afschrift in {stmt_curr}.",
        "Line {seq} of {what} is in {curr}, the statement in {stmt_curr}."),
    "Saldocontrole {what} klopt niet: beginsaldo {opening} + mutaties {total} = {sum}, eindsaldo {closing} "
    "(verschil {diff}).": (
        "Saldocontrole {what} klopt niet: beginsaldo {opening} + mutaties {total} = {sum}, eindsaldo {closing} "
        "(verschil {diff}).",
        "Balance check {what} fails: opening balance {opening} + entries {total} = {sum}, closing balance "
        "{closing} (difference {diff})."),
    "{What}: begin- of eindsaldo ontbreekt, saldocontrole niet mogelijk.": (
        "{What}: begin- of eindsaldo ontbreekt, saldocontrole niet mogelijk.",
        "{What}: opening or closing balance missing, balance check not possible."),
    "Beginsaldo {opening} sluit niet aan op het eindsaldo {previous} van het vorige afschrift ({date}).": (
        "Beginsaldo {opening} sluit niet aan op het eindsaldo {previous} van het vorige afschrift ({date}).",
        "Opening balance {opening} does not match the closing balance {previous} of the previous statement "
        "({date})."),
    "Grootboeksaldo rekening {accno} vóór {date}: {ledger}; beginsaldo afschrift: {opening} (verschil {diff}).": (
        "Grootboeksaldo rekening {accno} vóór {date}: {ledger}; beginsaldo afschrift: {opening} (verschil {diff}).",
        "Ledger balance of account {accno} before {date}: {ledger}; statement opening balance: {opening} "
        "(difference {diff})."),
    "{What} was al helemaal ingelezen; er is niets toegevoegd.": (
        "{What} was al helemaal ingelezen; er is niets toegevoegd.",
        "{What} was already imported completely; nothing was added."),
    "{n} regel(s) van {what} waren al eerder ingelezen en zijn overgeslagen.": (
        "{n} regel(s) van {what} waren al eerder ingelezen en zijn overgeslagen.",
        "{n} line(s) of {what} were imported before and have been skipped."),
    "Bankregel {line_id} bestaat niet.": ("Bankregel {line_id} bestaat niet.", "Bank line {line_id} does not exist."),
    "Bankregel {line_id} is al verwerkt ({status}).": (
        "Bankregel {line_id} is al verwerkt ({status}).", "Bank line {line_id} has already been processed ({status})."),
    "Koers {curr} op {date} ontbreekt; vul de koers in.": (
        "Koers {curr} op {date} ontbreekt; vul de koers in.",
        "Exchange rate {curr} on {date} is missing; enter the rate."),
    "Het totaal van de posten ({allocated}) sluit niet aan op het bankbedrag ({total}); verschil {rest}.": (
        "Het totaal van de posten ({allocated}) sluit niet aan op het bankbedrag ({total}); verschil {rest}.",
        "The total of the items ({allocated}) does not match the bank amount ({total}); difference {rest}."),
    "Rekening {accno} heeft geen btw-tarief op {date}.": (
        "Rekening {accno} heeft geen btw-tarief op {date}.", "Account {accno} has no VAT rate on {date}."),
    "Het bedrag van de boeking ({amount}) is niet gelijk aan het bankbedrag ({bank}).": (
        "Het bedrag van de boeking ({amount}) is niet gelijk aan het bankbedrag ({bank}).",
        "The amount of the transaction ({amount}) does not equal the bank amount ({bank})."),
    "Rekening {accno} bestaat niet of is niet boekbaar.": (
        "Rekening {accno} bestaat niet of is niet boekbaar.", "Account {accno} does not exist or cannot be posted to."),
    "Rekening {accno} is geen btw-rekening.": (
        "Rekening {accno} is geen btw-rekening.", "Account {accno} is not a VAT account."),
    "Vaste regel {rule_id} bestaat niet.": ("Vaste regel {rule_id} bestaat niet.", "Bank rule {rule_id} does not exist."),
    "onbekende actie": ("Onbekende actie.", "Unknown action."),
    # ---- parts.py
    "Artikel {part_id} niet gevonden.": ("Artikel {part_id} niet gevonden.", "Item {part_id} not found."),
    "Groep {name} bestaat al.": ("Groep {name} bestaat al.", "Group {name} already exists."),
    "Groep {group_id} niet gevonden.": ("Groep {group_id} niet gevonden.", "Group {group_id} not found."),
    "Deze groep is in gebruik: {n} artikel(en).": (
        "Deze groep is in gebruik: {n} artikel(en).", "This group is in use: {n} item(s)."),
    "Kies een {label}.": ("Kies een {label}.", "Choose an {label}."),
    "Rekening {accno} is niet gekoppeld als {label} ({link}).": (
        "Rekening {accno} is niet gekoppeld als {label} ({link}).",
        "Account {accno} is not linked as {label} ({link})."),
    "Rekening {accno} is geen btw-rekening voor dit soort artikel.": (
        "Rekening {accno} is geen btw-rekening voor dit soort artikel.",
        "Account {accno} is not a VAT account for this kind of item."),
    "{label} heeft meer dan {n} decimalen.": (
        "{label} heeft meer dan {n} decimalen.", "{label} has more than {n} decimals."),
    "{label} kan niet negatief zijn.": ("{label} kan niet negatief zijn.", "{label} cannot be negative."),
    "Artikel {number} wordt gebruikt in facturen of orders en kan niet verwijderd worden; zet het op vervallen.": (
        "Artikel {number} wordt gebruikt in facturen of orders en kan niet verwijderd worden; zet het op "
        "vervallen.",
        "Item {number} is used in invoices or orders and cannot be deleted; mark it obsolete."),
    "Een {kind} heeft geen {label}.": ("Een {kind} heeft geen {label}.", "A {kind} has no {label}."),
    # ---- assembly.py (samengestelde artikelen)
    "De stuklijst maakt een kring: {names}.": (
        "De stuklijst maakt een kring: {names}.", "The bill of materials makes a cycle: {names}."),
    "Een stuklijst kan hoogstens {n} niveaus diep zijn.": (
        "Een stuklijst kan hoogstens {n} niveaus diep zijn.", "A bill of materials can be at most {n} levels deep."),
    "Artikel {number} is een kit en kan geen onderdeel zijn.": (
        "Artikel {number} is een kit en kan geen onderdeel zijn.", "Item {number} is a kit and cannot be a component."),
    "Artikel {number} is vervallen.": ("Artikel {number} is vervallen.", "Item {number} is obsolete."),
    "Artikel {number} is geen samengesteld artikel.": (
        "Artikel {number} is geen samengesteld artikel.", "Item {number} is not an assembly."),
    "Artikel {number} is vervallen; assembleren kan niet meer.": (
        "Artikel {number} is vervallen; assembleren kan niet meer.",
        "Item {number} is obsolete; it can no longer be assembled."),
    "Demonteren kan niet verder dan de voorraad van het product ({onhand}).": (
        "Demonteren kan niet verder dan de voorraad van het product ({onhand}).",
        "Cannot disassemble more than the product stock ({onhand})."),
    "Productieregel {build_id} niet gevonden.": (
        "Productieregel {build_id} niet gevonden.", "Assembly entry {build_id} not found."),
    "Te weinig voorraad van {names}; bij deze onderdelen wordt de voorraad gecontroleerd.": (
        "Te weinig voorraad van {names}; bij deze onderdelen wordt de voorraad gecontroleerd.",
        "Not enough stock of {names}; stock is checked for these components."),
    "Te weinig voorraad van {names}.": ("Te weinig voorraad van {names}.", "Not enough stock of {names}."),
    "Artikel {number} is een onderdeel van een samengesteld artikel en kan niet verwijderd worden; zet het op "
    "vervallen.": (
        "Artikel {number} is een onderdeel van een samengesteld artikel en kan niet verwijderd worden; zet het op "
        "vervallen.",
        "Item {number} is a component of an assembly and cannot be deleted; mark it obsolete."),
    "Aantal heeft meer dan {n} decimalen.": (
        "Aantal heeft meer dan {n} decimalen.", "Quantity has more than {n} decimals."),
    "Bankbedrag is {rest} hoger dan openstaand: het meerdere wordt een vooruitbetaling.": (
        "Bankbedrag is {rest} hoger dan openstaand: het meerdere wordt een vooruitbetaling.",
        "Bank amount is {rest} more than the amount due: the excess becomes a prepayment."),
    "Bankbedrag is lager dan openstaand ({due}): gedeeltelijke betaling.": (
        "Bankbedrag is lager dan openstaand ({due}): gedeeltelijke betaling.",
        "Bank amount is less than the amount due ({due}): partial payment."),
    "Let op: er staat al een niet-afgeletterde boeking met hetzelfde bedrag op de bank ({ref}, {date}); mogelijk "
    "is dit al geboekt.": (
        "Let op: er staat al een niet-afgeletterde boeking met hetzelfde bedrag op de bank ({ref}, {date}); "
        "mogelijk is dit al geboekt.",
        "Note: there is already an unreconciled transaction with the same amount on the bank ({ref}, {date}); "
        "this may already have been posted."),
    "Betalingskenmerk van de factuur: {invnumbers}": (
        "Betalingskenmerk van de factuur: {invnumbers}",
        "Payment reference of the invoice: {invnumbers}"),
    "Factuurnummer in de omschrijving: {invnumbers}": (
        "Factuurnummer in de omschrijving: {invnumbers}", "Invoice number in the description: {invnumbers}"),
    "Rekeningnummer van {name}; open post {invnumber} met hetzelfde bedrag": (
        "Rekeningnummer van {name}; open post {invnumber} met hetzelfde bedrag",
        "Account number of {name}; open item {invnumber} with the same amount"),
    "Rekeningnummer van {name}; alle open posten samen": (
        "Rekeningnummer van {name}; alle open posten samen", "Account number of {name}; all open items together"),
    "Rekeningnummer van {name}; bedrag wijkt af van de open posten": (
        "Rekeningnummer van {name}; bedrag wijkt af van de open posten",
        "Account number of {name}; amount differs from the open items"),
    "Bestaande boeking ({what} {ref}, {date}{source}) met hetzelfde bedrag: alleen afletteren": (
        "Bestaande boeking ({what} {ref}, {date}{source}) met hetzelfde bedrag: alleen afletteren",
        "Existing transaction ({what} {ref}, {date}{source}) with the same amount: reconcile only"),
    # ---- bankimport.py: bankproef 2026-11 (BT2–BT10, VZ1–VZ6)
    'Ook bij {n} volgende afschrift(en) sluit het grootboeksaldo niet aan op het beginsaldo; laatste boeking op rekening {accno}: {date}.': ('Ook bij {n} volgende afschrift(en) sluit het grootboeksaldo niet aan op het beginsaldo; laatste boeking op rekening {accno}: {date}.', 'For {n} further statement(s) the ledger balance does not match the opening balance either; last transaction on account {accno}: {date}.'),
    'Hoort bij het voorstel van de bankregel van {date} ({amount}): samen afletteren.': ('Hoort bij het voorstel van de bankregel van {date} ({amount}): samen afletteren.', 'Belongs to the proposal for the bank line of {date} ({amount}): reconcile together.'),
    '{n} open posten met hetzelfde bedrag bij {name}; de oudste is gekozen, kies zo nodig een andere.': ('{n} open posten met hetzelfde bedrag bij {name}; de oudste is gekozen, kies zo nodig een andere.', '{n} open items with the same amount for {name}; the oldest was chosen, choose another one if needed.'),
    '{n} bestaande boekingen ({description}: {refs}) zijn samen het bedrag: samen afletteren': ('{n} bestaande boekingen ({description}: {refs}) zijn samen het bedrag: samen afletteren', '{n} existing transactions ({description}: {refs}) add up to the amount: reconcile together'),
    'Bestaande boeking ({what} {ref}, {date}) is samen met {n} andere bankregel(s) het bedrag: samen afletteren': ('Bestaande boeking ({what} {ref}, {date}) is samen met {n} andere bankregel(s) het bedrag: samen afletteren', 'Existing transaction ({what} {ref}, {date}) equals the amount together with {n} other bank line(s): reconcile together'),
    'Naam lijkt op {name}; open post {invnumber} met hetzelfde bedrag (geen rekeningnummer bij de relatie)': ('Naam lijkt op {name}; open post {invnumber} met hetzelfde bedrag (geen rekeningnummer bij de relatie)', 'Name resembles {name}; open item {invnumber} with the same amount (no account number for the contact)'),
    'Naam lijkt op {name}; bedrag wijkt af van de open posten (geen rekeningnummer bij de relatie)': ('Naam lijkt op {name}; bedrag wijkt af van de open posten (geen rekeningnummer bij de relatie)', 'Name resembles {name}; amount differs from the open items (no account number for the contact)'),
    'Zoals eerder: “{label}” ({date}) → {accno}{r_tax}': ('Zoals eerder: “{label}” ({date}) → {accno}{r_tax}', 'As before: “{label}” ({date}) → {accno}{r_tax}'),
    'Zoals eerder: {name} ({date}); open post {invnumber} met hetzelfde bedrag': ('Zoals eerder: {name} ({date}); open post {invnumber} met hetzelfde bedrag', 'As before: {name} ({date}); open item {invnumber} with the same amount'),
    'Zoals eerder: {name} ({date}); bedrag wijkt af van de open posten': ('Zoals eerder: {name} ({date}); bedrag wijkt af van de open posten', 'As before: {name} ({date}); amount differs from the open items'),
    'Zoals eerder: {name} ({date}); er staat geen open post met dit bedrag: voer eerst de factuur in, of boek een memoriaal.': ('Zoals eerder: {name} ({date}); er staat geen open post met dit bedrag: voer eerst de factuur in, of boek een memoriaal.', 'As before: {name} ({date}); there is no open item with this amount: enter the invoice first, or post a journal entry.'),
    'Bankregel {line_id} staat niet (meer) open op deze bankrekening.': ('Bankregel {line_id} staat niet (meer) open op deze bankrekening.', 'Bank line {line_id} is not (or no longer) open on this bank account.'),
    "Vaste regel: {r_iban}{r_and}{r_keyword} → {accno}{r_tax}": (
        "Vaste regel: {r_iban}{r_and}{r_keyword} → {accno}{r_tax}",
        "Bank rule: {r_iban}{r_and}{r_keyword} → {accno}{r_tax}"),
    # ---- invoice.py / cogs.py (facturen met artikelen)
    "invoice line {line_id} not found": (
        "Factuurregel {line_id} niet gevonden.", "Invoice line {line_id} not found."),
    "Kostprijsregel van boeking {tid} niet gevonden (rekening {chart}, {amount}, {date}); de boeking is buiten "
    "deze toepassing gewijzigd": (
        "Kostprijsregel van boeking {tid} niet gevonden (rekening {chart}, {amount}, {date}); de boeking is buiten "
        "deze toepassing gewijzigd.",
        "Cost of goods sold entry of transaction {tid} not found (account {chart}, {amount}, {date}); the "
        "transaction was changed outside this application."),
    "part {parts_id} not found": (
        "Artikel {parts_id} niet gevonden.", "Part {parts_id} not found."),
    "Unknown invoice type {type}": (
        "Interne fout: onbekende soort factuur ({type}).", "Internal error: unknown invoice type ({type})."),
    "Unknown account {accno}": (
        "Onbekende rekening: {accno}.", "Unknown account: {accno}."),
    "Onbekend artikel {parts_id}": (
        "Onbekend artikel ({parts_id}).", "Unknown part ({parts_id})."),
    "Regel {n}: korting moet tussen 0 en 100 liggen": (
        "Regel {n}: korting moet tussen 0 en 100 liggen.", "Row {n}: discount must be between 0 and 100."),
    # ---- attachments.py (maximale uploadgrootte, stap 4f)
    "Het bestand is groter dan {mb} MB.": (
        "Het bestand is groter dan {mb} MB.", "The file is larger than {mb} MB."),
    # ---- order.py
    "oe {oe_id} not found": (
        "Order of offerte {oe_id} niet gevonden.", "Order or quotation {oe_id} not found."),
    "Unknown order type {type}": (
        "Onbekende soort order: {type}.", "Unknown order type: {type}."),
    "Cannot convert {source} to {target}": (
        "Deze omzetting kan niet ({source} naar {target}).", "This conversion is not possible ({source} to {target})."),
    "Order {oe_id} is a {old}, not a {new}": (
        "Order {oe_id} is een {old}, geen {new}.", "Order {oe_id} is a {old}, not a {new}."),
    "Regel {n}: kits worden (nog) niet ondersteund": (
        "Regel {n}: kits worden (nog) niet ondersteund.", "Row {n}: kits are not supported (yet)."),
    "Regel {n}: artikel {partnumber} heeft geen opbrengstrekening": (
        "Regel {n}: artikel {partnumber} heeft geen opbrengstrekening.",
        "Row {n}: part {partnumber} has no income account."),
    "Regel {n}: een samengesteld artikel kan niet ingekocht worden": (
        "Regel {n}: een samengesteld artikel kan niet ingekocht worden.", "Row {n}: an assembly cannot be purchased."),
    "Regel {n}: artikel {partnumber} heeft geen voorraad- of kostenrekening": (
        "Regel {n}: artikel {partnumber} heeft geen voorraad- of kostenrekening.",
        "Row {n}: part {partnumber} has no inventory or expense account."),
    "Regel {n}: meer dan {decimals} decimalen": (
        "Regel {n}: meer dan {decimals} decimalen.", "Row {n}: more than {decimals} decimals."),
    "Not enough inventory in Row {n}": (
        "Onvoldoende voorraad in regel {n}.", "Not enough inventory in row {n}."),
    # algemeen (fase 6a), als laatste: specifiekere 'Regel …'-sjablonen gaan voor
    'Regel {line}: {error}': ('Regel {line}: {error}', 'Line {line}: {error}'),
    # ---- rgs.py (RGS-koppeling)
    'Kolommen ontbreken in het RGS-bestand: {columns}': (
        'Kolommen ontbreken in het RGS-bestand: {columns}', 'Columns missing in the RGS file: {columns}'),
    'RGS-code {code} bestaat niet in RGS {version}.': (
        'RGS-code {code} bestaat niet in RGS {version}.', 'RGS code {code} does not exist in RGS {version}.'),
    # back-up maken (6d-1)
    'pg_dump niet gevonden of niet uit te voeren ({program}): {error}': ('pg_dump niet gevonden of niet uit te voeren ({program}): {error}', 'pg_dump not found or not executable ({program}): {error}'),
    'pg_dump mislukt: {error}': ('pg_dump mislukt: {error}', 'pg_dump failed: {error}'),
    # ---- timecards.py (v1.1 interne uren)
    'Tarief {rate_id} niet gevonden.': (
        'Tarief {rate_id} niet gevonden.', 'Rate {rate_id} not found.'),
}

#: vaste termen in ingevulde waarden: (Nederlands, Engels)
TERMS: Dict[str, Tuple[str, str]] = {
    "customer": ("klant", "customer"), "vendor": ("leverancier", "vendor"),
    "ar": ("verkoop", "AR"), "ap": ("inkoop", "AP"), "AR": ("debiteuren", "AR"), "AP": ("crediteuren", "AP"),
    "verkoop": ("verkoop", "AR"), "inkoop": ("inkoop", "AP"), "memoriaal": ("memoriaal", "GL"),
    "boeking": ("boeking", "transaction"), "artikel": ("artikel", "part"),
    "Klant": ("Klant", "Customer"), "Leverancier": ("Leverancier", "Vendor"),
    "voorraadrekening": ("voorraadrekening", "inventory account"),
    "opbrengstrekening": ("opbrengstrekening", "income account"),
    "kostenrekening": ("kostenrekening", "expense account"),
    "dienst": ("dienst", "service"), "loon/overhead": ("loon/overhead", "labor/overhead"),
    "samengesteld artikel": ("samengesteld artikel", "assembly"), "kit": ("kit", "kit"),
    "Verkoopprijs": ("Verkoopprijs", "Sell price"), "Lijstprijs": ("Lijstprijs", "List price"),
    "Laatste inkoopprijs": ("Laatste inkoopprijs", "Last cost"), "Gewicht": ("Gewicht", "Weight"),
    "Bestelniveau": ("Bestelniveau", "Reorder point"),
}
#: woorddelen in samengestelde waarden (alleen Engels nodig: de bron is Nederlands)
FRAGMENTS_EN = [("Afschrift", "Statement"), ("afschrift", "statement"), ("rekeningnummer ", "account number "),
                ("trefwoord ", "keyword "), (", kenmerk ", ", source "), (" (btw ", " (VAT "), ("(onbekend)", "(unknown)")]
#: plaatshouders met een beperkte vorm (zodat gelijkende sjablonen elkaar niet vangen)
SHAPES = {"vc": r"customer|vendor", "table": r"ar|ap|gl|oe", "trans_id": r"\d+", "vc_id": r"\d+",
          "precision": r"\d+", "n": r"\d+", "mb": r"\d+",
          "r_iban": r"(?:rekeningnummer \S+)?", "r_and": r"(?: en )?", "r_keyword": r"(?:trefwoord “.*”)?",
          "r_tax": r"(?: \(btw [^)]*\))?"}
#: plaatshouders waarvan de waarde ook vertaald moet worden
TRANSLATED = {"vc", "arap", "side", "what", "What", "label", "kind", "link", "r_iban", "r_keyword", "r_and", "r_tax", "source",
              "used", "status"}


@lru_cache(maxsize=None)
def _patterns():
    out = []
    for source, texts in TEMPLATES.items():
        parts = re.split(r"\{(\w+)\}", source)
        rx, seen = "", set()
        for i, part in enumerate(parts):
            if i % 2 == 0:
                rx += re.escape(part)
            elif part in seen:
                rx += f"(?P={part})"
            else:
                seen.add(part)
                rx += f"(?P<{part}>{SHAPES.get(part, '.*?')})"
        out.append((re.compile(rx, re.S), source, texts))
    return out


def _value(name: str, value: str, language: str) -> str:
    if name not in TRANSLATED or not value:
        return value
    extra = LIBMSG.get(language)
    if value in TERMS:
        if extra and value in extra.TERMS:
            return extra.TERMS[value]
        return TERMS[value][0 if language == "nl" else 1]
    if language == "nl":
        return value
    found = CATALOGS.get(language, {}).get(value)
    if found:
        return found
    if name == "r_and" and value.strip() == "en":
        return {"de": " und ", "fr": " et "}.get(language, " and ")
    for nl, other in (extra.FRAGMENTS if extra else FRAGMENTS_EN):
        value = value.replace(nl, other)
    return value


def lib_message(exc_or_text: Union[BaseException, str]) -> str:
    """Melding uit de bibliotheek in de taal van de gebruiker."""
    text = str(exc_or_text)
    language = lang()
    own = OVERRIDES.get(language, ({}, {}))[0]
    exact = own.get(text) or CATALOGS.get(language, {}).get(text)
    if exact:
        return exact
    for rx, source, (nl, en) in _patterns():
        m = rx.fullmatch(text)
        if m:
            values = {k: _value(k, v, language) for k, v in m.groupdict().items()}
            if nl in own:  # eigen woorden (TL9), sleutel = Nederlandse tekst van het sjabloon
                return own[nl].format(**values)
            if language == "nl":
                return nl.format(**values)
            extra = LIBMSG.get(language)
            return ((extra.LIBMSG.get(source) if extra else None) or en).format(**values)
    return text
