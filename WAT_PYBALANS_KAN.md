# Wat PyBalans kan

*Overzicht voor ondernemers en boekhouders — stand 16 oktober 2026 (`main`, na v1.4.0)*

PyBalans is een boekhoudprogramma dat in de browser werkt. Het leest SQL-Ledger-administraties in en werkt daarna
op dezelfde gegevens verder: wat in SQL-Ledger stond, blijft er staan. Waar SQL-Ledger fouten had, rekent PyBalans
het goed; die verschillen staan in de README onder "Bewuste afwijkingen".

---

## 1. Grootboek

- **Memoriaalboekingen**: boeken, wijzigen (na bevestiging), verwijderen, "opslaan als nieuw"; totalen en verschil
  zie je meteen. Ook in vreemde valuta, en een aparte **koersverschilboeking**.
- **Boekingen zoeken** (journaal) met alle zoekopties van SQL-Ledger, kolommen naar keuze, lopend saldo per rekening
  en doorklikken naar de boeking.
- **Terugkerende boekingen**: memoriaal, verkoop- en inkoopboekingen, facturen en orders laten herhalen (elke n
  dagen/weken/maanden/jaren, een aantal keer), eventueel meteen mailen of als PDF. Niet voor offertes en kassabonnen.
- **Boekperiode afsluiten** (afsluitdatum) en **jaarafsluiting** met een voorbeeld van precies wat er geboekt wordt.
- **Boekjaar** dat niet in december eindigt is instelbaar.
- **Afdelingen** en **projecten** (kostenplaatsen / kostendragers).
- **Valuta en koersen**, met een vaste rekening voor koersverschillen.

## 2. Verkoop en inkoop

- **Verkoop- en inkoopboekingen zonder artikelen** (bijvoorbeeld één regel "advieswerk" of een kostenfactuur), en
  creditnota's.
- **Facturen met artikelen** (verkoop en inkoop, met creditfactuur/debetnota): korting per regel, btw per regel,
  prijzen inclusief of exclusief btw, valuta, afleveradres, notities, betaalkenmerk.
- **Inkoopfactuur uploaden**: een PDF of foto van de factuur slepen; naast de factuur vul je leverancier, datum,
  nummer en totaalbedrag in. PyBalans stelt kostenrekening, btw en vervaldatum voor op basis van eerdere boekingen.
  De PDF blijft als bijlage bij de boeking.
- **Klanten en leveranciers**: adressen, contactpersoon, bankgegevens, btw-nummer, standaardrekeningen,
  betalingstermijn, kredietlimiet; zoeken met veel filters, ongebruikte relaties opruimen.
- **Overzichten**: openstaande posten, overzicht verkoop/inkoop, ouderdomsanalyse debiteuren en crediteuren.

## 3. Orders en offertes

- **Verkooporders, inkooporders, offertes en offerteaanvragen** op één soort scherm.
- **Omzetten**: offerte → verkooporder, aanvraag → inkooporder (en terug), en order of offerte → factuur.
- **Deellevering**: bij het factureren de aantallen verlagen of regels weghalen; het restant wordt automatisch een
  **nabestelling**. Gefactureerde orders zijn gekoppeld aan bron, factuur en nabestelling.
- Orders heropenen, lijsten met status open / gesloten / gefactureerd / nabestelling.

## 4. Artikelen, voorraad en productie

- **Artikelen en diensten**: prijzen, eenheid, groep, barcode, bestelniveau, prijshistorie, voorraadcorrectie.
- **Voorraad** beweegt met de facturen; **kostprijs volgens FIFO** (first in, first out), ook bij retouren.
- **Samengestelde artikelen** met een stuklijst (tot 5 niveaus): prijzen uit de onderdelen,
  **assembleren en demonteren**, "hoeveel kan ik maken", stuklijsten uitgeklapt, "waar gebruikt" en
  **productiehistorie**. Het menu Productie verschijnt zodra er samengestelde artikelen zijn.

## 5. Betalingen en debiteurenbeheer

- **Ontvangsten en betalingen** per relatie of voor meerdere relaties tegelijk, met contante korting (inclusief
  btw-correctie), koersverschil en vooruitbetaling. Betalingen **corrigeren** (storneren) of opnieuw uitgeven.
- **Aanmaningen** in drie niveaus en **rekeningoverzichten**: als PDF of per mail naar de klant, in de taal van de
  klant.
- **Betalingsspecificatie** per betaling en een **betalingsregister**.

## 6. Bank

- **Bankafschrift inlezen**: CAMT.053 en MT940. De bankrekening wordt herkend aan het IBAN; dubbel ingelezen regels
  worden overgeslagen; het saldo wordt gecontroleerd.
- **Bankregels verwerken**: per regel een voorstel (betaalkenmerk, factuurnummer, IBAN van de relatie, bestaande
  boeking of een vaste regel). Boeken als betaling, als memoriaal (met btw), koppelen aan een bestaande boeking of
  negeren. "Alle zekere voorstellen boeken" in één keer.
- **Vaste bankregels** voor terugkerende posten (huur, bankkosten …).
- Alles wat vanuit de bank geboekt of gekoppeld wordt is meteen **afgeletterd**. Daarnaast het gewone
  **afletteren** van een rekening en een overzicht afgeletterde posten.
- **SEPA-betaalbestand** (pain.001) van te betalen inkoopfacturen in euro, om bij de bank te uploaden. Er wordt niets
  geboekt; het bankafschrift lettert de betaling later af.

## 7. Btw

- **Btw-aangifte Nederland** (rubrieken 1a–5g), **Verenigd Koninkrijk** (VAT Return, 9 boxes) en **België**
  (periodieke aangifte, plus de jaarlijkse **klantenlisting**). Altijd in **hele bedragen** (afgerond in uw
  voordeel), met doorklikken naar de boekingen en meldingen bij afwijkingen. Formulier als PDF, details in Excel.
  De btw-boeking (afdracht) maakt PyBalans zelf; daarna is het tijdvak vastgelegd.
- **ICP-opgaaf** (Nederland): per afnemer, uit dezelfde boekingen als rubriek 3b.
- **Btw-rubrieken**: per btw-rekening instellen in welke rubriek die valt (met voorstel).
- **Btw-rapporten**: btw verkoop, btw inkoop, onbelaste omzet en onbelaste inkoop.
- Op het dashboard een herinnering als de aangifte van het laatste tijdvak nog open staat.
- Let op: PyBalans **dient niet zelf digitaal in**. De bedragen neemt u over in het portaal van de Belastingdienst /
  SPF Finances; voor het VK maakt PyBalans een bestand (CSV/Excel) voor *bridging software* (Making Tax Digital).

## 8. Rapporten

- **Proefbalans**, **winst-en-verliesrekening** (per maand/kwartaal, vergelijking met vorig jaar, kas- of
  factuurstelsel, ook als **grafiek**), **balans**, rekeningschema met saldi, rekeningmutaties,
  **ouderdomsanalyse**, ontvangsten/betalingen, aanmaningslijst, **uren per project**.
- Elk rapport en elke lijst kan naar **PDF, Excel en CSV**.
- **Overzicht bijlagen**: alle bijlagen in de administratie op één plek.

## 9. Uren

- **Tijdkaarten**: uren per medewerker, project en dienst (ook met begin- en eindtijd), declarabel of niet.
- **Mijn uren**: een maandraster waarin een medewerker zelf zijn uren invult; **Maandstaat**: hetzelfde voor de
  boekhouder, voor elke medewerker.
- **Toewijzingen**: welke medewerker op welk project en welke dienst werkt, met eigen prijs en projectleider.
- **Interne uren**: projecten als *klantproject* of *intern project* (op een kostenplaats), **interne tarieven**
  per medewerker; het rapport Uren per project toont kostwaarde, verkoopwaarde en marge.
- **Verkooporders uit uren**: open uren aanvinken → per klant één verkooporder, die u daarna omzet naar een factuur.
- **Urenstaat (PDF)** per maand of per project, met een aftekenvak voor de klant.
- Rol **urenschrijver**: iemand die alleen zijn eigen uren mag schrijven en verder niets ziet.

## 10. Documenten en e-mail

- PDF's van facturen, creditnota's, orderbevestigingen, pakbonnen, picklijsten, offertes, aanvragen, aanmaningen,
  rekeningoverzichten, betalingsspecificaties en urenstaten (14 documentsoorten).
- **Huisstijl**: logo (grootte en plaats), accentkleur, lettertype, adresblok links of rechts (venstervelop NL/DE of
  CH), kop- en voetregel aan of uit.
- **Documentinstellingen** (vaste teksten, wat er op het document staat) en **eigen sjablonen** (downloaden,
  aanpassen, uploaden met controle, terug naar standaard, vorige versies).
- **Documenttaal**: Nederlands, Engels, Duits of Frans, per administratie en per klant.
- **E-mail** via uw eigen mailserver, met standaardteksten per documentsoort en taal; "testmail naar mijzelf".
- **Batch**: veel documenten tegelijk als één PDF of als losse mails.
- **Bijlagen** bij boekingen, facturen, orders, relaties en artikelen; foto's worden automatisch verkleind en
  rechtgezet, dubbele bestanden worden gemeld.

## 11. Rekeningschema's en inrichting

- **Nieuwe administratie** met een standaardrekeningschema: Nederland (Eenmanszaak/vof, BV, Basis), België (MKB),
  Frankrijk (PCG abrégé), Verenigd Koninkrijk (Limited company), Zwitserland (KMU/PME/PMI), of een leeg schema
  (ook Duitsland, met SKR03-nummers). Inclusief btw-tarieven.
- **RGS-koppeling** (Nederland): RGS-lijst inlezen en rekeningen koppelen.
- **GIFI-indeling** beheren.
- **Importeren** uit CSV of Excel: klanten, leveranciers, artikelen, diensten, artikelgroepen, rekeningen,
  GIFI-codes, memoriaalboekingen, verkoopfacturen, verkooporders, inkooporders en betalingen — eerst een proefrun
  met wat er zou gebeuren, dan pas echt. **Exporteren** naar CSV/Excel.

## 12. Meerdere administraties, gebruikers en beveiliging

- **Meerdere administraties** naast elkaar, met een **keuzemenu om te wisselen** (recent gebruikt bovenaan).
- **Gebruikers en rollen**: systeembeheerder, en per administratie beheerder, boekhouder, alleen-lezen of
  urenschrijver. Onderdelen (modules) zijn per administratie aan of uit te zetten.
- **Tweestapsverificatie** (code-app) verplicht voor beheerders, optioneel voor anderen.
- Blokkade na herhaalde verkeerde wachtwoorden, automatisch uitloggen na een uur niets doen.
- **Audittrail** (wie wijzigde welke boeking) en **logboek** (alle inlog- en beheeracties, met IP-adres).
- **Mijn instellingen** (⚙ bij uw naam): schermtaal, getal- en datumnotatie, handtekening voor e-mails.

## 13. Back-up en overstap

- **Back-up maken** per administratie of alles tegelijk, ook automatisch elke nacht in te plannen. Neemt alles mee,
  ook bijlagen, eigen sjablonen en logo.
- **Back-up inlezen** van:
  - **SQL-Ledger** tot en met 3.2.12 (ook oude versies zoals 2.8; die worden bijgewerkt);
  - **Run my Accounts / ledger123** (ook ingepakt als `.bz2`): de gewone gegevens gaan de boekhouding in, de extra
    gegevens van Run my Accounts worden apart bewaard en zijn te bekijken bij *Beheer > Run my Accounts-gegevens*.
    Na het inlezen controleert PyBalans dat proefbalans en openstaande posten exact gelijk zijn gebleven; anders
    wordt er niets ingelezen;
  - PyBalans zelf.
- Een database van **SQL-Ledger 4.x** kunt u koppelen, maar die versie wordt **niet ondersteund** (gebruik op eigen
  risico); een back-up van 4.x inlezen kan niet.
- **Overstaplijst** (*Beheer > Ingebruikname* en op het dashboard): wat er na het inlezen nog te doen is (btw,
  bank, huisstijl, nummering, gebruikers, back-up …), met vinkjes die PyBalans waar mogelijk zelf zet, en een
  **controleblad** om de cijfers naast de laatste cijfers uit SQL-Ledger te leggen.
- **Terug naar SQL-Ledger** kan (als het echt moet): de PyBalans-back-up in een nieuwe database zetten en die in
  SQL-Ledger koppelen. Wat alleen PyBalans kent (bankafschriften, bijlagen, btw-rubrieken) ziet SQL-Ledger dan niet.

## 14. Gebruiksgemak

- **Eerste start**: een wizard leidt u door taal, beheerder, tweestapsverificatie, eerste administratie (nieuw,
  back-up inlezen of bestaande database koppelen) en gebruikers.
- **Dashboard**: saldo bank en kas, te ontvangen en te betalen (naar ouderdom), resultaat dit jaar tegenover vorig
  jaar, en een **te-doen-lijst** (btw-aangifte, bankregels, vervallen facturen, terugkerende boekingen, artikelen
  onder bestelniveau, orders over hun leverdatum).
- **Talen**: het hele programma in Nederlands, Engels, Duits en Frans, per gebruiker.
- **Help op elk scherm** ("Hoe werkt het?"), ook in de vier talen, met waar nodig wat anders is dan in SQL-Ledger.
- Werkt ook op tablet en telefoon.
- **Docker**: installeren met Docker (programma + database in één keer). Een handleiding voor Windows (via Docker
  Desktop) staat nog op de planning.

---

## Wat (nog) niet kan

| Onderwerp | Stand |
|---|---|
| Kassa / POS (kassabonnen) | Niet gebouwd. Bestaande kassabonnen uit SQL-Ledger zijn alleen te bekijken. |
| Salarisadministratie | Niet aanwezig. |
| Magazijnen (voorraad per magazijn) | Niet gebouwd. Bij een Run my Accounts-back-up blijft de voorraad per magazijn bewaard, maar telt PyBalans alleen de totale voorraad per artikel (later: RM4). |
| Kits, gewicht/vracht, bulk "facturen genereren" en "samenvoegen" | Niet gebouwd. |
| Btw-aangifte Zwitserland, Duitsland en Frankrijk | Nog niet; alleen NL, VK en BE. Zwitserland komt later apart. |
| Digitaal indienen bij de belastingdienst | Niet: bedragen overnemen in het portaal (VK: bestand voor bridging software). |
| Terug naar Run my Accounts | Geen weg terug: inlezen gaat één kant op (de originele back-up blijft natuurlijk bestaan). |
| SQL-Ledger 4.x | Niet ondersteund: koppelen kan op eigen risico, een back-up inlezen niet. |
| Uren direct naar een factuur | Gaat via een verkooporder: uren → verkooporder → factuur. |
| SEPA in andere valuta dan euro, of incasso | Alleen betalingen in euro. |
| Eenvoudige versie (profiel Eenvoudig / Volledig) | Gepland, nog niet gebouwd. |
