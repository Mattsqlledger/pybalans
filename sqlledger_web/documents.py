"""Documenten maken: HTML-sjabloon (Jinja, sandbox) → PDF met WeasyPrint (fase 4, D1/D5/D6/D7).

Sjablonen worden in deze volgorde gezocht (eerste die bestaat):

1. ``<data_dir>/templates/<administratie-id>/<variant>/<soort>.html`` (variant = ``language_code`` van
   het document, zoals SQL-Ledger ``templates/<set>/<taal>/``; administratie C: Factor, Euros)
2. ``<data_dir>/templates/<administratie-id>/<soort>.html``
3. de standaardset in ``sqlledger_web/doctemplates/standard/<soort>.html`` (Nederlands of Engels)

Wat de beheerder instelt (Beheer > Documentinstellingen, ``sqlledger.docsettings``) staat voor elk sjabloon
klaar als ``doc``: kolommen, regels onder de omschrijving, teksten, btw-nummer, bank. Zonder instellingen is
de uitvoer gelijk aan de standaardset zonder instellingen.

Een sjabloon kan geen code uitvoeren (SandboxedEnvironment) en geen bestanden of internet lezen:
WeasyPrint krijgt alleen ``data:``-URL's (het logo wordt als data-URL meegegeven).
"""

from __future__ import annotations

import base64
import os
import shutil
from datetime import datetime
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader, TemplateNotFound, select_autoescape
from jinja2.exceptions import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup, escape

from sqlledger.arap import format_dcn
from sqlledger.docsettings import (AUTO, BANK_FIXED, COLUMN, FIELDS, GROUP_FIELDS, GROUP_OF, NOTE, DocSettings)
from sqlledger.docstyle import DocStyle
from sqlledger.num2text import document_language

from . import docfonts
from .i18n import N_
from sqlledger.printdata import LINE_LISTS, ORDER_LINE_LISTS, PAYMENT_LISTS, TAX_LISTS, TRANSACTION_LISTS

BUILTIN = Path(__file__).parent / "doctemplates"
NAME_RE = re.compile(r"^[a-z_][a-z_0-9]{0,39}$")
VARIANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
LOGO_NAMES = ("logo.png", "logo.jpg", "logo.jpeg", "logo.svg")


#: teksten van de standaardsjablonen
LABELS_EN = {
    "Factuur": "Invoice", "Creditfactuur": "Credit invoice", "Inkoopfactuur": "Purchase invoice",
    "Debetnota": "Debit note", "Factuurdatum": "Invoice date", "Factuurnummer": "Invoice number",
    "Vervaldatum": "Due date", "Klantnummer": "Customer number", "Leveranciersnummer": "Vendor number",
    "Uw referentie": "Your reference", "Ordernummer": "Order number", "Artikelnr.": "Item no.",
    "Omschrijving": "Description", "Aantal": "Qty", "Eenheid": "Unit", "Prijs": "Price", "Korting": "Disc.",
    "Bedrag": "Amount", "Subtotaal": "Subtotal", "over": "on", "Totaal": "Total", "Betaald": "Paid",
    "Te betalen": "Amount due", "Alle bedragen in": "All amounts in", "Afleveradres": "Delivery address",
    "Graag betalen vóór": "Please pay before", "onder vermelding van": "quoting",
    "Pagina": "Page", "van": "of", "Btw-nummer": "VAT number", "Telefoon": "Phone", "KvK": "Reg. no.",
    "Dank voor uw bestelling.": "Thank you for your order.", "Opmerkingen": "Notes",
    "Btw verlegd": "VAT reverse charged", "Leverdatum": "Delivery date", "t.a.v.": "Attn.",
    "Klant": "Customer", "Leverancier": "Vendor", "Orderbevestiging": "Order confirmation",
    "Orderdatum": "Order date", "Gewenste leverdatum": "Requested delivery date", "Inkooporder": "Purchase order",
    "Offerte": "Quotation", "Offertedatum": "Quotation date", "Offertenummer": "Quotation number",
    "Geldig tot": "Valid until", "Offerteaanvraag": "Request for quotation", "Aanvraagdatum": "Request date",
    "Aanvraagnummer": "Request number", "Verzending": "Ship via", "Pakbon": "Packing list", "Datum": "Date",
    "Vrachtbrief": "Waybill", "Besteld": "Ordered", "Eerder geleverd": "Delivered before",
    "Nu geleverd": "Delivered now", "Verpakking": "Packaging", "Gewicht": "Weight",
    "Totaal gewicht": "Total weight", "Ontvangen in goede orde": "Received in good order", "Naam": "Name",
    "Handtekening": "Signature", "Picklijst": "Pick list", "Medewerker": "Employee", "Locatie": "Bin",
    "Gepickt door": "Picked by", "Gecontroleerd door": "Checked by", "Rekening": "Account",
    "Creditnota": "Credit note",
    "Graag ons ordernummer vermelden op pakbon en factuur.": "Please quote our order number on packing list and invoice.",
    "Wij zien uw opdracht graag tegemoet.": "We look forward to receiving your order.",
    "Graag ontvangen wij uw offerte.": "We look forward to receiving your quotation.",
    "op rekening": "to account", "t.n.v.": "in the name of",
    "EAN": "EAN", "Goederencode": "HS code", "Land van herkomst": "Country of origin", "Project": "Project",
    "Serienummer": "Serial number", "Nettogewicht": "Net weight", "bij": "at", "Nettoprijs": "Net price",
    'Herinnering': 'Payment reminder',
    'Tweede herinnering': 'Second reminder',
    'Aanmaning': 'Final reminder',
    'Rekeningoverzicht': 'Statement of account',
    'Dagen vervallen': 'Days overdue',
    'Openstaand': 'Outstanding',
    'Te verrekenen': 'To be offset',
    'Vervallen': 'Overdue',
    'Graag het bedrag van': 'Please transfer the amount of',
    'overmaken op rekening': 'to account',
    'Per datum': 'As of',
    'Ouderdom in dagen vanaf de vervaldatum': 'Age in days from the due date',
    'Ouderdom in dagen vanaf de factuurdatum': 'Age in days from the invoice date',
    'Volgens onze administratie zijn de onderstaande facturen nog niet (volledig) betaald. Wellicht is dit aan uw aandacht ontsnapt. Wij verzoeken u het openstaande bedrag binnen 14 dagen te voldoen.': 'According to our records, the invoices below have not yet been (fully) paid. This may have escaped your attention. Please pay the outstanding amount within 14 days.',
    'Ondanks onze herinnering hebben wij de betaling van de onderstaande facturen nog niet ontvangen. Wij verzoeken u het openstaande bedrag binnen 7 dagen te voldoen.': 'Despite our reminder, we have not yet received payment of the invoices below. Please pay the outstanding amount within 7 days.',
    'Ondanks eerdere herinneringen staan de onderstaande facturen nog steeds open. Wij verzoeken u dringend het openstaande bedrag binnen 7 dagen te voldoen. Blijft betaling uit, dan zijn wij genoodzaakt de vordering uit handen te geven.': 'Despite previous reminders, the invoices below are still outstanding. We urgently request you to pay the outstanding amount within 7 days. If payment is not received, we will have to hand over the claim for collection.',
    'Heeft u inmiddels betaald? Dan kunt u deze brief als niet verzonden beschouwen.': 'If you have already paid, please disregard this letter.',
    'Hieronder vindt u het overzicht van uw openstaande posten. Neemt u bij vragen of verschillen contact met ons op.': 'Below is an overview of your outstanding items. Please contact us if you have any questions or find any differences.',
    'Betalingsspecificatie': 'Remittance advice',
    'Kenmerk': 'Reference',
    'Factuurbedrag': 'Invoice amount',
    'Vooruitbetaling': 'Prepayment',
    'Totaal ontvangen': 'Total received',
    'Totaal betaald': 'Total paid',
    'Ontvangen op rekening': 'Received on account',
    'Overgemaakt naar rekening': 'Transferred to account',
    'Wij hebben uw betaling ontvangen en als volgt verwerkt.': 'We have received your payment and allocated it as follows.',
    'Wij hebben het onderstaande bedrag aan u overgemaakt voor de volgende facturen.': 'We have transferred the amount below to you for the following invoices.',
    'Urenstaat': 'Timesheet',
    'Periode': 'Period',
    'Dienst': 'Service',
    'Uren': 'Hours',
    'Voor akkoord': 'Approved',
}

#: Duits (Deutschland; DT1/DT2). Vaktermen als in SQL-Ledger locale/de, waar die klopt.
LABELS_DE = {
    "Factuur": "Rechnung", "Creditfactuur": "Rechnungskorrektur", "Inkoopfactuur": "Eingangsrechnung",
    "Debetnota": "Belastungsanzeige", "Factuurdatum": "Rechnungsdatum", "Factuurnummer": "Rechnungsnummer",
    "Vervaldatum": "Fälligkeitsdatum", "Klantnummer": "Kundennummer", "Leveranciersnummer": "Lieferantennummer",
    "Uw referentie": "Ihre Referenz", "Ordernummer": "Auftragsnummer", "Artikelnr.": "Art.-Nr.",
    "Omschrijving": "Beschreibung", "Aantal": "Menge", "Eenheid": "Einheit", "Prijs": "Preis", "Korting": "Rabatt",
    "Bedrag": "Betrag", "Subtotaal": "Zwischensumme", "over": "auf", "Totaal": "Gesamt", "Betaald": "Bezahlt",
    "Te betalen": "Offener Betrag", "Alle bedragen in": "Alle Beträge in", "Afleveradres": "Lieferadresse",
    "Graag betalen vóór": "Bitte zahlen Sie bis", "onder vermelding van": "unter Angabe von",
    "Pagina": "Seite", "van": "von", "Btw-nummer": "USt-IdNr.", "Telefoon": "Telefon", "KvK": "Handelsregister",
    "Dank voor uw bestelling.": "Vielen Dank für Ihre Bestellung.", "Opmerkingen": "Bemerkungen",
    "Btw verlegd": "Steuerschuldnerschaft des Leistungsempfängers", "Leverdatum": "Lieferdatum",
    "t.a.v.": "z. Hd.", "Klant": "Kunde", "Leverancier": "Lieferant", "Orderbevestiging": "Auftragsbestätigung",
    "Orderdatum": "Auftragsdatum", "Gewenste leverdatum": "Gewünschtes Lieferdatum", "Inkooporder": "Bestellung",
    "Offerte": "Angebot", "Offertedatum": "Angebotsdatum", "Offertenummer": "Angebotsnummer",
    "Geldig tot": "Gültig bis", "Offerteaanvraag": "Angebotsanfrage", "Aanvraagdatum": "Anfragedatum",
    "Aanvraagnummer": "Anfragenummer", "Verzending": "Versand", "Pakbon": "Lieferschein", "Datum": "Datum",
    "Vrachtbrief": "Frachtbrief", "Besteld": "Bestellt", "Eerder geleverd": "Bereits geliefert",
    "Nu geleverd": "Jetzt geliefert", "Verpakking": "Verpackung", "Gewicht": "Gewicht",
    "Totaal gewicht": "Gesamtgewicht", "Ontvangen in goede orde": "Ware in einwandfreiem Zustand erhalten",
    "Naam": "Name", "Handtekening": "Unterschrift", "Picklijst": "Kommissionierliste", "Medewerker": "Mitarbeiter",
    "Locatie": "Lagerplatz", "Gepickt door": "Kommissioniert von", "Gecontroleerd door": "Geprüft von",
    "Rekening": "Konto", "Creditnota": "Rechnungskorrektur",
    "Graag ons ordernummer vermelden op pakbon en factuur.":
        "Bitte geben Sie unsere Bestellnummer auf Lieferschein und Rechnung an.",
    "Wij zien uw opdracht graag tegemoet.": "Wir freuen uns auf Ihren Auftrag.",
    "Graag ontvangen wij uw offerte.": "Wir freuen uns auf Ihr Angebot.",
    "op rekening": "auf das Konto", "t.n.v.": "lautend auf",
    "EAN": "EAN", "Goederencode": "Warentarifnummer", "Land van herkomst": "Ursprungsland", "Project": "Projekt",
    "Serienummer": "Seriennummer", "Nettogewicht": "Nettogewicht", "bij": "bei", "Nettoprijs": "Nettopreis",
    'Herinnering': 'Zahlungserinnerung',
    'Tweede herinnering': 'Zweite Zahlungserinnerung',
    'Aanmaning': 'Mahnung',
    'Rekeningoverzicht': 'Kontoauszug',
    'Dagen vervallen': 'Tage überfällig',
    'Openstaand': 'Offen',
    'Te verrekenen': 'Zu verrechnen',
    'Vervallen': 'Fällig',
    'Graag het bedrag van': 'Bitte überweisen Sie den Betrag von',
    'overmaken op rekening': 'auf das Konto',
    'Per datum': 'Stichtag',
    'Ouderdom in dagen vanaf de vervaldatum': 'Alter in Tagen ab Fälligkeitsdatum',
    'Ouderdom in dagen vanaf de factuurdatum': 'Alter in Tagen ab Rechnungsdatum',
    'Volgens onze administratie zijn de onderstaande facturen nog niet (volledig) betaald. Wellicht is dit aan uw aandacht ontsnapt. Wij verzoeken u het openstaande bedrag binnen 14 dagen te voldoen.': 'Laut unserer Buchhaltung sind die folgenden Rechnungen noch nicht (vollständig) bezahlt. Vielleicht ist dies Ihrer Aufmerksamkeit entgangen. Bitte begleichen Sie den offenen Betrag innerhalb von 14 Tagen.',
    'Ondanks onze herinnering hebben wij de betaling van de onderstaande facturen nog niet ontvangen. Wij verzoeken u het openstaande bedrag binnen 7 dagen te voldoen.': 'Trotz unserer Zahlungserinnerung haben wir die Zahlung der folgenden Rechnungen noch nicht erhalten. Bitte begleichen Sie den offenen Betrag innerhalb von 7 Tagen.',
    'Ondanks eerdere herinneringen staan de onderstaande facturen nog steeds open. Wij verzoeken u dringend het openstaande bedrag binnen 7 dagen te voldoen. Blijft betaling uit, dan zijn wij genoodzaakt de vordering uit handen te geven.': 'Trotz früherer Erinnerungen sind die folgenden Rechnungen noch immer offen. Wir fordern Sie dringend auf, den offenen Betrag innerhalb von 7 Tagen zu begleichen. Andernfalls sehen wir uns gezwungen, die Forderung zum Inkasso abzugeben.',
    'Heeft u inmiddels betaald? Dan kunt u deze brief als niet verzonden beschouwen.': 'Sollten Sie inzwischen bezahlt haben, betrachten Sie dieses Schreiben bitte als gegenstandslos.',
    'Hieronder vindt u het overzicht van uw openstaande posten. Neemt u bij vragen of verschillen contact met ons op.': 'Nachfolgend finden Sie eine Übersicht Ihrer offenen Posten. Bei Fragen oder Abweichungen wenden Sie sich bitte an uns.',
    'Betalingsspecificatie': 'Zahlungsavis',
    'Kenmerk': 'Referenz',
    'Factuurbedrag': 'Rechnungsbetrag',
    'Vooruitbetaling': 'Vorauszahlung',
    'Totaal ontvangen': 'Summe erhalten',
    'Totaal betaald': 'Summe bezahlt',
    'Ontvangen op rekening': 'Eingegangen auf Konto',
    'Overgemaakt naar rekening': 'Überwiesen auf Konto',
    'Wij hebben uw betaling ontvangen en als volgt verwerkt.': 'Wir haben Ihre Zahlung erhalten und wie folgt verbucht.',
    'Wij hebben het onderstaande bedrag aan u overgemaakt voor de volgende facturen.': 'Wir haben Ihnen den nachstehenden Betrag für die folgenden Rechnungen überwiesen.',
    'Urenstaat': 'Stundennachweis',
    'Periode': 'Zeitraum',
    'Dienst': 'Dienstleistung',
    'Uren': 'Stunden',
    'Voor akkoord': 'Einverstanden',
}

#: Frans (Frankrijk; DT6 met de aanpak van DT1/DT2).
LABELS_FR = {
    "Factuur": "Facture", "Creditfactuur": "Avoir", "Inkoopfactuur": "Facture fournisseur",
    "Debetnota": "Note de débit", "Factuurdatum": "Date de facture", "Factuurnummer": "Numéro de facture",
    "Vervaldatum": "Date d'échéance", "Klantnummer": "Numéro de client", "Leveranciersnummer": "Numéro de fournisseur",
    "Uw referentie": "Votre référence", "Ordernummer": "Numéro de commande", "Artikelnr.": "Réf.",
    "Omschrijving": "Désignation", "Aantal": "Qté", "Eenheid": "Unité", "Prijs": "Prix", "Korting": "Remise",
    "Bedrag": "Montant", "Subtotaal": "Sous-total", "over": "sur", "Totaal": "Total", "Betaald": "Payé",
    "Te betalen": "Reste à payer", "Alle bedragen in": "Tous les montants en", "Afleveradres": "Adresse de livraison",
    "Graag betalen vóór": "Merci de régler avant le", "onder vermelding van": "en indiquant",
    "Pagina": "Page", "van": "sur", "Btw-nummer": "N° de TVA", "Telefoon": "Téléphone", "KvK": "RCS",
    "Dank voor uw bestelling.": "Merci de votre commande.", "Opmerkingen": "Remarques",
    "Btw verlegd": "Autoliquidation", "Leverdatum": "Date de livraison", "t.a.v.": "À l'attention de",
    "Klant": "Client", "Leverancier": "Fournisseur", "Orderbevestiging": "Confirmation de commande",
    "Orderdatum": "Date de commande", "Gewenste leverdatum": "Date de livraison souhaitée",
    "Inkooporder": "Bon de commande", "Offerte": "Devis", "Offertedatum": "Date du devis",
    "Offertenummer": "Numéro de devis", "Geldig tot": "Valable jusqu'au", "Offerteaanvraag": "Demande de prix",
    "Aanvraagdatum": "Date de la demande", "Aanvraagnummer": "Numéro de la demande", "Verzending": "Expédition",
    "Pakbon": "Bon de livraison", "Datum": "Date", "Vrachtbrief": "Lettre de voiture", "Besteld": "Commandé",
    "Eerder geleverd": "Déjà livré", "Nu geleverd": "Livré maintenant", "Verpakking": "Emballage",
    "Gewicht": "Poids", "Totaal gewicht": "Poids total", "Ontvangen in goede orde": "Reçu en bon état",
    "Naam": "Nom", "Handtekening": "Signature", "Picklijst": "Liste de préparation", "Medewerker": "Employé",
    "Locatie": "Emplacement", "Gepickt door": "Préparé par", "Gecontroleerd door": "Contrôlé par",
    "Rekening": "Compte", "Creditnota": "Avoir",
    "Graag ons ordernummer vermelden op pakbon en factuur.":
        "Merci d'indiquer notre numéro de commande sur le bon de livraison et la facture.",
    "Wij zien uw opdracht graag tegemoet.": "Dans l'attente de votre commande.",
    "Graag ontvangen wij uw offerte.": "Dans l'attente de votre offre.",
    "op rekening": "sur le compte", "t.n.v.": "au nom de",
    "EAN": "EAN", "Goederencode": "Code SH", "Land van herkomst": "Pays d'origine", "Project": "Projet",
    "Serienummer": "Numéro de série", "Nettogewicht": "Poids net", "bij": "auprès de", "Nettoprijs": "Prix net",
    'Herinnering': 'Rappel de paiement',
    'Tweede herinnering': 'Deuxième rappel',
    'Aanmaning': 'Mise en demeure',
    'Rekeningoverzicht': 'Relevé de compte',
    'Dagen vervallen': 'Jours de retard',
    'Openstaand': 'Restant dû',
    'Te verrekenen': 'À déduire',
    'Vervallen': 'Échu',
    'Graag het bedrag van': 'Merci de virer le montant de',
    'overmaken op rekening': 'sur le compte',
    'Per datum': 'Au',
    'Ouderdom in dagen vanaf de vervaldatum': "Ancienneté en jours depuis la date d'échéance",
    'Ouderdom in dagen vanaf de factuurdatum': 'Ancienneté en jours depuis la date de facture',
    'Volgens onze administratie zijn de onderstaande facturen nog niet (volledig) betaald. Wellicht is dit aan uw aandacht ontsnapt. Wij verzoeken u het openstaande bedrag binnen 14 dagen te voldoen.': "D'après notre comptabilité, les factures ci-dessous ne sont pas encore (entièrement) réglées. Cela a peut-être échappé à votre attention. Nous vous prions de régler le montant dû sous 14 jours.",
    'Ondanks onze herinnering hebben wij de betaling van de onderstaande facturen nog niet ontvangen. Wij verzoeken u het openstaande bedrag binnen 7 dagen te voldoen.': "Malgré notre rappel, nous n'avons pas encore reçu le règlement des factures ci-dessous. Nous vous prions de régler le montant dû sous 7 jours.",
    'Ondanks eerdere herinneringen staan de onderstaande facturen nog steeds open. Wij verzoeken u dringend het openstaande bedrag binnen 7 dagen te voldoen. Blijft betaling uit, dan zijn wij genoodzaakt de vordering uit handen te geven.': 'Malgré nos rappels précédents, les factures ci-dessous restent impayées. Nous vous demandons instamment de régler le montant dû sous 7 jours. À défaut de paiement, nous serons contraints de confier le recouvrement de la créance à un tiers.',
    'Heeft u inmiddels betaald? Dan kunt u deze brief als niet verzonden beschouwen.': 'Si vous avez déjà effectué le paiement, merci de ne pas tenir compte de ce courrier.',
    'Hieronder vindt u het overzicht van uw openstaande posten. Neemt u bij vragen of verschillen contact met ons op.': "Vous trouverez ci-dessous le relevé de vos postes ouverts. N'hésitez pas à nous contacter en cas de question ou de différence.",
    'Betalingsspecificatie': 'Avis de paiement',
    'Kenmerk': 'Référence',
    'Factuurbedrag': 'Montant de la facture',
    'Vooruitbetaling': 'Acompte',
    'Totaal ontvangen': 'Total reçu',
    'Totaal betaald': 'Total payé',
    'Ontvangen op rekening': 'Reçu sur le compte',
    'Overgemaakt naar rekening': 'Viré sur le compte',
    'Wij hebben uw betaling ontvangen en als volgt verwerkt.': "Nous avons bien reçu votre paiement et l'avons imputé comme suit.",
    'Wij hebben het onderstaande bedrag aan u overgemaakt voor de volgende facturen.': 'Nous vous avons viré le montant ci-dessous pour les factures suivantes.',
    'Urenstaat': "Relevé d'heures",
    'Periode': 'Période',
    'Dienst': 'Service',
    'Uren': 'Heures',
    'Voor akkoord': 'Bon pour accord',
}

#: documentteksten per taal (Nederlands = de sleutel zelf)
LABELS = {"en": LABELS_EN, "de": LABELS_DE, "fr": LABELS_FR}
LANGUAGES = ("nl", "en", "de", "fr")


def _t(lang: str):
    labels = LABELS.get(lang, {})

    def t(text: str) -> str:
        return labels.get(text, text)
    return t


def _nl2br(value: Any) -> Markup:
    return Markup("<br>").join(escape(str(value or "")).split("\n"))


def _lines(value: Any) -> List[str]:
    """Niet-lege regels van een meerregelig veld (adres in Beheer kan lege regels of \\r bevatten)."""
    return [l.strip() for l in str(value or "").replace("\r", "").split("\n") if l.strip()]


_MARKUP = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]\n]+)\]\(((?:https?://|mailto:)[^\s)]+)\)")


def light_markup(text: Any) -> Markup:
    """Platte tekst met lichte opmaak (zoals SQL-Ledger 4.x): ``**vet**``, ``*cursief*``, ``[tekst](https://…)``,
    regeleinden. Alles wordt ge-escaped; alleen deze drie vormen worden HTML."""
    text = str(text or "").replace("\r", "")
    out: List[Markup] = []
    pos = 0
    for m in _MARKUP.finditer(text):
        out.append(escape(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(Markup("<strong>%s</strong>") % m.group(1))
        elif m.group(2) is not None:
            out.append(Markup("<em>%s</em>") % m.group(2))
        else:
            out.append(Markup('<a href="%s">%s</a>') % (m.group(4), m.group(3)))
        pos = m.end()
    out.append(escape(text[pos:]))
    return Markup("<br>").join(Markup("").join(out).split("\n"))


def _has_value(lines: List[Dict[str, Any]], name: str) -> bool:
    for r in lines:
        if not r.get("runningnumber"):
            continue
        if name == "discount":
            if r.get("discountrate") not in (None, "", "0"):
                return True
        elif name == "deliverydate":
            if r.get("deliverydate") or r.get("requiredate"):
                return True
        elif r.get(name):
            return True
    return False


def doc_context(settings: DocSettings, formname: str, lang: str, t, lines: List[Dict[str, Any]],
                bankname: str = "") -> Dict[str, Any]:
    """``doc`` voor de sjablonen: de instellingen uitgewerkt voor dit document en deze taal."""
    s = settings
    group = GROUP_OF.get(formname)

    def text(name: str, kind: Optional[str] = None) -> Markup:
        value = s.text(lang, name, kind)
        return light_markup(value) if value.strip() else Markup("")

    columns: List[Dict[str, str]] = []
    notes: List[Dict[str, str]] = []
    if group:
        for name in GROUP_FIELDS[group]:
            c, f = s.column(group, name), FIELDS[name]
            own = c.labels.get(lang, "")
            if c.mode == COLUMN or (c.mode == AUTO and _has_value(lines, name)):
                columns.append({"name": name, "label": own or t(f.label)})
            elif c.mode == NOTE:
                notes.append({"name": name, "label": own or (t(f.note_label) if f.note_label else "")})
    return {
        "group": group or "", "taxnumber": s.taxnumber, "return_line": s.return_line,
        "return_text": text("return_line"), "footer": text("footer"), "closing": text("closing", formname),
        "note": text("note", formname), "intro": text("intro", formname), "payinfo": s.payinfo, "bankname": bankname if s.bank_name else "",
        "weights": bool(group and s.weights.get(group)), "columns": columns, "notes": notes,
    }


def style_context(style: DocStyle, font_url_prefix: Optional[str] = None) -> Dict[str, Any]:
    """``style`` voor de sjablonen (huisstijl, HS5: ook voor eigen sjablonen). ``set`` is onwaar zolang de huisstijl
    nooit is opgeslagen: dan maakt de standaardset precies de opmaak van vóór de huisstijl."""
    height, width = style.logo_box
    return {
        "set": style.saved, "color": style.color, "font": Markup(docfonts.family(style.font)), "font_key": style.font,
        "font_face": Markup(docfonts.face_css(style.font, font_url_prefix)), "logo_height": height,
        "logo_width": width, "logo_size": style.logo_size, "logo_pos": style.logo_pos,
        "address_pos": style.address_pos, "show_head": style.show_head, "show_footer": style.show_footer,
    }


def rows(v: Dict[str, Any], keys: Iterable[str], length_key: str) -> List[Dict[str, Any]]:
    """Lijsten per regel samenvoegen tot een lijst van regels (voor ``{% for r in lines %}``)."""
    n = len(v.get(length_key) or [])
    out = []
    for i in range(n):
        out.append({k: (v.get(k) or [""] * n)[i] if i < len(v.get(k) or []) else "" for k in keys})
    return out


def _fetcher():
    """Alleen data:-URL's (logo); bestanden en internet zijn voor sjablonen dicht."""
    try:
        from weasyprint.urls import URLFetcher
    except ImportError:             # WeasyPrint < 66: een functie
        from weasyprint import default_url_fetcher

        def fetch(url, *a, **kw):
            if not url.startswith("data:"):
                raise ValueError("externe bron niet toegestaan in een document")
            return default_url_fetcher(url)
        return fetch
    return URLFetcher(allowed_protocols={"data"})


class DocumentRenderer:
    def __init__(self, data_dir: str | Path, company_id: Optional[int], doc_language: str = "nl",
                 settings: Optional[DocSettings] = None, banks: Iterable[Dict[str, Any]] = ()):
        self.data_dir = Path(data_dir).expanduser()
        self.company_id = company_id
        self.doc_language = doc_language if doc_language in LANGUAGES else "nl"
        self.settings = settings or DocSettings()
        #: lettertypen als URL in plaats van data-URL (live voorbeeld in de browser, Beheer > Huisstijl)
        self.font_url_prefix: Optional[str] = None
        #: rekeningen met bankgegevens (docsettings.bank_accounts): vaste rekening en banknaam
        self.banks = list(banks)

    # ---------------------------------------------------------------- sjablonen

    @property
    def company_dir(self) -> Optional[Path]:
        if self.company_id is None:
            return None
        return self.data_dir / "templates" / str(int(self.company_id))

    def search_path(self, variant: str = "") -> List[Path]:
        paths: List[Path] = []
        cd = self.company_dir
        if cd is not None:
            if variant and VARIANT_RE.match(variant):
                paths.append(cd / variant)
            paths.append(cd)
        paths.append(BUILTIN / "standard")
        return paths

    def variants(self) -> List[str]:
        """Variantmappen van de administratie (``<data_dir>/templates/<id>/<variant>/``)."""
        cd = self.company_dir
        if cd is None or not cd.is_dir():
            return []
        return sorted(p.name for p in cd.iterdir() if p.is_dir() and VARIANT_RE.match(p.name))

    def source(self, formname: str, variant: str = "") -> str:
        """Waar het sjabloon vandaan komt: 'variant', 'company' of 'standard'."""
        cd = self.company_dir
        if cd is not None:
            if variant and VARIANT_RE.match(variant) and (cd / variant / f"{formname}.html").is_file():
                return "variant"
            if (cd / f"{formname}.html").is_file():
                return "company"
        return "standard"

    def env(self, variant: str = "", overrides: Optional[Dict[str, str]] = None) -> SandboxedEnvironment:
        """Jinja-omgeving; ``overrides``: sjablonen die voor gaan (controle bij uploaden, DS11)."""
        loaders = [DictLoader(overrides)] if overrides else []
        loader = ChoiceLoader(loaders + [FileSystemLoader(str(p)) for p in self.search_path(variant) if p.is_dir()])
        env = SandboxedEnvironment(loader=loader, autoescape=select_autoescape(default=True, default_for_string=True))
        env.filters["nl2br"] = _nl2br
        env.filters["lines"] = _lines
        return env

    def logo(self) -> str:
        """Logo als data-URL (eerst variant-onafhankelijk in de map van de administratie)."""
        cd = self.company_dir
        for name in LOGO_NAMES:
            if cd is not None and (cd / name).is_file():
                path = cd / name
                break
        else:
            return ""
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"

    def language(self, language_code: str = "") -> str:
        return document_language(language_code, self.doc_language)

    # ---------------------------------------------------------------- maken

    def render_html(self, formname: str, v: Dict[str, Any], overrides: Optional[Dict[str, str]] = None) -> str:
        if not NAME_RE.match(formname):
            raise ValueError("ongeldige documentsoort")
        variant = str(v.get("language_code") or "")
        lang = self.language(variant)
        v = self.apply_bank(v)
        ctx = {k: val for k, val in v.items() if re.match(r"^[A-Za-z_]\w*$", k)}
        t = _t(lang)
        lines = rows(v, tuple(dict.fromkeys(LINE_LISTS + ORDER_LINE_LISTS + ("toship",))), "description")
        bankname = next((b["name"] for b in self.banks if v.get("iban") and b["iban"] == v.get("iban")), "")
        ctx.update({
            "v": v, "lang": lang, "t": t, "logo": self.logo(),
            "doc": doc_context(self.settings, formname, lang, t, lines, bankname),
            "style": style_context(self.settings.style, self.font_url_prefix),
            "lines": lines,
            "tlines": rows(v, TRANSACTION_LISTS, "amount"),
            "taxes": rows(v, TAX_LISTS, "taxdescription"),
            "payments": rows(v, PAYMENT_LISTS, "payment"),
        })
        try:
            tpl = self.env(variant, overrides).get_template(f"{formname}.html")
        except TemplateNotFound:
            raise ValueError(f"geen sjabloon voor {formname}")
        return tpl.render(**ctx)

    def apply_bank(self, v: Dict[str, Any]) -> Dict[str, Any]:
        """Vaste bankrekening (Documentinstellingen): IBAN, BIC en kenmerk van die rekening in plaats van die
        van de boeking. Het kenmerk volgt het sjabloon van de vaste rekening als die er een heeft."""
        s = self.settings
        if s.bank_mode != BANK_FIXED:
            return v
        bank = next((b for b in self.banks if b["accno"] == s.bank_accno), None)
        if bank is None:
            return v
        v = dict(v, iban=bank["iban"], bic=bank["bic"], membernumber=bank["membernumber"])
        if bank.get("dcn"):
            v["dcn"] = str(format_dcn(bank["dcn"], v) or "")
        if bank.get("rvc"):
            v["rvc"] = str(format_dcn(bank["rvc"], v) or "")
        return v

    def render_pdf(self, formname: str, v: Dict[str, Any], overrides: Optional[Dict[str, str]] = None) -> bytes:
        from weasyprint import HTML
        from .reportpdf import write_pdf
        html = self.render_html(formname, v, overrides)
        return write_pdf(HTML(string=html, url_fetcher=_fetcher(), base_url=None).render())

    def render_many(self, docs: Iterable[tuple]) -> bytes:
        """Meerdere documenten (``(soort, variabelen)``) als één PDF (SB14): elk document apart opgemaakt
        (eigen sjabloon, taal en paginanummering), daarna de pagina's achter elkaar. Geen externe tool."""
        from weasyprint import HTML
        rendered = [HTML(string=self.render_html(f, v), url_fetcher=_fetcher(), base_url=None).render()
                    for f, v in docs]
        if not rendered:
            raise ValueError("geen documenten")
        pages = [p for doc in rendered for p in doc.pages]
        from .reportpdf import write_pdf
        return write_pdf(rendered[0].copy(pages))


# ================================================================== sjabloonbeheer (DS11)

#: gedeelde delen van de standaardset die ook te vervangen zijn
PARTIALS = ("_base", "_invoice", "_order", "_transaction", "_parties", "_lines", "_reminder", "_payment")
MAX_TEMPLATE = 512 * 1024
MAX_LOGO = 1024 * 1024
KEEP_VERSIONS = 5
VERSION_RE = re.compile(r"^\d{8}-\d{6}$")


class TemplateError(Exception):
    """Sjabloon of logo geweigerd; ``text`` is de (Nederlandse) melding, ``values`` de invulwaarden."""

    def __init__(self, text: str, **values):
        super().__init__(text)
        self.text = text
        self.values = values


class TemplateStore:
    """Eigen sjablonen van een administratie: lezen, controleren, opslaan met de vorige versies, terug
    naar standaard, logo en varianten. Alles onder ``<data_dir>/templates/<id>/[<variant>/]``."""

    def __init__(self, renderer: DocumentRenderer, names: Iterable[str]):
        if renderer.company_dir is None:
            raise ValueError("geen administratie")
        self.r = renderer
        self.names = tuple(names) + PARTIALS

    # ---------------------------------------------------------------- paden

    def check(self, name: str, variant: str = "") -> None:
        if name not in self.names:
            raise TemplateError(N_("Onbekend sjabloon."))
        if variant and not VARIANT_RE.match(variant):
            raise TemplateError(N_("Ongeldige sjabloonvariant."))

    def folder(self, variant: str = "") -> Path:
        base = self.r.company_dir
        return base / variant if variant else base

    def path(self, name: str, variant: str = "") -> Path:
        self.check(name, variant)
        return self.folder(variant) / f"{name}.html"

    def own(self, name: str, variant: str = "") -> bool:
        return self.path(name, variant).is_file()

    def source(self, name: str, variant: str = "") -> str:
        """Tekst van het sjabloon dat nu gebruikt wordt (eigen variant, eigen, of standaard)."""
        self.check(name, variant)
        for folder in self.r.search_path(variant):
            p = folder / f"{name}.html"
            if p.is_file():
                return p.read_text(encoding="utf-8")
        raise TemplateError(N_("Onbekend sjabloon."))

    # ---------------------------------------------------------------- controleren en opslaan

    def validate(self, name: str, variant: str, text: str, sample: Optional[Dict[str, Any]] = None,
                 kind: Optional[str] = None) -> Optional[str]:
        """Controle vóór opslaan: opbouw (Jinja, sandbox) en, als er een document is, een proef-PDF.
        Geeft None of een melding met regelnummer."""
        self.check(name, variant)
        env = self.r.env(variant, {f"{name}.html": text})
        try:
            env.parse(text)
        except TemplateSyntaxError as exc:
            raise TemplateError(N_("Fout in regel {regel}: {fout}"), regel=exc.lineno, fout=exc.message or "")
        if sample is None:
            return N_("Opbouw in orde; er is nog geen document om een proef-PDF mee te maken.")
        try:
            self.r.render_pdf(kind or name, sample, overrides={f"{name}.html": text})
        except TemplateSyntaxError as exc:
            raise TemplateError(N_("Fout in regel {regel}: {fout}"), regel=exc.lineno, fout=exc.message or "")
        except Exception as exc:                     # SecurityError, UndefinedError, WeasyPrint …
            raise TemplateError(N_("Het sjabloon gaf een fout bij het maken van de proef-PDF: {fout}"),
                                fout=f"{type(exc).__name__}: {exc}"[:300])
        return None

    @staticmethod
    def decode(data: bytes) -> str:
        if len(data) > MAX_TEMPLATE:
            raise TemplateError(N_("Het sjabloon is te groot (maximaal 512 kB)."))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise TemplateError(N_("Het sjabloon moet UTF-8-tekst zijn."))
        return text.lstrip("\ufeff")

    def save(self, name: str, variant: str, text: str) -> None:
        p = self.path(name, variant)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._backup(p)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)

    def reset(self, name: str, variant: str = "") -> bool:
        """Terug naar standaard: eigen bestand weg (bewaard als vorige versie)."""
        p = self.path(name, variant)
        if not p.is_file():
            return False
        self._backup(p)
        p.unlink()
        return True

    def versions(self, name: str, variant: str = "") -> List[str]:
        p = self.path(name, variant)
        if not p.parent.is_dir():
            return []
        out = [x.name[len(p.name) + 1:] for x in p.parent.glob(f"{p.name}.*")
               if VERSION_RE.match(x.name[len(p.name) + 1:])]
        return sorted(out, reverse=True)

    def restore(self, name: str, variant: str, stamp: str) -> None:
        if not VERSION_RE.match(stamp or "") or stamp not in self.versions(name, variant):
            raise TemplateError(N_("Deze versie bestaat niet (meer)."))
        p = self.path(name, variant)
        text = (p.parent / f"{p.name}.{stamp}").read_text(encoding="utf-8")
        self.save(name, variant, text)

    def version_text(self, name: str, variant: str, stamp: str) -> str:
        if not VERSION_RE.match(stamp or "") or stamp not in self.versions(name, variant):
            raise TemplateError(N_("Deze versie bestaat niet (meer)."))
        p = self.path(name, variant)
        return (p.parent / f"{p.name}.{stamp}").read_text(encoding="utf-8")

    def _backup(self, p: Path) -> None:
        if not p.is_file():
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = p.with_name(f"{p.name}.{stamp}")
        n = 1
        while target.exists():                      # twee keer in dezelfde seconde
            target = p.with_name(f"{p.name}.{stamp[:-2]}{int(stamp[-2:]) + n:02d}")
            n += 1
        shutil.copy2(p, target)
        old = sorted((x for x in p.parent.glob(f"{p.name}.*") if VERSION_RE.match(x.name[len(p.name) + 1:])),
                     key=lambda x: x.name, reverse=True)
        for x in old[KEEP_VERSIONS:]:
            x.unlink()

    # ---------------------------------------------------------------- varianten

    def add_variant(self, code: str) -> None:
        if not VARIANT_RE.match(code or ""):
            raise TemplateError(N_("Ongeldige sjabloonvariant."))
        self.folder(code).mkdir(parents=True, exist_ok=True)

    def removable_variant(self, code: str) -> int:
        """Mag de variant weg? Met een eigen sjabloon erin niet (TemplateError); anders het aantal vorige versies
        dat mee weggaat (bevestigstap, F26 A)."""
        if not VARIANT_RE.match(code or ""):
            raise TemplateError(N_("Ongeldige sjabloonvariant."))
        d = self.folder(code)
        if not d.is_dir():
            return 0
        if any(d.glob("*.html")):
            raise TemplateError(N_("Deze variant heeft nog eigen sjablonen; zet die eerst terug naar standaard."))
        return sum(1 for x in d.glob("*.html.*") if VERSION_RE.match(x.name.split(".html.", 1)[1]))

    def remove_variant(self, code: str) -> None:
        """Variantmap weghalen, met de vorige versies erin (F26 A: na "Terug naar standaard" bleef de map anders
        voor altijd staan, want die versies blijven bewaard)."""
        self.removable_variant(code)
        d = self.folder(code)
        if d.is_dir():
            shutil.rmtree(d)

    # ---------------------------------------------------------------- logo

    def logo_file(self) -> Optional[Path]:
        for name in LOGO_NAMES:
            p = self.r.company_dir / name
            if p.is_file():
                return p
        return None

    def save_logo(self, data: bytes) -> str:
        if len(data) > MAX_LOGO:
            raise TemplateError(N_("Het logo is te groot (maximaal 1 MB)."))
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = "png"
        elif data.startswith(b"\xff\xd8\xff"):
            ext = "jpg"
        else:
            head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
            if not (head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)):
                raise TemplateError(N_("Alleen PNG, JPEG of SVG."))
            low = data.lower()
            if re.search(rb"<script|javascript:|\son\w+\s*=|<foreignobject|<!entity|(href|src)\s*=\s*[\"'](?!#|data:)",
                         low):
                raise TemplateError(N_("Dit SVG-bestand bevat scripts of verwijzingen naar buiten en is geweigerd."))
            ext = "svg"
        self.delete_logo()
        d = self.r.company_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / f"logo.{ext}").write_bytes(data)
        return ext

    def delete_logo(self) -> bool:
        found = False
        for name in LOGO_NAMES:
            p = self.r.company_dir / name
            if p.is_file():
                p.unlink()
                found = True
        return found
