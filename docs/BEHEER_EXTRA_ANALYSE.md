# Analyse: GIFI, boekjaar, importen/export betalingen, velden die niets doen (2026-10-13)

Besloten te bouwen door de gebruiker op 2026-10-13 (OVERDRACHT.md). Dit bestand is alleen analyse: **niets bouwen
vóór akkoord**. Paden: PL = `sqlledger_src/sql-ledger` (3.2.12).
Echte data: administratie A in de eigen installatie (alleen-lezen bevraagd), administratie B uit de dump van 16-4-2026
(alleen grep; B staat nog niet in de eigen installatie).

Beslispunten per onderwerp: **GF** (GIFI), **JA** (jaarafsluiting/boekjaar), **IM** (importen/export), **VW** (velden).
Advies staat telkens vooraan.

**AKKOORD gebruiker 2026-10-13: alles volgens advies** (GF1–GF6 A; JA1 A, JA2 A, JA3 B, JA4–JA8 A; IM1–IM8 A,
IM9 A1, IM10 A; VW1 A, VW2 niets, VW3 B, VW4 C, VW5 B, VW6 Kosten weg/Afronding laten/README 144 corrigeren,
VW7 A met voorrang). Volgorde: VW → JA → GF → IM → SEPA.

---

## 1. Beheerscherm GIFI

### Hoe SQL-Ledger het doet
- Menu *System > Chart of Accounts > Add GIFI / List GIFI* (PL `menu.ini:1314-1320`); `am.pl` `list_gifi` (455),
  `add_gifi`/`edit_gifi` (531/546), knoppen Save, Delete (alleen als niet in gebruik), *Copy to COA* (653: opent
  "nieuwe rekening" met GIFI ingevuld). `AM.pm` `gifi_accounts`/`get_gifi`/`save_gifi`/`delete_gifi` (282–375).
- Verwijderen kijkt alleen naar mutaties (`AM.pm:323-327`): een GIFI die aan rekeningen hangt zonder mutaties mag
  weg → rekeningen houden een dode verwijzing.
- **Perl-fout:** `$form->{id} *= 1` (`AM.pm:346,381`) maakt van de oude code een getal. "020" wordt
  20 → wijzigen/verwijderen raakt niets maar meldt "GIFI saved!"; een niet-numerieke code wordt 0 → INSERT botst.
- Geen import/export van GIFI (alleen kolom `gifi_accno` in de rekeningschema-import, `im.pl:861`). Bij een nieuwe
  dataset laadt SL `sql/<land>-gifi.sql` (o.a. Belgium, Swiss-German; geen Dutch/UK/France).

### Wat PyBalans heeft
- GIFI invullen bij een rekening (`templates/am/account.html:12`, `sqlledger/admin.py:221-243`), kolom in de
  rekeninglijst, rapporten op GIFI (balans, W&V, proefbalans, rekeningmutaties, grootboekkaart, btw-rapport),
  "GIFI onderaanneming" bij leveranciers, kolom in import/export rekeningschema.
- **Ontbreekt:** scherm voor de tabel `gifi` zelf (codes + omschrijving), menu, route, bibliotheekfuncties.
- Nieuwe administratie vult `gifi` niet (`newcompany.py:96-98`).

### Echte data
- **Administratie A:** 15 GIFI-codes; 27 van 163 rekeningen gekoppeld. Gebruikt als **verdichting** (meerdere
  rekeningen → één code). Eén code hangt aan niets (dubbel van een andere). **5 kopregels verwijzen naar codes die
  niet bestaan.**
- **Administratie B:** geen GIFI.

### Beslispunten
- **GF1 Omvang.** A: zoals SL — lijst, toevoegen, wijzigen, verwijderen, plus kolom "aantal rekeningen" en een
  signaal bij rekeningen die naar een niet-bestaande code verwijzen. B: alleen lijst. C: niet bouwen, verwijzen naar
  RGS. **Advies A** (rapporten op GIFI bestaan al, maar de codes zijn nergens te onderhouden; administratie A heeft dode
  verwijzingen).
- **GF2 Verwijderen.** A: alleen als geen enkele rekening de code gebruikt (strenger dan SL), anders melding met de
  rekeningen. B: zoals SL, alleen blokkeren bij mutaties. **Advies A.**
- **GF3 Code wijzigen.** A: hernoemen neemt `chart.gifi_accno` mee in één transactie, code als tekst (de `*1`-fout
  niet overnemen; bewuste afwijking). B: alleen omschrijving wijzigbaar. **Advies A.**
- **GF4 Koppelen bij de rekening.** A: GIFI-veld in het rekeningformulier wordt een keuzelijst (bestaande
  zoeklijst `combo.js`) uit de tabel, met waarschuwing bij een onbekende code; geen knop "Kopieer naar
  rekeningschema". B: knop *Copy to COA* zoals SL, veld blijft vrij. **Advies A.**
- **GF5 Import/export.** A: soort "GIFI" in Beheer > Importeren/Exporteren (twee kolommen code, omschrijving;
  nieuw/bijwerken/ongewijzigd). B: niet (zoals SL). **Advies A** (±1 uur extra, past in bestaand patroon).
- **GF6 Plaats.** A: Beheer, direct onder Rekeningschema en vóór RGS-koppeling, naam "GIFI-indeling", alleen
  beheerder; helptekst zegt dat RGS voor NL de officiële indeling is. B: tabblad binnen Rekeningschema. **Advies A.**

Niet voorgesteld: GIFI-tabellen voor CH/BE vullen bij een nieuwe administratie (onze schema's zijn eigen herzieningen).

**Schatting:** ½ sessie (3–4 uur) incl. GF5, toetsen, EN/DE/FR, helptekst.
**Bestanden:** `sqlledger/admin.py`, `routes_am.py`, nieuw `templates/am/gifi_list.html` + `gifi.html`,
`am/account.html`, `menu.py`, `help/am_gifi.html`, i18n ×3, `importer.py` + `routes_import.py` (GF5), toetsen, README.

---

## 2. Maand van de jaarafsluiting (boekjaar) instelbaar

### Hoe SQL-Ledger het doet
- In 3.2.12 is `defaults.yearend` **een overblijfsel**: het staat alleen in de `optional`-lijst (`am.pl:2711`), heeft
  geen invoerveld en geen enkele `.pm`/`.pl` leest het. Oorspronkelijk een kolom met een datum (`'1/31'`,
  `Pg-upgrade-1.2.7-1.4.0.sql:100`). `AM::save_defaults` wist en herschrijft defaults → `yearend` verdwijnt bij
  Opslaan (`AM.pm:1603-1628`).
- Jaarafsluiting (`am.pl:4125-4245`): vrij datumveld, geen voorstel.
- Rapporten: "Year" = **12 maanden vanaf de gekozen maand** (`Form::from_to`, `rp.pl:147-150`). Een gebroken boekjaar
  kon in SL dus al door bv. juli + Jaar te kiezen, maar de standaard volgt het boekjaar niet.
- "Vorig jaar" = datums min 1 jaar (`RP.pm:201-215`): werkt voor elk boekjaar.

### Wat PyBalans heeft
- **Er is al een veld "Boekjaar eindigt in"** bij Nieuwe administratie en in de wizard (`company_create.html:44`,
  `newcompany.py:41,55,104`; maandnummer, standaard 12) → `defaults.yearend`.
- Gebruikt op één plek: voorstel datum jaarafsluiting (`routes_gl.py:450-481`).
- **Niet** wijzigbaar in Beheer > Instellingen (`admin.py:61-67`). Bij een ingelezen SL-administratie ontbreekt het
  (→ 12).

### Plekken die "boekjaar = kalenderjaar" aannemen

| Plek | Nu | Bij gebroken boekjaar | Moet? |
|---|---|---|---|
| `reportdefs.py:229` `period_dates` "Jaar" (**F5**) | 1-1..31-12 | zie JA3 | moet |
| `reportdefs.py:152` standaardperiode ("lopend boekjaar t/m vandaag") | 1 jan t/m vandaag | begin boekjaar t/m vandaag | moet |
| `reportdefs.py:154-157` standaard jaar | kalender | boekjaar | moet |
| `routes_reports.py:159-161` `_years()` keuzelijst | kalenderjaren | "2025/2026" | moet |
| `reportdefs.py:235` `asof_date` balans "Jaar" | 31-12 | laatste dag boekjaar | moet |
| `routes_reports.py:197` boeking → rekeningmutaties | kalenderjaar | boekjaar van de datum | moet |
| `routes_dashboard.py:205` bank → mutaties | kalenderjaar | boekjaar | moet (volgt JA3) |
| `sqlledger/dashboard.py:15,255-276` resultaat dit/vorig jaar, maandgrafiek | kalender | boekjaar, maanden in boekjaarvolgorde | moet |
| `templates/dashboard/_tegels.html:3,5` "Boekjaar {jaar}" | "2026" | "2025/2026" | moet |
| `plchart.py:162` jaarlabel | alleen 1-1..31-12 | ook boekjaar herkennen | netjes |
| `routes_switch.py:393` controleblad Ingebruikname (**F22**) + help `am_switch_sheet.html:15` | 1 jan | begin boekjaar van de peildatum | moet |
| `routes_gl.py:450-465` voorstel jaarafsluiting | volgt al `yearend` | — | klaar |
| `reports.py` W&V zonder afsluitboekingen, resultaat lopend jaar, vorig jaar | op datums | — | hoeft niet |
| btw-aangifte, ICP, btw-todo dashboard (`vatreturn.py:112`, `dashboard.py:324-345`) | kalender | blijft kalender (wettelijk) | hoeft niet |
| `printdata.py:206-212` `fdy`/`ldy`, `numbering.py:75` `<%yyyy%>` | kalender | Perl doet hetzelfde | hoeft niet |
| `switchover.py:190-199` boekingen per jaar | kalender | — | hoeft niet |
| `admin.py:61-67` + Instellingen | ontbreekt | toevoegen | moet |

### Echte data
- **Administratie A:** geen `yearend` in defaults; 14 afsluitingen, **allemaal op 31-12** (2011–2024).
- **Administratie B (dump):** geen `yearend`; afsluitingen 31-12-2016 t/m 31-12-2024.
- **Voor jouw administraties verandert er dus niets**; het is voor andere gebruikers.

### Beslispunten
- **JA1 Opslag.** A: maandnummer `defaults.yearend` (bestaat al, 1–12, leeg = 12). B: tabel boekjaren met begin/eind
  per jaar (zoals Exact; ook verlengd/verkort eerste jaar). **Advies A** — afwijkend eerste/overgangsjaar kan via
  vrije datums en de jaarafsluiting met vrije datum. B kost 1,5–2 dagen extra.
- **JA2 Instellen.** A: ook in Beheer > Instellingen, keuzelijst "Boekjaar eindigt in" (zelfde tekst als bij
  aanmaken). B: alleen bij aanmaken. **Advies A.**
- **JA3 "Jaar" in rapporten (F5).** A: "Jaar" wordt boekjaar; btw-rapporten houden een eigen kalenderjaar.
  B: "Jaar" blijft kalenderjaar en er komt een keuze **"Boekjaar"** bij (alleen zichtbaar als het boekjaar niet op 31-12
  eindigt); standaardperiode en dashboardlinks gebruiken "Boekjaar". C: niets meeschuiven. **Advies B** — niets
  verandert voor btw en voor wie een kalenderboekjaar heeft.
- **JA4 Benaming.** A: kalenderboekjaar "2026", gebroken "2025/2026" (waarde = beginjaar). B: alleen eindjaar.
  **Advies A.**
- **JA5 Wijzigen als er al afsluitingen zijn.** A: toegestaan met waarschuwing (laatste afsluitdatum tonen, uitleg
  overgangsjaar), bestaande afsluitingen blijven staan, audittrail. B: blokkeren na de eerste afsluiting. **Advies A.**
- **JA6 Dashboard.** A: resultaat dit/vorig jaar en maandgrafiek volgen het boekjaar. B: blijft kalender.
  **Advies A.**
- **JA7 Controleblad Ingebruikname (F22).** A: begin boekjaar van de peildatum. B: blijft 1 januari. **Advies A.**
- **JA8 Btw en ICP.** A: blijven altijd per kalendertijdvak. **Advies A** (geen keuze eigenlijk, ter bevestiging).

**Schatting:** 1–1,5 sessie (één helper `fiscal_year(d, eindmaand)` + `fiscal_label` in `sqlledger/dates.py`, en alle
"moet"-plekken uit de tabel), incl. toetsen, browsercontrole, EN/DE/FR, helpteksten. README open punt r2661 vervalt.

---

## 3. Importeren van verkoopfacturen, betalingen, orders; exporteren van betalingen

### Hoe SQL-Ledger het doet
- Menu Import: Sales Invoices, Payments, Sales Orders, Purchase Orders; Export: Payments (`menu.ini:1137-1177`).
- CSV met kopregel van **interne veldnamen** (optioneel vertaald via `import.map`). Rijen eerst naar
  `report`/`reportvars`, dan een voorbeeldlijst met vinkje per document. **Commit per document**: bij een fout stopt
  het halverwege en blijft wat al geboekt was staan.
- **Verkoopfacturen** (`im.pl:478-646, 1310-1377`; `IM.pm:18-245, 515-648`): opeenvolgende rijen met hetzelfde
  `invnumber` + `customernumber` = één factuur. Kopvelden o.a. `customernumber, invnumber, transdate, duedate, curr,
  exchangerate, AR, taxincluded, notes, ordnumber, ponumber, employeenumber, department, shipto*`, meteen betaald
  `paid_1/datepaid_1/AR_paid_1`; regels `partnumber, qty, sellprice, discount, description, unit, projectnumber`.
  Btw via klant/artikel, boeken met `IS->post_invoice` (dus ook voorraad/kostprijs). **Bestaand nummer: openstaand →
  vervangen**, (deels) betaald → geweigerd.
- **Orders** (`im.pl:665-836`, `IM.pm:248-453, 651-759`): zelfde opzet, `OE->save`. **Perl-fout:** bestaand order
  gezocht op ordernummer (`IM.pm:703`) zonder filter op soort/relatie: een inkooporder kan een verkooporder of offerte
  met hetzelfde nummer overschrijven. Daarnaast een beveiligingsfout op die plek: Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- **Betalingen** (`im.pl:1446-1687`, `IM.pm:1581-1721`): CSV `datepaid, credit, debit, dcn, source, memo` of
  Zwitsers ESR `.v11`. Koppelen alleen op `dcn` of op **exact het openstaande bedrag (eerste open post van wie dan
  ook)**; `invnumber` wordt niet gebruikt. Perl-fout: AP-deel `JOIN bank` sluit inkoopfacturen zonder `bank_id` uit
  (`IM.pm:1609`).
- **Export betalingen** (`im.pl:180-475, 2173-2904`): **geen betaalopdracht**, maar een lijst van al geboekte, nog
  niet afgeletterde betalingen van één bankrekening als CSV of Zwitsers vaste-lengtebestand (DTA-stijl), daarna
  "Reconcile Payments".

### Wat PyBalans heeft
- Importer: upload CSV/xlsx → automatisch koppelen (SL-naam, label in elke taal, onthouden koppeling) →
  **proefrun** (zelfde bibliotheek, teruggedraaid) → **alles of niets** in één transactie. Groeperen op referentie
  bestaat al voor het memoriaal (`importer.py:334`), bestaande referentie = rij uit.
- Herbruikbaar: `InvoiceLedger.post_invoice` (`invoice.py:486`), `OrderLedger.save_order` (`order.py:321`),
  `Payments.post_payment` (`payments.py:351`, met `expected_due`), `bankimport.py` (CAMT.053/MT940, koppelt al op
  betalingskenmerk → factuurnummer → IBAN+bedrag).
- **Geen** pain.001/SEPA-code. Eerder besloten (FASE4 **D18 = A**: geen facturen/orders/betalingen-CSV, "CAMT/MT940
  dekt dit"; **D16**: pain.001 niet in fase 4). Dit besluit herziet D18 en D16.

### Echte data
- Administratie A: ar 14.136 (2011–2025), ap ±18.000; **orders: alleen 22 inkooporders uit 2011–2012**, geen verkooporders.
  `report`/`reportvars` leeg, hoogstens 25 facturen per dag → **geen aanwijzing dat er ooit geïmporteerd is**.
- Administratie B: 9 ar, 7 ap, 0 orders.
- Voor jou is dit dus vooral voor de volledigheid / andere overstappers; de SEPA-export kan voor jou wel nuttig zijn
  (inkoopfacturen betalen).

### Voorgestelde kolommen (SL-namen blijven werken; NL-labels als alias)
- **Verkoopfactuur** kop: `invnumber, customernumber, transdate, duedate, invoicedescription, ordnumber, ponumber,
  curr, exchangerate, taxincluded, AR, notes, intnotes, employeenumber, department, dcn`; regel: `partnumber,
  description, qty, sellprice, discount, unit, projectnumber`; optioneel `paid_1, datepaid_1, AR_paid_1`.
- **Orders:** idem met `ordnumber`, `customernumber`/`vendornumber`, `reqdate`, `orderdescription`.
- **Betaling:** `datepaid, amount` (of `credit`/`debit`), `invnumber, dcn, customernumber/vendornumber, source, memo,
  exchangerate`.

### Beslispunten
- **IM1 Soorten.** A: alle vier (verkoopfacturen, betalingen, verkooporders, inkooporders). B: alleen facturen +
  betalingen. C: alleen facturen. **Advies A** (orders gebruiken dezelfde code).
- **IM2 Groeperen.** A: alle rijen met hetzelfde nummer + relatie, ook niet-opeenvolgend (zoals ons memoriaal); rij
  zonder nummer start een nieuw document als relatie of datum wijzigt, en krijgt het volgende nummer. B: alleen
  opeenvolgend (SL). **Advies A.**
- **IM3 Bestaand nummer.** A: fout, document uit; nooit vervangen; controle per soort en relatie. B: openstaande
  vervangen zoals SL. **Advies A.**
- **IM4 Boeken.** A: facturen direct boeken via `post_invoice` (proefrun = controle). B: boeken met "in de wacht".
  **Advies A.**
- **IM5 Btw.** A: berekend uit klant en artikel zoals op het scherm; het voorbeeld toont netto/btw/totaal per
  document. B: btw-bedrag uit het bestand. **Advies A.**
- **IM6 Onbekend artikel/relatie.** A: fout per document, niets automatisch aanmaken (eerst relaties/artikelen
  importeren). B: regel zonder artikel toestaan met omschrijving + omzetrekening. **Advies A.**
- **IM7 Betalingen koppelen.** A: op `invnumber` (+ relatie), anders `dcn`; nooit alleen op bedrag; geen koppeling =
  fout. B: zoals SL (dcn, anders eerste open post met dat bedrag). C: betalingen-CSV niet bouwen, alleen
  Bankafschrift inlezen. **Advies A**, met in de helptekst een verwijzing naar Bankafschrift inlezen voor
  bankbestanden.
- **IM8 Alles of niets.** A: zoals de bestaande importer (proefrun, één transactie). **Advies A.**
- **IM9 Export betalingen.** A: **SEPA-betaalbestand (pain.001.001.03, NL-banken; .09 als keuze)** van te betalen
  inkoopfacturen: open posten selecteren (vervaldatum, leverancier), IBAN/BIC van de leverancier, omschrijving =
  factuurnummer of betalingskenmerk (gestructureerd). Daarna **A1** niets boeken — het bankafschrift lettert later af
  via de bestaande koppeling — of **A2** meteen als "in afwachting" boeken. B: lijst van geboekte, niet-afgeletterde
  betalingen als CSV, zoals SL. C: beide. **Advies A met A1** (SL's formaat is Zwitsers en verouderd).
- **IM10 Plaats en rechten.** A: facturen/orders/betalingen in Beheer > Importeren (beheerder); SEPA-bestand onder
  Betalingen als "Betaalbestand maken" voor wie mag betalen. **Advies A.**

**Schatting:** facturen + orders ±1 sessie, betalingen-CSV ½, SEPA (selectie, XML, XSD-controle, toetsen) ½–1,
vertalingen/help/Perl-vergelijking ½ → **2,5–3 sessies**.
**Bestanden:** `importer.py`, `routes_import.py`, `templates/am/import*.html`, nieuw `sqlledger/sepa.py` + route en
sjabloon, `menu.py`, i18n ×3, help, toetsen, README (afwijking 230), FASE4_ANALYSE (D16/D18 herzien).

---

## 4. Velden die niets doen (V3 golf 3)

Twee van de zes klopten niet: **"Vast" werkt al** en **"Afronding" werkt al** (op PDF en mail, zoals SL). En er is
één belangrijker veld gevonden: **"Korting %" op de klantkaart doet niets** (zie VW7).

| Veld | SQL-Ledger | PyBalans nu | Echte data |
|---|---|---|---|
| Rapporten standaard op kasbasis (`defaults.method`) | beginstand van het rondje Stelsel in rapporten (`Form.pm:3009`, `rp.pl:217`) | opgeslagen, rapporten staan vast op Factuurstelsel (`reportdefs.py:131`) | niet ingesteld |
| "Vast" klant-/leveranciersnummer | nummer bestaande relatie alleen-lezen (`ct.pl:1985`) | **werkt** (`contacts.py:456,540`, `ct/form.html:44`) | niet ingesteld |
| Map/url referentiedocumenten (`referenceurl`) | referentiecode wordt link naar url+code, uploaden verdwijnt (`cm.pl:66-75`) | opgeslagen, nergens gebruikt | niet ingesteld |
| Decimalen per valuta (`curr.prec`) | rekenprecisie per boekingsvaluta (`Form.pm:2539`, `OE.pm:2223`) | alles rekent met `defaults.precision` | alleen EUR, 2 |
| Korting (%) bij Branches (`business.discount`) | verlaagt verkoopprijs van klanten in die branche (`PM.pm:136-142`) | opgeslagen, niet gebruikt | geen branches |
| Kosten bij Betaalmethoden (`fee`) | **ook in SL zonder effect** | idem | geen betaalmethoden |
| Afronding bij Betaalmethoden (`roundchange`) | afronden "te betalen" op afdruk (`IS.pm:532-592`) | **werkt** op PDF/mail (`printdata.py:369,501`) | geen |
| **Korting % op klant/leverancier** (`customer.discount`) | vult regelkorting op nieuwe factuur-/orderregels (`is.pl:1282`, `oe.pl:1324`) | **niet gebruikt** (`arap.py:208` selecteert het niet) | **164 klanten** met korting; 9.807 van 13.513 factuurregels hebben korting, **8.150 daarvan precies de klantkorting** |

README afwijking 144 ("Niet gebouwd: kasafronding (`roundchange`)") klopt dus niet meer en moet worden aangepast.

### Beslispunten
- **VW1 Kasbasis.** A: `defaults.method` wordt de beginstand van "Stelsel" in balans en W&V (jaarafsluiting blijft
  factuurstelsel, zoals SL). B: veld weghalen. **Advies A** (±1 uur).
- **VW2 "Vast".** Werkt al; alleen helptekst nalopen en het punt van de V3-lijst halen. **Advies: niets bouwen.**
- **VW3 Referentie-url.** A: laten werken (code wordt link, alleen http/https/file, uploaden blijft mogelijk).
  B: veld van het scherm halen (waarde blijft in de database, back-ups ongemoeid). **Advies B** — PyBalans heeft
  echte bijlagen en niemand gebruikt het.
- **VW4 Decimalen per valuta.** A: rekenen per valuta (1,5–3 dagen, hoog risico: btw-afronding, koersverschillen).
  B: invoerveld weghalen. C: tonen als alleen-lezen met uitleg "PyBalans rekent met de algemene precisie
  (Instellingen)". **Advies C** — A pas als iemand een valuta zonder decimalen (bv. JPY) gebruikt.
- **VW5 Branchekorting.** A: verkoopprijs × (1 − korting) bij artikel kiezen, "Handelskorting x %" in de kop (±½ dag,
  prijs verandert stil). B: kortingsveld bij Branches weghalen, branche zelf blijft. **Advies B** (geen data, overlapt
  met VW7).
- **VW6 Betaalmethoden.** "Kosten": B weghalen (ook in SL zonder effect). "Afronding": blijft; README 144 corrigeren.
  Eventueel `defaults.roundchange` op Instellingen (zoals `am.pl:2555`). **Advies: Kosten weg, Afronding laten,
  README corrigeren; standaardafronding op Instellingen niet (YAGNI).**
- **VW7 Korting % van de relatie op nieuwe regels (nieuw gevonden).** A: bij een nieuwe artikelregel op factuur of
  order wordt de regelkorting standaard de korting van de klant/leverancier (zoals SL); bestaande regels en
  facturen blijven ongemoeid. B: veld van de klantkaart halen (kan niet goed: 164 klanten). **Advies A, met
  voorrang** — dit raakt jouw dagelijkse facturering in administratie A zodra je weer factureert.

**Schatting:** VW7 ½ sessie, VW1/3/4/5/6 samen ½ sessie → ±1 sessie.

---

## 5. Volgorde van bouwen en schatting

| # | Onderdeel | Schatting | Waarom deze plek |
|---|---|---|---|
| 1 | Velden (VW1–VW7), VW7 eerst | ±1 sessie | raakt echte data (kortingen in administratie A), klein, ruimt schermen op |
| 2 | Boekjaar (JA1–JA8, F5, F22) | 1–1,5 sessie | raakt rapporten en dashboard; beter vóór de importen klaar |
| 3 | GIFI-beheer (GF1–GF6) | ½ sessie | los, klein; GF5 gebruikt de importer al |
| 4 | Importen facturen/orders/betalingen (IM1–IM8, IM10) | 2 sessies | grootste stuk, bouwt op bestaande importer |
| 5 | SEPA-betaalbestand (IM9) | ½–1 sessie | los van de importer; eventueel naar voren als je het snel wilt gebruiken |

**Totaal ±5–6 sessies** (de eerdere schatting van 1–2 sessies was te laag: vooral de importen en SEPA zijn groter).
Werkwijze per onderdeel zoals afgesproken: eigen branch + worktree, toetsen, EN/DE/FR,
helptekst, keuzes binnen A ter keuring in README Open punten, niets samenvoegen zonder akkoord.
Een nieuwe chat per onderdeel (of per twee kleine) is verstandig.
