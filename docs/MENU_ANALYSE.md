# Menu — kleine aanpassingen (vraag gebruiker 2026-10-11)

**Status: akkoord gebruiker 2026-10-11, MN1–MN7 alles A** (één voor één gekozen). Bij MN4 na de keuze rechtgezet: in
SQL-Ledger is Batch een eigen hoofdmenu (`menu.ini` [Batch]), niet onder AR/AP; A blijft staan tenzij de gebruiker
anders zegt. Bouw als losse stap op branch `fase4-menu`, vóór de btw-aangifte.

**Wens.** De SQL-Ledger-indeling zoveel mogelijk houden, maar beter ordenen wat er nu staat; in elk geval de
btw-onderdelen uit Verkoop en Inkoop halen en in een eigen rubriek zetten. Ter inspiratie een menu met hoofdrubrieken
en per rubriek kopjes (Exact-achtig: Financieel / Relaties / Verkoop / Inkoop, met "Aanmaken / Overzicht"). Niet te
veel afwijken.

## Wat er nu staat (`sqlledger_web/menu.py`)

Zijbalk met rubrieken, gelijk aan de SQL-Ledger-modules: Dashboard · **Verkoop** (boeking, verkoopfactuur,
creditnota, overzicht, openstaand, ouderdomsanalyse, aanmaningen, rekeningoverzichten, *Btw verkoop*, *Onbelaste
omzet*, klanten zoeken/toevoegen) · **Inkoop** (zelfde opbouw + inkoopfactuur uploaden, *Btw inkoop*, *Onbelaste
inkoop*, leveranciers) · **Kas en bank** (14 items) · Orders · Offertes · Documenten (alleen Batch) · Artikelen ·
Grootboek · Rapporten · Terugkerend · Afdelingen en projecten · Beheer (16 items, o.a. *Btw* = tarieven) · Systeem.

SQL-Ledger zelf (menu.ini): btw-rapporten staan onder AR > Reports > Tax collected / Non-taxable en AP > Reports >
Tax paid / Non-taxable; btw-tarieven onder System > Taxes. Er is geen btw-aangifte (komt bij ons na stap 6).

## Beslispunten (advies steeds A)

**MN1 Rubriek Btw.** A: nieuwe rubriek **Btw** (na Grootboek, vóór Rapporten) met: Btw-aangifte (zodra gebouwd,
met de knop Btw-boeking maken), Btw verkoop, Btw inkoop, Onbelaste omzet, Onbelaste inkoop; voor de beheerder ook
**Btw-tarieven** en **Btw-rubrieken** (BA3), dus weg uit Verkoop/Inkoop en "Btw" weg uit Beheer. Zichtbaar als Verkoop
of Inkoop aan staat (geen nieuwe module). B: alleen de vier rapporten verhuizen, tarieven blijven onder Beheer.

**MN2 Kopjes binnen een rubriek.** A: in de lange rubrieken kleine tussenkopjes zonder iets te verplaatsen:
Verkoop = *Boeken* (boeking, verkoopfactuur, creditnota) · *Overzichten* (overzicht, openstaand, ouderdom) ·
*Debiteurenbeheer* (aanmaningen, rekeningoverzichten) · *Klanten*; Inkoop gelijk (*Crediteuren* zonder aanmaningen);
Kas en bank = *Ontvangen en betalen* · *Corrigeren* · *Bank* (inlezen, regels, vaste regels, afletteren) ·
*Overzichten*; Beheer = *Administratie* · *Btw en valuta* · *Documenten en e-mail* · *Afsluiten* · *Controle*. B: geen
kopjes.

**MN3 Klanten en leveranciers.** A: blijven onder Verkoop en Inkoop (zoals SQL-Ledger AR/AP); geen aparte rubriek
Relaties. B: rubriek **Relaties** (klanten, leveranciers) zoals in het voorbeeld.

**MN4 Rubriek Documenten (alleen Batch).** A: Batch verhuist naar Verkoop (kopje *Overzichten*, regel "Documenten in
batch") en Inkoop krijgt dezelfde link; de rubriek Documenten vervalt. B: zo laten.

**MN5 Volgorde rubrieken.** A: Dashboard · Verkoop · Inkoop · Kas en bank · Orders · Offertes · Artikelen ·
Grootboek · **Btw** · Rapporten · Terugkerend · Afdelingen en projecten · Beheer · Systeem (alleen Btw erbij). B:
volgorde van het voorbeeld (Financieel eerst).

**MN6 Uitklapmenu met kolommen (zoals het voorbeeld).** A: **nee**, de zijbalk blijft (werkt op de telefoon,
SQL-Ledger-gevoel); de kopjes van MN2 geven de rust. B: bovenbalk met uitklappanelen in kolommen.

**MN7 Plek in de planning.** A: kleine losse stap direct na stap 6 (vóór de btw-aangifte, zodat die meteen in de
nieuwe rubriek landt); vertalingen EN/DE/FR, toetsen menu/rechten/modules, browsercontrole incl. 375px. B: samen met
de btw-aangifte.
