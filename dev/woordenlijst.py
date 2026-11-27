"""Woordenlijst kernbegrippen NL/EN/DE/FR voor stap 2b (TL2).

Bron voor de vertalingen in ``i18n_de.py``/``i18n_fr.py`` en voor de toets "geen Nederlandse woorden
uit de woordenlijst op een Duitse/Franse pagina" (TL8). Maakt met ``--xlsx <pad>`` het overzicht ter
beoordeling, met de SQL-Ledger-vertaling (``locale/de``, ``locale/fr``) als naslag.
"""

from __future__ import annotations

# (groep, Nederlands, Engels-sleutel in SL (voor de naslagkolom, of ""), Duits, Frans, toelichting)
TERMS = [
    # --- Algemeen en grootboek
    ("Grootboek", "Administratie", "Company", "Mandant", "Dossier",
     "Gangbaar in Duitse (DATEV) en Franse boekhoudsoftware voor 'een boekhouding'. 'Firma'/'Société' kan ook, maar botst met de bedrijfsgegevens."),
    ("Grootboek", "Boeking", "Transaction", "Buchung", "Écriture", "SL-fr 'Transaction' is een anglicisme."),
    ("Grootboek", "Boeken", "Post", "Buchen", "Comptabiliser", "Knop/werkwoord."),
    ("Grootboek", "Geboekt", "Posted", "Gebucht", "Comptabilisé", ""),
    ("Grootboek", "Memoriaal", "General Ledger", "Umbuchung", "Opération diverse (OD)",
     "Handmatige journaalpost. FR 'OD' is het vaste begrip; DE 'Umbuchung' (DATEV: Dialogbuchung)."),
    ("Grootboek", "Grootboek", "General Ledger", "Hauptbuch", "Grand livre", ""),
    ("Grootboek", "Rekening", "Account", "Konto", "Compte", "Grootboekrekening; 'Sachkonto' waar onderscheid met debiteur/crediteur nodig is."),
    ("Grootboek", "Rekeningschema", "Chart of Accounts", "Kontenplan", "Plan comptable", "SL-de heeft een tikfout ('Kontenbersicht')."),
    ("Grootboek", "Rekeningmutaties", "", "Kontoblatt", "Extrait de compte", "Rapport met alle mutaties van een rekening."),
    ("Grootboek", "Kop", "Heading", "Überschrift", "Titre", "Kop in het rekeningschema."),
    ("Grootboek", "Omschrijving", "Description", "Beschreibung", "Libellé",
     "FR: 'Libellé' bij boekingen; op documenten bij artikelregels 'Désignation' (al zo in stap 2)."),
    ("Grootboek", "Kenmerk", "Reference", "Belegnummer", "N° de pièce", "Referentie van een boeking."),
    ("Grootboek", "Boekdatum", "", "Buchungsdatum", "Date comptable", ""),
    ("Grootboek", "Periode", "Period", "Zeitraum", "Période", ""),
    ("Grootboek", "Debet", "Debit", "Soll", "Débit", ""),
    ("Grootboek", "Credit", "Credit", "Haben", "Crédit", ""),
    ("Grootboek", "Saldo", "Balance", "Saldo", "Solde", ""),
    ("Grootboek", "Proefbalans", "Trial Balance", "Summen- und Saldenliste", "Balance générale",
     "SL-de 'Saldenbilanz' is ongebruikelijk; 'SuSa' is de Duitse standaard."),
    ("Grootboek", "Winst en verlies", "Income Statement", "Gewinn- und Verlustrechnung", "Compte de résultat", ""),
    ("Grootboek", "Balans", "Balance Sheet", "Bilanz", "Bilan", ""),
    ("Grootboek", "Opbrengsten", "Income", "Erträge", "Produits", ""),
    ("Grootboek", "Kosten", "Expense", "Aufwendungen", "Charges", ""),
    ("Grootboek", "Omzet", "Sales", "Umsatz", "Chiffre d'affaires", ""),
    ("Grootboek", "Afdeling", "Department", "Abteilung", "Département",
     "FR niet 'Service': dat botst met 'Dienst' (prestation) en is in SL-fr dubbel ('Service-Département')."),
    ("Grootboek", "Project", "Project", "Projekt", "Projet", ""),
    ("Grootboek", "Terugkerende boekingen", "Recurring Transactions", "Wiederkehrende Buchungen", "Écritures récurrentes",
     "SL-fr 'ABONNEMENTS' (hoofdletters, andere betekenis)."),
    ("Grootboek", "Herhaalschema", "", "Wiederholungsplan", "Planification", ""),
    ("Grootboek", "Afsluitdatum", "Closing Date", "Festschreibungsdatum", "Date de clôture",
     "Tot deze datum kan niet meer geboekt worden. DE 'Festschreibung' is de GoBD-term."),
    ("Grootboek", "Jaarafsluiting", "Yearend", "Jahresabschluss", "Clôture de l'exercice", ""),
    ("Grootboek", "Audittrail", "Audit Trail", "Änderungsprotokoll", "Piste d'audit", ""),

    # --- Btw en valuta
    ("Btw en valuta", "Btw", "Tax", "Umsatzsteuer (USt.)", "TVA",
     "SL-de 'MWST' is Zwitsers/Oostenrijks; in Duitsland 'USt.'."),
    ("Btw en valuta", "Btw verkoop", "", "Umsatzsteuer", "TVA collectée", ""),
    ("Btw en valuta", "Btw inkoop", "", "Vorsteuer", "TVA déductible", ""),
    ("Btw en valuta", "Btw-nummer", "Tax Number", "USt-IdNr.", "N° de TVA", "Zoals al op de documenten (stap 2)."),
    ("Btw en valuta", "Btw verlegd", "Reverse Charge", "Steuerschuldnerschaft des Leistungsempfängers", "Autoliquidation",
     "Wettelijke vermelding; zoals op de documenten."),
    ("Btw en valuta", "Tarief", "Rate", "Steuersatz", "Taux", "Btw-tarief."),
    ("Btw en valuta", "Onbelaste omzet", "", "Steuerfreie Umsätze", "Chiffre d'affaires exonéré", ""),
    ("Btw en valuta", "Valuta", "Currency", "Währung", "Devise", ""),
    ("Btw en valuta", "Standaardvaluta", "Default Currency", "Standardwährung", "Devise par défaut", ""),
    ("Btw en valuta", "Koers", "Exchange Rate", "Wechselkurs", "Taux de change", ""),
    ("Btw en valuta", "Koersverschil", "Exchange Difference", "Kursdifferenz", "Écart de change", ""),

    # --- Relaties
    ("Relaties", "Relatie", "", "Geschäftspartner", "Tiers",
     "FR 'Tiers' is het vaste boekhoudbegrip voor klant/leverancier."),
    ("Relaties", "Klant", "Customer", "Kunde", "Client", "SL-fr 'Tiers-Client' is te omslachtig."),
    ("Relaties", "Leverancier", "Vendor", "Lieferant", "Fournisseur", ""),
    ("Relaties", "Debiteuren", "AR", "Debitoren", "Comptes clients", ""),
    ("Relaties", "Crediteuren", "AP", "Kreditoren", "Comptes fournisseurs", ""),
    ("Relaties", "Contactpersoon", "Contact", "Ansprechpartner", "Interlocuteur", ""),
    ("Relaties", "Verzendadres", "Shipping Address", "Lieferadresse", "Adresse de livraison", "Zoals 'Afleveradres' op documenten."),
    ("Relaties", "Betalingstermijn", "Terms", "Zahlungsziel", "Délai de paiement", ""),
    ("Relaties", "Kredietlimiet", "Credit Limit", "Kreditlimit", "Encours autorisé", ""),
    ("Relaties", "Branche", "Type of Business", "Branche", "Secteur d'activité", ""),

    # --- Verkoop en inkoop
    ("Verkoop en inkoop", "Verkoop", "Sales", "Verkauf", "Ventes", "Menu."),
    ("Verkoop en inkoop", "Inkoop", "Purchases", "Einkauf", "Achats", "Menu."),
    ("Verkoop en inkoop", "Factuur", "Invoice", "Rechnung", "Facture", ""),
    ("Verkoop en inkoop", "Verkoopfactuur", "Sales Invoice", "Ausgangsrechnung", "Facture de vente", ""),
    ("Verkoop en inkoop", "Inkoopfactuur", "Vendor Invoice", "Eingangsrechnung", "Facture fournisseur", ""),
    ("Verkoop en inkoop", "Creditfactuur", "Credit Invoice", "Rechnungskorrektur", "Avoir",
     "LET OP: documenten stap 2 zeggen 'Gutschrift'. In Duitsland betekent 'Gutschrift' sinds 2013 (§ 14 UStG) een "
     "factuur die de afnemer zelf opstelt; voor een correctie is 'Rechnungskorrektur' juist. Voorstel: documenten mee aanpassen."),
    ("Verkoop en inkoop", "Creditnota", "Credit Note", "Rechnungskorrektur", "Avoir",
     "Zelfde begrip als creditfactuur, zonder artikelen; zie daar."),
    ("Verkoop en inkoop", "Debetnota", "Debit Note", "Belastungsanzeige", "Note de débit",
     "LET OP: documenten stap 2 (en SL) zeggen 'Lastschrift' = automatische incasso. Voorstel: documenten mee aanpassen."),
    ("Verkoop en inkoop", "Inkoopboeking", "AP Transaction", "Eingangsbuchung", "Écriture d'achat", ""),
    ("Verkoop en inkoop", "Factuurdatum", "Invoice Date", "Rechnungsdatum", "Date de facture", ""),
    ("Verkoop en inkoop", "Vervaldatum", "Due Date", "Fälligkeitsdatum", "Date d'échéance", ""),
    ("Verkoop en inkoop", "Leverdatum", "", "Lieferdatum", "Date de livraison", ""),
    ("Verkoop en inkoop", "Openstaand", "Outstanding", "Offene Posten", "Non soldé", "Menu: openstaande posten."),
    ("Verkoop en inkoop", "Ouderdomsanalyse", "", "Fälligkeitsanalyse", "Balance âgée", "FR 'Balance âgée' is het vaste begrip."),
    ("Verkoop en inkoop", "Aanmaning", "Reminder", "Mahnung", "Relance", ""),
    ("Verkoop en inkoop", "Betaling", "Payment", "Zahlung", "Paiement",
     "SL-de 'Belastung' is fout (= belasting/last). Uitgaand: 'Zahlungsausgang' waar nodig."),
    ("Verkoop en inkoop", "Ontvangst", "Receipt", "Zahlungseingang", "Encaissement", "SL-de 'Quittung' = kwitantie, fout in deze betekenis."),
    ("Verkoop en inkoop", "Vooruitbetaling", "", "Geleistete Anzahlung", "Acompte versé", ""),
    ("Verkoop en inkoop", "Vooruitontvangst", "", "Erhaltene Anzahlung", "Acompte reçu", ""),
    ("Verkoop en inkoop", "Korting", "Discount", "Rabatt", "Remise", ""),
    ("Verkoop en inkoop", "Contante korting", "Cash Discount", "Skonto", "Escompte", ""),
    ("Verkoop en inkoop", "Betaalmethode", "Payment Method", "Zahlungsart", "Mode de paiement", ""),

    # --- Bank en afletteren
    ("Bank en afletteren", "Afletteren", "Reconciliation", "Kontenabstimmung", "Rapprochement",
     "Correctie op TL3 (daar 'Ausgleich'/'lettrage'): ons Afletteren is het afstemmen van een rekening met het "
     "afschrift (rc.pl). Dat heet 'Abstimmung'/'rapprochement'; 'lettrage' is facturen aan betalingen koppelen."),
    ("Bank en afletteren", "Afgeletterd", "Reconciled", "Abgestimmt", "Rapproché", ""),
    ("Bank en afletteren", "Bankafschrift", "Bank Statement", "Kontoauszug", "Relevé bancaire", ""),
    ("Bank en afletteren", "Afschriftsaldo", "Statement Balance", "Auszugssaldo", "Solde du relevé", ""),
    ("Bank en afletteren", "Bankregel", "", "Kontoumsatz", "Ligne de relevé", "Eén regel van een ingelezen afschrift."),
    ("Bank en afletteren", "Vaste bankregel", "Bank Rule", "Buchungsregel", "Règle d'affectation", "Regel die een bankregel automatisch boekt."),
    ("Bank en afletteren", "Koppelen", "", "Zuordnen", "Associer", "Bankregel aan factuur of boeking."),
    ("Bank en afletteren", "Voorstel", "", "Vorschlag", "Proposition", ""),

    # --- Orders en offertes
    ("Orders en offertes", "Verkooporder", "Sales Order", "Kundenauftrag", "Commande client",
     "Het scherm; het document heet Orderbevestiging (Auftragsbestätigung)."),
    ("Orders en offertes", "Orderbevestiging", "", "Auftragsbestätigung", "Confirmation de commande", "Zoals op de documenten."),
    ("Orders en offertes", "Inkooporder", "Purchase Order", "Bestellung", "Bon de commande", "Zoals op de documenten."),
    ("Orders en offertes", "Offerte", "Quotation", "Angebot", "Devis", "SL-de 'Offerte' is Zwitsers."),
    ("Orders en offertes", "Offerteaanvraag", "Request for Quotation", "Angebotsanfrage", "Demande de prix", "Zoals op de documenten."),
    ("Orders en offertes", "Nabestelling", "Backorder", "Rückstand", "Reliquat", "FR 'Reliquat' is het vaste begrip; SL-fr 'En souffrance' klopt niet."),
    ("Orders en offertes", "Pakbon", "Packing List", "Lieferschein", "Bon de livraison", "Zoals op de documenten."),
    ("Orders en offertes", "Picklijst", "Pick List", "Kommissionierliste", "Liste de préparation", "Zoals op de documenten; SL-de 'Lagerliste'."),
    ("Orders en offertes", "Geleverd", "Shipped", "Geliefert", "Livré", ""),
    ("Orders en offertes", "Gefactureerd", "Invoiced", "Fakturiert", "Facturé", ""),
    ("Orders en offertes", "Geldig tot", "", "Gültig bis", "Valable jusqu'au", ""),

    # --- Artikelen en voorraad
    ("Artikelen", "Artikel", "Part", "Artikel", "Article", "SL-de 'Ware', SL-fr 'Produit'."),
    ("Artikelen", "Dienst", "Service", "Dienstleistung", "Prestation", ""),
    ("Artikelen", "Samengesteld artikel", "Assembly", "Baugruppe", "Article composé",
     "SL-de 'Erzeugnis' kan ook (productieterm); 'Baugruppe' is herkenbaarder voor samengestelde artikelen."),
    ("Artikelen", "Stuklijst", "Bill of Materials", "Stückliste", "Nomenclature", ""),
    ("Artikelen", "Onderdeel", "", "Komponente", "Composant", "Onderdeel van een samengesteld artikel."),
    ("Artikelen", "Assembleren", "", "Fertigen", "Assembler", ""),
    ("Artikelen", "Demonteren", "", "Zerlegen", "Désassembler", ""),
    ("Artikelen", "Productiehistorie", "", "Fertigungshistorie", "Historique de production", ""),
    ("Artikelen", "Artikelgroep", "Group", "Warengruppe", "Famille d'articles", ""),
    ("Artikelen", "Voorraad", "On Hand", "Lagerbestand", "Stock", ""),
    ("Artikelen", "Voorraadcorrectie", "", "Bestandskorrektur", "Ajustement de stock", ""),
    ("Artikelen", "Kostprijs", "Cost", "Einstandspreis", "Prix de revient", "Kostprijs per artikel; kostprijs omzet = 'Wareneinsatz' / 'Coût des ventes'."),
    ("Artikelen", "Verkoopprijs", "Sell Price", "Verkaufspreis", "Prix de vente", ""),
    ("Artikelen", "Inkoopprijs", "Last Cost", "Einkaufspreis", "Prix d'achat", ""),
    ("Artikelen", "Lijstprijs", "List Price", "Listenpreis", "Prix catalogue", ""),
    ("Artikelen", "Eenheid", "Unit", "Einheit", "Unité", ""),
    ("Artikelen", "Locatie", "Bin", "Lagerplatz", "Emplacement", "Zoals op de picklijst."),
    ("Artikelen", "Serienummer", "Serial No.", "Seriennummer", "Numéro de série", ""),
    ("Artikelen", "Aantal", "Qty", "Menge", "Quantité", "Op documenten afgekort 'Qté'."),
    ("Artikelen", "Regel", "Line", "Position", "Ligne", "Regel op een factuur/order/boeking."),
    ("Artikelen", "Bedrag", "Amount", "Betrag", "Montant", ""),
    ("Artikelen", "Totaal", "Total", "Summe", "Total", "Op documenten DE 'Gesamt' (staat al zo)."),

    # --- Documenten en mail
    ("Documenten en mail", "Sjabloon", "Template", "Vorlage", "Modèle", "SL-de 'Schablone' is ouderwets."),
    ("Documenten en mail", "Sjabloonvariant", "", "Vorlagenvariante", "Variante de modèle", ""),
    ("Documenten en mail", "Documenttaal", "", "Belegsprache", "Langue des documents", ""),
    ("Documenten en mail", "Afdrukken", "Print", "Drucken", "Imprimer", ""),
    ("Documenten en mail", "Afzender", "", "Absender", "Expéditeur", ""),
    ("Documenten en mail", "Handtekening", "Signature", "Signatur", "Signature",
     "E-mailhandtekening; op de pakbon (met de hand) 'Unterschrift', zoals op de documenten."),
    ("Documenten en mail", "Bijlage", "Attachment", "Anhang", "Pièce jointe", ""),

    # --- Beheer, systeem en bediening
    ("Beheer en systeem", "Beheer", "", "Verwaltung", "Administration", "Menu (instellingen van een administratie)."),
    ("Beheer en systeem", "Systeem", "System", "System", "Système", ""),
    ("Beheer en systeem", "Instellingen", "Preferences", "Einstellungen", "Paramètres", ""),
    ("Beheer en systeem", "Mijn instellingen", "", "Meine Einstellungen", "Mes paramètres", ""),
    ("Beheer en systeem", "Gebruiker", "User", "Benutzer", "Utilisateur", ""),
    ("Beheer en systeem", "Beheerder", "Administrator", "Administrator", "Administrateur", "Rol."),
    ("Beheer en systeem", "Boekhouder", "", "Buchhalter", "Comptable", "Rol."),
    ("Beheer en systeem", "Logboek", "", "Protokoll", "Journal d'activité", "Niet 'Journal' alleen: botst met dagboek."),
    ("Beheer en systeem", "Locks", "", "Sperren", "Verrous", "Boekingen die in gebruik zijn."),
    ("Beheer en systeem", "Op slot", "Locked", "Gesperrt", "Verrouillé", ""),
    ("Beheer en systeem", "Inactief", "Inactive", "Inaktiv", "Inactif", ""),
    ("Beheer en systeem", "Talen", "Languages", "Sprachen", "Langues", "SL-fr 'Langage' is fout (= programmeertaal/taalgebruik)."),
    ("Beheer en systeem", "Inloggen", "Login", "Anmelden", "Se connecter", ""),
    ("Beheer en systeem", "Uitloggen", "Logout", "Abmelden", "Se déconnecter", ""),
    ("Beheer en systeem", "Wachtwoord", "Password", "Passwort", "Mot de passe", ""),
    ("Beheer en systeem", "Tweestapsverificatie", "", "Zwei-Faktor-Authentifizierung", "Authentification à deux facteurs", ""),
    ("Beheer en systeem", "Sessie", "Session", "Sitzung", "Session", ""),
    ("Beheer en systeem", "Opslaan", "Save", "Speichern", "Enregistrer", "Knop."),
    ("Beheer en systeem", "Toevoegen", "Add", "Anlegen", "Ajouter",
     "DE 'anlegen' voor gegevens (Kunde anlegen), 'hinzufügen' voor regels in een formulier."),
    ("Beheer en systeem", "Wijzigen", "Edit", "Bearbeiten", "Modifier", ""),
    ("Beheer en systeem", "Verwijderen", "Delete", "Löschen", "Supprimer", ""),
    ("Beheer en systeem", "Zoeken", "Search", "Suchen", "Rechercher", ""),
    ("Beheer en systeem", "Overzicht", "", "Übersicht", "Liste", "Menu-item (lijst van boekingen/artikelen)."),
    ("Beheer en systeem", "Vrijgeven", "", "Freigeben", "Libérer", "Een lock opheffen."),
]


def _sl_lookup() -> dict:
    import glob
    import os
    import re
    out = {}
    for lang in ("de", "fr"):
        d = {}
        for f in sorted(glob.glob(os.path.join(os.path.expanduser(os.environ.get("SRC", "~/sqlledger-test/sqlledger_src/sql-ledger")), "locale", lang, "*"))):
            try:
                t = open(f, encoding="utf-8").read()
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            for m in re.finditer(r"^\s*'((?:[^'\\]|\\.)*)'\s*=>\s*'((?:[^'\\]|\\.)*)'", t, re.M):
                d.setdefault(m.group(1).replace("\\'", "'"), m.group(2).replace("\\'", "'"))
        out[lang] = d
    return out


def write_xlsx(path: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    from sqlledger_web.i18n_en import EN

    sl = _sl_lookup()
    wb = Workbook()
    ws = wb.active
    ws.title = "Woordenlijst"
    head = ["Nr", "Groep", "Nederlands", "Engels (app)", "Duits", "Frans", "SL Duits", "SL Frans",
            "Toelichting", "Akkoord? (ja/nee)", "Jouw voorstel / opmerking"]
    ws.append(head)
    font = Font(name="Arial", size=10)
    bold = Font(name="Arial", size=10, bold=True)
    flag = PatternFill("solid", fgColor="FFF2CC")
    edit = PatternFill("solid", fgColor="E2EFDA")
    for i, (group, nl, slkey, de, fr, note) in enumerate(TERMS, 1):
        en = EN.get(nl, slkey or "")
        sl_de = sl["de"].get(slkey, "") if slkey else ""
        sl_fr = sl["fr"].get(slkey, "") if slkey else ""
        ws.append([i, group, nl, en, de, fr, sl_de, sl_fr, note, "", ""])
        r = ws.max_row
        if note.startswith(("LET OP", "Correctie")):
            for c in range(1, 10):
                ws.cell(r, c).fill = flag
    for row in ws.iter_rows():
        for c in row:
            c.font = bold if c.row == 1 else font
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for r in range(2, ws.max_row + 1):
        ws.cell(r, 10).fill = edit
        ws.cell(r, 11).fill = edit
    dv = DataValidation(type="list", formula1='"ja,nee"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"J2:J{ws.max_row}")
    for col, w in zip("ABCDEFGHIJK", (5, 18, 22, 22, 26, 26, 20, 20, 60, 12, 30)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:K{ws.max_row}"

    info = wb.create_sheet("Uitleg")
    for line in [
        "Woordenlijst stap 2b (TL2): kernbegrippen die overal in de schermen hetzelfde moeten heten.",
        "Duits = Duitsland, 'Sie'; Frans = Frankrijk, 'vous'; knoppen in de infinitief (TL3).",
        "SL Duits / SL Frans = wat SQL-Ledger zegt (alleen naslag); leeg = SL kent het begrip niet.",
        "Gele regels: wijkt af van wat al gebouwd is of van de analyse (zie toelichting).",
        "Groene kolommen: vul 'ja' of 'nee' in; bij nee je voorstel in de laatste kolom. Leeg = akkoord.",
    ]:
        info.append([line])
        info.cell(info.max_row, 1).font = font
    info.column_dimensions["A"].width = 110
    wb.save(path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--xlsx":
        write_xlsx(sys.argv[2])
        print(f"{len(TERMS)} begrippen -> {sys.argv[2]}")
    else:
        print(__doc__)
