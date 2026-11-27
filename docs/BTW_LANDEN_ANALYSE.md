# Btw-aangifte per land — analyse (2026-10-13)

**Status: akkoord, alles A (gebruiker 2026-10-13).** Bouwen in deelstappen volgens BL14, elk met akkoord. Beslispunten BL1–BL14 (algemeen) en per land BE1–BE6,
UK1–UK6, FR1–FR6, CH1–CH6, DE1–DE7. Advies steeds A. Branch `btw-landen`, worktree `~/sqlledger-test/btwlanden`.

## Wens van de gebruiker (2026-10-13)
- Naast de Nederlandse aangifte (BA1–BA9, ICP1–ICP11) een aangifte voor de andere standaardschema's: **België**
  (periodieke aangifte), **Verenigd Koninkrijk** (VAT Return), **Frankrijk** (CA3), **Zwitserland**
  (MWST-Abrechnung) en daarna **Duitsland** (UStVA, met een eigen HGB-schema).
- Per land: rubrieken, tijdvakken, afronding, btw-boeking, de tegenhanger van de ICP, formulier en export, en wat de
  officiële bron is. Rubrieken controleren tegen de belastingdienst van elk land.
- Film: in de btw-scène alleen **NL en UK** (gebruiker 2026-10-13); de andere landen komen later.

## Wat er al is
- `sqlledger/vatreturn.py` rekent voor NL: btw per rubriek = mutaties op de btw-rekeningen; grondslag per boeking
  (`split_base`: één tarief, factuurregels met artikelen, of btw ÷ tarief); omzet zonder btw via de rubriek van de klant
  (BA4); afronden in uw voordeel (BA5); btw-boeking als ingevuld memoriaal (BA6); vastleggen + verschilmelding (BA7).
  `FORMS = {"NL": BOXES}` was al bedoeld als haakje voor andere landen.
- `x_vat_box (chart_id, country, box)` (migratie 16): de schema's leggen hun rubriek al met landcode vast, maar
  **alleen per btw-rekening en alleen de btw-kant**: CH 303/313/343/400, GB 1/4, FR 08/09/9B/T6/20, BE 54/59
  (`chartfiles.COUNTRY_BOXES`). Afdracht- en afrondingsrekening staan in de kop van elk schema.
- De niet-NL-schema's hebben **geen** rekeningen voor verlegde btw, EU-verwerving, invoer of diensten uit het buitenland
  (NL heeft daarvoor btw-paren met negatief tarief, 1550–1573 in `nl-bv`).
- Land van de administratie: `x_setting.country` wordt alleen bij een nieuwe administratie gezet; bij een ingelezen
  SQL-Ledger-back-up is het leeg.
- **Echte data**: `adm_c` (Brits, GBP) heeft kwartaalaangiften 2014–2016 als memoriaal "VAT RETURN Q1 MAR 2016" e.d.
  tegen 1520 "VAT - Payments to/from HMC&E", tot op de penny (bijv. Q1 2016 £ 28.441,37): bruikbaar als toets zoals administratie A
  voor NL. Let op: 1520 heeft zelf een btw-koppeling (tarief 0); 1530 "VAT EC sales @ 0%". Voor BE/FR/CH/DE is er geen
  echte administratie: toetsen met eigen testgegevens.

## Officiële bronnen (gelezen 2026-10-13)

| Land | Formulier | Bron |
|---|---|---|
| BE | periodieke btw-aangifte, model bijlage I KB nr. 1 (laatst vervangen KB 29-03-2021) | ejustice.just.fgov.be (KB nr. 1, KB 29-03-2021, KB nr. 50, KB nr. 23, KB nr. 19); Intervat-help eservices.minfin.fgov.be; MAR cnc-cbn.be. *financien.belgium.be weigerde automatische toegang (botcontrole), niet omzeild.* |
| UK | VAT Return (9 boxes) | gov.uk VAT Notice 700/12 (bijgewerkt 19-12-2025), 700, 700/22 (MTD); HMRC Developer Hub VAT (MTD) API 1.0 |
| FR | 3310-CA3-SD millésime 2026 (cerfa 10963\*31) + notice 50449#29 | impots.gouv.fr formulaires 2026; BOFiP BOI-TVA-BASE-20-20, BOI-RES-TVA-000253; douane.gouv.fr (état récapitulatif, DES); ANC plan de comptes 2026. *Legifrance gaf 403: CGI-artikelen alleen via notice/BOFiP.* |
| CH | MWST-Abrechnung effektive Methode, Muster gültig ab 01.01.2024 (4470) | estv.admin.ch / estv2.admin.ch; MWSTG fedlex (stand 31-03-2025); eCH-0217 V2.0.0 (ech.ch); MWST-Info 15; Schulkontenrahmen VEB (kmu.admin.ch) |
| DE | Vordruck USt 1 A 2026 (BMF-Schreiben 29-12-2025) | bundesfinanzministerium.de; gesetze-im-internet.de (UStG §§ 18, 18a, 19, 20; HGB §§ 266, 275; AO § 149); elster.de (upload, ERiC); bzst.de (ZM); esteuer.de (E-Bilanz-taxonomie 6.9/6.10) |

## Per land in het kort

### België — periodieke aangifte
- **Roosters** (alle bedragen positief, euro **met centen**):
  - uitgaand: 00 bijzondere regeling, 01/02/03 grondslag 6/12/21 %, 44 diensten EU (afnemer verlegt), 45 verlegging
    binnenland (medecontractant), 46 IC-leveringen + ABC, 47 overige vrijgesteld/uitvoer/buitenland,
    **48/49 uitgereikte creditnota's** (op 44+46 resp. de rest);
  - inkomend: **81 handelsgoederen/grond- en hulpstoffen, 82 diensten en diverse goederen, 83 bedrijfsmiddelen**
    (grondslag zonder aftrekbare btw), **84/85 ontvangen creditnota's**, 86 IC-verwervingen + ABC, 87 overige verlegging
    (o.a. invoer), 88 IC-diensten ontvangen;
  - btw: 54 op 01–03, 55 op 86+88, 56 op 87, 57 invoer verlegd, 61 regularisaties Staat, 63 terug te storten op ontvangen
    creditnota's, 59 aftrekbaar, 62 regularisaties aangever, 64 te recupereren op uitgereikte creditnota's;
  - XX = 54+55+56+57+61+63, YY = 59+62+64; **71** = XX−YY of **72** = YY−XX (één van beide verplicht, 71 = 0,00 bij niets).
  - Rooster **91 bestaat niet meer** (voorschotten afgeschaft per 10-04-2021); de Intervat-help is daar verouderd.
- **Tijdvak**: maand (20e) of kwartaal (25e) bij omzet ≤ € 2,5 mln en geen maandelijkse IC-opgave. Teruggaaf: vakje in
  kader I (minimum € 400 kwartaal 1–3, € 50 laatste tijdvak).
- **Tegenhanger ICP**: *intracommunautaire opgave* (KB nr. 50): per afnemer btw-nummer + bedrag per code **L** (leveringen),
  **T** (driehoeksverkeer/ABC), **S** (diensten); maand, of kwartaal als leveringen ≤ € 50.000; creditnota's negatief;
  geen nihil-opgave. Daarnaast de **jaarlijkse klantenlisting** (KB nr. 23, vóór 31 maart): per Belgische btw-plichtige
  klant omzet en btw (drempel € 250 per klant: *niet nagelezen in het Wetboek*).
- **Indienen**: Intervat (ook via MyMinfin), handmatig of XML-upload (`VatIntra`/`VatList` 1.3 voorbeelden openbaar; het
  XSD van de periodieke aangifte staat "in Intervat zelf": *niet vrij te downloaden gevonden*).
- **Boeking**: 451 te betalen / 411 terug te vorderen tegen de rekening-courant btw (in ons schema 451900).
- **Ons schema**: alleen 54/59; mist 81/82/83-indeling, creditnota-roosters en btw-paren voor 55/56/57.

### Verenigd Koninkrijk — VAT Return
- **Boxes**: 1 VAT due on sales and other outputs, 2 VAT due on acquisitions (alleen goederen Noord-Ierland ↔ EU),
  3 = 1+2, 4 VAT reclaimed (incl. box 2), 5 = 3−4 (absolute waarde), 6 total sales ex VAT (alle omzet, ook 0 %/vrijgesteld/
  export), 7 total purchases ex VAT (ook import en verlegd, **geen lonen**), 8/9 alleen NI-goederen naar/van de EU.
  Box 1–5 in pence, **6–9 hele ponden** (afkappen of afronden: *nergens officieel*; gangbaar is afkappen).
  Verlegd: diensten uit het buitenland → 1, 4, 6 en 7; binnenlandse verlegging (bouw/CIS) → 1, 4, 7 (niet 6).
  PIVA (uitgestelde invoer-btw) → 1 en 4, waarde in 7.
- **Tijdvak**: kwartaal in drie "stagger"-groepen (eindigend maart/juni/sep/dec, april/juli/okt/jan, mei/aug/nov/feb),
  maand op verzoek; termijn 1 maand + 7 dagen. Regelingen: cash accounting (≤ £ 1,35 mln), flat rate (≤ £ 150.000; box 1 =
  % × omzet incl. btw), annual accounting.
- **Making Tax Digital is verplicht voor iedereen** (sinds april 2022); sinds 1-11-2022 kan de aangifte **niet meer** in
  het HMRC-webformulier. Overtypen of kopiëren/plakken telt **niet** als "digital link"; een CSV/XLSX-bestand dat
  *bridging software* inleest wél. Eigen MTD-koppeling = OAuth + client secret + wettelijk verplichte fraud-prevention-
  headers + productiegoedkeuring door HMRC (verantwoordelijke organisatie, vragenlijsten, privacybeleid, datalekproces);
  een gedeeld secret in open-source software is in feite openbaar (*geen officiële regel gevonden*).
- **Tegenhanger ICP**: EC Sales List alleen nog voor goederen vanuit Noord-Ierland (XI).
- **Ons schema**: 1/4 per btw-rekening; mist reverse-charge-paren.

### Frankrijk — CA3
- **Cadre A** (grondslagen): A1 ventes/prestations, A2 autres imposables, **A3 achats de services à un non-établi
  (283-2)**, **A4 importations** (autoliquidation verplicht sinds 2022), B2 acquisitions intracom, B4 283-1, B5
  régularisations; **E1 export**, **E2 autres non imposables** (ook B2B-diensten aan EU-afnemers), **F2 livraisons
  intracom**, overige E/F-regels zelden.
- **Cadre B**: per tarief grondslag + btw: **08 20 %, 09 5,5 %, 9B 10 %, T6 2,1 % (continentaal)** (DOM/Corse 10, 11,
  T1–T5, TC: niet nodig); I1–I6 invoer (vooraf ingevuld); 15, 5B; **16 totaal**, waarvan **17 btw op IC-verwerving van
  goederen** (niet op diensten); aftrek **19 immobilisations, 20 autres biens et services**, 21 autre, **22 report du
  crédit = regel 27 van de vorige aangifte**, 2C, **23 totaal**, waarvan 24 invoer; **25 crédit** of **TD due**;
  26 remboursement (form. 3519, vanaf € 760; € 150 in dec/Q4), **27 crédit à reporter**, **28 TVA nette due**,
  29 taxes assimilées, **32 total à payer**. Btw-regels 2026 ongewijzigd t.o.v. 2025.
- **Afronding**: per regel op **hele euro's, rekenkundig** (≥ 0,50 omhoog), ook 19 en 20 apart; nooit negatief.
- **Tijdvak/regime**: CA3 per maand (kwartaal bij btw < € 4.000/jaar), indienen 15–24e. **Het régime simplifié (CA12)
  vervalt per 1-1-2027**: laatste CA12 over 2026; daarna CA3 per kwartaal (omzet ≤ € 1 mln) of per maand.
  Franchise en base (≤ € 85.000 goederen / € 37.500 diensten): geen aangifte.
- **Exigibilité**: goederen bij levering, **diensten standaard bij ontvangst van de betaling** (encaissements), tenzij
  "option pour les débits". Dit raakt welke boekingen in een tijdvak tellen.
- **Tegenhanger ICP**: bij de douane: *état récapitulatif TVA* (goederen, maandelijks, 10e werkdag) en *DES* (diensten,
  maandelijks, vanaf de eerste euro, XML DTI+ mogelijk).
- **Indienen**: elektronisch verplicht; **zelf invullen in het espace professionnel (EFI) mag** → overzicht volstaat.
- **Boeking (PCG)**: 44571/4452 en 44566/44562/44567 tegen **44551 TVA à décaisser** of **44567 crédit à reporter**.
- **Ons schema**: 08/09/9B/T6/20; mist 4452-paren (IC-verwerving, A3-diensten, invoer) en de 19-koppeling (445620 heeft
  geen btw-koppeling, FR4).

### Zwitserland — MWST-Abrechnung (effectieve methode)
- **Ziffern**: 200 totaal Entgelte (excl. Bezugsteuer), 205 (info, optiert); aftrek 220 befreit/export, 221 Ausland,
  225 Meldeverfahren, 230 ausgenommen, 235 Entgeltsminderungen, 280 Diverses; 289 som; **299 = 200 − 289** = som van de
  grondslagen; **303 8,1 %, 313 2,6 %, 343 3,8 %, 383 Bezugsteuer** (2018–2023: 302/312/342/382); 399 totaal;
  **400 Vorsteuer Material/Dienstleistungen, 405 Vorsteuer Investitionen/übriger Betriebsaufwand**, 410, 415 (−), 420 (−);
  479; **500 zu bezahlen / 510 Guthaben**; 900/910 Mittelflüsse.
- **Afronding**: btw = grondslag × tarief op 2 decimalen zonder tussenafronding; 500/510 kaufmännisch of op 5 Rappen in
  het voordeel van de belastingplichtige (anders ePortal-fout MWST-0006).
- **Tijdvak**: kwartaal; halfjaar bij Saldosteuersatz; maand op verzoek; **jaarlijks sinds 2025** (omzet ≤ CHF 5 mln, met
  Raten). Termijn 60 dagen. **Vereinbart** (factuur) standaard, vereinnahmt (betaling) op verzoek. Jaarafstemming
  (Art. 72 MWSTG): correcties binnen 180 dagen na het boekjaar, alleen verschillen.
- **Indienen**: ePortal verplicht sinds 2025, "MWST-Abrechnung pro" accepteert **XML eCH-0217 V2.0.0** (XSD en
  voorbeelden openbaar op ech.ch; geen certificering genoemd).
- **Tegenhanger ICP**: geen.
- **Boeking (Kontenrahmen KMU)**: 1170 Vorsteuer Material/Waren/DL, **1171 Vorsteuer Investitionen**, 2200 Geschuldete
  MWST, **2201 Abrechnungskonto MWST**.
- **Ons schema wijkt af van de officiële KMU**: bij ons zijn 1171/1172 en 2201/2202 de rekeningen voor 2,6/3,8 % en is 2209
  de afrekenrekening; 383 en 405 ontbreken.

### Duitsland — Umsatzsteuer-Voranmeldung
- **Kennzahlen 2026** (grondslag in **hele euro's**, btw met centen): 81 19 %, 86 7 %, **87 = 0 %** (zonnepanelen),
  35/36 andere tarieven; 41 IC-leveringen (USt-IdNr.), 43 overige vrijgesteld met aftrek (export), 48 zonder aftrek (ook
  Kleinunternehmer); IC-verwerving 89 19 %, 93 7 %, 90 0 %, 95/98 andere, 91 vrijgesteld; § 13b 46/47 diensten EU-
  ondernemer, 84/85 overige, 73/74; 42 driehoek, 60 § 13b als leverancier, **21 niet-belastbare EU-diensten (ZM)**,
  45 overige niet-belastbaar; Vorsteuer **66** facturen, **61** IC-verwerving, **62** EUSt, **67** § 13b, 63, 59, 64;
  65, 69; **39** Sondervorauszahlung (december); **83** verbleibende Vorauszahlung/Überschuss (altijd invullen); 50/37
  oninbare vorderingen; kop 10 berichtigt, 22 Belege, 70 wissel Kleinunternehmer. Bij 81/86/87/89/93/90 berekent het
  formulier de btw zelf.
- **Tijdvak**: kwartaal; maand bij > € 9.000 btw vorig jaar en voor starters (2 jaar); vrijstelling ≤ € 2.000; termijn
  10e; Dauerfristverlängerung +1 maand met Sondervorauszahlung 1/11 (maandaangevers). Ist-Versteuerung ≤ € 800.000.
  Kleinunternehmer (≤ € 25.000 / € 100.000): geen UStVA.
- **Tegenhanger ICP**: *Zusammenfassende Meldung* aan het BZSt: per afnemer USt-IdNr., som in hele euro's, soort
  **L/D/S**; maand (25e), kwartaal als leveringen ≤ € 50.000; **CSV-import in het BZSt-portaal** officieel beschreven
  (`#v3.0`, UTF-8, max. 1.500 regels, per nummer+soort samenvoegen).
- **Indienen**: met de hand in *Mein ELSTER* kan; **XML-upload in Mein ELSTER bestaat** (alleen het Nutzdaten-deel,
  ISO-8859-15), maar het XSD zit in het ERiC-ontwikkelaarspakket (alleen na registratie). ERiC zelf: gesloten
  C-bibliotheek, licentie niet openbaar → botst mogelijk met GPL.
- **Schema**: niets voorgeschreven; DATEV-PDF's: "Nachdruck – auch auszugsweise – nicht gestattet". HGB § 266/§ 275 geven
  de indeling; de E-Bilanz-taxonomie (6.9 voor 2026, 6.10 vanaf 2027, gratis, © XBRL Deutschland, geen open licentie) is
  het Duitse tegenstuk van RGS. **Let op: `leeg-de` gebruikt nu SKR03-nummers** (1571/1576/1771/1776/1789/8400).

## Beslispunten — algemeen (advies steeds A)

**BL1 Opzet.** A: **één rekenkern, een formulier per land**: `vatreturn.py` blijft de kern (grondslag per boeking,
afdrachtboekingen uitsluiten, btw-boeking, vastleggen, verschilmelding); per land een klein bestand
`sqlledger/vatforms/<land>.py` met de rubrieken (kant, grondslag/btw, volgorde, kaders), totaalformules,
afrondingsregel en standaardkoppeling; labels in de webapp (vertaalbaar). NL wordt het eerste formulier in die vorm
(uitkomst NL moet exact gelijk blijven: alle bestaande toetsen). B: per land een kopie van `vatreturn.py`.

**BL2 Welk land.** A: instelling **Aangifte volgens** op *Btw > Btw-rubrieken* (NL/BE/GB/FR/CH/DE), standaard uit
`x_setting.country` (nieuwe administratie); bij een ingelezen back-up leeg tot de beheerder kiest (voorstel uit de
valuta en de rekeningnamen). Eén land per administratie; de koppelingen per land blijven bewaard bij wisselen
(`x_vat_box` heeft al een landcode). B: alleen het land van het schema, niet te wijzigen.

**BL3 Grondslag per grootboekrekening (nieuw).** Sommige formulieren delen de grondslag niet naar btw-rekening maar naar
soort kosten: BE 81/82/83, CH 400/405 (via de btw-rekening, zie CH2), FR 19/20 (via de btw-rekening). A: naast de rubriek
per btw-rekening een **rubriek per kosten-/activarekening** (tabel `x_vat_account_box`, alleen gebruikt waar het
formulier dat vraagt; nu alleen BE 81/82/83), voorstel uit de rekeningklasse (MAR 60 → 81, 61–64 → 82, 2 → 83), per
rekening te wijzigen op Btw-rubrieken. B: aparte btw-rekeningen per soort (bijv. 411 per 81/82/83) — botst met
SQL-Ledger (één btw-rekening per tarief op het artikel).

**BL4 Omzet zonder btw.** A: zoals BA4 de **rubriek bij de klant**, met per land eigen keuzes (BE 44/46/47, UK alleen
6, FR E1/E2/F2, CH 220/221/230, DE 41/43/21/45) en een standaard voor de rest (BE 47, UK 6, FR E2, CH 230, DE 45; het scherm meldt hoeveel boekingen zo zijn ingedeeld); het
veld op het klantscherm toont de keuzes van het land van de aangifte. B: alleen via 0 %-btw-rekeningen.

**BL5 Verlegde btw, EU-verwerving, invoer.** A: zoals NL met **btw-paren** (negatief tarief op de te betalen kant,
positief op de aftrekkant) in de standaardschema's én de lege schema's van elk land: BE 86/88 → 55 + 59, 87 → 56 + 59;
UK diensten uit het buitenland → 1 + 4 (+ 6/7), binnenlands verlegd → 1 + 4 (+7), PIVA → 1 + 4; FR B2 → 08 + 17 + 20,
A3 → 08 + 20, A4 → I1 + 20/24; CH Bezugsteuer → 383 + 400; DE 89/93 → 61, 46/84 → 67. `chartfiles` laat het negatieve
tarief dan per land toe voor die rubrieken. Bestaande administraties: de beheerder voegt de rekeningen zelf toe
(uitleg op Btw-rubrieken). B: verlegging niet ondersteunen.

**BL6 Afronding.** A: per land volgens de bron:

| Land | Grondslag | Btw | Saldo |
|---|---|---|---|
| NL | hele euro omlaag (in uw voordeel, BA5) | verschuldigd omlaag, voorbelasting omhoog | uit afgeronde bedragen |
| BE | centen | centen | 71/72 uit de roosters |
| UK | box 6–9 hele ponden, **omlaag afkappen** (niet officieel vastgelegd; zo melden op het scherm) | box 1–4 pence | box 5 = 3 − 4 |
| FR | hele euro **rekenkundig**, per regel | idem per regel (19 en 20 apart) | uit de afgeronde regels |
| CH | centen | **grondslag × tarief** (zoals ePortal narekent) | 500/510 op 2 decimalen |
| DE | hele euro **omlaag** (niet officieel vastgelegd) | centen; bij 81/86/89/93 grondslag × tarief | Kz 83 |

**Besluit gebruiker 2026-10-13 (vervangt de tabel): elke aangifte in hele bedragen** — geboekt wordt per cent/penny,
de aangifte staat altijd zonder decimalen (verschuldigde btw omlaag, aftrek omhoog, grondslag omlaag, zoals NL BA5);
ook voor BE, FR, CH en DE. Het VK is zo gebouwd (pence in box 1–5 vervalt).

Verschil tussen de geboekte btw en het formulierbedrag (afronding of "grondslag × tarief") gaat in de btw-boeking naar de
rekening afrondingsverschillen, zoals nu. B: overal gewoon rekenkundig afronden.

**BL7 Tijdvak.** A: de bestaande instelling *Btw-aangifte per* (maand/kwartaal/jaar) plus een keuze **kwartaal eindigt
in** (mrt/apr/mei) voor de Britse stagger-groepen en **halfjaar** (alleen CH Saldosteuersatz, zie CH5). B: alleen
kalenderkwartalen.

**BL8 Kasstelsel.** FR diensten (standaard!), CH vereinnahmt, UK cash accounting en DE Ist-Versteuerung tellen op
betaaldatum. A: **nu alleen factuurstelsel**, met een duidelijke melding op het formulier ("op factuurdatum; kasstelsel
wordt nog niet ondersteund"); kasstelsel later als **één aparte stap voor alle landen tegelijk** (betalingen toerekenen
aan de btw van de factuur, ook NL kent het kasstelsel). B: kasstelsel nu meebouwen (groot: elke deelbetaling naar rato).

**BL9 Btw-boeking en afdracht.** A: zoals BA6: **Btw-boeking maken** opent een ingevuld memoriaal; per land de
afdrachtrekening uit het schema (BE 451900, UK 2202, FR 44551 bij te betalen en **44567 bij een tegoed**, CH 2201 na CH1,
DE eigen Vorauszahlungsrekening); vastleggen in `x_vat_return` met landcode; verschilmelding (BA7) per land. B: geen
boeking voor de andere landen.

**BL10 Formulierweergave.** A: zoals NL twee schermen: het **overzicht** (rubrieken met doorklik, PDF, Excel) in de taal van
de gebruiker, en het **formulier** in de opmaak en officiële taal van het land (BE in nl of fr naar keuze, CH de of fr, UK
en, FR fr, DE de) met "Proefafdruk" tot vastgelegd. Rubriekomschrijvingen in alle vier schermtalen
(nieuwe teksten meteen EN/DE/FR). B: alleen het overzicht.

**BL11 Rubrieken die niet uit de boekhouding komen.** A: niet berekenen, op 0 met de opmerking "zelf invullen", behalve
waar het formulier een vorige aangifte nodig heeft: FR **22 = 27 van de vorige vastgelegde aangifte**, DE **Kz 39** en
FR **26** handmatig in te vullen en te bewaren bij het tijdvak. Regularisaties (BE 61/62, FR 15/21, CH 410–420, DE 50/37,
64, 65, 69) en Saldosteuersatz/flat rate niet. B: alles op 0.

**BL12 Tegenhanger ICP.** A: de bestaande ICP-module wordt **per land**: zelfde selectie (rubriek "EU-levering/-dienst"),
ICP-soort bij de klant (leveringen/diensten/ABC) vertaald naar BE L/S/T, DE L/D/S; **BE intracommunautaire opgave**,
**DE Zusammenfassende Meldung** (met **CSV volgens het BZSt-formaat**), **FR état récapitulatif + DES** (twee lijsten,
altijd per maand). UK (alleen NI) en CH: niet. De **Belgische jaarlijkse klantenlisting**: apart beslispunt BE5. B: alleen
de aangifte, geen opgaven.

**BL13 Indienen.** A: **geen eigen koppeling met een belastingdienst** (zoals NL). Per land wel de officiële
**uploadbestanden** waar die openbaar en gratis zijn: CH eCH-0217 XML (UK3, CH4), UK CSV voor bridging software (UK2),
DE ZM-CSV; BE Intervat-XML en DE ELSTER-XML pas als het XSD officieel verkregen is (BE4, DE5). B: ook koppelingen
(HMRC MTD, ERiC) — zie UK2 en DE5.

**BL14 Volgorde en planning.** A: deelstappen, elk met akkoord: (1) kern ombouwen + NL ongewijzigd, (2) **UK** (administratie C als
echte toets; daarna de film met NL + UK), (3) BE, (4) FR, (5) CH (eerst schemacorrectie CH1), (6) DE (eerst het schema
DE1), (7) ICP per land (BL12). Alles op branch `btw-landen`, per deelstap gecommit; samenvoegen in `main` per deelstap na
akkoord. B: eerst alle landen, dan de film.

## Beslispunten — België

**BE1 Creditnota's.** A: uitgereikte creditnota's (verkoopboeking met negatief bedrag) naar **48/49** (grondslag) en
**64** (btw); ontvangen creditnota's naar **84/85** en **63**; zo blijven alle roosters positief, zoals Intervat eist.
B: verrekenen in de gewone roosters (Intervat weigert negatieve bedragen).

**BE2 Inkopen 81/82/83.** A: grondslag van **alle** inkoop (ook zonder btw en ook die van 86/87/88) naar de rubriek van de
kostenrekening (BL3), het niet-aftrekbare deel van de btw inbegrepen; per rekening te wijzigen. B: alles naar 82.

**BE3 Schemawijziging.** A: `be-mar` en `leeg-be` krijgen de btw-paren voor 55/56/57 (IC-verwerving goederen en diensten,
verlegging binnenland, invoer) en de 81/82/83-koppeling per kostenrekening; controle tegen de MAR-lijst van de CBN. B: niets
toevoegen.

**BE4 Intervat-XML.** A: **nu niet** (XSD niet vrij te downloaden gevonden, het oude voorbeeld uit 2010 is te oud om op te
bouwen); als jij met je Intervat-toegang het huidige XSD ophaalt, later toevoegen met XSD-controle (zoals SEPA). B: XML
bouwen op het oude voorbeeld.

**BE5 Jaarlijkse klantenlisting.** A: **meebouwen** (eenvoudig: per Belgische btw-klant omzet en btw over het jaar, drempel
€ 250 eerst nalezen in art. 53quinquies, "nihil" als er geen klanten zijn), overzicht + PDF/Excel. B: niet.

**BE6 Teruggaaf.** A: bij rooster 72 een vinkje-advies "teruggaaf vragen" met het minimum (€ 400 / € 50) als tekst; niets
boeken. B: niets tonen.

## Beslispunten — Verenigd Koninkrijk

**UK1 Box 6 en 7.** A: box 6 = nettobedrag van **alle** verkoopboekingen (ook zonder btw) + memorialen met btw op een
box-1-rekening; box 7 = alle inkoopboekingen + memorialen met btw op een box-4-rekening (lonen zitten dan vanzelf buiten);
binnenlands verlegd niet in 6. B: alleen boekingen met btw.

**UK2 Making Tax Digital.** A: **nu een bestand voor bridging software** (CSV en XLSX met de 9 boxes, periode, VRN; zo is er
een "digital link"), met uitleg op het scherm dat overtypen niet mag; een eigen MTD-koppeling (HMRC-API) **later als
apart besluit**: vraagt een verantwoordelijke organisatie, productiegoedkeuring door HMRC en een oplossing voor het
client secret (eerst navragen bij HMRC SDST). B: nu de MTD-koppeling bouwen.

**UK3 Tijdvak en stagger.** A: kwartaal met keuze "kwartaal eindigt in" (BL7), maand; annual accounting niet. B: alleen
kalenderkwartalen.

**UK4 Regelingen.** A: flat rate scheme, cash accounting en annual accounting **niet** (melding op het scherm); NI-boxes
2/8/9 alleen als er een klant/leverancier met XI- of EU-nummer is, anders 0. B: flat rate wel (box 1 = % × omzet incl. btw).

**UK5 Schemawijziging.** A: `uk-ltd` en `leeg-uk` krijgen btw-paren voor diensten uit het buitenland, binnenlandse
verlegging (CIS) en PIVA, met box 1/4 en de 6/7-regel. B: niets toevoegen.

**UK6 Echte data.** A: **administratie C 2014–2016 narekenen** tegen de eigen memorialen "VAT RETURN" op 1520 (box 5 per kwartaal);
verschillen verklaren met boekingen die later zijn ingevoerd of gewijzigd (audittrail), zoals administratie A. Gegevens van C komen
niet in de repo of de film. B: alleen testgegevens.

## Beslispunten — Frankrijk

**FR1 Rubrieken in het schema.** A: verkoop-btw 20/10/5,5/2,1 % → 08/9B/09/T6 met grondslag ook in **A1**; aftrek →
**20**, en 445620 (immobilisations) een eigen btw-koppeling → **19** (herziet FR4 van de rekeningschema's); IC-verwerving
via een 4452-paar → B2 + 08 + 17 + 20; diensten van een niet-gevestigde → A3 + 08 + 20; invoer → A4 + I1 + 20 (24).
Omzet zonder btw via de klant: E1 export, E2 overige/EU-diensten, F2 IC-leveringen. B: alleen de binnenlandse regels.

**FR2 Crédit de TVA.** A: regel **22** automatisch uit regel **27** van de vorige vastgelegde aangifte (melding als die
ontbreekt, dan zelf invullen); 26 (remboursement) handmatig; de btw-boeking zet een tegoed op 44567 en een teruggaaf-
verzoek op 44583. B: 22 handmatig.

**FR3 Exigibilité diensten.** A: zie BL8: nu factuurdatum, met een duidelijke waarschuwing voor dienstverleners zonder
"option pour les débits"; het kasstelsel in de latere stap. B: nu al op betaaldatum voor diensten.

**FR4 CA12.** A: **niet bouwen** (vervalt per 1-1-2027; de laatste CA12 over 2026 doe je zelf); de instelling "per jaar" is voor FR niet te kiezen.
B: CA12 voor boekjaar 2026.

**FR5 Douane-opgaven.** A: état récapitulatif (goederen) en DES (diensten) als twee lijsten in de ICP-module per maand
(BL12), overzicht + Excel/CSV; geen DTI+-XML. B: niet.

**FR6 Bron.** A: rubrieken vastleggen volgens de CA3 millésime 2026 + notice; bij een nieuw millésime (januari) controle in
de jaarlijkse onderhoudslijst. B: —.

## Beslispunten — Zwitserland

**CH1 Schema naar de officiële Kontenrahmen KMU.** A: **corrigeren**: 2201 wordt de afrekenrekening (was 2209), 1171 de
Vorsteuer op investeringen; de tarieven 2,6/3,8 % krijgen vrije nummers (voorstel 2202/2203 geschuldet, 1172/1173 Vorsteuer
Material/DL) — eerst tegen de VEB-schoolversie controleren; bestaande administraties blijven zoals ze zijn (alleen de CSV
verandert). B: laten zoals het is (wijkt af van KMU, herziet CH1 van de rekeningschema's).

**CH2 Ziffern per rekening.** A: verkoop-btw → 303/313/343, Vorsteuer Material/DL → 400, Vorsteuer Investitionen → 405,
Bezugsteuer-paar → 383 + 400; omzet zonder btw via de klant → 220 (export) / 221 (buitenland) / 230 (ausgenommen); **200 =
alle omzet** (verkoop + opbrengstrekeningen in memorialen), 289/299 berekend, met een melding als 299 ≠ som van de
grondslagen. B: alleen de btw-Ziffern.

**CH3 Bedragen.** A: Ziffer 3xx: grondslag uit de boekingen, **btw = grondslag × tarief** (zoals ePortal narekent); verschil
met de geboekte btw in de btw-boeking naar afrondingsverschillen; 500/510 op 2 decimalen (geen 5-Rappen-afronding). B: btw
uit de boekingen.

**CH4 eCH-0217-XML.** A: **bouwen**: XML eCH-0217 V2.0.0 (effectieve methode, vereinbart, netto) met XSD-controle (zoals
SEPA, XSD meeleveren als de licentie van eCH dat toelaat, anders eenmalig laten downloaden), uploaden in ePortal "pro" doe
je zelf. B: alleen het overzicht.

**CH5 Saldosteuersatz en jaarafstemming.** A: Saldosteuersatz **niet** (eigen formulier per activiteit, bruto); de
**jaarafstemming** als extra overzicht: omzet per Ziffer volgens de boekhouding tegen de som van de vastgelegde aangiften
van het jaar, verschil = in te dienen correctie. B: geen van beide.

**CH6 Tijdvak.** A: kwartaal (standaard), maand, en **jaar** (sinds 2025 toegestaan; Raten boek je zelf). B: alleen kwartaal.

## Beslispunten — Duitsland

**DE1 Eigen schema.** A: nieuw schema **`de-hgb`** (± 130 rekeningen) met een **eigen nummering per HGB-post** (geen
SKR03/04, geen IKR): klassen in de volgorde van § 266 (0 Anlagevermögen, 1 Umlaufvermögen, 2 Eigenkapital,
3 Rückstellungen/Verbindlichkeiten) en § 275 Gesamtkostenverfahren (4 Umsatzerlöse … 8 Steuern/Ergebnis); kopjes =
HGB-posten; één schema voor GmbH/UG en Einzelunternehmen (Privatkonto, zoals FR5); namen de/en/nl/fr; controle-Excel
voor jou. B: SKR03 bijwerken (licentierisico, RS8).

**DE2 `leeg-de`.** A: **omnummeren naar de nieuwe indeling** (nu SKR03-nummers 1571/1576/1771/1776/1789/8400 — klein, maar
zelfde risico). B: laten.

**DE3 Btw-rekeningen en Kennzahlen.** A: Umsatzsteuer 19/7 % → 81/86; Vorsteuer 19/7 % → 66; IC-verwerving 19/7 %-paren →
89/93 + 61; § 13b-paren (EU-diensten → 46/47 + 67, binnenland → 84/85 + 67); EUSt → 62; 0 % (PV) → 87; omzet zonder btw
via de klant → 41 / 43 / 21 / 45 / 48; Umsatzsteuer-Vorauszahlungen als afdrachtrekening. B: alleen 19/7 %.

**DE4 E-Bilanz-koppeling.** A: **later** (zoals RGS: elke rekening een element-ID van de HGB-taxonomie 6.9/6.10, alleen
verwijzen, bestanden niet meeleveren — geen open licentie). B: nu meebouwen.

**DE5 ELSTER.** A: **nu alleen het overzicht** (overnemen in Mein ELSTER, net als NL) en de **ZM-CSV**; de **UStVA-XML voor
upload in Mein ELSTER** pas als het XSD rechtmatig te krijgen is (gratis ontwikkelaarsregistratie door jou; licentie
nalezen); **geen ERiC** (gesloten bibliotheek, licentie niet openbaar). B: ERiC inbouwen.

**DE6 Sondervorauszahlung en Kz 83.** A: Kz 39 handmatig bij het december-tijdvak (BL11); Kz 83 = btw − Vorsteuer − 39;
de 1/11-melding (Kz 38, USt 1 H) niet. B: Kz 39 niet.

**DE7 Teaser.** A: DE pas noemen als DE6 af is (eerlijke claims). B: —.

## Film (na deelstap UK)
- Btw-scène: shot **NL-aangifteformulier** (zoals nu) + shot **UK VAT Return** (formulier + "Export for bridging
  software"), op de demo-UK-administratie uit `seed.py` (verzonnen, nooit C). Tekstkaart: "NL · UK ready" en
  "BE · FR · CH · DE coming soon – tell us what you need". `LEESMIJ.md` en `scenes.SCENES` bijwerken.

## Toetsen (bij A)
- **NL ongewijzigd**: alle bestaande btw/ICP-toetsen groen zonder aanpassing; daarnaast een vergelijking oude vs nieuwe kern
  op administratie A (alle maanden 2011–2026, identieke rubrieken).
- Per land pytest: rubriekindeling, omzet zonder btw per klant, verlegd/EU-paren, creditnota's (BE 48/49/63/64), afronding
  (tabel BL6), totaalformules (BE 71/72, UK 3/5, FR 16/23/25/27/28/32, CH 289/299/399/479/500, DE 83), btw-boeking per land,
  vastleggen + verschil, rechten, vier talen; schema-CSV's door `chartfiles`-toets (ook SL opent ze).
- Echte data **administratie C** (UK6); CH-XML tegen het XSD van eCH-0217 V2.0.0 en de officiële voorbeeld-XML's; DE ZM-CSV tegen
  de BZSt-beschrijving.
- Browsercontrole per land incl. 375 px, formulier naast het officiële PDF-model.

## Niet officieel vastgesteld (vóór de bouw van dat land nalezen)
- UK: afkappen of afronden van box 6–9; regel voor client secrets bij open-source software.
- BE: huidig XSD periodieke aangifte; drempel € 250 klantenlisting; betaaltermijn in de wet (KB nr. 24 opgeheven 2025).
- FR: CGI-artikelen (Legifrance onbereikbaar); kwartaalregel € 4.000 na 2027.
- CH: exacte Saldosteuersatz-Ziffern 2025+; boekingsvoorschrift 2200/2201 (gangbaar, niet officieel).
- DE: afrondingsrichting grondslag; UStVA-XSD; ERiC-licentie; licentie E-Bilanz-taxonomie.
