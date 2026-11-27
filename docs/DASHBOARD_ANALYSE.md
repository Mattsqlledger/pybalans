# Dashboard — analyse en voorstel (2026-10-11)

Wens gebruiker (2026-10-10): een eigen dashboard bij het inloggen. Voorbeeld: de "Financiële cockpit" van
Exact Online (PDF van de gebruiker, van administratie C), dus geen kopie maar een eigen versie op de gegevens
die we al hebben. **Status: akkoord gebruiker 2026-10-11, DB1–DB13 alles A; GEBOUWD op `fase4-dashboard` (nacht 2026-10-11), keuzes binnen A 1–13 in README "Open punten" ter beoordeling** (DB1 met een vast menu-item Dashboard; DB5: een niet afgevinkt tijdvak blijft na de termijn staan als "te laat"). Een mockup met demogetallen staat in
`dev/voorbeeld-pdf/dashboard/mockup.html` (niet in git).

## Wat Exact Online laat zien

| Blok | Inhoud |
|---|---|
| Vier tegels | Bank/kas huidig saldo · Verkoop openstaande posten · Inkoop openstaande posten · Resultaat boekjaar t/m periode |
| Bank/kas | saldo per bankrekening, "bijgewerkt t/m" (laatste afschrift), knop afschriften importeren, kasboek |
| Verkoop | staafgrafiek openstaand per ouderdom (0, 1–30, 31–60, > 60 dagen), knop nieuwe verkoopfactuur |
| Inkoop | idem voor te betalen, knop inkoopoverzicht |
| Resultaat | omzet, kosten, resultaat: dit jaar t/m periode vs vorig jaar, en de periode zelf vs dezelfde periode vorig jaar; staafgrafiek resultaat per periode (dit jaar en vorig jaar) |
| To do | btw-aangifte en ICP-opgaaf binnen 21 dagen, nog af te handelen banktransacties, verlopen verkoopfacturen (aantal + bedrag), verlopen inkoopfacturen |

## Wat wij al hebben

Alles op het Exact-dashboard bestaat al als rapport of lijst, bewezen tegen Perl:

- **Saldo betaalrekeningen**: rekeningmutaties/balans; bankafschriften met laatste afschrift, regels per status
  (`x_bank_statement`, `x_bank_line`: open/gekoppeld/geboekt/genegeerd).
- **Openstaand verkoop/inkoop per ouderdom**: ouderdomsanalyse (`reports_arap.aging`, open per peildatum).
- **Omzet/kosten/resultaat per periode, met vorig jaar**: W&V met `includeperiod=month` en `previousyear`
  (`reports.income_statement`, sluit jaarafsluitingsboekingen uit).
- **Verlopen facturen**: openstaand met vervaldatum; aanmaningslijst (`dunning`).
- **Btw**: btw-rapporten per tijdvak.
- **Extra wat Exact hier niet toont, maar wij wel hebben**: terugkerende boekingen die klaarstaan, open orders en
  offertes, artikelen onder het bestelniveau, inkoopfacturen die binnenkort vervallen, documenten die nog niet
  gemaild/geprint zijn (batch).

**Valkuilen (gemeten op administraties A en C):**
- Losse SQL is snel (< 30 ms), maar geeft verkeerde getallen. "Betaalrekeningen" via de koppeling `paid` neemt ook
  kruisposten, betalingsverschillen en rekening-couranten mee. Resultaat per maand zonder de bibliotheek telt de
  jaarafsluiting mee (bij administratie C in juli 2015 een grote afwijking). Het dashboard moet dus dezelfde bibliotheekfuncties gebruiken als de
  rapporten, zodat een getal op het dashboard altijd gelijk is aan het rapport waar je op doorklikt.
- De ouderdomsanalyse duurt op administratie A 5 s; voor een startscherm is dat te traag (zie DB7).
- Administraties A en C hebben geen boekingen in het lopende jaar (A boekt sinds 2025 geen verkoop meer, C stopt in 2016):
  een dashboard "dit jaar" is daar leeg (zie DB9).

## Voorstel (zie de mockup)

```
┌ Testbedrijf BV ──────────────────────────────────────────── boekjaar 2026 t/m oktober ┐
│ Bank en kas        │ Te ontvangen        │ Te betalen          │ Resultaat 2026          │
│ € 57.291           │ € 1.603  (2 verv.)  │ € 1.734             │ € 12.480  (2025: 9.120) │
├────────────────────┴─────────────────────┴─────────────────────┴─────────────────────────┤
│ Bank en kas            │ Te ontvangen (ouderdom)  │ Te betalen (ouderdom)                 │
│ ABN-AMRO   € 53.011    │  ▇▇  ▅  ▂  ▁             │  ▇  ▂  ▁  ▁                           │
│  afschrift t/m 09-10   │  0 1-30 31-60 >60        │  0 1-30 31-60 >60                     │
│  3 regels te verwerken │ [Verkoopfactuur] [Aanm.] │ [Inkoopfactuur uploaden] [Betalen]    │
├────────────────────────┴──────────────────────────┴───────────────────────────────────────┤
│ Resultaat per maand (2026 vs 2025)               │ Te doen                                 │
│ ▆▅▇▃▆▅▇▆▅▄   omzet/kosten/resultaat              │ • Btw 3e kwartaal: aangifte vóór 31-10  │
│ deze maand vs oktober 2025                       │ • 3 bankregels te verwerken             │
│                                                  │ • 2 vervallen verkoopfacturen € 1.603   │
│                                                  │ • 4 terugkerende boekingen klaar        │
│                                                  │ • 2 artikelen onder bestelniveau        │
└──────────────────────────────────────────────────┴─────────────────────────────────────────┘
```

## Beslispunten (advies steeds A)

**DB1 Plek.** A: het dashboard wordt het startscherm (`/`) na inloggen en kiezen van de administratie; de huidige
gegevens (database, versie, afgesloten t/m, medewerker, rol) komen klein onderaan onder "Over deze administratie".
B: een apart menu-item Dashboard, het startscherm blijft zoals het is.

**DB2 Vier tegels bovenaan.** A: Bank en kas (som betaalrekeningen), Te ontvangen (open verkoop, met aantal
vervallen), Te betalen (open inkoop, met aantal vervallen), Resultaat boekjaar t/m vandaag (met dezelfde periode
vorig jaar). Elke tegel klikt door naar het rapport waar het getal vandaan komt. B: als A plus een vijfde tegel
Omzet.

**DB3 Blokken.** A: (a) **Bank en kas** per betaalrekening: saldo, "afschrift t/m" (laatst ingelezen afschrift),
aantal te verwerken bankregels, knop Afschrift inlezen; (b) **Te ontvangen** staafgrafiek per ouderdom (0, 1–30,
31–60, > 60 dagen, zoals de ouderdomsanalyse), knoppen Verkoopfactuur en Aanmaningen; (c) **Te betalen** idem, knoppen
Inkoopfactuur uploaden en Betalingen; (d) **Resultaat**: omzet, kosten en resultaat dit jaar t/m vandaag vs vorig
jaar, en deze maand vs dezelfde maand vorig jaar, plus staafgrafiek resultaat per maand (dit jaar en vorig jaar
naast elkaar); (e) **Te doen** (DB4). B: ook "top 5 klanten (omzet dit jaar)" en "open orders" als blok.

**DB4 Te doen-lijst.** A: alleen regels die er zijn (aantal > 0) en alleen voor modules die aanstaan, elk met een
link: btw-aangifte van het afgelopen tijdvak (DB5); bankregels te verwerken; vervallen verkoopfacturen (aantal +
bedrag → Aanmaningen); vervallen inkoopfacturen en inkoopfacturen die binnen 7 dagen vervallen (→ Betalingen);
terugkerende boekingen die klaarstaan (→ Terugkerend); artikelen onder bestelniveau (→ Artikelen, status "tekort");
verkooporders met een verstreken leverdatum (→ Orders). B: ook "documenten nog niet verstuurd" (Batch) en ICP-opgaaf.

**DB5 Btw-aangifte herinnering.** SQL-Ledger kent geen aangiftetijdvak. A: nieuwe instelling bij Beheer >
Instellingen "Btw-aangifte per": kwartaal (standaard) / maand / jaar / geen. De herinnering staat er in de maand na
het tijdvak ("Btw 3e kwartaal: aangifte vóór 31-10", NL-termijn = einde van de volgende maand) met een link naar
de btw-rapporten van dat tijdvak, en een knop **Afgehandeld** die het tijdvak afvinkt (eigen tabel
`x_dashboard_done`, met logregel). B: zonder afvinken (staat er de hele maand). C: geen btw-herinnering.

**DB6 Grafieken.** A: eigen eenvoudige SVG-staafgrafieken die de server maakt (geen JavaScript-bibliotheek, werkt
zonder internet, past in licht/donker en op 375px, bedragen ook als tekst/tooltip). B: Chart.js (externe bibliotheek).

**DB7 Snelheid.** A: de pagina verschijnt direct; elk blok laadt los (HTMX), zodat een traag blok de rest niet
ophoudt. Elk getal komt uit dezelfde bibliotheekfunctie als het rapport; waar die te traag is (ouderdom administratie A 5 s) maak
ik een snelle variant in de bibliotheek die pytest én een controle op administraties A en C gelijk moeten bewijzen aan het rapport.
Doel: elk blok < 1 s op administraties A en C. B: als A, maar resultaten 10 minuten bewaren (cache) in plaats van een snelle variant.

**DB8 Rechten.** A: elk blok volgt de module (Verkoop-blok alleen met module Verkoop, Resultaat alleen met
Rapporten, enz.); alleen-lezen ziet alle getallen maar geen actieknoppen; Afgehandeld (DB5) alleen boekhouder+.
B: getallen alleen voor boekhouder en beheerder.

**DB9 Welk jaar.** A: het lopende boekjaar t/m vandaag (vorig jaar = dezelfde periode). Zijn er dit jaar nog geen
boekingen, dan toont het dashboard het laatste jaar mét boekingen, met de melding "Laatste boekjaar met boekingen:
2016" (handig voor oude administraties zoals administratie C en voor testen). B: altijd het lopende jaar (leeg als er niets is).

**DB10 Aanpassen per gebruiker.** A: nu een vaste indeling (blokken alleen weg als de module uit staat); later
eventueel blokken aan/uit per gebruiker. B: nu al per gebruiker blokken aan/uit zetten bij Mijn instellingen.

**DB11 Overzicht over administraties.** A: nu niet. B: op het scherm "administratie kiezen" per administratie de vier
kerncijfers (voor wie meerdere administraties heeft, zoals de gebruiker).

**DB12 Planning en toetsen.** A: als **stap 5b** direct na akkoord op stap 5 (vóór 6 bijlagen, 7 import), eigen branch
`fase4-dashboard`. Toetsen: pytest (elk getal = het rapport waar het naar doorklikt, op testdata; te-doen-regels;
rechten; talen NL/EN/DE/FR), controle op administraties A en C tegen de rapporten, tijdmeting per blok, browsercontrole incl. 375px.
B: na fase 4.

**DB13 Welke rekeningen tellen als "Bank en kas".** (Gevonden tijdens de bouw: administraties A en C hebben ook rekening-couranten,
betalingsverschillen en W&V-rekeningen als betaalrekening.) A (akkoord): alleen betaalrekeningen uit de balans-activa,
zoals afletteren en bankafschrift; rekeningen zonder saldo en zonder recente mutaties verborgen. B: plus uitsluiten per
rekening bij Beheer. C: alleen rekeningen met een IBAN.
