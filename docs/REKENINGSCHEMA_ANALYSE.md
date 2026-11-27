# Standaardrekeningschema's voor nieuwe administraties (vraag gebruiker 2026-10-11)

**Status: akkoord gebruiker 2026-10-11, RS1–RS13 alles A** (één voor één gekozen). Bouw na de btw-aangifte, vóór stap 7. Open actie gebruiker: SBR (Beheergroep RGS) vragen of de RGS-codes in GPL-software mogen (RS3, RS13).

**Wens.** Wie zonder SQL-Ledger-back-up begint, kiest een standaardschema voor het MKB. Minimaal NL, UK, FR; de
Zwitserse MKB-schema's (KMU/PME/PMI) erbij; MKB-schema's voor andere landen gezocht.

## Wat er nu is

- **Onze webapp kan geen nieuwe administratie aanmaken.** Systeem > Administraties koppelt alleen een bestaande
  SQL-Ledger-database. Een nieuwe database maken we nu alleen voor de toetsen (`compare/make_template.sh`: Perl-schema
  `Pg-tables.sql` + `Dutch-Standard-chart.sql` + seed). Een schema kiezen kan dus pas als er een stap "nieuwe
  administratie" is (RS1).
- **SQL-Ledger 3.2.12** levert ±70 `sql/*-chart.sql`. Indeling per bestand: `chart` (accno, omschrijving, A/H,
  gifi_accno, categorie, link[, contra]), `tax` (alleen tarief), `defaults` (voorraad/omzet/kosten/koersverschil),
  `curr`. **Alle tarieven zijn verouderd** en geen schema kent btw-rubrieken.

| Bestand | Rekeningen | Btw in het bestand | Opmerking |
|---|---|---|---|
| Dutch-Default | 57 (+13 kopjes) | 19 %/6 % | 2001, klein |
| Dutch-Standard | ±194 (+10 kopjes) | 19 %/6 % | 2002/2011, decimaal "Philips-stijl" (kostenverdeling 4/5/6), zwaar voor MKB |
| UK_General | 101 | 17,5 %/5 % | Sage-achtige nummers (0xxx activa, 4000 omzet, 5000 inkoop) |
| France | ±927 (+7) | 19,6 %/5,5 % | 2001, vrijwel volledig PCG, koersverschil op 999000 (niet PCG) |
| Belgium | ±344 (+77) | 6 %/21 % | PCMN/MAR, 2006, ook UTF-8-variant |
| Germany-SKR03 | 251 | 16 %/7 % | DATEV-nummers |

- **Zwitserland**: er bestaat een KMU/PME/PMI-schema uit 2012 (GPL): **KMU (de, 146 regels), PME (fr, 136) en
  PMI (it, 136)**, UTF-8, kopjes op elk niveau, gifi =
  bovenliggende groep, 10 waardecorrecties met `contra`, btw 8,0/2,5/3,8 % (sinds 2024: **8,1/2,6/3,8 %**).

## Per land (onderzoek 2026-10-11)

- **Nederland** — niets voorgeschreven. Standaard is **RGS** (Referentie GrootboekSchema, beheer SBR; huidige versie
  **3.8**, december 2025, Excel + XBRL; >4.500 codes; referentiecode zoals `BIvaGooAkp`). Gebruik is gratis, maar er is
  **geen expliciete licentietekst**; voorwaarde: RGS-codes niet wijzigen (eigen uitbreiding met extensiecode 5–9).
  De bekende MKB-selectie "RGS MKB" (GBNED/Boekhoudplaza, ±1.200 codes, decimaal 5 cijfers) is **auteursrechtelijk
  van GBNED: niet overnemen**. RGS kent **geen** btw-rubrieken; die koppeling leggen wij zelf (BA3).
- **Ter vergelijking: Exact Compact+ "basis-administratie"** (Exact-doc 04.457.619, aangereikt door de gebruiker
  2026-10-11): ±60 rekeningen, klassiek decimaal (0 vaste activa/eigen vermogen/hypotheek · 1 kas, bank, debiteuren,
  crediteuren, btw · 2 kruisposten/betalingen onderweg · 3 voorraad · 4 kosten · 7 kostprijs · 8 omzet · 9 kortingen en
  afrondingsverschillen); per rekening balans/W&V, btw-code, verdichting (8 groepen) en hoofdverdichting (activa,
  passiva, kosten, opbrengsten); "***"-scheidingsregels als kopjes. Verouderd (btw 19/6 %, Postbank). Bevestigt de
  indeling van RS3; niet overnemen (Exact), de nummerindeling is algemeen gebruik.
- **Ter vergelijking: Exact Online en RGS** (kennisbankartikel "RGS | Map to G/L accounts", aangereikt door de
  gebruiker 2026-10-11): de klant houdt een eigen schema; per rekening stelt Exact tot **drie RGS-codes** voor (op
  basis van de rekening of een eerdere koppeling), de gebruiker kiest, niets gaat automatisch; koppeling **per
  RGS-versie** (bij een nieuwe versie voorstellen uit de oude koppeling); dashboards/kengetallen kunnen op RGS draaien
  (benchmark); accountants koppelen in een sjabloonadministratie, extra rekeningen bij de klant apart.
- **Verenigd Koninkrijk** — niets voorgeschreven (alleen de jaarrekeningformats van de Companies Act/FRS 102/105).
  Gangbaar: Sage-nummerbanden (0 vaste activa, 1 vlottend, 2 schulden met 2200 btw, 3 vermogen, 4 omzet, 5 kostprijs,
  6–8 kosten). Btw: VAT100 met 9 boxen (6 en 7 = netto omzet/inkoop), btw 20/5/0 %.
- **Frankrijk** — **verplicht**: PCG (ANC 2014-03), gewijzigd door **ANC 2022-06** voor boekjaren vanaf 1-1-2025
  (o.a. 675/775 → 657/757, 79x vervalt). SL's France-chart is dus inhoudelijk verouderd. Btw 20/10/5,5/2,1 %;
  aangifte CA3 (regelnummers wisselen per jaar). Officiële tekst, vrij te gebruiken (Odoo/Dolibarr doen het ook).
- **België** — **verplicht**: minimumrekeningschema (MAR/PCMN). Officiële tekst. Btw 21/12/6 %.
- **Zwitserland** — niets verplicht; de **Schweizer Kontenrahmen KMU** (veb.ch) is de norm. Het KMU/PME/PMI-schema uit
  2012 staat onder GPL; of de KMU-indeling zelf rechten heeft is niet zeker.
- **Duitsland** — niets verplicht; in de praktijk DATEV **SKR03/SKR04**, volgens meerdere bronnen auteursrechtelijk
  beschermd (geen open licentie). Odoo/GnuCash leveren ze toch mee; juridisch niet zuiver.
- **Voorbeeld voor btw**: Odoo/OCA koppelt btw-codes aan rubrieken ("tags" 1a, 1b, …) per land — dat model past bij
  BA3 en bij latere andere landen.

## Beslispunten (advies steeds A)

**RS1 Nieuwe administratie aanmaken.** A: **Systeem > Administraties > Nieuwe administratie** in Python: database
aanmaken met het SQL-Ledger-schema (dezelfde tabellen als nu, zodat SQL-Ledger hem kan openen), land, taal,
standaardvaluta, boekjaar en een gekozen standaardschema; daarna direct koppelen. B: nieuwe administraties blijven via
SQL-Ledger zelf (Perl).

**RS2 Welke landen.** A: eerste ronde **NL, BE (NL- en FR-omschrijvingen), UK, FR, CH (KMU/PME/PMI)**; Duitsland
later (zie RS8). Plus altijd een **leeg schema** (alleen de verplichte koppelrekeningen). B: alleen NL, UK, FR en CH.

**RS3 Nederland.** A: **eigen compact MKB-schema**, ±150 rekeningen, decimaal (0 vaste activa/eigen vermogen,
1 financiële rekeningen, 2 tussenrekeningen, 3 voorraad, 4 kosten, 7 kostprijs, 8 omzet, 9 resultaat/privé), eigen
omschrijvingen, **per rekening de RGS 3.8-referentiecode** (zodat rapportage naar RGS later kan); rekeningen voor btw
21 %/9 %/0 %/verlegd/EU. Varianten: **eenmanszaak/vof** (privé-rekeningen), **bv** (aandelenkapitaal, rc dga) en een
**basis** van ±60 rekeningen zoals Exact (zelf uitbreiden). B: Dutch-Default bijwerken (tarieven) en verder laten. C: volledig RGS (te groot voor MKB).
*Daarnaast:* jij vraagt SBR (Beheergroep RGS) per mail of RGS-codes in GPL-software mogen; tot dan alleen de codes,
geen RGS-omschrijvingen.

**RS4 Verenigd Koninkrijk.** A: eigen schema ±100 rekeningen in Sage-achtige banden, met UK_General als basis
(GPL), btw 20/5/0 % en rekeningen voor de VAT100-boxen; Engelse namen. B: UK_General alleen tarieven bijwerken.

**RS5 Frankrijk.** A: **PCG abrégé** ±200 rekeningen, 6 cijfers, nomenclatuur 2025 (ANC 2022-06), btw 20/10/5,5/2,1 %,
koersverschil op 666/766; Franse namen. B: SL's France-chart (927 rekeningen, 2001) bijwerken.

**RS6 Zwitserland.** A: het KMU/PME/PMI-schema uit 2012 (GPL), **KMU (de), PME (fr), PMI (it)**, overnemen met bron (GPL, 2012), btw bijwerken
naar 8,1/2,6/3,8 %, verder ongewijzigd. B: alleen KMU (Duits).

**RS7 België.** A: Belgium-chart (PCMN) als basis, MKB-selectie ±200 rekeningen, btw 21/12/6/0 %, NL- en FR-variant.
B: België later.

**RS8 Duitsland en verder.** A: **nu niet**; later een eigen kleine indeling volgens HGB §266/275 (geen SKR-kopie),
als er vraag is. Oostenrijk, Spanje, Italië e.d. ook later. B: SL's SKR03 bijwerken en meeleveren (licentierisico).

**RS9 Btw in het schema.** A: elk schema levert de btw-rekeningen **met de actuele tarieven en ingangsdatum** (zoals
BT1); voor **NL meteen de rubriekkoppeling** van BA3 (1a, 1b, 2a, 3b, 4a, 5b …), zodat de btw-aangifte direct werkt.
Voor de andere landen leggen we de koppeling in dezelfde tabel vast **met een landcode** (UK-boxen, FR-regels), maar
een aangiftescherm komt er alleen voor NL (BA8). B: geen rubrieken in de schema's; altijd zelf instellen.

**RS10 Bestandsvorm.** A: per schema één leesbaar bestand in de code (`sqlledger/charts/<land>-<naam>.csv` +
kopregel met land, taal, valuta, standaardrekeningen, bron/licentie), los van de database; een toets laadt elk schema
in een lege administratie en controleert: alle koppelingen (AR/AP/betaal/btw/voorraad) aanwezig, standaardrekeningen
bestaan, geen dubbele nummers, kopjes kloppen, en SQL-Ledger (Perl) kan de administratie openen (`compare`). B: de
SL-`.sql`-bestanden zelf inlezen.

**RS11 Oude SL-schema's.** A: **niet** aanbieden (verouderde tarieven); wie een oud SL-schema wil, leest een SL-back-up
in. B: alle ±70 SL-schema's als "SQL-Ledger (oud)" in de lijst.

**RS12 Plek in de planning.** A: **na de btw-aangifte** (die levert de rubriekentabel voor RS9) en vóór stap 7 import;
volgorde: RS1 nieuwe administratie + leeg schema → NL → CH → UK → FR → BE. Elk land een eigen deelstap met
browsercontrole en vertalingen. B: vóór de btw-aangifte.

**RS13 RGS-koppeling voor elk schema (ook ingelezen SL-administraties).** A: op **Beheer > Rekeningschema** per
rekening een **RGS-code** (versie 3.8, met versienummer opgeslagen, eigen tabel zodat SL niets merkt), plus
een scherm **RGS-koppelen** zoals Exact Online: per rekening tot drie voorstellen (op nummerreeks, omschrijving,
categorie en btw-koppeling), de gebruiker kiest, niets automatisch; bij een nieuwe RGS-versie voorstellen uit de oude
koppeling. Onze NL-standaardschema's (RS3) hebben de codes al. Rapportage/dashboard op RGS **later** (eigen
beslispunt). Vereist dat de RGS-codelijst mee mag (vraag aan SBR, RS3). B: RGS alleen in onze standaardschema's, geen
koppelscherm.

## Deelstap 3: Zwitserland (RS6) — besluiten 2026-10-11

Gevonden bij het voorbereiden: het KMU/PME/PMI-schema uit 2012 (GPL) gebruikt de **oude** nummering van de Kontenrahmen KMU
(MWST 2011–2013, Vorsteuer 1171–1174, rente 6800, koersverschil 3950/3955, btw 2,5/3,8/8,0 %); ons lege CH-schema
(deelstap 1, `leeg-ch.csv`) volgt al de **huidige** nummering (Vorsteuer 1170–1172, MWST 2200–2202, afrekening 2209,
koersverschil 6940, afronding 6990).

**CH1 Nummering.** A: selectie en omschrijvingen van het schema uit 2012 overnemen, **hernummerd naar de huidige Kontenrahmen KMU**
(gelijk aan `leeg-ch`); bron in de kopregel "KMU/PME/PMI 2012 (GPL), hernummerd". B: het schema uit 2012 ongewijzigd.
**Keuze gebruiker 2026-10-11: A.**

**Keuzes binnen A — akkoord gebruiker 2026-10-11 (1–7):**
1. Eén bestand `sqlledger/charts/ch-kmu.csv` met omschrijvingen de, fr, it, en, nl (niet drie bestanden); naam in de
   keuzelijst KMU (de) / PME (fr) / PMI (it) / SME (en) / MKB (nl).
2. Italiaans alleen als taal van de rekeningnamen (`it` in de schema-talen van `chartfiles`); het scherm blijft NL/EN/DE/FR.
3. Btw 8,1 / 2,6 / 3,8 % vanaf 1-1-2024, één rekening per tarief zoals `leeg-ch`; "Vorsteuer auf Investitionen" een
   gewone rekening zonder btw-koppeling.
4. MWST-Ziffern met landcode CH (RS9): Umsatzsteuer 8,1 % → 303, 2,6 % → 313, 3,8 % → 343; Vorsteuer → 400 (405
   Investitionen heeft geen btw-koppeling, dus geen Ziffer). Geen aangiftescherm, alleen de koppeling.
5. Standaardrekeningen zoals `leeg-ch`: voorraad 1200, omzet 3200, kosten 4200, koersverschil 6940, btw-afdracht 2209,
   afronding 6990.
6. Kopjes op alle niveaus en WB-rekeningen (Wertberichtigung) als contrarekening; tikfout "Lanfristige"
   hersteld.
7. Werkwijze: branch `fase4-rsch` vanaf `fase4`, eigen worktree; toetsen (schema-toets,
   `compare/newcompany_check.py` met Perl, pytest, run_compare), browsercontrole (nieuwe administratie met CH-schema,
   DE en FR, 375 px), controle-Excel `dev/rekeningschema_ch_controle.xlsx`, README + vertalingen. Niet samenvoegen in
   `fase4` zonder akkoord. Testserver op poort **8770** (8769 is van de huisstijl).


**Na de analyse van ledger123 (2026-10-11):** `ledger123/sql-ledger` = 3.2.12 ongewijzigd; `ledger123/runmyaccounts` (RMA,
2.8-tak) heeft `RMACH_{DEU,FRA,ENG}_Basiskontenplan` (huidige KMU-nummering, 2022, GPL-2.0, 159 rekeningen, geen btw-tarieven,
geen Italiaans, met RMA-eigen rekeningen).

**CH2 Basis.** A: de selectie uit het schema uit 2012 (de/fr/it) hernummerd; RMACH alleen ter controle van nummers en namen (beide in de
bronregel). B: RMACH als basis, opgeschoond, btw en Italiaans zelf. **Keuze gebruiker 2026-10-11: A.**

**CH3 Koersverschil.** A: 6940 Kursdifferenzen zoals `leeg-ch` (keuze 5). B: zoals RMACH 6942 Kursverluste / 6952 Kursgewinne,
6940 Bankspesen (ook `leeg-ch` wijzigen). **Keuze gebruiker 2026-10-11: A.**

## Deelstap 4: Verenigd Koninkrijk (RS4) — analyse 2026-10-11, keuzes volgens advies (gebruiker afwezig, vooraf akkoord op het advies; ter keuring achteraf)

Gevonden: SL's `UK_General-chart.sql` (GPL) heeft 101 rekeningen in Sage-banden maar **geen kopjes** (onze
controle eist ze), btw 17,5 %/5 % op één rekening voor verkoop én inkoop, koerswinst/-verlies apart (4920/5220),
"Dividends" als kosten (8500) en de creditcard 1240 als schuld tussen de activa. Ons lege VK-schema (`leeg-uk`, deelstap 1)
zet de kopjes op 1000/2000/3000/4000/5000/7000 en de rekeningen daarbinnen op x001 (voorraad 1001, omzet 4001, kosten
5001, algemene kosten 7001), btw 20/5 % apart voor verkoop (2200/2201) en inkoop (2210/2211), afdracht 2202, koersverschil
7906, afronding 7907. Ledger123/RMA heeft voor het VK niets nieuwers (zelfde 2013-bestand).

**UK1 Basis.** A: UK_General als basis (selectie, namen, Sage-banden), aangepast aan `leeg-uk` (kopjes, x001-nummers,
btw per tarief en richting); alleen hernummeren waar het botst. B: eigen schema vanaf nul. **Gekozen: A.**
**UK2 Rechtsvorm.** A: één schema voor een **limited company** (zoals UK_General: aandelenkapitaal, corporation tax,
directors' loan), te gebruiken door een sole trader door rekeningen toe te voegen. B: ook een sole-trader-variant (capital
introduced, drawings). **Gekozen: A.**
**UK3 Btw.** A: 20 %, 5 % en 0 % voor verkoop (2200/2201/2203) en inkoop (2210/2211/2212); VAT100-box met landcode GB in
`x_vat_box`: verkoop → box 1, inkoop → box 4 (box 6/7 = de grondslag, af te leiden; geen aangiftescherm, BA8). B: alleen
20 en 5 % zoals `leeg-uk`. **Gekozen: A.**
**UK4 Fouten in UK_General.** A: herstellen — creditcard naar de schulden (2120), dividenden naar het eigen vermogen
(3300), koersverschil één rekening 7906 (zoals `leeg-uk`), afschrijvingsrekeningen 0012 als contra. B: overnemen zoals
het is. **Gekozen: A.**
**UK5 Lege schema's.** A: `leeg-uk` krijgt dezelfde VAT100-boxen (1/4) en `leeg-ch` de MWST-Ziffern (303/313/343/400),
zodat elk schema van een land dezelfde koppeling meebrengt. B: alleen de volle schema's. **Gekozen: A.**

## Deelstap 5: Frankrijk (RS5) — analyse 2026-10-11, keuzes volgens advies (gebruiker afwezig; ter keuring achteraf)

Gevonden: SL's `France-chart.sql` (2001, ASCII zonder accenten, 927 rekeningen, btw 19,6/5,5 %, koersverschil op 999000,
voorraad als standaard op 681730) is te groot en verouderd; ledger123 heeft hetzelfde bestand. Ons lege schema
`leeg-fr` (deelstap 1) volgt de PCG-nummers: 101000, 110000, 370000, 401000, 411000, 445510 afdracht, 445660–445663
voorbelasting en 445710–445713 te betalen btw per tarief, 512000, 530000, 606000, 607000, 658000 afronding, 666000
koersverschil, 706000, 707000.

**FR1 Basis.** A: **eigen PCG-abrégé-selectie** (±175 rekeningen, 6 cijfers, nomenclatuur ANC 2014-03 zoals gewijzigd
door ANC 2022-06, voor boekjaren vanaf 2025: geen 79 "transferts de charges", cessions van vaste activa op 657/757 in
plaats van 675/775), met de nummers van `leeg-fr`; Franse namen eigen werk naar de officiële benamingen, plus en/nl/de.
B: SL's France-chart inkorten. **Gekozen: A.**
**FR2 Koersverschil.** A: één rekening 666000 "Pertes et gains de change" zoals `leeg-fr` en CH3 (geen 766000).
B: 666000/766000 apart. **Gekozen: A.** *(Na keuring B5: de ene rekening is **656000** "Pertes et gains de change sur créances et dettes
commerciales", want sinds ANC 2022-06 is 666 alleen voor financiële koersverschillen.)*
**FR3 Btw-regels CA3.** A: met landcode FR in `x_vat_box`: te betalen 20 % → 08, 10 % → 9B, 5,5 % → 09, 2,1 % → 14
(taux particulier, bijlage 3310-A); voorbelasting → 20 (autres biens et services); geen aangiftescherm. **Nog te
controleren tegen de actuele notice 3310-CA3-SD** (regelnummers wisselen soms). *(Gecontroleerd tegen formulier 2026, B2: 2,1 % staat op
regel **T6**, niet 14.)* B: geen regels. **Gekozen: A.**
**FR4 TVA sur immobilisations.** A: 445620 als gewone rekening zonder btw-koppeling (zoals CH "Vorsteuer auf
Investitionen"), zodat een inkoop niet twee 20 %-tarieven krijgt. B: met koppeling en CA3-regel 19. **Gekozen: A.**
**FR5 Rechtsvorm.** A: één schema voor SARL/SAS én entreprise individuelle (108000 compte de l'exploitant, 644000/646000
exploitant naast 101000 capital). B: twee varianten. **Gekozen: A.**

## Deelstap 6: België (RS7) — analyse 2026-10-11, keuzes volgens advies (gebruiker afwezig; ter keuring achteraf)

Gevonden: SL's `Belgium-chart.sql` (2006, ±344 rekeningen + 77 kopjes, btw 6/21 %) volgt het minimumrekeningschema
(MAR/PCMN, KB 12-09-1983), maar is groot en verouderd (o.a. vóór het WVV van 2019 en de "niet-recurrente" rubrieken
66/76 sinds 2016). Ons lege schema `leeg-be` (deelstap 1): 100000, 140000, 340000, 400000, 411006/411012/411021
voorbelasting, 440000, 451006/451012/451021 te betalen btw, 451900 rekening-courant btw, 550000, 570000, 604000, 612000
algemene kosten, 654000 koersverschil, 657000 afronding, 700000.

**BE1 Basis.** A: **eigen MKB-selectie uit het MAR** (±125 rekeningen, 6 cijfers, nummers van `leeg-be`, 66/76
niet-recurrent, afschrijvingen op 2X9 als contra), één bestand met nl/fr/en/de (de "NL- en FR-variant" van RS7 = de
taalkeuze). B: SL's Belgium-chart inkorten. **Gekozen: A.**
**BE2 Btw-roosters.** A: met landcode BE in `x_vat_box`: te betalen btw → rooster 54, aftrekbare btw → 59 (de
grondslagen 01/02/03 en 81/82 volgen uit de rekeningen); geen 0 %-rekening, geen medecontractant/intracommunautair
(zelf toevoegen); geen aangiftescherm. Ook in `leeg-be`. B: geen roosters. **Gekozen: A.**
**BE3 Resultaatverwerking.** A: rubrieken 69/79 (resultaatverwerking) **niet** opnemen: de jaarafsluiting in SQL-Ledger
boekt naar de rekening overgedragen winst (140000). B: wel opnemen. **Gekozen: A.**
**BE4 Koersverschil.** A: één rekening 654000 zoals `leeg-be` en CH3/FR2 (geen 754000). B: apart. **Gekozen: A.**
