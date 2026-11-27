# Fase 3 — analyse en beslispunten (2026-10-09)

Onderdelen: artikelen/voorraad (ic.pl/IC.pm), verkoop- en inkoopfacturen met artikelen (is.pl/ir.pl/io.pl,
IS.pm/IR.pm) en orders/offertes (oe.pl/OE.pm). Buiten scope (besluit 2026-10-07): samengestelde artikelen/kits,
magazijnen, prijsgroepen, uren/JC, kassa/POS, batches/vouchers.

**Status: 2026-10-09 akkoord gebruiker op alle beslispunten (A, ook F3 A). Bouw op branch `fase3`.**

**Scopewijziging 2026-10-09 (gebruiker):** samengestelde artikelen met stuklijst (BOM) gaan **volledig** mee in
fase 3 (was buiten scope sinds 2026-10-07 en AT1). Eigen analyse + beslispunten "SA" volgen hieronder zodra klaar.

## Gebruik in de echte data

| | administratie A | administratie C |
|---|---|---|
| artikelen | 196, allemaal voorraadartikel (22 vervallen) | 95 voorraadartikel + 640 dienst (546 vervallen) |
| loon/overhead, samengesteld, kits | 0 / 0 / 0 | 0 / 0 / 0 |
| verkoopfacturen met artikelen | **11.764** (2011 – nov 2024), alle EUR | **3.478** (2011 – 2016), GBP + 65 EUR |
| inkoopfacturen met artikelen | 218 (alleen 2011–2013) | 211 (2011–2013) |
| factuurregels (max per factuur) | 13.933 (25) | 7.115 (58) |
| regels met korting | 9.807 | 0 |
| `serialnumber` / `unit` | 12.912 / gevuld — **gebruikt als datum resp. getal** (maatwerk) | 42 ("TBD") / leeg |
| afleveradres bij factuur | 6.534 | 0 |
| prijs met > 2 decimalen | 1.343 regels | 2 |
| kostprijs (COGS) geboekt | slechts 236 facturen; **96 artikelen negatieve voorraad** (tot −322.335) | 30 facturen |
| `invoice.allocated` ≠ 0 | 568 | 338 |
| dubbele artikelnummers | 12 | 98 |
| orders verkoop / inkoop | 0 / 22 (2011–2012, alle dicht) | **2.289 / 2.827** (2011–2016), 112 open |
| order → factuur gekoppeld (`aa_id`) | 13 | 2.263 |
| offertes / aanvragen | 0 | 0 |
| nabestellingen, deels geleverd | 0 | 0 backorders, 2 deels |
| aanbetalingen op order | 0 | 0 |
| magazijn-/`inventory`-regels, `cargo` | 0 | 0 |
| leveranciers-/klantprijzen, merk/model, vertalingen | 0 | 1 / 0 / 0 |
| terugkerende facturen | 0 | 0 |

Conclusies:
- **Administratie A** factureert nog (tot nov. 2024) met artikelen, maar koopt sinds 2013 niet meer via inkoopfacturen in. De
  voorraad loopt daardoor diep negatief en kostprijs wordt vrijwel nooit geboekt. In de praktijk is de voorraad
  daar dus een "prijslijst", geen voorraadadministratie.
- **Administratie C** gebruikte orders intensief (verkoop én inkoop, omzetten naar factuur), artikelen vooral als diensten.
- Offertes, nabestellingen, verzenden/ontvangen, aanbetalingen, magazijnen, prijzen per relatie: niet gebruikt.

## Hoe SQL-Ledger het doet (kern)

- **Soort artikel** staat nergens als kolom: afgeleid uit de rekeningen (voorraad+opbrengst+kostprijs = artikel;
  geen voorraadrekening = dienst; geen opbrengstrekening = loon; `assembly` = samengesteld).
- **Factuur boeken** = eerst alles terugdraaien (`reverse_invoice`), dan opnieuw invoegen — zoals bij AR/AP.
  Per regel: opbrengst/kosten, btw per rekening (`partstax` ∩ `customertax`/`vendortax`), korting per regel,
  koersverschil op de grootste regel, betalingen in de factuur.
- **Kostprijs (FIFO):** verkoop zoekt inkoopregels met `qty+allocated<0` (oudste eerst), hoogt `allocated` op en
  boekt kostprijs/voorraad in de verkoop. Is er geen voorraad, dan boekt de látere inkoopfactuur de kostprijs
  in de oude verkoopboeking (op de inkoopdatum). `parts.onhand` is een teller.
- **Order → factuur:** order wordt meteen gesloten, `ship` = aantal, voorraad gaat bij het opslaan van de order al
  omlaag; deellevering = nieuwe nabestelling; koppeling `oe.aa_id` pas bij boeken van de factuur.

## Gevonden Perl-fouten (gaan naar README "Bewuste afwijkingen" en de memo)

- **Beveiligingsfouten** (SQL-injectie in artikelen, facturen en orders; padtraversal bij het uploaden van een
  artikelafbeelding; XSS in artikelvelden; vervalsbare invoer voor btw-tarieven en afgesloten periode):
  beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. PyBalans neemt
  btw-tarieven en de afgesloten periode nooit uit het formulier (zie F2, F6).
- **Kostprijs:** terugdraaien is LIFO terwijl toewijzen FIFO is → ongewijzigd opnieuw opslaan van een oude
  verkoop verandert de kostprijs; retouren van klanten komen tegen verkoopprijs in de FIFO-rij; retour-
  terugdraaien raakt de verkeerde verkopen; inkoopprijs afgerond op 2 decimalen (3 × 0,333 → 0,01 blijft voor
  altijd in voorraad); btw-inbegrepen + vreemde valuta geeft een foute kostprijs; afrondingsverschil kan op de
  voorraadregel landen; IR schrijft artikel-id i.p.v. regel-id in `acc_trans.id`; geen lock → twee gelijktijdige
  verkopen nemen dezelfde inkoop.
- **Facturen:** afgesloten periode alleen in het scherm gecontroleerd (oude datum en verwijderen helemaal niet);
  btw-tarief verouderd als de datum wijzigt zonder "Bijwerken"; volledig betaald + btw inbegrepen → 0,01 open;
  `avgcost`/`lastcost` buiten de transactie en niet terug bij verwijderen; vouchers raken los bij opnieuw boeken;
  betaling-id's hernummerd; boeken overschrijft `parts.bin`/`weight`.
- **Artikelen:** verwijderen zonder servercontrole op gebruik; loon krijgt bij elk opslaan voorraad 0;
  leveranciersregels zonder prijs en klantprijzen van 0 verdwijnen; voorraadcorrectie overschrijft `onhand`
  zonder logboek; rekeningen van een gebruikt artikel alleen in het scherm geblokkeerd; marge met verkeerd teken
  (io.pl 1008) en deling door nul; import zet ontbrekende kolommen op NULL, wist leveranciers, telt voorraad op.
- **Orders:** "Offerte" klikken op een order sluit de order; factuur maken sluit de order en verlaagt de voorraad
  ook als de factuur nooit geboekt wordt; aanbetaling verdwijnt uit het grootboek bij omzetten / wordt dubbel
  geboekt bij "opslaan als nieuw" en samenvoegen; opnieuw opslaan wist de verzendhistorie; verzenden boekt het
  niet-afgekapte aantal; verwijderen van een gefactureerde order alleen in het scherm geblokkeerd; samenvoegen
  hangt alle referenties aan elke nieuwe order en neemt kopvelden van de laatste order.

## Beslispunten

Advies is steeds **A**.

### Algemeen

**X1 Volgorde.** A: (0) restpunten fase 2 → (1) artikelen → (2) verkoop-/inkoopfactuur met kostprijs →
(3) orders/offertes + omzetten naar factuur → (4) herhalen van facturen/orders + artikellijsten. Elke stap:
bibliotheek + scherm + pytest + Perl-vergelijking + echte data. B: orders vóór facturen.

**X2 Werkwijze.** A: zoals fase 1/2 — afwijkingen van Perl alleen bij aantoonbare fouten, gedocumenteerd.
Rekenen met Decimal ROUND_HALF_UP (niet de 0,501-eigenaardigheid), SQL met `::numeric`, `INSERT … RETURNING`,
SQL-Ledger-schema houden; eigen gegevens in `x_*`-tabellen.

**X3 Rechten/modules.** A: artikelen = module `ic` (bekijken alle rollen, wijzigen boekhouder+), facturen =
modules `ar`/`ap`, orders/offertes = nieuwe module `oe`; alleen-lezen → leespagina.

### Artikelen (IC)

**AT1 Soorten.** A: artikel (voorraad) en dienst; soort blijft afgeleid uit de rekeningen (schema). Loon/overhead,
samengesteld en kit: niet aanmaken; bestaande (geen in administratie A/C) alleen-lezen tonen.

**AT2 Velden.** A: nummer, omschrijving, eenheid, groep, verkoop-/lijst-/laatste inkoopprijs, prijsdatum,
rekeningen (voorraad/opbrengst/kostprijs), btw-vinkjes, notities, barcode, gewicht, bestelniveau (rop), bak (bin),
vervallen, voorraad (alleen tonen). Niet in het scherm maar **bij opslaan ongemoeid laten**: afbeelding/tekening/
microfiche, merk/model, leveranciers- en klantprijzen, douane/herkomst, lot/houdbaarheid, vertalingen
(Perl wist die). B: ook leveranciers-/klantprijzen bouwen (niet gebruikt in de data).

**AT3 Artikelnummer.** A: leeg → volgend nummer (`partnumber`-teller); bestaand nummer → waarschuwing, opslaan
mag (administratie A 12 en C 98 dubbelen bestaan al). Omschrijving verplicht. B: dubbel weigeren.

**AT4 Voorraad (onhand).** A: nooit in het artikelscherm te wijzigen; alleen via facturen en een apart scherm
**Voorraadcorrectie** (nieuw aantal of +/−, met reden; vastgelegd in `x_`-tabel + logboek; géén
grootboekboeking, zoals Perl). B: correctie mét memoriaalboeking voorraad ↔ correctierekening.

**AT5 Gebruikt artikel.** A: rekeningen en soort van een artikel met boekingen kunnen niet meer wijzigen (server
weigert); wie dat wil maakt een nieuw artikel en zet het oude op vervallen. Changeup niet bouwen.

**AT6 Verwijderen.** A: alleen als het artikel nergens gebruikt is (facturen, orders) — servercontrole; anders
"vervallen" voorstellen.

**AT7 Lijsten.** A: artikellijst zoals ic.pl search met filters (soort, groep, actief/vervallen/ongebruikt,
voorraad, onder bestelniveau, gekocht/verkocht/besteld in periode), kolommen naar keuze, CSV/xlsx, doorklikken;
prijshistorie per artikel. Niet: vraag/aanbod, behoefte (requirements). Artikelgroepen beheren (partsgroup).

**AT8 Import artikelen (im.pl).** A: fase 4 (bij de andere imports), dan alleen meegegeven velden bijwerken.

### Facturen met artikelen (IS/IR)

**F1 Scherm.** A: één scherm verkoop- én inkoopfactuur + creditnota (zoals aa-scherm stap 5): kop zoals
verkoop/inkoop (relatie, datums, valuta/koers, afdeling, ordernr/PO-nr, medewerker, betaalkenmerk, notities,
afleveradres), regels met artikel (zoekveld nummer/omschrijving), omschrijving, aantal, eenheid, prijs, korting %,
leverdatum, serienummer (vrij tekstveld; administratie A gebruikt het), bedrag; betalingen + contante korting zoals stap 5/6.
De server rekent alles (fetch `action=calc`), JS alleen totalen, werkt zonder JS.

**F2 Btw.** A: tarieven altijd door de server: `partstax` ∩ btw van de relatie, geldig op de factuurdatum (nooit
uit het formulier); btw inbegrepen ondersteund; zelfde rekenwijze per btw-rekening als Perl.

**F3 Kostprijs (FIFO).** De grootste keuze.
- A: **FIFO zoals SQL-Ledger** (`invoice.allocated`, kostprijsregels in de verkoopboeking), maar **eerlijk
  terugdraaien**: elke toewijzing wordt vastgelegd in `x_cogs_allocation` (verkoopregel, inkoopregel, aantal,
  prijs) en bij wijzigen/verwijderen exact teruggedraaid. Inkoopprijs in volle precisie. Lock op het artikel
  tijdens boeken. Bestaande toewijzingen uit Perl worden bij de eerste wijziging gereconstrueerd uit
  `acc_trans.id` (verkoop) / herberekend per artikel.
- B: na elke wijziging de FIFO van dat artikel opnieuw doorrekenen vanaf de vroegste geraakte datum (raakt
  kostprijsregels van latere boekingen).
- C: geen kostprijs per verkoop (periodieke voorraadwaardering); instelling per administratie. Past bij hoe administratie A
  in de praktijk werkt, maar wijkt af van Perl.

**F4 Negatieve voorraad.** A: toegestaan (administratie A heeft het nodig), zoals Perl: de latere inkoop boekt de kostprijs
in de oude verkoop op de inkoopdatum. Waarschuwing in het scherm bij verkoop boven voorraad, niet weigeren
(instelling `checkinventory` volgen als die aan staat).

**F5 Retouren (creditnota met artikelen).** A: klantretour komt terug tegen de **oorspronkelijke kostprijs**
(via de toewijzing uit F3) i.p.v. tegen verkoopprijs; leveranciersretour haalt voorraad weg tegen FIFO-kostprijs.
Afwijking van Perl, gedocumenteerd. B: Perl volgen (met de bekende fouten).

**F6 Wijzigen/verwijderen.** A: zoals verkoop/inkoop stap 5: vervangen na bevestiging, lock, afgesloten periode op
**oude én nieuwe datum** en bij verwijderen (server), leespagina bij afgesloten/alleen-lezen. Betalingen die
elders geboekt zijn blijven intact (id, afletterdatum, betaalmethode). Verwijderen zet een gekoppelde order weer
open.

**F7 Koers.** A: zoals stap 5 (koers per datum voorstellen; de factuurkoers schrijft — net als nu in arap.py —
naar de koerstabel). Btw inbegrepen + valuta: kostprijs in basisvaluta correct (Perl-fout opgelost).

**F8 Bijzaken.** A: niet bouwen: kasafronding (`roundchange`), kostprijs/leverancier per verkoopregel tonen
(wel bewaren), gewicht/vracht, `parts.bin` overschrijven, bulk "facturen genereren" en "facturen samenvoegen".
Wel: `lastcost` en `avgcost` bijwerken binnen de transactie bij inkoop.

**F9 Bestaande facturen.** A: `/boekingen/<id>` van een factuur met artikelen → nieuw factuurscherm (nu
leespagina). Echte data: alle 11.764 verkoopfacturen van administratie A en 3.478 van C + inkoopfacturen openen en ongewijzigd
opslaan via de webcode → grootboek, voorraad en `allocated` gelijk aan het origineel (afwijkingen alleen waar de
Perl-fouten van F3/F5 spelen, verklaard).

### Orders en offertes (OE)

**O1 Soorten.** A: verkooporder, inkooporder, offerte en offerteaanvraag in één scherm (zelfde regels als de
factuur), plus lijsten (open/gesloten, periode, relatie, nummer), CSV/xlsx.

**O2 Omzetten.** A: offerte → order, order → factuur (aanvraag → inkooporder → inkoopfactuur). Bron wordt pas
gesloten als het doel echt geboekt/opgeslagen is, in één transactie; koppeling vastleggen (`oe.aa_id` zoals Perl,
plus een `x_`-koppeling offerte→order). "Order → offerte" sluit de order niet meer.

**O3 Voorraad en orders.** A: voorraad beweegt alleen door facturen (en voorraadcorrectie), niet door het
opslaan van een order. Afwijking van Perl, gedocumenteerd. Geen apart verzend-/ontvangstscherm.

**O4 Deellevering.** A: factuur kan minder dan het orderaantal bevatten → restant blijft als nabestelling, zoals
Perl (nieuwe order met `backorder`), maar pas bij het boeken van de factuur. B: alleen hele orders factureren.

**O5 Niet bouwen.** A: aanbetalingen op orders (niet gebruikt, Perl-fouten), inkooporders genereren uit
verkooporders, orders samenvoegen, verzenden/ontvangen met vracht, pak-/picklijsten. Later op verzoek.

**O6 Opslaan.** A: wijzigen gesloten/gefactureerde order geweigerd (server), btw uit de database, regels
vervangen zoals Perl (orderitems-id's mogen wijzigen). Echte data: alle 5.116 orders van administratie C openen + ongewijzigd
opslaan → identiek.

### Rest

**R1 Herhalen.** A: facturen met artikelen en orders kunnen een herhaalschema krijgen en worden verwerkt (de
fase-2-melding "fase 3" vervalt).

**R2 Documenten.** A: printen/mailen van facturen, orders, offertes en pakbonnen: fase 4.

**R3 Toetsing.** A: pytest; Perl-driver voor is.pl/ir.pl/oe.pl/ic.pl als browser vs webcode (test-sjabloon met
eigen artikelen, inkoop vóór en ná verkoop, retouren, valuta, btw inbegrepen, korting); echte data administratie A + C
zoals F9/O6; browsercontrole incl. 375px.

**R4 Fase-2-restpunten** (als stap 0): README "Open punten" bijwerken, bibliotheekmeldingen vertaalbaar maken,
taalfout "Ongeldig koers".

## Samengestelde artikelen / stuklijst (BOM) — toegevoegd 2026-10-09 (scopewijziging)

**Status: 2026-10-09 akkoord gebruiker, SA1–SA12 alles A.** Gebruik in de echte data: administratie A 0, C 0 (geen `assembly`-rijen, geen kits).

### Hoe SQL-Ledger het doet
- Tabel `assembly` (aid = product, parts_id = onderdeel, qty, `bom`, `adj`); een samengesteld artikel heeft
  `parts.assembly` en alleen een opbrengstrekening (geen eigen voorraad- of kostprijsrekening). Nesten mag.
- **Assembleren** (`Stock Assembly`): onderdelen −n×aantal, product +n; geen grootboekboeking, geen FIFO-toewijzing.
- **Verkopen:** voorraad van het product omlaag, per onderdeel een factuurregel (`assemblyitem`) en kostprijs
  (FIFO) van de **onderdelen** op hun eigen rekeningen. Inkopen van een samengesteld artikel kan niet.
- Prijzen van het product = som van de onderdelen, bij elk "Bijwerken" opnieuw berekend; `adj` = wijzigingen van
  onderdelen doorgeven. Een gebruikte stuklijst ligt vast (wijzigen via "Changeup": oude artikel vervalt).
- **Kit:** set zonder eigen voorraad of rekeningen; bij verkoop per onderdeel opbrengst, btw en kostprijs.

### Perl-fouten (voor README/memo)
Prijswijziging van een onderdeel wordt nooit doorgegeven aan het product (voorwaarde omgedraaid, IC.pm 281);
doorgeven gebeurt met verschillen i.p.v. herberekenen (float-drift); creditnota van een samengesteld artikel
gebruikt `cogs` met negatief aantal i.p.v. `cogs_returns` (allocated kan negatief worden, IS.pm 1672); "alleen
diensten"-controle kijkt naar één willekeurige rij (IS.pm 1124, OE.pm 1940); cycli (A in B in A) niet
gedetecteerd → oneindige recursie; dubbele onderdelen toegestaan; "hoeveel kan ik maken" rekent fout
(IC.pm 860) en geeft zonder magazijn niets (r822); assembleren controleert alleen een verborgen veld
(ic.pl 4494); `include_assembly` koppelt groep op het verkeerde id (IC.pm 1818); SQL-injectie in de
stuklijstfuncties: beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.

### Beslispunten (advies steeds A)

**SA1 Soorten.** A: samengestelde artikelen (met eigen voorraad, assembleren) nu; kits later (nice to have:
vooral verkooplogica met prijsverdeling en btw per onderdeel; niet in de data). B: kits ook in fase 3.

**SA2 Nesten.** A: een samengesteld artikel mag onderdelen hebben die zelf samengesteld zijn; cycli en meer dan
5 niveaus worden geweigerd; elk onderdeel één keer per stuklijst; aantal > 0 (max. 6 decimalen).

**SA3 Prijzen.** A: lijstprijs en kostprijs van het product worden altijd berekend uit de onderdelen (getoond,
niet opgeslagen als bron); verkoopprijs = som van de onderdelen, tenzij "verkoopprijs zelf bepalen" aan staat.
Prijswijzigingen van onderdelen tellen dus vanzelf mee (Perl-fout opgelost). `adj` blijft in de database maar
wordt niet meer gebruikt. B: Perl volgen (prijzen opslaan en doorgeven).

**SA4 Assembleren.** A: scherm **Assembleren** (datum, aantal, reden/kenmerk) → onderdelen −, product +, vastgelegd
als productieregel in `x_assembly_build` (+ regels per onderdeel) en logboek; **geen grootboekboeking** (zoals
Perl: de kostprijs komt bij verkoop uit de onderdelen). B: met grootboekboeking (eigen voorraadrekening voor het
product, kostprijs vastgezet bij assembleren) — juister voor voorraadwaardering, maar groter en anders dan Perl.

**SA5 Demonteren.** A: in hetzelfde scherm een negatief aantal / knop "Demonteren": omgekeerde beweging,
vastgelegd. Alleen tot de voorraad van het product (geen negatieve productvoorraad door demonteren).

**SA6 Onvoldoende onderdelen.** A: waarschuwen, niet weigeren (negatieve voorraad mag, zoals F4); met
"voorraad controleren" op het onderdeel wel weigeren. Lijst "hoeveel kan ik maken" correct per product.

**SA7 Verkopen.** A: zoals Perl: voorraad product omlaag, per onderdeel (ook uit sub-stuklijsten) een regel en
kostprijs via FIFO van het onderdeel (F3); creditnota via de retourlogica van F5 per onderdeel (Perl-fout opgelost).
Inkopen van een samengesteld artikel: niet (zoals Perl).

**SA8 Stuklijst wijzigen.** A: mag zolang de voorraad van het product 0 is (anders eerst demonteren of
verkopen), ook als het product al verkocht is: oude facturen houden hun eigen onderdeelregels. Changeup niet
bouwen. B: zoals Perl vastzetten zodra gebruikt.

**SA9 Lijsten.** A: **BOM-lijst** (stuklijst uitgeklapt over alle niveaus: aantal per stuk, totaal, kostprijs,
voorraad), **waar gebruikt** per onderdeel, **hoeveel kan ik maken**, productiehistorie; CSV/Excel; afdrukken in
fase 4.

**SA10 Vinkje "op documenten".** A: per onderdeel bewaren (`bom`), effect bij documenten in fase 4.

**SA11 Orders/offertes.** A: samengestelde artikelen mogen op orders en offertes; geen voorraadbeweging (O3).

**SA12 Volgorde en toetsing.** A: na de artikelschermen (stap 1b), vóór de facturen; de verkoopkant (SA7) komt
met de facturen. Toetsing: pytest + Perl-driver ic.pl (stuklijst, assembleren) en is.pl (verkoop) op testdata;
geen echte data om op te toetsen.

## Orders: aanvullende beslispunten stap 3 (2026-10-10, akkoord gebruiker, alles A)

**ON1 Gesloten order zonder factuur** (administratie C 2.741). A: leespagina met knop **Heropenen** (logboek); een
gefactureerde order (`aa_id`) en een order in een afgesloten periode blijven altijd alleen-lezen. De echte-
datatoets bewijst het opslaan van álle 5.116 orders via een interne testroute (niet in het scherm).
B: zoals Perl bewerkbaar met Open/Gesloten.

**ON2 Deellevering (O4).** A: in het **factuurscherm**: "Factuur maken" neemt alle orderregels over, aantallen
verlagen/regels weghalen; bij boeken wordt het restant een nabestelling en krijgt `orderitems.ship` het
gefactureerde aantal (zoals Perl). Geen kolom Verzonden in het orderscherm. B: kolom Verzonden + Alles verzenden.

**ON3 Factuur met nabestelling verwijderen.** A: order weer open (F6) én de nabestelling die bij die factuur
ontstond wordt verwijderd als hij nog open en onaangeroerd is; anders blijft hij staan met melding. B: Perl
(nabestelling blijft, restant dubbel open).

**ON4 Offerte/aanvraag direct naar factuur.** A: ja (zoals Perl); offerte gesloten bij boeken van de factuur,
koppeling in `x_oe_link`. B: alleen via een order.

## Herhalen: aanvullende beslispunten stap 4 (2026-10-10, akkoord gebruiker, alles A)

Perl: `am.pl process_transactions` laadt factuur/order zoals het scherm en boekt een kopie (`IS/IR->post_invoice`,
`OE->save`); prijzen van het origineel, btw op de nieuwe datum, kop-leverdatum order schuift mee. Perl-fouten:
orderkopie neemt `ship` en `backorder` over; `IS::post_invoice` controleert de voorraad niet. T3–T7 (fase 2)
blijven gelden.

**RH1 Prijzen.** A: prijs, korting, omschrijving en kostprijs per regel van het origineel (Perl). B: actuele prijzen.

**RH2 Btw.** A: tarieven en btw-instellingen van artikel en relatie op de nieuwe datum, zoals een nieuwe factuur;
btw-rekeningen die alleen op het origineel staan tellen niet mee.

**RH3 Voorraadtekort** (verkoopkopie, artikel met "voorraad controleren"). A: schema overslaan met melding, volgende
datum blijft staan. B: toch boeken (Perl).

**RH4 Datums op regels.** A: leverdatum factuur-/orderregels en kop-leverdatum order schuiven evenveel dagen mee.
B: zoals Perl alleen de kop.

**RH5 Orderkopie.** A: nieuwe open order, geleverd 0, geen nabestelvlag, geen koppeling (ook als het origineel
gefactureerd/gesloten is); herkomst in de audittrail. B: koppeling "herhaling van" in `x_oe_link`.

**RH6 Offertes/aanvragen.** A: geen herhaalschema (zoals Perl; server weigert). Een factuur uit een order wordt
gekopieerd zonder koppeling naar die order.
