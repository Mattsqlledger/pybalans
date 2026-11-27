# Fase 4 — analyse en beslispunten (2026-10-09)

Onderdelen volgens plan: documenten (printen/PDF/mailen), betalingsregister, import. Plus alles wat eerder naar
fase 4 is doorgeschoven: R2 (facturen/orders/offertes/pakbonnen printen en mailen), R6 (rapporten als PDF),
R7 (aanmaningen opslaan/printen/mailen), T8 (kopieën printen/mailen bij herhalen), P13 (betalingen printen),
M9 (uploads bij boekingen), AT8 (artikelimport), I10 (CSV-betalingsimport), documenttaal per relatie
(`language_code`), SA10 (onderdelen "op documenten"), kassabon (POS, nice to have).

**Status: wacht op akkoord gebruiker.**

## Gebruik in de echte data

| | administratie A | administratie C |
|---|---|---|
| eigen documentsjablonen | `adm_a`: factuur, creditfactuur, pakbon, uitsorteerbon (pick_list), inkooporder — LaTeX → PDF, met logo | `adm_c`: factuur (+ varianten `Factor`, `Euros`, `Euro`, `US_EN`), order, inkooporder, picklijst, rekeningoverzicht — LaTeX |
| statustabel (geprint/gemaild) | 13 facturen, 1 pakbon, 5 inkooporders, 1 kassabon | 22 facturen, 8 inkooporders, 2 orders, 1 picklijst, 4 transacties |
| "taal" op factuur | geen | **gebruikt als sjabloonkeuze**: `Factor` 1.025 facturen (2011–2015), `Euros` 60 (2014–2016) |
| vertalingen artikelen, taal bij relatie | 0 / 0 | 0 / 0 |
| klanten met e-mail / met cc of bcc | 33 / 0 | 692 / 166 |
| leveranciers met e-mail | 22 | 111 |
| afleveradres | 6.585 | 0 |
| artikelvelden op documenten | barcode 73, `tariff_hscode` 72, `countryorigin` 50; `serialnumber` = THT-datum, `unit` = colli (maatwerk, zie fase 3) | — |
| referentiedocumenten/archief | 0 | 0 |
| cheques/kwitanties, betaalmethodes | 1 numerieke betaalreferentie / 0 | 0 / 0 |
| kenmerk (`dcn`) op verkoopfactuur | 11 | 3.823 |
| imports | geen sporen (geen `import.map`, im.pl ongewijzigd) | idem |

Conclusies:
- De statustabel is bijna leeg, terwijl er tienduizenden facturen zijn. Er is dus vooral op het scherm geprint
  en via de browser of WLprinter (Java-hulpprogramma dat afdrukken naar de lokale printer stuurde) naar de printer
  gestuurd, zonder vinkjes te zetten.
- **Wat echt telt zijn de eigen sjablonen.** Administratie A: factuur met EAN, artikelcode, leverdatum, THT, pallet, colli,
  korting, btw per tarief, gewichtstabel, betalingskorting en meerdere pagina's met "transport". Administratie C: factuur
  met factoringvariant (bankgegevens factormaatschappij) en euro-variant.
- Beide oude installaties draaiden **SQL-Ledger 2.8.30**, niet 3.2.12. Er staan sjabloonsets voor 12
  administraties (ook diverse andere, deels verwante administraties). De nieuwste
  back-up van administratie A is van **16 april 2026** (schema 2.8.37). Administratie A lijkt dus nog live te zijn,
  terwijl onze testdatabase `adm_a` maar tot februari 2025 loopt.
- Mailen is in SQL-Ledger alleen mogelijk via `sendmail`. Administratie C heeft veel e-mailadressen en cc/bcc, maar de
  statustabel laat zien dat er weinig vanuit SQL-Ledger is gemaild.
- Import en export zijn nooit aangepast en laten geen sporen na. De eigen wens uit "Import Payments.doc"
  (Rabo/ING-CSV) is nooit gebouwd en wordt nu gedekt door CAMT.053/MT940 (fase 2).

## Hoe SQL-Ledger het doet (kern)

- **Sjablonen** per set (`templates/<set>/[<taal>/]<soort>.<html|tex|txt>`). De engine kent `<%var%>`,
  `<%foreach%>`, `<%if%>`, `<%include%>` en `<%pagebreak%>` (handmatige paginering met overdracht). Velden worden
  per formaat ge-escaped. PDF loopt via latex + dvipdf/pdflatex.
- **Documentsoorten**: factuur, creditfactuur, debetfactuur, inkoopfactuur, AR/AP-transactie, credit-/debetnota,
  pakbon, picklijst, bin list, order, werkorder, inkooporder, offerte, aanvraag, rekeningoverzicht, aanmaning 1–3,
  cheque, kwitantie, remittance voucher, urenbriefje, kassabon. Daarnaast de rapporten balans en W&V als HTML.
- **Media**: scherm, printer (shell-pipe naar `lpr`/WLprinter), wachtrij (spoolmap + `status.spoolfile`) of
  e-mail. Bij e-mail kies je bijlage of inline, To/Cc/Bcc, onderwerp en bericht. Na het mailen komt er een notitie
  in `intnotes` en wordt `status.emailed` gezet. "Printen en boeken" boekt eerst en print dan.
- **Batch** (bp.pl): de wachtrij printen, mailen of samenvoegen (pdftk), en selecties van facturen/orders in één
  keer printen of mailen.
- **Aanmaningen**: niveau = hoogste `status.formname='reminderN'` + 1. Sjabloon `reminder1..3`. Rekeningoverzicht
  met ouderdomskolommen.
- **Import** (im.pl): CSV met kopregel = veldnamen (of een mapfile). Eerst klaarzetten in `report`, dan rijen
  aanvinken. Soorten: relaties, artikelen/diensten, groepen, rekeningschema, memoriaal, verkoopfacturen,
  orders, betalingen (CSV/V11). **Export**: betalingen (CSV/vaste lengte) + afletteren.
- **Referentiedocumenten** (rd.pl): bestanden ge-uuencode in `archive`/`archivedata`, gekoppeld via `reference`,
  zonder groottelimiet. Afbeeldingen staan op schijf.
- **Cheques/kwitanties** (cp.pl) met bedrag in woorden (Num2text). Betalingsregister met ongeldig maken en
  opnieuw uitgeven van cheques.

## Gevonden Perl-fouten (naar README "Bewuste afwijkingen" en de memo; beveiliging eerst privé)

Beveiliging (ingelogde gebruiker). Bij elk punt: beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders,
details volgen daarna. PyBalans vermijdt deze klassen via D1 (sandbox, geen eval), D5/DS11 (sjabloonbeheer), D7 (geen
printercommando's), D9 (mailen via smtplib) en D17 (typecontrole op inhoud).
1. Code-uitvoering via de sjabloonengine.
2. Shell-injectie via het printercommando.
3. Padtraversal en onveilig openen van bestanden (sjablonen, spoolbestanden, uploads).
4. Laden van code op basis van formuliervelden.
5. SQL-injectie (status, import, batch).
6. Mail-header-injectie.
7. XSS bij het importeren; onvolledige LaTeX-escaping.
8. Shell-injectie in de WLprinter-patch.

Overig:
9. Betalingsimport: het bedrag is altijd `credit − debit`, een kolom `amount` wordt genegeerd → bedrag 0
   (IM.pm r1668; de eigen `mapfile.txt` gebruikt juist `amount`).
10. Betalingsimport op bedrag: de eerste open post met hetzelfde bedrag wint, ongeacht de relatie. Bij inkoop is
    `JOIN bank` nodig, dus leveranciers zonder bankgegevens worden nooit gevonden (IM.pm r1581–1721).
11. V11: `! $neg % 5` heeft de verkeerde operatorvolgorde (im.pl r1475).
12. Opruimen van latex-tijdelijke bestanden mislukt soms: er bleven .aux/.log-bestanden achter in de oude
    installaties.
13. Vertalingen `locale/de` (stap 2b, woordenlijst): Payment = "Belastung" (= last/belasting), Receipt = "Quittung" (= kwitantie), Chart of Accounts = "Kontenbersicht" (tikfout), Debit Note = "Lastschrift" (= incasso), Tax = "MWST" en Quotation = "Offerte" (Zwitsers, niet Duitsland), Trial Balance = "Saldenbilanz" (gangbaar: Summen- und Saldenliste), Credit Note = "Gutschrift" (sinds 2013 § 14 UStG: zelffacturering).
14. Vertalingen `locale/fr`: Languages = "Langage", Backorder = "En souffrance" (gangbaar: reliquat), Recurring Transactions = "ABONNEMENTS", Customer/Vendor = "Tiers-Client"/"Tiers-Fournisseur", Department = "Service-Département", General Ledger = "JOURNAL-GRAND-LIVRE", Transaction = "Transaction" (anglicisme; écriture).

## Beslispunten (advies steeds A)

### Documenten

**D1 Techniek.** A: HTML-sjablonen (Jinja2, autoescape, sandbox, geen eval) → PDF met **WeasyPrint** (zuiver
Python + pango; in Docker één apt-regel). Paginering en "transport"/"blad x van y" via CSS
paged media. B: LaTeX houden (bestaande .tex-sjablonen bijna 1-op-1 te gebruiken, maar TeX in Docker is ±500 MB
en de injectierisico's blijven). C: headless Chromium. Het plan van 2026-10-07 zei al HTML→PDF.

**D2 Documentsoorten.** A: factuur, creditfactuur, inkoopfactuur (eigen kopie), AR/AP-transactie (verkoop/inkoop
zonder artikelen), pakbon, picklijst/uitsorteerbon, order, inkooporder, offerte, aanvraag, rekeningoverzicht,
aanmaning 1–3. **Niet**: werkorder, bin list, cheque, kwitantie, remittance voucher, urenbriefje (niet gebruikt
of buiten scope). Kassabon: zie D12.

**D3 Standaardsjablonen.** A: één nette Nederlandse en Engelse standaardset (opbouw zoals de SL-sets: kop met
logo/bedrijfsgegevens uit Beheer, adresblok geschikt voor vensterenvelop, regels, btw per tarief, betaald,
totaal, betalingsvoorwaarde, IBAN/kenmerk in de voet).

**D4 Eigen sjablonen administratie A en C.** A: ik bouw `adm_a` (factuur, creditfactuur, pakbon, uitsorteerbon,
inkooporder) en `adm_c` (factuur + Factor/Euros-variant, order, inkooporder, picklijst, overzicht) na in HTML,
zo dicht mogelijk bij het origineel. Hergebruikte velden krijgen een duidelijke naam in het sjabloon (EAN, THT,
colli …). Toets: een PDF van dezelfde factuur uit Perl/LaTeX naast die van Python, met dezelfde getallen. De
overige 10 sets uit de oude installatie alleen als ze nog nodig zijn (vraag: welke administraties gaan mee?).

**D5 Sjablonen per administratie beheren.** A: sjablonen staan als bestanden in een map per administratie
(datamap, buiten de code). Beheer → Sjablonen: downloaden, uploaden, terug naar standaard, logo uploaden,
voorbeeld-PDF. Geen bewerken in de browser. Jinja-sandbox zodat een sjabloon geen code kan uitvoeren.
B: ook een eenvoudige editor in de browser.

**D6 Varianten en taal.** A: per document een **sjabloonvariant** (veld `language_code`, zoals SL; administratie C: Factor,
Euros), vooringevuld vanuit de relatie. Documentteksten in NL/EN volgens de taal van de variant/relatie.
Vertaalde artikelomschrijvingen (`translation`) worden gelezen maar krijgen geen beheerscherm (niet gebruikt).

**D7 Printen.** A: alleen "PDF tonen/downloaden" (de browser print). Geen printercommando's op de server en geen
WLprinter (shell-injectie, werkt niet gehost). Knoppen op factuur-, order-, verkoop/inkoop- en betaalscherm:
**PDF**, **E-mail**, en bij nieuwe facturen **Opslaan en PDF** (= "Print and Post").

**D8 Status.** A: `status.printed`/`emailed` blijven bijgewerkt zoals in SL (compatibel), plus het eigen
logboek. Vinkjes zichtbaar op het scherm. Geen wachtrij/spoolmap (vervalt door D7 en D10).

### Mailen

**D9 Verzenden.** A: SMTP (smtplib, STARTTLS/SSL, gebruiker/wachtwoord) **per administratie** in Beheer, met
afzendernaam/-adres, standaardonderwerp en -tekst per documentsoort (met `{nummer}`, `{bedrag}`, `{vervaldatum}`),
handtekening per gebruiker. Mailscherm: Aan (relatie, bij pakbon het afleveradres), Cc/Bcc vooringevuld uit de
relatie, onderwerp, bericht, PDF als bijlage (geen inline). Na verzenden: notitie in `intnotes` zoals SL,
`status.emailed`, logboek. Ontwikkelen/testen: modus "map" die mails als .eml wegschrijft. Wachtwoord versleuteld
in de app-database. B: één SMTP-instelling voor de hele app.

**D10 Batch.** A: lijst facturen/orders/offertes (bestaande zoeklijsten) met vinkjes → **één samengevoegde PDF**
of **ieder z'n eigen mail** (alleen relaties met een e-mailadres; overzicht vooraf, resultaat per document).
Geen aparte wachtrij.

**D11 Herhalen (T8).** A: bij het verwerken van herhaalschema's de print/mail-instelling uit het schema
(`recurringemail`/`recurringprint`) uitvoeren: mailen via D9, "printen" = één PDF van alle nieuwe documenten.
Geen automatische tijdplanner (verwerken blijft een handeling). B: ook een opdrachtregelcommando voor cron.

**D12 Kassa/POS.** A: buiten fase 4 laten (nice to have na fase 4, aparte analyse). De ene kassabon van administratie A wordt
als gewone factuur getoond.

### Aanmaningen, overzichten en rapporten

**D13 Aanmaningen (R7).** A: in de bestaande aanmaningslijst vinkjes → PDF/mailen per klant met sjabloon
`aanmaning1..3`. Niveau opslaan zoals SL (`status.formname='reminderN'`, niveau +1), terugzetten mogelijk.
Rekeningoverzicht (statement) per klant met ouderdomskolommen, als PDF/mail.

**D14 Rapporten als PDF (R6).** A: balans, W&V, proefbalans, grootboekkaart, open posten en de overige
rapportlijsten krijgen naast CSV/xlsx een **PDF**-knop (zelfde engine, liggend waar nodig).

### Betalingen

**D15 Betalingsregister/cheques (P13).** A: geen cheques, kwitanties of chequeregister (in NL niet gebruikt;
administratie A: 1 numerieke referentie). Wel per betaling een **betalingsspecificatie** als PDF/mail (welke facturen,
bedragen, korting). Dit vervangt de remittance voucher.

**D16 Betaalbestand leveranciers (nieuw, vervangt "Export Payments").** A: niet in fase 4. B: SEPA-
overschrijvingsbestand (pain.001) maken van te betalen inkoopfacturen, om in te lezen bij de bank. Dat is de
moderne vervanger van im.pl "Export → Payments", maar het is nieuwe functionaliteit (± een halve bouwsessie).
*Herzien 2026-10-13 (BEHEER_EXTRA_ANALYSE.md §3, IM9 A1): SEPA-betaalbestand (pain.001) wordt bouwstap 5.*

### Uploads

**D17 Bijlagen bij boekingen (M9).** A: bestanden (pdf/jpg/png, max. 10 MB) koppelen aan een boeking, relatie of
artikel. Opslag in de SL-tabellen `reference`/`archive`/`archivedata`, zodat back-ups compleet blijven en
SQL-Ledger ze ook ziet. Daarbij dezelfde codering als SL (uuencode in blokken), zodat parallel draaien werkt. Typecontrole op inhoud (magic bytes, geen shell). Vertrouwelijk-vlag (`login`)
zoals SL. B: opslag op schijf in de datamap.

### Import

**D18 Welke imports.** A: **relaties** (klanten/leveranciers), **artikelen/diensten** (AT8, alleen meegegeven
velden bijwerken), **artikelgroepen**, **rekeningschema** en **memoriaal**. Niet: verkoopfacturen en orders
(webshop-koppelingen waren maatwerk, niet gebruikt) en betalingen-CSV (I10: CAMT.053/MT940 dekt dit; de eigen
Rabo/ING-wens is daarmee ingelost). B: ook verkoopfacturen/orders.
*Herzien 2026-10-13 (BEHEER_EXTRA_ANALYSE.md §3, IM1 A): verkoopfacturen, verkooporders, inkooporders en betalingen-CSV
zijn er toch bij gekomen (bouwstap 4, README afwijking 242).*

**D19 Werkwijze import.** A: CSV of xlsx uploaden → kolommen koppelen (automatisch op SL-veldnaam én Nederlandse
kop, koppeling per soort te bewaren) → **voorbeeld met per rij nieuw/bijwerken/fout** → aanvinken → importeren in
één transactie (alles of niets), resultaat met logboek. Sleutel zoals SL (relatienummer, artikelnummer,
rekeningnummer; memoriaal gegroepeerd op referentie en alleen in balans). Bijwerken via de bestaande
bibliotheek (`contacts`, `parts`, `admin`, `gl`), dus dezelfde controles als de schermen. Decimale komma en
datum volgens de notatie van de gebruiker.

**D20 Export.** A: elk lijstscherm heeft al CSV/xlsx. Daarnaast een export "relaties" en "artikelen" in precies
het importformaat (terugzetbaar). Geen betalingsexport (zie D16).

### Toetsen en volgorde

**D21 Toetsen.** A:
- Documenten: de **gegevens** die naar een sjabloon gaan (regels, btw per tarief, totalen, korting, bedrag in
  woorden indien gebruikt, vervaldatum, kenmerk) vergelijken met wat Perl in `print_form` klaarzet. Dat gaat via
  een nieuwe driverstap `print_ui` die `$form` na de voorbereiding dumpt, op testdata én steekproeven
  uit administratie A en C. Opmaak: PDF's van administratie A en C naast de LaTeX-PDF's van SL (visueel, door mij en jou).
- Mailen: pytest met een nep-SMTP-server (aiosmtpd) en de mapmodus.
- Import: pytest, en dezelfde CSV via im.pl (driver) vs Python → databases vergelijken. Daarbij: import van een
  export moet 0 wijzigingen geven op administratie A en C.
- Aanmaningen: niveaus en selectie vs `RP.pm` via de driver.
- Browsercontrole per stap (ook 375px).

**D22 Volgorde.** A: 0 engine + standaardsjablonen + factuur-PDF → 1 mailen (SMTP, mailscherm, status) →
2 overige documentsoorten + sjabloonbeheer + varianten → 3 eigen sjablonen administratie A en C → 4 batch + herhalen (T8) +
aanmaningen/overzichten + betalingsspecificatie → 5 rapporten-PDF → 6 bijlagen → 7 imports/exports. Elke stap
apart te keuren, op branch `fase4`.

## Vragen aan de gebruiker (naast de beslispunten)

- **V1** Draait administratie A nog live op SQL-Ledger 2.8 (back-up 16-04-2026)? Dan wil ik die back-up als nieuwe
  `adm_a`-testdatabase laden (bijwerken naar 3.2.4 zoals eerder), zodat de toetsen op de actuele data draaien.
- **V2** Welke administraties uit de oude installatie (12 sjabloonsets) moeten mee, behalve administratie A en C? Ook
  `adm_b` (staat naast administratie A in de back-up)?
- **V3** Heb je voor administratie A en C nog echte PDF-voorbeelden (factuur, pakbon) die als "zo moet het eruitzien" dienen?
  Anders maak ik ze zelf met de oude LaTeX-sjablonen.

---

## Stap 2 — overige documentsoorten, sjabloonbeheer, varianten (analyse 2026-10-10)

**Status: akkoord gebruiker 2026-10-10, DS1–DS12 alles A.** Kader: D2, D5, D6, D7, D8, D22 (alles A).

### Wat er nu staat (stap 0/1)
- `PrintData.invoice_vars` (io.pl print_form + IS/IR invoice_details) voor facturen met artikelen; sjablonen
  `invoice`, `credit_invoice`, `vendor_invoice`, `debit_invoice`; route `/document/<soort>/<id>.pdf` kent alleen
  die vier (`FORM_VC`), knoppen PDF/E-mail alleen op het factuurscherm.
- Variant = `language_code` van het document; zoekvolgorde variant → administratie → standaard bestaat al in
  `DocumentRenderer`, maar **er is nergens een keuzeveld**: `language_code` is een verborgen veld op factuur-,
  order- en verkoop/inkoopscherm. Alleen het relatiescherm heeft een taalkeuze; Beheer > Talen bestaat (fase 2).
- `company.doc_language` (nl/en) bestaat in de app-db, maar zonder beheerscherm.
- Samengestelde artikelen: `invoice_vars` klapt de stuklijst nog niet uit (Perl: `assembly_details`, SA10).

### Hoe SQL-Ledger het doet (per scherm de keuzelijst "formname")
| scherm | Perl biedt | sjabloon |
|---|---|---|
| verkoopfactuur | factuur, picklijst, pakbon (+ barcode, remittance voucher) | invoice, pick_list, packing_list |
| creditfactuur | creditfactuur, bin list | credit_invoice, bin_list |
| inkoopfactuur | factuur, bin list (ontvangstlijst) | vendor_invoice, bin_list |
| debet-inkoopfactuur (retour) | debetfactuur, picklijst, pakbon | debit_invoice, pick_list, packing_list |
| verkooporder | order, werkorder, picklijst, pakbon | sales_order, work_order, pick_list, packing_list |
| inkooporder | inkooporder, bin list | purchase_order, bin_list |
| offerte / aanvraag | offerte / aanvraag | sales_quotation / request_quotation |
| verkoop/inkoop zonder artikelen | transactie, creditnota/debetnota (+ cheque/kwitantie) | ar_transaction, ap_transaction, credit_note, debit_note |

- Orders lopen via `OE::order_details`: dezelfde lijsten als een factuur plus `ship` (geleverd), `requiredate`,
  `totalship`; datumvelden `orddate`/`reqdate` i.p.v. `invdate`/`duedate`; nummer `ordnumber`/`quonumber`.
  Bij een pak-/picklijst uit een **factuur** is `ship` = `qty`. Uit een order controleert Perl
  geleverd ≤ besteld, en met een magazijn kapt het af op de voorraad daar (magazijnen: niet gebruikt).
- Transacties zonder artikelen: `arapprn.pl print_transaction` — regels = boekingsregels (rekening, omschrijving,
  bedrag, project), btw per rekening, betalingen, bedrag in woorden; bestand `ar_transaction`/`ap_transaction`.
- Afleveradres: bij creditfactuur, inkooporder, aanvraag, bin list en inkoopfactuur vult Perl het **eigen
  bedrijfsadres** als afleveradres (je ontvangt zelf); anders het afleveradres van het document of de relatie.
- Perl print het formulier zoals het op het scherm staat, ook niet-opgeslagen; zonder nummer trekt printen een
  nieuw nummer uit de teller.

### Echte data
| | administratie A | administratie C |
|---|---|---|
| statustabel | invoice 13, packing_list 1, purchase_order 5, pos_invoice 1 | invoice 22, pick_list 1, purchase_order 8, sales_order 2, **transaction 4** |
| verkoop/inkoop zonder artikelen | 2.372 / 14.567 | 479 / 8.442 |
| orders verkoop / inkoop / offertes | 0 / 22 / 0 | 2.289 / 2.827 / 0 |
| variant op documenten | — | facturen Factor 1.025, Euros 60; orders Factor 13, Euros 1 |
| talen (tabel `language`) | — | Factor, Euros (US_EN en Euro bestaan alleen als map) |
| samengestelde artikelen | 0 | 0 |
| eigen sjablonen | alle soorten aanwezig, gebruikt: factuur, creditfactuur, pakbon, picklijst, inkooporder | idem in 4 varianten + hoofdmap |

Pak-/picklijsten, inkooporders en orderbevestigingen zijn dus echt geprint; transacties zonder artikelen af en toe.

### Beslispunten

**DS1 Documentsoorten per scherm.** A: binnen D2:
- verkoopfactuur: **Factuur, Pakbon, Picklijst**; creditfactuur: **Creditfactuur**
- inkoopfactuur: **Inkoopfactuur**; retour leverancier (debet): **Debetnota**
- verkooporder: **Orderbevestiging, Pakbon, Picklijst**; inkooporder: **Inkooporder**
- offerte: **Offerte**; aanvraag: **Prijsaanvraag**
- verkoop zonder artikelen: **Factuur** (`ar_transaction`) of **Creditnota** (`credit_note`); inkoop zonder
  artikelen: **Inkoopboeking** (`ap_transaction`) of **Debetnota** (`debit_note`)

Niet (D2/D15): bin list, werkorder, barcode, remittance voucher, cheque, kwitantie; ook geen pakbon/picklijst bij
een retour aan een leverancier. B: ook bin list en werkorder (Perl-compleet, niet gebruikt in de data).

**DS2 Knoppen.** A: op elk scherm uit DS1 één knop **PDF** met een keuzelijstje als er meer soorten zijn
(Factuur ▾ Pakbon / Picklijst), en **E-mail** voor dezelfde keuze. Alleen voor opgeslagen documenten; een
gewijzigd, niet-opgeslagen formulier geeft eerst "Eerst opslaan". **Opslaan en PDF** (D7) bij nieuwe facturen,
orders en offertes. Regel Geprint/Gemaild per soort onder de knoppen (zoals nu bij de factuur).
B: aparte knop per soort.

**DS3 Gegevens orders/offertes.** A: `PrintData.order_vars` = port van `OE::order_details` + de order-tak van
print_form, uit de **opgeslagen** order (zoals `invoice_vars`): dezelfde lijsten + `ship`, `requiredate`,
`totalship`, `orddate`/`reqdate`, `quonumber`; gedeelde code met `invoice_vars` (bedrijfsgegevens, btw per
tarief, groeperen/sorteren, `fdld`) wordt één hulpfunctie, zodat de cijfers niet uiteenlopen. Geen
magazijnafkapping (magazijnen niet in gebruik, README). Toets: Perl-driverstap `print_ui` uitbreiden met oe.pl
(alle soorten uit DS1) + `compare_print.py --oe` op orders van administratie C (steekproef 600) en inkooporders van A (22).

**DS4 Pakbon/picklijst uit een order: welk aantal.** A: zoals Perl: kolommen **besteld** (`qty`) en **geleverd**
(`ship` = wat al gefactureerd is); de standaardpakbon toont daarnaast **nu te leveren** = besteld − geleverd
(berekend in het sjabloon, geen nieuw gegeven). Uit een factuur: geleverd = aantal. B: alleen besteld.

**DS5 Gegevens transacties zonder artikelen.** A: `PrintData.transaction_vars` = port van
`print_transaction` (arapprn.pl) uit de opgeslagen boeking: regels (rekening, omschrijving, bedrag, project),
btw per rekening, betalingen, contante korting, bedrag in woorden, kenmerk; zelfde afwijkingen als bij
facturen (Decimal, geen "NULL"). Toets: driverstap `print_ui` ook voor ar.pl/ap.pl + `compare_print --arap`
op steekproeven uit administratie A en C.

**DS6 Afleveradres.** A: zoals Perl: bij creditfactuur, inkoopfactuur, inkooporder en prijsaanvraag is het
afleveradres het eigen bedrijfsadres (uit Beheer), anders afleveradres van het document → van de relatie →
factuuradres. B: altijd het adres van het document/de relatie.

**DS7 Samengestelde artikelen op documenten (SA10).** A: zoals `assembly_details`: onder een samengesteld
artikel de onderdelen met vinkje "op documenten" (`bom`) als ingesprongen regels (aantal × stuklijstaantal,
geen prijs), op factuur, pakbon, picklijst en order. Ook in `invoice_vars` (nu nog niet). Toets met de
bestaande assembly-scenario's in de driver. Echte data heeft geen samengestelde artikelen.

**DS8 Standaardsjablonen.** A: alle soorten uit DS1 in de bestaande standaardset (`_base.html`), NL/EN:
- facturen/creditfactuur/inkoop: bestaand
- orderbevestiging, inkooporder, offerte, prijsaanvraag: opbouw als de factuur, met leverdatum ("gewenst op"),
  bij offerte "geldig tot" (= `reqdate`), geen betaalgegevens/IBAN in de voet bij inkoop
- pakbon: geen prijzen; afleveradres groot, artikelnummer, omschrijving, aantallen (DS4), eenheid,
  serienummer, gewicht/colli als die er zijn, handtekeningvak "ontvangen"
- picklijst: geen prijzen; locatie (`bin`) vooraan, gesorteerd op locatie, afvinkvakje
- transacties: regels per rekening met omschrijving en bedrag, btw, totaal, betaalgegevens

**DS9 Variant kiezen op het document.** A: zichtbaar keuzeveld **Sjabloonvariant** (lijst = Beheer > Talen +
mappen die bij de administratie bestaan) op factuur-, order-, offerte- en verkoop/inkoopscherm, vooringevuld uit
de relatie (`customer.language_code`), opgeslagen in `language_code` zoals SL. In het PDF-/mailscherm
eenmalig af te wijken (`?variant=`, bestaat al). Variantnaam bepaalt ook de documenttaal als hij met en/us/uk/nl
begint (bestaat al), anders de taal van de administratie. Verborgen veld vervalt. B: alleen keuze bij het printen.

**DS10 Documenttaal administratie.** A: `doc_language` (nl/en) op **Beheer > Instellingen** van de administratie
(beheerder), uitleg "taal van documenten zonder variant". Relaties met een taal/variant krijgen die.

**DS11 Sjabloonbeheer (D5).** A: **Beheer > Sjablonen** (beheerder): tabel per documentsoort (rijen) en
variant (kolommen): bron (standaard/eigen), **downloaden** (eigen of standaard als startpunt), **uploaden**
(.html, max. 512 kB, UTF-8; bij uploaden eerst compileren in de sandbox én een proef-PDF met de laatste factuur/
order van die soort, fout → geweigerd met regelnummer), **terug naar standaard** (bevestiging), **voorbeeld-PDF**
(laatste document van die soort, of kiezen op nummer). Ook downloaden/uploaden van `_base.html` en
`_invoice.html` (gedeelde delen). **Logo** uploaden (png/jpg/svg, max. 1 MB, magic-bytes-controle; svg zonder
scripts) en verwijderen. **Variant toevoegen** = map aanmaken met de naam van een taal uit Beheer > Talen
(VARIANT_RE). Elke wijziging in het logboek en de audittrail (wie, welk bestand). Oude versie bewaard als
`<soort>.html.<datum>` (laatste 5), terugzetten vanuit de lijst. Geen bewerken in de browser (D5 A).
B: zonder versiebeheer.

**DS12 Toetsen.** A: pytest per soort (gegevens + PDF wordt gemaakt + geen externe bronnen), sjabloonbeheer
(upload met fout, sandbox-ontsnapping zoals `{{ ''.__class__ }}`, path traversal in naam/variant, te groot,
verkeerd type, rollen); driver `print_ui` uitgebreid (oe.pl, ar.pl, ap.pl, pak-/picklijst uit is.pl) en
`compare_print` met steekproeven uit administratie A en C: **0 onverklaarde verschillen**; voorbeeld-PDF's van alle soorten in
`dev/voorbeeld-pdf/` ter beoordeling; browsercontrole (ook 375px) van knoppen, variantkeuze en sjabloonbeheer.

### Volgorde binnen stap 2 (voorstel)
1. `order_vars` + `transaction_vars` + gedeelde hulpfunctie, driver + compare_print (DS3, DS5, DS6, DS7)
2. route/knoppen voor alle soorten + mailen van alle soorten (DS1, DS2, DS4)
3. standaardsjablonen nieuwe soorten (DS8) + voorbeeld-PDF's
4. variantkeuze en documenttaal (DS9, DS10)
5. Beheer > Sjablonen (DS11)
6. README (tabelregels, webtabel, afwijkingen), toetsen, browsercontrole (DS12)

---

## Duits als taal (wens gebruiker 2026-10-10) — beslispunten DT1–DT6

**Status: akkoord gebruiker 2026-10-10, DT1–DT6 alles A.**

Uitgangspunt: SQL-Ledger heeft 44 talen (`locale/<taal>/<module>`), wisselend compleet en soms fout. Onze app heeft
eigen teksten (1.461, Nederlands met Engelse catalogus); daarvan staat maar 10–13% letterlijk in een SL-taal
(Duits: 187). Bulk overnemen geeft dus een grotendeels onvertaalde app met de SL-fouten erin. Taalkeuze per
gebruiker bestaat al (Mijn instellingen, nl/en), documenttaal per administratie (`doc_language`, nl/en) en per
relatie/document (`language_code`, DS9).

**DT1 Schermtaal Duits.** A: volledige Duitse catalogus (`i18n_de.py`: alle schermteksten, bibliotheekmeldingen
en contexten), door mij vertaald; SL `locale/de` alleen als woordenlijst voor vaktermen (Debitoren, Kreditoren,
Hauptbuch, Umsatzsteuer, OP-Ausgleich …). Duits uit Duitsland (niet Zwitsers/Oostenrijks). "Deutsch" verschijnt
dan vanzelf in Mijn instellingen. B: alleen documenten in het Duits.

**DT2 Documenten in het Duits.** A: documenttaal nl/en/**de** (Beheer > Instellingen, DS10; variant of relatietaal
die met `de`/`ch`/`at` begint → Duits); Duitse teksten in de standaardsjablonen; Duitse standaardmailteksten;
bedrag in woorden in het Duits (port van SL `locale/de/Num2text`, nagekeken en waar nodig verbeterd, zoals bij NL).

**DT3 Waar kies je de taal.** A: zoals nu, maar met Duits erbij: schermtaal per gebruiker (Mijn instellingen),
documenttaal per administratie, taal/variant per relatie en per document. Het inlogscherm (gebruiker nog
onbekend) volgt de browsertaal (nl/en/de), anders Nederlands. B: ook een standaard schermtaal per administratie
voor nieuwe gebruikers.

**DT4 Kwaliteit.** A: pytest eist dat elke tekst een Duitse vertaling heeft en dat plaatshouders (`{n}` e.d.)
gelijk blijven; een overzicht NL/EN/DE naast elkaar (xlsx) ter beoordeling door jou of een Duitstalige; fouten die
ik in SL `de` tegenkom gaan naar de community-memo.

**DT5 Moment.** A: DT2 (documenten) gaat mee in stap 2 (raakt DS8–DS10); DT1/DT3/DT4 (schermen) als eigen stap
**2b** direct na stap 2, apart te keuren. B: alles na fase 4.

**DT6 Volgende talen.** A: een taal toevoegen = één catalogusbestand + documentteksten + bedrag in woorden; geen
bulkimport uit SL. Andere SL-talen alleen op verzoek, met dezelfde aanpak.

**Aanvulling 2026-10-10: Frans erbij** (verzoek gebruiker, volgens DT6 met dezelfde aanpak als Duits: DT1–DT5
gelden ook voor Frans). Frans uit Frankrijk; SL `locale/fr` (93% compleet) als woordenlijst. Documenten in het
Frans (teksten, mails, bedrag in woorden uit SL `locale/fr/Num2text`, nagekeken) mee in stap 2; schermvertaling
Frans samen met Duits in stap 2b. Overzicht ter controle wordt NL/EN/DE/FR.

---

## Stap 2b — schermen in het Duits en Frans (analyse 2026-10-10)

**Status: akkoord gebruiker 2026-10-10, TL1–TL8 alles A; woordenlijst akkoord (ook: DE-documenten Rechnungskorrektur/Belastungsanzeige, Afletteren = Kontenabstimmung/rapprochement, Administratie = Mandant/Dossier). Gebouwd 2026-10-10 incl. TL9 (A), wacht op keuring; keuzes binnen A akkoord.** Kader: DT1, DT3, DT4, DT6 (alles A) + aanvulling Frans.

### Wat er nu staat
- `sqlledger_web/i18n.py`: Nederlandse teksten in de code, `CATALOGS`/`CONTEXTS` per taal, `LANGUAGES = nl/en`;
  taal per verzoek uit `app_user.language` (`main.py _user_language`), zonder sessie altijd `nl`.
- `i18n_en.py`: **1.491** schermteksten (`EN`), **94** bibliotheekteksten (`LIBRARY`), 2 contexten; 134 met
  plaatshouders, geen HTML in de teksten. Samen ca. 7.200 Nederlandse woorden.
- `libmsg.py`: **158** meldingsjablonen uit `sqlledger/` als tuple (NL, EN).
- JavaScript haalt zijn teksten uit `data-…`-attributen (al vertaald via de sjablonen); getal- en datumnotatie zijn
  instellingen per gebruiker, los van de taal. Documenten (sjablonen, mailteksten, bedrag in woorden) zijn al
  Duits en Frans (stap 2, DT2).
- Rekeningnamen komen, zoals in SL, uit de tabel `translation` met de landcode van de administratie; artikel- en
  relatiegegevens zijn invoer en worden niet vertaald.
- Niet vertaald: `cli.py` (beheerdersopdrachten), blijft zo.

### Beslispunten (advies steeds A)

**TL1 Opbouw.** A: per taal één bestand `i18n_de.py` / `i18n_fr.py`, opgebouwd zoals `i18n_en.py` (`DE`/`FR`,
`LIBRARY`, `CONTEXT`) plus `LIBMSG` (sleutel = het sjabloon uit `libmsg.TEMPLATES`, waarde = de tekst in die taal).
`libmsg.TEMPLATES` blijft (NL, EN), zodat de 158 bestaande regels niet veranderen. `LANGUAGES` krijgt Deutsch en
Français. Daardoor verschijnen ze vanzelf in Mijn instellingen en bij gebruikers in Beheer.
B: `libmsg` ombouwen naar één woordenboek per taal.

**TL2 Woordenlijst eerst.** A: eerst een woordenlijst van ca. 120 kernbegrippen NL/EN/DE/FR (boeking, relatie,
memoriaal, grootboek, afletteren, btw-soorten, kostenplaats/project, pakbon, nabestelling, vervaldatum …) als
xlsx ter goedkeuring. Pas daarna vertaal ik de ca. 1.750 teksten, zodat één begrip overal hetzelfde heet.
Dit kost één extra keuringsronde. B: alles in één keer vertalen en achteraf beoordelen.

**TL3 Aanspreekvorm en terminologie.** A:
- **Duits**: "Sie". Knoppen in de infinitief ("Speichern", "Buchen"). Gangbaar HGB/DATEV-taalgebruik: Buchung,
  Debitoren/Kreditoren, Sachkonto, Hauptbuch, Umsatzsteuer/Vorsteuer, Offene Posten, Ausgleich (afletteren),
  Kostenstelle.
- **Frans**: "vous". Knoppen in de infinitief ("Enregistrer", "Comptabiliser"). Taalgebruik van het Franse PCG:
  écriture, clients/fournisseurs, grand livre, TVA collectée/déductible, lettrage, centre de coûts.
- SL `locale/de` en `locale/fr` alleen als naslag bij twijfel. Duitsland en Frankrijk, niet Zwitserland, Oostenrijk
  of België.

B: zo dicht mogelijk bij de woorden van SL.

**TL4 Pagina's zonder sessie (DT3).** A: inloggen, tweestapscode, wachtwoord vergeten/herstellen en foutpagina's
zonder sessie volgen de browsertaal (`Accept-Language`, q-waarden; de eerste van nl/en/de/fr, anders nl). Er wordt
niets opgeslagen. B: daarnaast links NL · EN · DE · FR op het inlogscherm (taalcookie).

**TL5 Gegevens uit de administratie.** A: zoals SL. Rekeningnamen volgen de tabel `translation` met de landcode van
de administratie (bestaat al), artikel- en relatiegegevens blijven zoals ingevoerd. B: rekeningnamen volgen de
taal van de gebruiker (wijkt af van SL).

**TL6 Notatie.** A: getal- en datumnotatie blijven een eigen instelling per gebruiker. Ze veranderen niet
automatisch als iemand Duits of Frans kiest. Wel stelt de keuzelijst bij een nieuwe gebruiker de notatie voor die
bij de taal past (DE `1.000,00` / `dd.mm.yy`, FR `1000,00` / `dd/mm/yy`; een spatie als duizendtalscheiding
kent SL niet en voegen we niet toe).
B: niets voorstellen.

**TL7 Opmaak.** A: lange Duitse samenstellingen afbreken met CSS `hyphens: auto` (`<html lang>` staat al goed).
Browsercontrole van alle hoofdschermen in DE en FR, ook op 375px. Een kortere kolomkop alleen waar het anders niet
past (context `"kolom"`). B: geen afbreekregels, alleen kortere teksten.

**TL8 Toetsen en kwaliteit (DT4).** A:
- `tests/web/test_i18n.py` uitbreiden: elke gemarkeerde tekst heeft een DE- en FR-vertaling, plaatshouders zijn per
  tekst gelijk aan het NL-origineel, en geen vertaling is gelijk aan de Nederlandse tekst behalve op een korte
  uitzonderingslijst (PDF, IBAN, BIC, OK …).
- `test_libmsg.py`: elk sjabloon heeft DE en FR.
- Een renderproef van alle GET-pagina's in DE en FR zonder fout en zonder Nederlandse woorden uit de woordenlijst.
- Overzicht `dev/vertalingen.xlsx` (NL/EN/DE/FR naast elkaar, gegroepeerd per scherm) ter beoordeling door jou of
  een moedertaalspreker.
- Fouten die ik in SL `de`/`fr` tegenkom gaan naar de community-memo.

B: alleen de volledigheidstoets.

**TL9 Eigen woorden voor de schermen** (vraag gebruiker 2026-10-10: "in SQL-Ledger kun je woorden aanpassen in een tekstbestand"). A: per installatie `<data_dir>/vertalingen/<taal>.txt` (`Nederlandse tekst = eigen tekst`, `[menu] …` voor een context), bovenop de ingebouwde vertaling, overleeft updates, opnieuw gelezen bij wijziging, foute regels overgeslagen en gelogd, `sqlledger-web translations <taal> [--starter]`. B: plus een scherm Systeem > Vertalingen. C: niet bouwen. **Akkoord gebruiker 2026-10-10: A, gebouwd in stap 2b.**

### Volgorde binnen stap 2b (voorstel)
Op branch `fase4-stap2b` (eigen worktree):

1. TL1 opbouw + toetsen (TL8).
2. Woordenlijst xlsx → **jouw akkoord** (TL2).
3. Vertalen per module (DE en FR samen).
4. Inlogscherm en browsertaal (TL4) + notatievoorstel (TL6).
5. Opmaak en browsercontrole (TL7).
6. xlsx-overzicht, README (webtabel Talen, open punten), regressie (pytest, standaardset).

---

## Stap 3 — eigen sjablonen administratie A (analyse 2026-10-10)

**Status: VERVALLEN (2026-10-10).** Antwoorden gebruiker: VS1 "een beheerder moet dit zelf kunnen instellen (in
SQL-Ledger stond dat in een ini-bestand)", VS2 onbekend, VS3 **administratie A is gestopt**, de back-up is alleen testdata.
PS1–PS11 worden daarom niet gebouwd; stap 3 is herzien, zie "Stap 3 (herzien)" hieronder. De analyse blijft staan
als bron (veldgebruik administratie A, fouten 15–18 voor de memo). Kader: D4, D5, D22, V3 (alles A; V2: overige administraties, ook C,
gebruiken de standaardset).

### Bronnen
- Back-upmap van de gebruiker: de eigen sjablonen voor factuur, creditfactuur, pakbon en uitsorteerbon (en een
  factuursjabloon van een andere bv). Ze zijn **byte-gelijk** aan `invoice.tex`, `credit_invoice.tex`,
  `packing_list.tex` en `pick_list.tex` in `templates/adm_a/` van de oude installatie (stand 2013/2014).
  De overige 24 .tex-bestanden in die map zijn de SL-standaard met kleine NL-aanpassingen (o.a. `purchase_order`).
- Logo's: factuur `images/logo_factuur.png`, creditfactuur `images/logo_credit.png` (twee verschillende).

### Echte data (administratie A, `adm_a`)
| | |
|---|---|
| verkoopfacturen 2020–2024 | 846 / 754 / 804 / 828 / 575; laatste in de testdatabase 25-11-2024, in de back-up van 16-04-2026 de laatste op 19-02-2025 |
| creditfacturen | 63 in totaal, 1–5 per jaar |
| regels per factuur (2022+) | max **3**, dus nooit een vervolgpagina |
| betalingskorting (`cashdiscount`) | 857 van 2.207 (2022+) |
| afleveradres (`shipto`) | 1.174 van 2.207 |
| cargo | `package` 3.069× gevuld (getallen of korte vrije tekst), `volume` **altijd 0**, `grossweight` = gewicht per stuk, `netweight` = aantal × gewicht |
| statustabel | invoice 13, packing_list 1, purchase_order 5 (laatste inkooporder 2012) |
| Beheer (`defaults`) | adres, tel/fax in +31-notatie, KvK, geen btw-nummer; het adres en de bankrekening die vast in de sjablonen staan wijken af van Beheer. In `bank` staan alleen andere IBAN's |

### Wat de sjablonen doen (veldgebruik, de "hergebruikte" velden)
| kolom op het document | SL-veld | betekenis bij administratie A |
|---|---|---|
| EAN | `barcode` | EAN-code |
| Art. | `hscode` (`tariff_hscode`) | eigen artikelcode |
| Levering | `deliverydate` | leverdatum regel |
| Houdbaar | `serialnumber` | THT-datum |
| Pallet | `package` (factuur) / `volume` (pakbon, uitsorteerbon) | aantal pallets / emballage |
| Eenh.Collo | `unit` | stuks per colli |
| "Netto per CE:" | `countryorigin` | nettogewicht per consumenteneenheid |
| Gewicht informatie | `grossweight` per regel, `totalnetweight` | gewicht per stuk, totaalgewicht |

- **Factuur**: kop met logo + bedrijfsgegevens (vast in het sjabloon), retouradresregel, "Factuur aan" en
  "Verzenden aan" naast elkaar, opmerkingen, titel F A C T U U R, kadertabel nummer/datum/vervaldatum (vet)/PO/
  "Aan: <eigen bedrijfsnaam>", regels in 8 pt (per regel: omschrijving, "Netto per CE", dan de kolommen),
  subtotaal, betalingskorting, btw per tarief, reeds betaald, totaal, "Alle prijzen in EUR", "Kortingspercentage
  wordt over Collo berekend", gewichtstabel. Voet: betaalgegevens (vast).
- **Creditfactuur**: een **oudere** opmaak: kolommen No/Omschrijving/Levering/Aantal/Unit/St.Prijs/Kort.%/Prijs/
  Bedrag (geen EAN/THT), bedragen met een minteken ervoor, e-mail van de klant, vaste regel met het gewicht per
  consumenteneenheid in de gewichtstabel, ander logo.
- **Pakbon** = afdruk op een **voorgedrukt CMR-formulier**: `textpos` absoluut, eenheid 30 mm, oorsprong 10 mm/10 mm
  (bijv. blok 1 op x 25 mm, y 34 mm). Vakken: 1 afzender (+ een vast nummer rechts), 2 geadresseerde, 3 plaats van
  aflevering (+ betalingskorting), 4 plaats inontvangstneming, 16 vervoerder (= administratie A zelf), 18 "Ordernummer:
  <factuurnummer>", PO-nummer, "THIS IS ALSO A DELIVERY NOTE", 11 totaalgewicht, 6–9 regels, 19 "PLACING OUR
  PLASTIC PALLET…", 13 EMBALLAGE (pallet per regel), 14 "X", 21 plaatsnaam, 22 bedrijfsnaam + plaatsnaam.
- **Uitsorteerbon** (`pick_list`): dezelfde CMR, 6 mm verder naar links en enkele blokken 1–3 mm lager, plus het
  kortingsbedrag.

### Fouten in de originelen (naar de memo)
15. Pakbon/uitsorteerbon: kolom "Pallet" toont `volume` (altijd 0), de factuur gebruikt `package`.
16. Pakbon/uitsorteerbon: `\begin{tabular*}` gesloten met `\end{tabular}`; uitsorteerbon: `&` buiten een tabel in
    blok 3. LaTeX geeft fouten (SL draaide in batchmode, de PDF kwam er waarschijnlijk toch uit).
17. Factuur/creditfactuur: de vervolgpagina (`pagebreak`) heeft een kop met 9 kolommen van de oude opmaak, niet de
    11 van de factuur.
18. Bedrijfsgegevens vast in het sjabloon wijken af van Beheer (huisnummer, bankrekening).

### Beslispunten (advies steeds A)

**PS1 Welke sjablonen.** A: de vier uit de back-upmap: factuur, creditfactuur, pakbon (CMR), uitsorteerbon
(CMR). Inkooporder en de rest: standaardset (laatste inkooporder 2012). B: ook `purchase_order` nabouwen.

**PS2 Bedrijfsgegevens.** A: uit **Beheer** (naam, adres, telefoon, fax, e-mail, website, KvK, btw-nummer) en
IBAN/BIC van de debiteurenrekening (zoals de standaardset), zodat een wijziging op één plek gebeurt. Vooraf
Beheer van administratie A corrigeren waar nodig (zie vraag VS1). B: letterlijk overnemen zoals in de .tex.

**PS3 Trouw aan het origineel.** A: zelfde indeling, kolommen, labels, volgorde, vette/8 pt-delen en vaste
teksten; fouten 15–17 verbeterd (Pallet = `package`, één kolomkop op elke pagina). Bovenaan elk sjabloon een
commentaar met de veldbetekenissen uit de tabel hierboven. B: ook fout 15 laten staan (0 in de kolom).

**PS4 Vervolgpagina's.** A: kolomkop herhaald + "Pagina x van y" in de voet, **geen transportregel** (CSS kan geen
lopend subtotaal; sinds 2022 max. 3 regels per factuur). B: transport ("Subtotaal van vorige pagina") door in
Python vaste aantallen regels per pagina te maken, zoals `pagebreak 50 20 37`.

**PS5 Creditfactuur.** A: eigen (oudere) opmaak getrouw, incl. mintekens, e-mail en de vaste regel met het gewicht
per consumenteneenheid. B: zelfde opmaak als de factuur met titel CREDITFACTUUR (dan ook EAN/THT op de credit).

**PS6 CMR-posities.** A: pagina zonder marges, blokken met `position: absolute` in mm, exact omgerekend uit de
textpos-coördinaten (blokbreedtes idem); bovenaan het sjabloon twee instelwaarden `--cmr-x`/`--cmr-y` (mm,
standaard 0) om het geheel te verschuiven als een printer anders uitlijnt. Vaste CMR-teksten (vast nummer, "X",
plaatsnaam, vak 16/19/22) letterlijk. Eén gedeeld deel `_cmr.html`; `packing_list.html` en `pick_list.html`
verschillen alleen in posities en het kortingsbedrag. Werkt vanuit een factuur (`invnumber`) en, voor de
volledigheid, vanuit een order (`ordnumber`). B: aparte volledige sjablonen zonder gedeeld deel.

**PS7 Lettertype.** A: Helvetica/Arial-stapel zoals de standaardset (geen extra bestanden; regelafstanden
nagebootst: 10/12 pt, tabel 8/10 pt). LaTeX gebruikte Computer Modern Sans: lichtjes anders van beeld, posities
op de CMR blijven gelijk. B: Latin Modern Sans meeleveren (vergt lettertypes in de sjabloonmap via data:-URL,
uitbreiding van de engine).

**PS8 Logo's.** A: één logo per administratie (`logo_factuur.png`) voor factuur en creditfactuur; de engine
blijft ongewijzigd. B: creditfactuur met het eigen `logo_credit.png`: logo per variant/soort toestaan (kleine
uitbreiding `logo()`).

**PS9 Waar de sjablonen staan.** A: in de repo onder `dev/sjablonen/adm_a/` (niet in het pakket,
klantspecifiek), installeren via **Beheer > Sjablonen** uploaden (de bestaande route, met compileer- en
proef-PDF-controle) of een kopie naar `<data_dir>/templates/<id-van-A>/`. B: als tweede ingebouwde set.

**PS10 Naam "Uitsorteerbon".** A: de soort blijft in het scherm "Picklijst" heten; wie "Uitsorteerbon" wil, zet
`Picklijst = Uitsorteerbon` in `<data_dir>/vertalingen/nl.txt` (TL9, bestaat al; geldt per installatie). B: een
weergavenaam per soort per administratie in Beheer > Sjablonen (nieuw).

**PS11 Toetsen.** A:
- pytest: elk sjabloon van administratie A geeft een PDF met echte data van A (factuur met korting + afleveradres, factuur zonder,
  creditfactuur, pakbon, uitsorteerbon), alle velden aanwezig, geen externe bronnen; CMR-blokken op de berekende
  mm-posities (HTML-controle).
- naast elkaar: `tectonic` (brew, geen sudo; haalt bij de eerste run LaTeX-pakketten van internet) maakt de
  originele .tex-PDF's van dezelfde facturen; de .tex komt uit de Perl-driver (`print_ui` met de set van administratie A). PDF's
  in `dev/voorbeeld-pdf/stap3/` ter beoordeling door jou; voor de CMR een overlay (origineel rood, nieuw zwart).
- jij: één proefafdruk op een echt CMR-formulier (dat kan ik niet).
- browsercontrole: kiezen en tonen vanaf het factuurscherm.
B: zonder tectonic, alleen de nieuwe PDF's.

### Vragen aan de gebruiker
- **VS1** Welke gegevens kloppen nu: het adres in Beheer of dat in de sjablonen? Welke rekening moet op de factuur:
  die uit het sjabloon of een van de rekeningen in `bank`? Is het btw-nummer nog juist?
- **VS2** Wat is het vaste nummer rechtsboven op de CMR (formuliernummer, klantnummer bij de vervoerder)? Vast laten?
- **VS3** In de back-up van april 2026 is de laatste verkoopfactuur van 19-02-2025. Factureert administratie A nog (dan is deze
  stap nuttig), of is het vooral voor het archief/heruitdraaien?

### Volgorde binnen stap 3 (voorstel)
1. Beheer administratie A (VS1) + `_cmr.html`, pakbon, uitsorteerbon (PS6) + overlay-toets
2. factuur + creditfactuur (PS3–PS5, PS7, PS8)
3. tectonic-vergelijking, voorbeeld-PDF's, pytest, README (installatie sjablonen administratie A), memo fouten 15–18

---

## Stap 3 (herzien) — documentinstellingen door de beheerder (besluit 2026-10-10)

**Status: akkoord gebruiker 2026-10-10, DI1–DI4 alles A.** Uitwerking hieronder = keuzes binnen A, ter keuring
bij de bouw.

**DI1 Inhoud (A).** Geen sjablonen van administratie A nabouwen. Nieuw scherm **Beheer > Documentinstellingen** (beheerder), per
administratie, zonder HTML-kennis. De standaardset (`doctemplates/standard/`) leest alles hieruit; niets meer vast
in een sjabloon. Administratie A dient als testgeval: zo instellen dat de factuur de oude factuur van A benadert.
**DI2 CMR (A).** Geen CMR/vrachtbrief als standaarddocument; een eigen geüpload sjabloon kan het nog (DS11).
**DI3 Opslag (A).** In de database van de administratie (gaat mee met back-up/terugzetten, geldt voor alle
gebruikers), niet in een ini-bestand.
**DI4 Logo (A).** Eén logo per administratie (bestaat al).

### Wat er al is
- Beheer > Instellingen (`sqlledger/admin.py` `TEXT_SETTINGS`, tabel `defaults`): bedrijfsnaam, adres, telefoon,
  fax, e-mail, website, KvK (`businessnumber`). `PrintData.company_details` leest die al.
- IBAN/BIC komen nu uit de bankrekening van de betaling/factuur (`bank`-tabel via het rekeningschema).
- Documenttaal (`company.doc_language`), varianten, Beheer > Sjablonen (upload/download/logo) uit stap 2.
- In SL 2.8 stonden company/tel/fax/e-mail/sjabloonset in `users/<naam>.conf` (per gebruiker!); in SL 3 en bij
  ons per administratie in `defaults`. Alles wat administratie A vast in de .tex had, is nu nog niet instelbaar.

### Uitwerking (keuzes binnen A, voorstel)
1. **Opslag** (herzien, akkoord 2026-10-10): eigen tabel `x_doc_setting (key, value)` in de
   database van de administratie (migratie in `extensions.py`), **niet** in `defaults`: SL 3.2.12 wist bij
   opslaan van Systeem > Standaarden alle onbekende `defaults`-sleutels. Lijsten (kolommen) als JSON-tekst.
2. **Bedrijf op documenten**: btw-nummer (`taxnumber`, nieuw), plus verwijzing naar de bestaande velden.
3. **Bank**: keuze "rekening van de boeking" (huidig gedrag) of één vaste bankrekening uit het rekeningschema;
   tonen: IBAN, BIC, banknaam (`bank.name`). Betaalzin onderaan aan/uit.
4. **Teksten** (per documenttaal nl/en/de/fr, leeg = standaardtekst): retouradresregel boven het adres (aan/uit,
   eigen tekst), voettekst (vervangt de standaardvoet), per documentsoort slottekst ("Dank voor uw bestelling.")
   en een opmerking onder de totalen (bijv. "Kortingspercentage wordt over colli berekend").
5. **Kolommen per documentsoort** (factuur/credit, inkoop, order/offerte, pakbon, picklijst): vaste kolommen
   (artikelnr., omschrijving, aantal, eenheid, prijs, bedrag) + aan/uit: EAN (`barcode`), artikelcode
   (`hscode`), leverdatum, serienummer, pallet/verpakking (`package`), korting, gewicht; eigen kolomnaam per
   kolom (administratie A: serienummer = "Houdbaar", eenheid = "Eenh.Collo", artikelcode = "Art."). Extra regel onder de
   omschrijving: veld kiezen + label (administratie A: "Netto per CE:" + `countryorigin`).
6. **Gewichtstabel** onder de totalen aan/uit (per regel gewicht, totaalgewicht).
7. **Voorbeeld**: knop "Voorbeeld-PDF" op het scherm (laatste document van de soort, bestaande route `?voorbeeld=1`).
8. **Eigen geüpload sjabloon** krijgt dezelfde instellingen als variabelen (`doc.*`), zodat ook daar niets vast
   hoeft.
9. **Toetsen**: pytest (opslaan/lezen, rollen, elke instelling zichtbaar in de PDF-HTML, standaard = huidige
   uitvoer ongewijzigd), administratie A ingesteld → voorbeeld-PDF naast de oude layout (`dev/voorbeeld-pdf/stap3/`),
   browsercontrole incl. 375px, schermteksten NL/EN/DE/FR (i18n-test).
10. **README**: webtabel "Documentinstellingen", afwijking t.o.v. SL (ini/vaste sjablonen → instellingen).

Volgorde: opslag + scherm (1–4) → kolommen/gewichtstabel in de standaardset (5–6) → voorbeeld, `doc.*`, instelling
administratie A, toetsen, README (7–10).

**Gebouwd (2026-10-10, branch `fase4-stap3`)**: 1–10 zoals hierboven, met T2 (eigen tabel) en T4 (lichte opmaak).
Zie OVERDRACHT voor de toetsen; keuzes binnen A ter keuring in README "Open punten".

## Stap 4 — aanmaningen, rekeningoverzicht, betalingsspecificatie, batch, herhalen (analyse 2026-10-10)

**Status: akkoord gebruiker 2026-10-10, SB1–SB16 alles A; deelstappen 4a–4d apart keuren.**
4a en 4b af (akkoord); 4c (batch, SB9–SB11, SB14) af 2026-10-10, keuzes 1–7 akkoord; 4d (herhalen, SB12–SB13)
af 2026-10-10, keuzes binnen A 1–8 akkoord; daarna 4e (inkoopfactuur uploaden).
Kader: D8, D10, D11, D13, D15, D21, D22 (alles A) en stap 3 (documentinstellingen). Beslispunten **SB1–SB16**, advies
steeds A. Waar A afwijkt van de letterlijke tekst van een D-punt staat dat erbij.

### Echte data
- **Nooit gebruikt** in administratie A en C: aanmaningen (`status` reminder1–3: 0), rekeningoverzichten, batch/spool (1 spoolbestand
  in C), print/mail vanuit herhaalschema's (`recurringemail`/`recurringprint` leeg; A 3 verlopen schema's, C geen).
  Printed/emailed in `status` nauwelijks bijgehouden (A 21, C 38 rijen).
- **Open posten**: administratie A 0, administratie C 64 posten bij 32 klanten (GBP 60, EUR 4; 5 negatief), laatste boekdatum 29-06-2016 →
  bruikbare testset met peildatum 29-06-2016 (31 niet vervallen, 25 0–30, 7 30–60, 1 >90). Alle 32 klanten hebben e-mail.
- **E-mail klanten**: administratie A 33 van 399, C 692 van 835 (cc 162, bcc 12, 7 met meerdere adressen in één veld).
- **Betalingen**: één betaling voor meerdere facturen is gewoon (A: AP tot 53, C: AP tot 55 facturen). Het kenmerk
  (`source`) is bijna altijd leeg (A: 5 van ±14.000), dus een oude betaling is alleen te herkennen aan relatie +
  betaalrekening + datum. Korting bij betaling alleen bij verkoop van A (1.864 regels).
- Gevolg voor toetsen: aanmaningen, overzichten, batch en herhalen met **synthetische data** + de open posten van C.

### Hoe SQL-Ledger het doet (3.2.12)
- **Aanmaning** (`rp.pl generate_reminder`, `RP::reminder`): open AR-facturen met vervaldatum ≤ vandaag (geen peildatum),
  **één document per factuur**, sjabloon `reminder<niveau>`. Niveau = hoogste `status.reminderN` + 1; ophogen alléén met
  de knop "Save Level", niet bij printen/mailen. Mailen één per keer; geen status/audittrail/notitie bij printen of mailen.
  Creditnota's (negatief open) komen ook in de lijst. Bankgegevens van de factuur (`bank_id`) gaan mee.
- **Rekeningoverzicht** (`rp.pl aging` → print/mail, alleen AR): één document per klant en valuta, kolommen c0/c15/…/c90
  naar keuze, sjabloon `statement`, bedragen in de factuurvaluta. Open bedrag = **actueel** (`paid` nu, niet per peildatum).
  Geen status/audittrail. Onze `Reports.aging` doet hetzelfde (actuele `paid`).
- **Batch** (`bp.pl`, menu Batch → Print/E-mail/Queue per soort): alleen facturen met artikelen (`invoice='1'`) en orders;
  filters open/gesloten, (niet) geprint, (niet) gemaild; per document `print_form`; spool + combineren met pdftk. De
  zoeklijsten zelf hebben geen "selectie printen/mailen". Geen audittrail in een batch.
- **Herhalen** (`am.pl process_transactions`): na het boeken per formname een subproces `reprint` naar printer of mail;
  bericht uit het schema, ontvangers uit relatie/boeking, onderwerp "<soort> <nummer>".
- **Betalingen** (`cp.pl print_payment(s)`): formulieren `receipt` (verkoop) en `check` (inkoop), alleen PDF, geen mail;
  per relatie de betaalde facturen met bedrag, korting en overschot. Geen status. De "remittance voucher" van SL is
  géén betalingsspecificatie maar een Zwitsers betaalstrookje bij de factuur (README 181: niet).

### Perl-fouten (naar de memo; nummering loopt door; 24 en 29 = beveiliging, eerst privé)
19. Rekeningoverzicht: klant met open posten in twee valuta krijgt maar één overzicht (`rp.pl:3450`).
20. Ouderdom: `c45` dubbel (`RP.pm:1313`); met alleen kolom c0 vallen facturen van de peildatum zelf weg (`RP.pm:1331`);
    open bedrag = actueel i.p.v. per peildatum (`RP.pm:1253/1288`).
21. Aanmaning na niveau 3: lijst toont 1, opslaan zet terug naar 1 (`rp.pl:2656`, `RP.pm:1582`, `Form.pm:326`); meerdere
    reminder-rijen geven dubbele regels (`RP.pm:1580`); `current_date` i.p.v. peildatum; creditnota's in de lijst.
22. Status `printed`/`emailed` en de `[email]`-notitie worden gezet **vóór** het echte printen/versturen (`io.pl:1829/1859`,
    `bp.pl:529`); in een batch geen audittrail (`io.pl:1838`); printen uit de wachtrij zet niets (`bp.pl:423`).
23. Herhalen: mislukt mailen → schema schuift toch door, niet opnieuw; mislukt printen na boeken → schema schuift níét
    door → volgende run boekt een **dubbele factuur** (`am.pl:4784/4893`); `$myconfig->{printer}` altijd leeg (`am.pl:4917`); orders mailen ook als opslaan mislukte (`am.pl:4846`).
24. (beveiliging) SQL-injectie in rapporten en batch: beveiligingsfout in SQL-Ledger; wordt gemeld aan de
    onderhouders, details volgen daarna.
25. Overig: `print_payments` geeft geen korting door (`cp.pl:2043`); `audittrail` met transdate geeft een SQL-fout die
    stil wordt ingeslikt (`Form.pm:5191`); sendmail-sluiten niet gecontroleerd, charset "UTF8" (`Mailer.pm:33/123`).
26. (stap 4b) Scherm voor meerdere relaties (`cp.pl post_payments`): `customer_id`/`vendor_id` wordt maar één keer
    gezet, op de eerste relatie in de lijst (`cp.pl:1869`); een vooruitontvangst/-betaling van een
    andere relatie wordt daardoor bij die eerste relatie geboekt (met het kenmerk van de juiste). Gevonden met
    `compare/compare_payspec.py`.
27. (stap 4b) Zelfde scherm: bij een post met betalingskorting waarop niets meer overblijft wordt "betaald" min de
    korting geboekt (`cp.pl:1856`): een negatieve betaling ter grootte van de korting, die als
    vooruitontvangst bij (volgens 26) de eerste relatie terechtkomt.
28. (stap 4e) Bijlagen: `Form::save_reference` en `Form::delete_references` wissen een archiefstuk zodra de verwijzing
    van déze boeking verdwijnt, ook als `rd.pl` "Koppelen aan" (`RD::attach_document`) hetzelfde stuk aan een andere
    boeking hing (`Form.pm:4035/4136`); alleen `RD::delete_document` telt de verwijzingen. Gevolg:
    verwijderen of opnieuw opslaan van de ene boeking haalt het bestand bij de andere weg. "Opslaan als nieuw" in
    aa.pl neemt de verwijzing mee en maakt zo ook een gedeeld stuk. Bij ons opgelost (`support._delete_unused_archive`,
    kopiëren neemt geen bijlagen mee), toets `tests/test_attachments.py`.
29. (beveiliging, stap 4e) SQL-injectie bij het koppelen van bijlagen: beveiligingsfout in SQL-Ledger;
    wordt gemeld aan de onderhouders, details volgen daarna.

### Wat er bij ons al is
- Aanmaningslijst en ouderdomsanalyse als alleen-lezen rapport (`reportdefs.run_reminders`/`run_aging`,
  `reports_arap.reminders`/`aging`), per factuur, peildatum, voorgesteld niveau. `save_reminder_levels` bestaat maar
  wordt in het scherm niet gebruikt.
- Herhaalschema's bewaren `recurringemail`/`recurringprint` (uit Perl-data) maar voeren ze niet uit; het schemascherm
  heeft er geen velden voor.
- Documenten: 14 soorten, alle met één `trans_id`; PDF, mailscherm, status, notitie, documentinstellingen, sjabloonbeheer.
  Samenvoegen van PDF's en documenten op relatie- of betalingsniveau bestaan nog niet.
- Betalingen boeken (één/meerdere relaties, corrigeren), `payment_register`; geen PDF/mail.
- Lijsten orders/offertes en facturen (rapport) hebben geen selectievinkjes.

### Beslispunten (advies steeds A)

**Aanmaningen (D13)**

**SB1 Eenheid.** A: **één aanmaning per klant en valuta** met alle vervallen facturen van die klant (tabel factuur,
datum, vervaldatum, open bedrag, dagen vervallen, niveau), zoals D13 al zei. Het niveau blijft **per factuur** in
`status.reminderN` (compatibel met SL); het document gebruikt het hoogste niveau van de opgenomen facturen
(tekst aanmaning 1/2/3). B: zoals SL één document per factuur.

**SB2 Selectie.** A: zoals de huidige lijst (peildatum, vervaldatum ≤ peildatum, klant/afdeling), plus **minimum aantal
dagen vervallen** (standaard 0) en **creditnota's/negatieve posten niet als regel om te betalen** maar onder "te
verrekenen", in mindering op het totaal. Klant waarvan het totaal ≤ 0 is: geen aanmaning. Facturen met `onhold` (4.x)
bestaan bij ons niet. B: alles zoals SL (ook negatieve posten als aanmaning).

**SB3 Niveau ophogen.** A: bij **definitief** printen (PDF zonder "voorbeeld") of **geslaagd** mailen wordt het niveau
van de opgenomen facturen automatisch opgeslagen (+1, maximaal 3), met `printed`/`emailed` in dezelfde
`status.reminderN`-rij en een regel in het logboek (audittrail). Daarnaast op het scherm per factuur het niveau
handmatig zetten of terugzetten (0–3, 0 = wissen). Na niveau 3 blijft het 3 (Perl-fout 21). B: zoals SL alleen met een
aparte knop "Niveau opslaan".

**SB4 Scherm.** A: de bestaande lijst Verkoop > Aanmaningen wordt een **werkscherm** (POST): per klant een blok met
vinkjes per factuur, knoppen Voorbeeld, PDF (geselecteerde klanten samen in één PDF), Mailen (per klant een eigen mail
naar het klantadres + cc/bcc, overzicht vooraf: wie geen e-mailadres heeft valt af), Niveau opslaan. CSV/Excel blijven.
B: los nieuw scherm naast de bestaande rapportlijst.

**SB5 Rekeningoverzicht.** A: per klant **en per valuta** (Perl-fout 19 opgelost) vanuit de ouderdomsanalyse (alleen
verkoop): vinkjes per klant, Voorbeeld/PDF/Mailen zoals SB4. Kolommen = de kolommen die de ouderdomsanalyse al heeft
(0/30/60/90). Open bedrag **per peildatum** (betalingen ná de peildatum tellen niet; ook in de ouderdomsanalyse zelf
rechtgezet, als bewuste afwijking + vergelijker verklaard). Geen status (SL heeft er geen), wel logboek. B: actueel open
bedrag zoals SL (overzicht met een oude datum klopt dan niet).

**SB6 Teksten.** A: standaardsjablonen `reminder1`, `reminder2`, `reminder3` (één gedeeld sjabloon met tekst per niveau),
`statement` in NL/EN/DE/FR, opgebouwd als de factuur (kop, adres, bank, voet uit Documentinstellingen). In
**Documentinstellingen** een groep "Aanmaningen en overzichten": inleidende en afsluitende tekst per niveau en per taal
(leeg = standaard), en de standaard mailteksten per soort in Beheer > E-mail zoals bij de andere soorten. Taal = taal van
de klant (`language_code`), anders documenttaal administratie. B: alleen vaste teksten.

**Betalingsspecificatie (D15)**

**SB7 Inhoud en herkenning.** A: document **betalingsspecificatie** per betaling per relatie: datum, betaalrekening,
kenmerk, per factuur nummer/datum/bedrag/betaald/korting, overschot (vooruitbetaling), totaal. Verkoop (`receipt`) en
inkoop (`check`, naar de leverancier: "wij hebben betaald"). Een betaling = relatie + betaalrekening + datum + kenmerk
(zoals `payment_register`); bij oude data zonder kenmerk worden betalingen op dezelfde dag samengenomen (staat in de
uitleg). Geen eigen betalings-id/tabel. B: nieuwe tabel `x_payment` met een id per betaling (alleen nieuwe betalingen
herkenbaar, oude niet).

**SB8 Waar.** A: knoppen PDF/E-mail in de melding na het boeken (één relatie: direct; meerdere relaties: per relatie +
"alle als één PDF" en "alle mailen"), op het corrigeerscherm en als regelactie in het **betalingsregister** (menu-item
dat nu op "F4 gepland" staat, wordt gebouwd als lijst betalingen met filters). Geen `status` (SL heeft er geen),
wel logboek. B: alleen na het boeken.

**Batch (D10)**

**SB9 Plek.** A: **één scherm "Documenten > Batch"** (zoals SL `bp.pl`) i.p.v. vinkjes in elke zoeklijst: soort kiezen
(factuur, creditfactuur, inkoopfactuur, verkoop-/inkooporder, offerte, aanvraag, pakbon, picklijst, AR/AP-transactie),
filters periode, relatie, open/gesloten, **niet geprint / niet gemaild** (zoals SL); lijst met vinkjes en status;
acties **één samengevoegde PDF** of **ieder z'n eigen mail**. Reden: de factuurlijsten zijn generieke rapporten (GET,
geen vinkjes) en één plek met de "nog niet verstuurd"-filters is handiger. Wijkt af van de letterlijke tekst van D10
("bestaande zoeklijsten"). B: vinkjes in de bestaande lijsten (orders/offertes en rapporten overzicht verkoop/inkoop).

**SB10 Status en logboek.** A: `printed` alleen na een gelukte samengevoegde PDF, `emailed` en de `[email]`-notitie alleen
na geslaagde verzending (Perl-fout 22 opgelost), audittrail per document ook in een batch. Een "voorbeeld" van de
samengevoegde PDF zet niets. B: zoals SL vooraf zetten.

**SB11 Mailen in batch.** A: synchroon in het verzoek, maximaal **200 documenten per keer** (meer = melding om te
splitsen); overzicht vooraf (wie wordt overgeslagen: geen e-mailadres); onderwerp/bericht = standaardtekst per soort
(eventueel één keer aan te passen voor de hele batch, met plaatshouders per document); resultaat per document
(verzonden/fout + reden); één fout stopt de rest niet. Geen wachtrij/spool (D8). B: achtergrondtaak met voortgang.

**Herhalen (D11, T8)**

**SB12 Uitvoeren.** A: na het verwerken van de schema's: mailen per nieuw document volgens `recurringemail` (bericht uit
het schema, ontvangers uit relatie/boeking, standaardonderwerp); "printen" volgens `recurringprint` = **één
samengevoegde PDF van alle nieuwe documenten** als download op de resultaatpagina (printernaam genegeerd, D7). Mislukt
mailen: de boeking blijft, het schema schuift door (de boeking is gelukt), de fout staat in het resultaat en het
logboek, `emailed` niet gezet; opnieuw mailen kan vanaf het document. Printen kan de doorloop nooit tegenhouden
(Perl-fout 23: dubbele factuur). B: zoals SL (doorschuiven hangt af van het printen).

**SB13 Instellen.** A: het schemascherm krijgt de keuzes **Mailen** (welke documentsoort, bericht) en **PDF** (welke
documentsoort), met de soorten die bij die boeking horen (factuur: factuur/pakbon/picklijst; order: order/pakbon …).
Formnames zoals SL (`invoice`, `sales_order`, `transaction` …), zodat Perl-schema's werken en andersom. B: alleen
uitvoeren wat al in de Perl-data staat.

**Algemeen**

**SB14 Techniek samenvoegen.** A: WeasyPrint rendert elk document apart (eigen sjabloon, taal, instellingen) en de
pagina's worden samengevoegd tot één PDF (`document.copy(pages)`), paginanummering per document. Geen pdftk of andere
externe tool. B: één grote HTML met pagina-einden (één sjabloon per batch).

**SB15 Rollen.** A: aanmaningen, overzichten, batch en betalingsspecificatie: boekhouder en hoger (zoals PDF met status
en mailen nu); alleen-lezen rollen zien alleen voorbeelden. Herhalen zoals nu.

**SB16 Toetsen.** A: pytest (bibliotheek + web, nep-SMTP en mapmodus). Perl-vergelijking van de **gegevens**:
driverstappen `reminder_ui` (rp.pl print reminder, `parse_template` onderschept), `statement_ui` (rp.pl print statement)
en `payment_print_ui` (cp.pl print), vergeleken met onze gegevens per factuur/regel (indeling per klant is bewust anders,
SB1), op synthetische data en administratie C (peildatum 29-06-2016); Perl-fouten 19–21 en 25 als verklaarde verschillen.
Batch en herhalen: pytest (SL doet het via subprocessen/printers, niet zinvol te vergelijken). Regressie: standaardset
run_compare, `dev/doc_html_dump.py` (bestaande documenten ongewijzigd), browsercontrole incl. 375px.

### Volgorde binnen stap 4 (voorstel)
4a aanmaningen + rekeningoverzicht (SB1–SB6, incl. peildatum in de ouderdomsanalyse) → 4b betalingsspecificatie +
betalingsregister (SB7–SB8) → 4c samenvoegen + batch (SB9–SB11, SB14) → 4d herhalen (SB12–SB13). Eén branch
`fase4-stap4` (eigen worktree), keuring per deelstap of in één keer naar keuze.

## Btw-tarieven wijzigen (vraag gebruiker 2026-10-10, tijdens stap 4b) — beslispunten BT1–BT2

**Status: akkoord gebruiker 2026-10-10, BT1 en BT2 beide A; BT1 gebouwd op `fase4-stap4` (README 203).**

**Vraag.** Een boeking met btw 0 moet goed gaan; btw-tarieven moet je kunnen invullen en wijzigen, en bij een
wijziging moeten oude verkoop- en inkoopfacturen hun oude waarden houden.

**Onderzocht (toetsen `tests/test_tax_rates.py`).**
- **Btw 0** (verkoop en inkoop, artikel met een 0%-rekening en artikel zonder btw): geboekt zonder btw-regel,
  totaal = netto, op de factuur een regel "Btw 0%" met 0,00 (zoals SQL-Ledger IS.pm "process 0 taxes"),
  opnieuw opslaan verandert niets, betalingsspecificatie klopt. **In orde.** (Let op: zoals in SQL-Ledger telt
  een btw-rekening alleen als zowel het artikel als de klant/leverancier hem heeft.)
- **Invullen en wijzigen** kan in Beheer > Btw: per btw-rekening tariefregels met "geldig tot"; een nieuw tarief =
  einddatum op het huidige tarief zetten + "Tarief toevoegen". Dan houden oude facturen hun tarief: document,
  doorrekenen en opnieuw opslaan blijven 21%, een factuur na de ingangsdatum krijgt 23%. **In orde.**
- **Probleem:** typ je in het tariefveld van het huidige tarief gewoon een ander percentage (of verwijder je een
  regel), dan wordt het oude tarief overschreven. Het grootboek blijft staan, maar een oude factuur toont bij
  afdrukken het nieuwe tarief en een ander totaal dan geboekt (25% / 125,00 bij geboekt 121,00), en opnieuw
  opslaan boekt hem om naar het nieuwe tarief. SQL-Ledger (AM::save_taxes) doet hetzelfde. Vastgelegd als
  `xfail`-toets (`test_overwriting_used_rate_keeps_old_invoices`).

**BT1 Gebruikt tarief beschermen.** A: een tariefregel waarin al geboekt is (er bestaat een verkoop-, inkoop- of
orderboeking met een datum binnen de periode van die regel) kan in Beheer > Btw niet meer van percentage
veranderen of verwijderd worden, en de "geldig tot" kan niet vóór de laatst gebruikte datum gezet worden; het
nummer mag wel. Een nieuw tarief gaat via **"Nieuw tarief per datum"** (percentage + ingangsdatum): de huidige
regel krijgt automatisch "geldig tot" = de dag vóór de ingangsdatum. Melding bij een poging tot overschrijven,
bv. "Het tarief van 21% is al gebruikt in boekingen tot en met 01-03-2026. Voeg een nieuw tarief toe met een
ingangsdatum." Geldt ook voor 0%. B: alleen een waarschuwing, opslaan mag toch. C: zoals nu/SQL-Ledger.

**BT2 Bestaande documenten.** A: het document rekent, zoals nu en zoals SQL-Ledger, met het tarief uit de periode
van de factuurdatum; met BT1 is dat altijd het oorspronkelijke tarief. B: de btw op een bestaand document altijd
uit de geboekte bedragen halen (robuuster, ook voor oude Perl-data die al overschreven is, maar een grotere
wijziging en anders dan SQL-Ledger).

## Inkoopfactuur uploaden (wens gebruiker 2026-10-10) — beslispunten IU1–IU8

**Status: akkoord gebruiker 2026-10-10, IU1–IU8 alles A; wordt stap 4e (na 4d), eigen branch.**

**Wens.** Bij een nieuwe inkoopfactuur de PDF erin slepen, de juiste leverancier en het totaalbedrag kiezen; de rest
(btw, kostenrekening, vervaldatum …) vult de applicatie in vanuit de leverancier.

**Echte data (inkoopboekingen zonder artikelen, bedrag > 0).**
- Administratie A: 14.171 boekingen (met artikelen maar 218): 14.132 met één kostenrekening; bij 12.525 van 13.769 (91%) is dat
  dezelfde rekening als bij de vorige boeking van die leverancier. Btw: 11.857 één btw-rekening, 2.193 geen, 121 twee.
- Administratie C: 8.258 (met artikelen 211): 6.417 met één kostenrekening (1.841 meer), 73% gelijk aan de vorige boeking;
  btw: 5.048 één, 3.207 geen.
- Een leverancier heeft in SQL-Ledger **geen standaard kostenrekening** (wel btw-rekeningen `vendortax`, termijn,
  valuta, crediteuren-, betaal- en kortingsrekening, "btw inbegrepen"). 45 leveranciers van A en 32 van C hebben twee
  btw-rekeningen gekoppeld; SL rekent dan beide (aa.pl), wat bij één totaalbedrag fout gaat.
- Bijlagen: SL 3.2.12 kan bestanden bij een boeking bewaren (`rd.pl`, `Form.pm` r3954/4051: `reference`,
  `archive`, `archivedata`); in administratie A en C nooit gebruikt (0 rijen). D17 (A, akkoord) kiest al voor die tabellen.

**IU1 Scherm.** A: menu Inkoop > **Inkoopfactuur uploaden**: een sleepvak (plus gewone bestandskeuze, ook foto
vanaf de telefoon); na het slepen staat de PDF naast het formulier (op de telefoon eronder) met alleen
**leverancier, factuurdatum, factuurnummer en totaalbedrag**; de rest is voorgesteld (IU3–IU5) en zichtbaar,
aan te passen; knoppen **Boeken** en **Volledig scherm** (het gewone boekingsscherm, alles ingevuld, de PDF als
bijlage). Het sleepvak staat ook op het gewone scherm Inkoop > Boeking toevoegen. B: alleen een sleepvak op het
gewone boekingsscherm (geen kort formulier).

**IU2 Eén of meer tegelijk.** A: **één factuur per keer** (slepen → boeken → volgende). B: ook een **bak "te
boeken"**: meerdere PDF's tegelijk slepen, die blijven bewaard (eigen tabel) en worden één voor één afgehandeld;
meer werk (status, wie pakt wat op, opruimen).

**IU3 Kostenrekening.** A: voorstel = de kostenrekening van de **laatste boeking van deze leverancier** (administratie A 91%,
C 73% raak); had die boeking meerdere kostenregels, dan de grootste met een melding (splitsen kan in Volledig
scherm). Daarnaast in het leveranciersscherm een **vaste standaard kostenrekening** (optioneel, eigen tabel
`x_vendor_default` omdat SL onbekende kolommen niet kent) die vóór de laatste boeking gaat. Geen boeking en geen
vaste rekening: veld leeg, verplicht kiezen. B: alleen de laatste boeking. C: alleen een vaste instelling.

**IU4 Btw.** A: het **totaalbedrag is inclusief btw**. Btw-rekening(en) = die van de laatste boeking van deze
leverancier (voor zover nog aan de leverancier gekoppeld), anders de gekoppelde btw-rekeningen; bij twee of meer
zonder historie kies je er één. Netto = totaal / (1 + tarief op de factuurdatum), btw = totaal − netto (afgerond
op 2 decimalen), live zichtbaar. Staat op de factuur een ander btw-bedrag (afronding), dan kun je dat invullen; het
netto past zich aan zodat het totaal gelijk blijft. Geen btw-rekening (bv. buitenland, vrijgesteld): netto = totaal.
B: zoals SL alle gekoppelde btw-rekeningen tegelijk.

**IU5 Overige velden.** A: zoals het gewone scherm: vervaldatum uit de betalingstermijn, valuta en
crediteurenrekening van de leverancier, afdeling van de laatste boeking; omschrijving leeg (optioneel);
**factuurnummer verplicht** met de bestaande waarschuwing bij een dubbel nummer van dezelfde leverancier; boeken via
dezelfde bibliotheek (`arap`) als het gewone scherm, dus dezelfde controles (boekperiode, koers). B: factuurnummer
niet verplicht.

**IU6 Opslag van de PDF.** A: volgens D17 in `reference`/`archive`/`archivedata` (codering zoals SL, zodat SL hem
ook ziet), gekoppeld aan de boeking; pdf/jpg/png tot 10 MB, gecontroleerd op inhoud (niet op bestandsnaam); op het
boekingsscherm een blok **Bijlagen** (bekijken, downloaden, verwijderen, nog een toevoegen). Daarmee komt de basis
van D17 naar voren: bijlagen bij verkoop-, inkoop- en memoriaalboekingen; bij relaties en artikelen blijft het
voor stap 6. B: alleen de PDF bij inkoopboekingen, de rest van D17 in stap 6.

**IU7 Herkennen van de factuur.** A: **nu niet**: jij kiest leverancier en bedrag, de PDF staat ernaast. Later als
uitbreiding mogelijk: tekst uit een digitale PDF lezen en een voorstel doen (leverancier via IBAN/btw-nummer,
totaal via "Totaal"), lokaal, zonder externe dienst. B: dat voorstel nu al bouwen (werkt niet bij scans/foto's).
C: OCR/AI-dienst (factuurgegevens gaan naar een externe partij).

**IU8 Plek in de planning.** A: als **stap 4e** direct na 4d (vóór 5 rapporten-PDF, 6 rest bijlagen, 7 import),
op een eigen branch. B: nu meteen, vóór 4d. C: pas in stap 6 bij de bijlagen.

**Toetsen (bij A).** pytest (voorstellen, btw-berekening incl. afronding en overschrijven, verplichte velden,
bestandscontrole, rollen); de boeking zelf via de bestaande Perl-vergelijking `aa_ui` (zelfde bedragen → zelfde
grootboek); een bijlage die Python opslaat moet SL (`rd.pl`) kunnen openen en andersom (driverstap); echte data:
voorstel kostenrekening op administratie A en C terugrekenen tegen de werkelijke boekingen (hoe vaak raak); browsercontrole
incl. 375px en slepen.

## Opslag en back-up van bijlagen; maximale uploadgrootte (vraag gebruiker 2026-10-10, na stap 4e) — BB1–BB4, MU1–MU5

**Status: akkoord, alles A (gebruiker 2026-10-10).** BB1–BB4 A (BB2 met het back-uponderdeel van fase 6, BB3/BB4 met
stap 6); MU1–MU5 A → stap **4f** op branch `fase4-stap4f`, vóór stap 5.

**Stand na 4e.** Bijlagen staan in de database van de administratie (`reference`/`archive`/`archivedata`, D17 A), zoals
SQL-Ledger 3.2.12: elk bestand in blokken van 512 bytes als uuencode-tekst. Gemeten: een PDF van 44,6 kB
kost 62,0 kB, een foto van 1,0 MB kost 1,46 MB (×1,39); PostgreSQL comprimeert de kleine rijen niet; 1 MB = ruim
2.000 rijen. Schatting: administratie A ±700–800 inkoopfacturen per jaar (56 MB boekhouding), C ±1.400 (37 MB); bij 200 kB per
factuur ±0,2 GB per jaar (7 jaar bewaarplicht ±1,5 GB), met telefoonfoto's (2–4 MB) 1–2 GB per jaar. De bijlagen
worden dan >95% van de database: back-up groot en traag, boekhouding niet apart terug te zetten.

**Was SL slim?** In de database bewaren: verdedigbaar (één back-up, boeking + bijlage in één transactie). De codering
(+38%, geen compressie, duizenden rijen, `bytea` had gekund) en het ontbreken van een aparte back-up: niet.

**SQL-Ledger 4.x.** Opslag ongewijzigd (zelfde tabellen en codering, geen aparte back-up). Nieuw: een kolom
`archive.hash` (sha256) en een `defaults`-sleutel `max_upload_size` (MB) in Systeem > Standaarden.

**Mogelijkheden opslag.** A: zoals nu in de SL-tabellen. B: losse bestanden (schijf/objectopslag, naam = sha256), in de
database alleen de gegevens; boekhouding klein, bijlagen apart en incrementeel te back-uppen (zoals Exact); SL ziet
nieuwe bijlagen niet. C: aparte PostgreSQL-database: geen gezamenlijke transactie, SL ziet niets, geen voordeel boven B.

**BB1 Waar opslaan.** A: nu in de SL-tabellen zolang SQL-Ledger parallel draait; bij de overstap (fase 6, SL uit)
automatisch omzetten naar losse bestanden met sha256 (B), met een omzetscript (administratie A en C hebben in SL nooit bijlagen
gebruikt). B: nu al losse bestanden (SL ziet nieuwe bijlagen niet).

**BB2 Back-up in twee delen.** A: ook zolang alles in de database staat twee back-ups, "boekhouding" (alles behalve de
inhoud van `archivedata`) en "bijlagen" (met PostgreSQL zelf, `pg_dump --exclude-table-data` / `-t`); hoort bij het
back-up-onderdeel van fase 6. B: één back-up zoals nu.

**BB3 Foto's verkleinen.** A: jpg/png bij uploaden verkleinen tot een goed leesbare scan (lange kant hoogstens 2.400 px,
meestal 0,3–0,6 MB); PDF's nooit aanraken (digitaal ontvangen factuur in oorspronkelijke vorm bewaren). B: niets
verkleinen.

**BB4 Dubbele bestanden.** A: sha256 opslaan (in `archive.hash` als die kolom bestaat, SQL-Ledger 4.x, anders eigen tabel)
en **waarschuwen** als hetzelfde bestand al bij een andere boeking hangt (vaak een dubbel geboekte factuur), niet
stilzwijgend delen. B: ontdubbelen (één archiefstuk delen). C: niet controleren.
Planning BB3/BB4: met stap 6 (rest bijlagen), tenzij anders gekozen.

**MU1 Waar bewaren (instelbare maximale uploadgrootte, eis gebruiker: moet erin).** A: `defaults`-sleutel
`max_upload_size` in MB, zodat SQL-Ledger 4.x dezelfde waarde gebruikt; let op: SL 3.2.12 wist hem bij opslaan van
Systeem > Standaarden, wij vallen dan terug op de standaard. B: eigen tabel (overleeft dat, SQL-Ledger 4.x ziet hem niet).

**MU2 Standaard en grenzen.** A: leeg = 10 MB (zoals nu), instelbaar 1–25 MB (bovengrens beschermt de server: het
bestand wordt in zijn geheel gelezen en kost ×1,39 in de database). B: leeg = geen grens.

**MU3 Waar het geldt.** A: alle bijlagen (uploadscherm en blok Bijlagen); bankafschrift (10 MB), sjablonen en logo houden
hun eigen vaste grens. B: ook daar.

**MU4 Controle.** A: altijd op de server, daarnaast in de browser vóór het versturen (geen wachttijd voor een te groot
bestand). B: alleen in de browser.

**MU5 Planning.** A: kleine stap **4f** op een eigen branch, vóór stap 5 (plek: Beheer > Instellingen). B: met stap 6.

---

## Stap 5 — rapporten als PDF (D14) — analyse 2026-10-10

**Status: akkoord gebruiker 2026-10-10, RP1–RP10 alles A.** Kader: D2, D5, D14, D21, D22 (alles A).

**Wat er nu is.** Alle rapporten onder `/rapporten/<key>` (22 stuks in `reportdefs.REPORTS`) gebruiken één model
(`tables.Table`/`Col`/`Row`) voor scherm, CSV en Excel; daarnaast hebben relaties, artikelen, orders/offertes,
afgeletterde posten, bankafschrift en stuklijsten/productiehistorie hetzelfde model met eigen CSV/Excel. Er is al
een knop **Afdrukken** (browser, `@media print`), maar die drukt alleen de getoonde schermpagina af (lange rapporten
zijn in pagina's van 500 regels verdeeld). De PDF-engine (WeasyPrint, `documents.py`) staat er sinds stap 0.

**SQL-Ledger.** `rp.pl print_report` is een stub (`$form->debug`): SL kon rapporten niet als PDF maken. Alleen
W&V en balans hadden "Sjabloon gebruiken" (`income_statement.html`/`balance_sheet.html`, HTML op het scherm); Administratie A
en C hebben daar eigen versies van (Nederlandse kop, kolom vorig jaar). Er is dus niets om tegen Perl te vergelijken.

**Meting WeasyPrint** (tabel met 6 kolommen, A4): 1.000 regels = 16 blz. in 2 s; 5.000 = 76 blz. in 13 s;
20.000 = 304 blz. in 63 s.

**RP1 Welke schermen.** A: alle 22 rapporten onder Rapporten/Verkoop/Inkoop/Kas en bank **plus** de lijsten met
hetzelfde tabelmodel (relaties, artikelen, orders/offertes, afgeletterde posten, bankafschrift, stuklijsten,
productiehistorie) en de leespagina van een boeking: één gedeelde functie, dus weinig extra werk. Niet: audittrail.
B: alleen de 22 rapporten. C: alleen balans, W&V, proefbalans, rekeningmutaties en openstaande posten.

**RP2 Hoeveel regels.** A: de PDF bevat **alle** regels (niet alleen de schermpagina), tot **5.000 regels**
(± 75 blz., ± 15 s); daarboven een melding "te groot voor PDF, verfijn de selectie of gebruik Excel". B: grens
20.000 (tot ruim een minuut wachten). C: alleen de getoonde schermpagina.

**RP3 Kop en voet.** A: elke pagina een kop met administratienaam, rapporttitel en periode/filters (zoals boven het
scherm), kolomkoppen herhaald op elke pagina, beginsaldo/totalen zoals op het scherm; voet met "Afgedrukt op
<datum tijd>" en "Pagina x van y". Geen logo, geen bedrijfsadres. B: eerste pagina ook logo + adres (zoals het
SL-sjabloon `<%company%> <%address%>`).

**RP4 Staand of liggend.** A: automatisch op breedte: tot ± 7 kolommen staand, breder liggend; heel brede rapporten
(balans/W&V per maand met vorig jaar = tot 26 bedragkolommen) liggend met kleinere letter (min. 6,5 pt); nooit
kolommen afkappen. B: als A, plus een keuze Automatisch/Staand/Liggend bij "Meer opties". C: altijd staand.

**RP5 Taal en notatie.** A: schermtaal en getal-/datumnotatie van de gebruiker (zoals het scherm en CSV/Excel;
rapporten zijn intern). B: documenttaal van de administratie.

**RP6 Sjabloon.** A: één vaste opmaak in de code (`doctemplates/standard/_report.html`), niet via Beheer >
Sjablonen of Documentinstellingen; de SL-HTML-sjablonen voor W&V/balans (ook de eigen versies van A en C) worden
niet nagebouwd. B: W&V en balans krijgen een aanpasbaar sjabloon in Beheer > Sjablonen.

**RP7 Mailen.** A: alleen downloaden (knop PDF naast CSV/Excel); geen mailknop, niets bewaard of vastgelegd,
alleen een regel in het logboek zoals bij CSV/Excel. B: ook een knop "Mailen" (bijv. naar de accountant).

**RP8 Knop Afdrukken.** A: houden (snel afdrukken van wat op het scherm staat), PDF komt ernaast. B: Afdrukken
vervangen door PDF.

**RP9 Rechten.** A: wie het rapport mag zien mag de PDF maken (zelfde modulecontrole), ook alleen-lezen-gebruikers.
B: alleen boekhouder en beheerder.

**RP10 Toetsen.** A: pytest per rapport/lijst: PDF wordt gemaakt, tekst uit de PDF (pypdf als testafhankelijkheid)
bevat dezelfde bedragen als het scherm (zelfde `cell_text`), grens RP2, liggend/staand, rechten, 4 talen;
regressie pytest + run_compare 123/0 + doc_html_dump ongewijzigd; tijdmeting op administratie A en C (grootste
journaal); voorbeeld-PDF's van A en C in `dev/voorbeeld-pdf/stap5/` (balans per maand met vorig jaar, W&V, proefbalans,
rekeningmutaties, journaal, openstaand) om samen te bekijken; browsercontrole incl. 375px. Geen Perl-vergelijking
(SL kan het niet). Bouw op branch `fase4-stap5` (eigen worktree en lokale testserver).

---

## Stap 6 — rest bijlagen (D17 + BB3/BB4) — analyse 2026-10-11

**Status: akkoord, alles A (gebruiker 2026-10-11).** Bouw op `fase4-stap6` in delen 6a/6b/6c (RB8).

### Wat er al is (4e/4f)
- Bijlagen in `reference`/`archive`/`archivedata` met de codering van SL (`sqlledger/attachments.py`, `routes_attach.py`,
  `templates/_attach.html`), heen en terug getoetst met SL's eigen code (`compare/compare_attach.py`, 16/0).
- Blok **Bijlagen** alleen op verkoop-/inkoopboekingen **zonder** artikelen en op het memoriaal (`FORMNAMES` = ar/ap/gl).
- pdf/jpg/png op inhoud, maximale uploadgrootte instelbaar (4f), vertrouwelijk-vlag, logboek, Perl-fout 28 gerepareerd
  (`support._delete_unused_archive`), alleen pdf/jpg/png inline met eigen CSP (S-N3 speelt bij ons niet).
- `order.py` bewaart bestaande SL-verwijzingen al (`save_references`), maar er is geen scherm om toe te voegen.

### Hoe SQL-Ledger het doet
- `rd.pl formnames` kent 24 soorten: de 8 boekingssoorten + 4 order-/offertesoorten, `gl`, `project`, `job`,
  `customer`, `vendor`, `part`/`service`/`assembly`/`labor`, `employee`, `timecard`, `storescard`.
- Menu **Referentiedocumenten**: Document toevoegen (los, zonder koppeling), Documenten zoeken/lijst (omschrijving,
  map, bestandsnaam, vertrouwelijk, "Gekoppeld aan" met link), Koppelen aan / Ontkoppelen.
- Orders samenvoegen (`OE.pm` consolidate, r2392) kopieert de verwijzingen naar de nieuwe order (zelfde archiefstuk);
  facturen samenvoegen (`IS.pm` r2964/3089) verplaatst ze.
- Echte data: administratie A en C 0 rijen in `reference`; er gaat dus niets verloren of om.

### Beslispunten (advies steeds A)

**RB1 Op welke schermen.** A: het bestaande blok Bijlagen ook op **facturen met artikelen** (verkoop/inkoop, ook
credit), **orders en offertes** (4 soorten), **relaties** (klant/leverancier) en **artikelen** (artikel, dienst,
samengesteld, arbeid). Niet: projecten/jobs, medewerkers (geen scherm), urenstaat/magazijnkaart (fase Uren beslist
daar zelf). B: alleen facturen met artikelen en orders. C: ook projecten.

**RB2 Omzetten.** A: bij offerte → order en order → factuur gaan de bijlagen **mee als verwijzing naar hetzelfde
archiefstuk** (geen kopie; dankzij de reparatie van Perl-fout 28 blijft het bestand bestaan zolang één van beide het
nog heeft); op het nieuwe scherm staat "uit order 123". Kopiëren en "Opslaan als nieuw" nemen niets mee (zoals 4e).
B: niets meenemen, alleen de bestaande koppeling order ↔ factuur.

**RB3 Overzicht.** A: **Rapporten > Bijlagen**: lijst/zoeken op omschrijving, bestandsnaam, soort, gekoppeld aan, datum
toegevoegd (uit het logboek, anders leeg), met link naar de boeking/relatie/artikel, bekijken en downloaden; vertrouwelijk
van een ander niet zichtbaar (zoals SL); losse archiefstukken zonder koppeling (uit SL "Document toevoegen") ook,
met verwijderen voor de beheerder. Geen los "Document toevoegen" en geen "Koppelen aan" (slepen op het scherm
volstaat). B: overzicht plus losse documenten en koppelen zoals SL. C: geen overzicht.

**RB4 (= BB3) Foto's verkleinen — uitwerking.** A: jpg/png met de lange kant boven 2.400 px verkleinen naar 2.400 px;
jpg kwaliteit 85; eerst rechtop zetten volgens de EXIF-oriëntatie en **alle metadata weg** (telefoonfoto's bevatten
GPS-locatie en toestel); png blijft png; het verkleinde bestand alleen bewaren als het kleiner is; het origineel niet.
PDF nooit aanraken. Met Pillow (komt al mee met weasyprint; wordt een expliciete afhankelijkheid), met de bescherming
tegen "decompression bombs" (te veel pixels = weigeren). HEIC niet: Safari/iOS levert bij uploaden al jpg. B: ook het
origineel bewaren. C: metadata laten staan.

**RB5 (= BB4) Dubbele bestanden — uitwerking.** A: sha256 bij elk nieuw archiefstuk, in `archive.hash` als die kolom
bestaat (SQL-Ledger 4.x), anders in eigen tabel `x_archive_hash` (migratie 11); bestaande stukken krijgen de hash in de
migratie (administratie A en C: 0). Hangt hetzelfde bestand al bij een **andere** boeking/relatie/artikel, dan een waarschuwing met
link ("Dit bestand hangt al bij inkoopboeking 2024-0815 van Leverancier X, € 121,00") en een vinkje "toch toevoegen";
op het uploadscherm vóór Boeken. Na bevestigen een **nieuw** archiefstuk (niet stilzwijgend delen). Hetzelfde bestand
twee keer bij dezelfde boeking: geweigerd. Vertrouwelijke stukken van een ander: wel waarschuwen, niet zeggen waar.
B: na bevestigen het bestaande archiefstuk delen (scheelt ruimte). C: alleen waarschuwen op het uploadscherm.

**RB6 Rechten.** A: toevoegen/verwijderen mag wie het scherm mag bewerken (relaties/artikelen: dezelfde rol als
opslaan daar); alleen-lezen bekijkt en downloadt; bij een gesloten order of afgesloten periode mag een bijlage nog
(zoals 4e keuze 7). B: bijlagen bij relaties en artikelen alleen voor de beheerder.

**RB7 Verwijderen van een relatie, artikel, order of factuur.** A: alle verwijzingen weg, ook vertrouwelijke van andere
gebruikers (SL/`support.delete_references` laat die nu als wees achter), archiefstuk pas weg bij de laatste verwijzing;
een relatie of artikel met bijlagen kan verder alleen weg als dat nu al mag. B: zoals SL (alleen eigen en
niet-vertrouwelijke verwijzingen).

**RB8 Volgorde.** A: branch `fase4-stap6`, in drie delen: **6a** schermen (RB1, RB2, RB6, RB7), **6b** verkleinen en
dubbele bestanden (RB4, RB5), **6c** overzicht (RB3); per deel pytest en akkoord. B: alles in één keer.

**Toetsen (bij A).** pytest per scherm/soort (toevoegen, rechten, vertrouwelijk, verwijderen, omzetten); verkleinen
(oriëntatie, metadata weg, png, bom-bescherming, PDF onaangeroerd); hash (beide opslagplekken);
`compare/compare_attach.py` uitbreiden met de nieuwe formnames (SL `rd.pl` ziet onze bijlagen bij
order, factuur, relatie en artikel en andersom; opnieuw opslaan in is.pl/oe.pl/ct.pl/ic.pl laat ze staan);
regressie run_compare; browsercontrole incl. 375px en NL/EN/DE/FR.

## Stap 7 import en export (D18–D20 A) — keuzes binnen A, 2026-10-11 (gebruiker afwezig, vooraf akkoord op het advies) — **keuzes 1–18 AKKOORD gebruiker 2026-10-11**

Gevonden in im.pl/IM.pm: CSV met kopregel = SL-veldnamen of een `import.map` per taal; rijen worden in `reportvars`
klaargezet (per login), dan aangevinkt en per rij opgeslagen via `CT->save`, `IC->save`, `AM->save_account`,
`GL->post_transaction`. Fouten: `dataline` kan geen regeleinde binnen aanhalingstekens; een relatie zonder nummer krijgt
een nieuw nummer; een lege cel overschrijft het veld; memoriaal groepeert **opeenvolgende** rijen met dezelfde
referentie en slaat een boeking alleen over als een rekening niet bestaat (niet als hij niet in balans is: dan breekt
GL af); artikelgroepen krijgen altijd `pos = 1`; SQL-injectie bij het importeren (FASE4 lijst punt 5; beveiligingsfout in SQL-Ledger, wordt gemeld aan de onderhouders, details volgen daarna).

Keuzes (alle volgens advies A):
1. **Plek en rechten**: Beheer > **Importeren** en Beheer > **Exporteren** (rubriek "Administratie"); importeren alleen
   voor de beheerder (bulkwijzigingen), exporteren voor wie de lijst van die soort mag zien.
2. **Soorten**: klanten, leveranciers, artikelen, diensten, artikelgroepen, rekeningschema, memoriaal (D18). Niet:
   arbeid/overhead, samengestelde artikelen (stuklijst), facturen, orders, betalingen.
3. **Bestand**: CSV (scheidingsteken automatisch `;` `,` of tab; aanhalingstekens met regeleinden en `""`; UTF-8 met of
   zonder BOM, anders Windows-1252) of xlsx (eerste blad, eerste niet-lege rij = kop); hoogstens 5.000 rijen en de
   gewone uploadgrens.
4. **Kolommen koppelen**: automatisch op SL-veldnaam (`customernumber`, `name`, …) en op de kolomkop zoals onze eigen
   export die schrijft, in elke schermtaal; per kolom te wijzigen of "negeren"; de koppeling per soort wordt onthouden
   in de administratie (`x_setting` `import_map.<soort>`, geen migratie). Kolommen die we (nog) niet kennen (bv. SL's
   `make`, `model`, `image`) staan als "niet ondersteund" in de lijst.
5. **Sleutel**: relatienummer, artikelnummer, rekeningnummer, groepsnaam (zoals SL). Leeg nummer bij relatie of
   artikel = nieuw met het volgende nummer. Memoriaal: alle rijen met dezelfde referentie vormen één boeking (in de
   volgorde van het bestand, ook niet-opeenvolgend); zonder referentie: elke rij met datum begint een nieuwe boeking.
6. **Bijwerken**: alleen kolommen die in het bestand staan; een **lege cel laat het veld ongewijzigd** (leegmaken kan op
   het scherm). Relaties en artikelen via de bibliotheek (`Contacts.save`, `Parts.save`, `Admin.save_account`,
   `GeneralLedger.post_transaction`): dezelfde controles en audittrail als de schermen.
7. **Voorbeeld**: per rij **nieuw / bijwerken (welke velden) / ongewijzigd / fout (waarom)**; vinkje per rij (fout en
   ongewijzigd uit); memoriaal per boeking (in balans, rekeningen bestaan, datum in een open periode); een referentie
   die al bestaat = waarschuwing, standaard niet aangevinkt.
8. **Uitvoeren** in één transactie (alles of niets); mislukt er één, dan niets en de melding met de rij. Resultaat:
   aantallen en lijst nieuw/bijgewerkt; logboek `import` (soort, bestand, aantallen).
9. **Getallen en datums** in de notatie van de gebruiker; daarnaast altijd `1234.56` en ISO `2026-10-11`. Een `'` die
   onze export voor `= + - @` zet (formule-injectie), wordt bij het inlezen weggehaald.
10. **Verwijzingen**: rekeningen als rekeningnummer, btw-rekeningen als nummers gescheiden door spatie of `:` (SL),
    branche/prijsgroep/betaalmethode/verkoper op omschrijving (onbekend = fout), artikelgroep op naam (onbekend =
    aanmaken zoals SL, met een melding in het voorbeeld), valuta moet bestaan, land vrij. Nieuw artikel of nieuwe dienst
    zonder rekeningen: de standaardrekeningen uit de instellingen (zoals SL).
11. **Export** (D20): klanten, leveranciers, artikelen, diensten, artikelgroepen en rekeningschema in precies het
    importformaat (kop = SL-veldnaam), CSV (`;`, notatie van de gebruiker) of xlsx; een export ongewijzigd terug
    importeren = alles "ongewijzigd".
12. **Toetsen**: pytest per soort; Perl-driver `im_ui` (im.pl als browser met dezelfde CSV) vs Python op twee kopieën →
    databases vergelijken (verschillen alleen waar SL-fouten hierboven); export → import op administratie A en C = 0 wijzigingen;
    browsercontrole (ook 375 px). Vertalingen EN/DE/FR meteen.
