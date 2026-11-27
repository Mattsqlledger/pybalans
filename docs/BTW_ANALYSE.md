# Btw-aangifte — analyse (2026-10-11)

**Status: akkoord, alles A (gebruiker 2026-10-11).** Beslispunten BA1–BA9. Planning (besluit gebruiker
2026-10-11): analyse nu, bouwen direct na fase 4 stap 6, vóór stap 7 import.

## Wens van de gebruiker (2026-10-11)
- Alleen een **overzicht** om de aangifte zelf in te vullen (bij de Belastingdienst), geen koppeling met de
  Belastingdienst (geen SBR/Digipoort), geen ICP-opgaaf.
- Instellen of de aangifte **per maand of per kwartaal** gaat.
- Een **btw-uitdraai** van de maand of het kwartaal, zoals het voorbeeld uit Exact Online
  ("Btw-aangifte (Factuurstelsel): 2026 - 3de kwartaal"): rubrieken 1A–1E, 2A, 3A–3C, 4A–4B, 5A–5D en 5G, per rubriek
  grondslag en btw in hele euro's.
- Na het doen van de aangifte een **btw-boeking** maken (Exact: tabblad "Boeking").

## Wat er al is
- Beheer > Instellingen **Btw-aangifte per** kwartaal/maand/jaar/geen (`x_setting`, sleutel van `dashboard.vat_period`,
  migratie 10); het dashboard toont "btw-aangifte van het laatst afgesloten tijdvak" met Afgehandeld (`x_task_done`).
- Rapporten Btw verkoop/inkoop (`reports_arap.tax_report`, port van RP::tax_report): per btw-rekening de boekingen met
  netto en btw, ook "onbelast". Geeft geen rubrieken.
- SQL-Ledger zelf heeft geen aangiftemodule en geen koppeling btw-rekening → rubriek.

## Echte data
- **Administratie A** (NL): btw-rekeningen 1830/1831/1832 (21/9/0%) en 1835/1836/1837 (voorbelasting 21/9/0%), alle met
  `AR_tax:AP_tax`; 1840 "Rc BTW" en 1800 "Kortlopende schulden omzetbelasting". Administratie A deed **maandaangifte** en boekte daarna
  zelf per maand een memoriaal voor de btw-afdracht (347 stuks, 2011–2026): alle btw-rekeningen in **hele euro's** tegen 1840.
  De centen bleven op de btw-rekeningen staan (jaarsaldi van centen tot enkele tientjes).
  Kassabonnen staan ook als memoriaal met btw-regel (moeten dus mee in 5B).
- Narekenen (alle btw-mutaties per maand behalve de afdrachtboekingen, tegen die afdrachtboekingen): ongeveer de helft
  van de maanden in 2019/2024 klopt tot op de euro; de rest wijkt af door boekingen die ná de aangifte nog in die
  maand zijn ingevoerd of gewijzigd (bijv. een maand in 2024 met een verschil in de voorbelasting). Vandaar BA7.
- Adressen: land bij 38 van 815 adressen ingevuld (vrije tekst: "NL", "Nederland", "Belgie" …) → EU/niet-EU is niet
  betrouwbaar uit het land af te leiden. 0%-rekeningen 1832/1837 maar 7 keer gebruikt (2022).
- **Administratie C** is een Britse administratie (VAT on Sales/Purchases, "Payments to/from HMC&E", GBP): de Nederlandse
  rubrieken passen daar niet.

## Beslispunten (advies steeds A)

**BA1 Plek en scherm.** A: menu **Rapporten > Btw-aangifte** (naast Btw verkoop/inkoop). Tijdvak kiezen (standaard het
laatst afgesloten tijdvak volgens de instelling; ook een ander tijdvak of vrije periode). Scherm zoals het voorbeeld:
rubrieken met grondslag en btw; per rubriek doorklikken naar de boekingen; PDF (zoals stap 5) en Excel. Bovenaan:
administratie, btw-nummer, tijdvak, "factuurstelsel". Op het dashboard linkt de taak btw-aangifte hierheen. B: eigen
hoofdmenu "Btw".

**BA2 Maand of kwartaal.** A: de bestaande instelling **Btw-aangifte per** (Beheer > Instellingen) gebruiken, één
instelling voor dashboard en aangifte; "jaar" blijft voor wie dat mag, "geen" verbergt het menu-item. B: aparte instelling.

**BA3 Rubriek per btw-rekening.** A: nieuw scherm **Beheer > Btw-rubrieken**: per btw-rekening kiezen bij welke
rubriek hij hoort: 1A, 1B, 1C, 1D, 2A, 1E, 3A, 3B, 3C (omzet), 4A, 4B (verwerving/invoer: telt bij 5A én mag als
voorbelasting), 5B (voorbelasting) of "niet in de aangifte". Voorstel bij de eerste keer uit tarief en naam
("voorbelasting"/"te vorderen" → 5B, 21% → 1A, 9% → 1B, 0% → 1E), zichtbaar en aan te passen. Opslag in eigen tabel
`x_vat_box` (SL wist onbekende sleutels in `defaults`). Grondslag = netto van de boekingen met die btw-rekening (zoals het
bestaande btw-rapport); btw = de mutaties op de btw-rekening in het tijdvak. B: vaste indeling op rekeningnummer.

**BA4 Omzet zonder btw.** A: verkoopboekingen zonder btw-rekening tellen als grondslag in **1E**, tenzij bij de klant
een rubriek is ingesteld (veld "btw-rubriek" op het klantscherm: 1E, 3A buiten de EU, 3B binnen de EU; eigen tabel);
het scherm meldt hoeveel boekingen zo zijn ingedeeld. B: alleen via aparte 0%-btw-rekeningen (BA3).

**BA5 Afronden.** A: per rubriek op hele euro's **in uw voordeel** zoals de Belastingdienst toestaat (te betalen btw en
grondslag omlaag, voorbelasting omhoog); 5A, 5C en 5G uit de afgeronde bedragen; de exacte bedragen staan in de
doorklik en in Excel. B: gewoon afronden.

**BA6 Btw-boeking.** A: knop **Btw-boeking maken** (boekhouder+), bedoeld voor nadat je de aangifte zelf hebt gedaan:
opent het memoriaalscherm ingevuld (jij controleert en slaat op): per btw-rekening het exacte saldo van het tijdvak
tegengeboekt, het totaal van 5G (afgerond) op de **afdrachtrekening** en het afrondingsverschil (centen) op een
**rekening afrondingsverschillen**; datum laatste dag van het tijdvak, omschrijving "Btw-aangifte 2026 3e kwartaal".
Beide rekeningen in te stellen op Beheer > Btw-rubrieken (voorstel bij administratie A: 1840 Rc BTW). Na opslaan wordt de boeking
aan het tijdvak gekoppeld (eigen tabel `x_vat_return`: tijdvak, bedragen per rubriek, boeking, wie/wanneer) en is de
taak op het dashboard afgehandeld. B: zoals administratie A: alleen hele euro's, centen blijven op de btw-rekeningen staan.
C: direct boeken zonder memoriaalscherm.

**BA7 Afdrachtboekingen en latere wijzigingen.** A: boekingen die op de afdrachtrekening boeken (ook de oude
handmatige van administratie A) tellen niet mee in het overzicht. Een tijdvak met een btw-boeking toont de vastgelegde bedragen
naast de huidige; wijkt iets af (boeking later ingevoerd of gewijzigd), dan een melding met het verschil en de
boekingen die het veroorzaken; de suppletie of correctie in de volgende aangifte doe je zelf. Advies op het scherm na
de btw-boeking: de periode afsluiten (bestaande "boeken tot en met"). B: geen vergelijking.

**BA8 Andere landen.** A: alleen de **Nederlandse** aangifte; het menu-item staat er alleen als er rubrieken zijn
ingesteld; voor een buitenlandse administratie (zoals administratie C) blijven de rapporten Btw verkoop/inkoop. B: ook de Britse
VAT return (boxes 1–9).

**BA9 Rubrieken die niet uit de boekhouding komen.** A: 5D (kleineondernemersregeling) en eventuele correcties niet
berekenen: 5D staat op 0 met de opmerking dat je hem zelf invult; geen ICP-opgaaf (wens gebruiker). B: 5D handmatig
invullen en bewaren bij het tijdvak.

## Toetsen (bij A)
- pytest: indeling per rekening, omzet zonder btw (1E/3A/3B per klant), afronden in uw voordeel, verkoop- en
  inkoopfacturen met en zonder artikelen, memoriaal met btw-regel, creditnota's, valuta (bedragen in euro's),
  afdrachtboekingen uitgesloten, btw-boeking (bedragen, afrondingsverschil, koppeling), rechten, vier talen.
- **Echte data A**: alle maanden 2011–2026 narekenen tegen de eigen afdrachtboekingen; verschillen moeten
  te verklaren zijn met boekingen die na de datum van de afdrachtboeking zijn ingevoerd of gewijzigd (audittrail).
- Controle tegen het bestaande btw-rapport (zelfde totalen per btw-rekening).
- Browsercontrole incl. 375px, PDF naast het Exact-voorbeeld.
- Geen Perl-vergelijking (SL kan dit niet).
