# Fase Uren — analyse en beslispunten (2026-10-11)

**Status: UR1–UR11 en de keuzes binnen A 1–14 AKKOORD gebruiker 2026-10-11.** U1 = A (gebruiker 2026-10-10: eigen fase na fase 4, vóór fase 6, eerst analyse). Deze analyse is gemaakt
terwijl de gebruiker weg was, met vooraf akkoord op het advies ("op alles ja wat je advies is"); alle beslispunten
hieronder zijn daarom **volgens advies gekozen en ter keuring achteraf**. Achtergrond: `TIMESHEET_ANALYSE.md` (de
2011-uitbreiding; urenrapport verbeterd in SQL-Ledger 4.2.0).

## Wat SQL-Ledger 3.2.12 heeft

- **Tijdkaart** (`jc.pl`, `JC.pm`; menu Projecten/Productie > Tijdkaart): één regel in `jcitems` = medewerker, project,
  dienst (artikel zonder voorraad) of arbeid, datum, in/uit-tijd (`checkedin`/`checkedout`), aantal (`qty`, uren),
  `allocated` (al naar een order), verkoopprijs, omschrijving, notities. Lijst met filters (project, medewerker,
  periode, open/afgesloten), afdrukken (`timecard`-sjabloon), batch. **Voorraadkaart** (`storescard`) = materiaal naar
  een productie-opdracht (job).
- **Verkooporder uit project** (`pe.pl project_sales_order` → `generate_sales_orders`, `PE.pm get_jcitems`,
  `allocate_projectitems`): open uren (qty − allocated) per project, per regel of samengevat per dienst, aanvinken →
  per klant één verkooporder met de uren als regels (prijs van de tijdkaart, project op de regel, `reqdate` = datum
  van de uren); daarna `allocated` = qty. Factureren gaat daarna via de order.
- **Productie-opdrachten** (jobs: project met `parts_id`, `stock_assembly`): materiaal en arbeid naar een eindproduct.

Fouten in de SL-code:
1. `generate_sales_orders` neemt de btw van een regel alleen over als **alle** btw-rekeningen van het artikel als één
   tekst in de lijst passen (`$_ =~ /$taxaccounts_i/`): een artikel met twee btw-rekeningen krijgt er geen.
2. Mislukt `allocate_projectitems`, dan wordt de order gewist, maar mislukt de tweede order, dan staat de eerste er al
   (geen alles-of-niets over de klanten heen).
3. `JC::save` maakt een nieuwe regel met een tijdstempel-tekst als tijdelijke omschrijving en zoekt hem daarop terug
   (twee gebruikers tegelijk = kans op de verkeerde regel); daarnaast een beveiligingsfout. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
4. Wijzigen van een tijdkaart waarvan de uren al op een order staan (`allocated` > 0) mag gewoon, ook lager dan wat al
   gefactureerd is.

Echte data: administratie A 0 projecten en 0 tijdkaarten; administratie C 3 projecten, 2 tijdkaarten (2012), geen jobs. Het wordt dus vooral
**nieuwe** functionaliteit (de wens van 2011: uren per medewerker per maand, aftekenen door de klant).

## Beslispunten (advies steeds A; gekozen volgens advies, ter keuring)

**UR1 Omvang.** A: tijdkaarten (zoals SL, alleen uren op een project/dienst), verkooporder uit project, toewijzing
medewerker → project/dienst, maandstaat en aftekenbare urenstaat (PDF). **Niet**: voorraadkaarten en
productie-opdrachten (jobs) — niet gebruikt in administraties A/C; later als er vraag is. B: alles incl. jobs. C: alleen
tijdkaarten. **Gekozen: A.**

**UR2 Uren factureren.** A: zoals SL via een **verkooporder** (per klant één order, project op de regel, `allocated`
bijgewerkt), daarna factuur uit de order zoals nu. B: ook direct een factuur. **Gekozen: A** (B later als knop).

**UR3 Aftekenen.** A: urenstaat per medewerker/project/maand als **PDF** via de documentengine (eigen sjabloon
`timesheet`, huisstijl), met ruimte voor handtekening; mailen kan met de bestaande mailfunctie. B: digitaal aftekenen
door de klant (link). **Gekozen: A.**

**UR4 Rol urenschrijver.** A: nieuwe rol **urenschrijver** (naast alleen-lezen/boekhouder/beheerder): ziet alleen
Uren > Mijn uren (maandstaat) en eigen tijdkaarten; de server dwingt af dat het alleen de eigen medewerker is (niet
het formulier). B: geen eigen rol (boekhouder voert uren in). **Gekozen: A.**

**UR5 Toewijzing.** A: eigen tabel `x_assignment` (medewerker, project, dienst, van, tot, verkoopprijs optioneel,
projectleider), nooit wissen maar afsluiten met een einddatum, geen overlap van dezelfde combinatie; de maandstaat
toont de toegewezen combinaties. B: geen toewijzing (elke medewerker alle projecten). **Gekozen: A.**

**UR6 Maandstaat opslaan.** A: alleen gewijzigde dagen; een dag waarvan de uren al op een order staan (`allocated`)
is vergrendeld (niet te wijzigen, wel te zien); een lege dag = regel weg (als niet op een order). Eén `jcitems`-regel
per dag per project/dienst (zoals de 2011-versie). B: hele maand wissen en opnieuw (2011, fout). **Gekozen: A.**

**UR7 Tijden.** A: uren als aantal (stappen van 0,1 uur / 6 minuten zoals 2011 en in elke notatie); `checkedin` =
datum 00:00 en `checkedout` = `checkedin` + aantal uren, zodat SL de regels gewoon toont; op de tijdkaart blijven
begin/eind-tijd invulbaar zoals SL. B: alleen begin/eind. **Gekozen: A.**

**UR8 Prijs.** A: verkoopprijs van de toewijzing, anders van de dienst (artikel); vast op de tijdkaart bij opslaan
(zoals SL). **Gekozen: A.**

**UR9 Fouten SL.** A: btw op de orderregel = alle btw-rekeningen van de dienst (fout 1); alle orders in één transactie
(fout 2); id via `RETURNING` (fout 3); `allocated` > 0 → aantal niet lager dan `allocated` (fout 4). **Gekozen: A.**

**UR10 Toetsen.** A: pytest; Perl-driver `jc_ui` (jc.pl tijdkaart opslaan/wijzigen) en `pe_so` (pe.pl verkooporders
genereren) vs Python, tabellen vergelijken (verschillen alleen waar UR9); browsercontrole (ook 375 px), NL/EN/DE/FR.
**Gekozen: A.**

**UR11 Volgorde.** A: U-1 tijdkaarten (bibliotheek + scherm + lijst) → U-2 verkooporder uit project → U-3 toewijzing
+ rol urenschrijver + maandstaat → U-4 urenstaat-PDF. Eigen branch `fase-uren`; niets samenvoegen zonder akkoord.
**Gekozen: A.**

## Interne uren (v1.1) — IN1–IN7, gekozen: alles A (2026-10-12)

Wens uit de proefronde (`V101_ANALYSE.md`): ook uren op **interne projecten** registreren (tegen een intern tarief,
niet factureren) en per project zien wat de uren kosten en opbrengen. SQL-Ledger kent alleen projecten met of zonder
klant; `jcitems.sellprice` is de verkoopprijs. Alles hieronder in eigen `x_`-tabellen (SL blijft de database lezen).

**IN1 Soort project.** A: op het projectscherm kies je **Klantproject: uren factureren** of **Intern project: uren
tegen een intern tarief, niet factureren**, met één regel uitleg onder elke keuze. Klantproject: klant verplicht.
Intern project: kostenplaats verplicht. B: zonder klant = intern. **Gekozen: A.**

**IN2 Kostenplaats.** A: een intern project hoort bij een afdeling van het soort **kostenplaats** (SL
`department.role` = 'C'; 'P' = winstplaats/profitcenter), meteen aan te maken vanuit het projectscherm; uren erven de
kostenplaats via het project. B: kostenplaats per urenregel. **Gekozen: A.**

**IN3 Intern tarief.** A: toewijzing (nieuw veld intern tarief) > **intern uurtarief van de medewerker** (eigen tabel,
met geldig-vanaf) > **kostprijs van de dienst** (`parts.lastcost`); vastgelegd op de tijdkaart bij opslaan (eigen
tabel; `jcitems.sellprice` blijft de verkoopprijs). Geldt ook voor klantprojecten (marge). B: één tarief per dienst.
**Gekozen: A.**

**IN4 Verwerking.** A: alleen registratie + rapport, **geen boeking**. B: ook een maandelijkse doorbelastingsboeking.
**Gekozen: A.**

**IN5 Rapport Uren per project.** A: filters soort (klant/intern/alles), project, kostenplaats, klant, medewerker,
periode; kolommen uren, intern tarief/kostwaarde, verkoopwaarde, op een order (`allocated`) en nog niet, marge
(verkoopwaarde − kostwaarde) bij klantprojecten; CSV/Excel/PDF zoals de andere rapporten. **Gekozen: A.**

**IN6 Mijn uren / verkooporder.** A: interne projecten staan op Mijn uren/Maandstaat met label "intern";
**Verkooporders uit uren** neemt ze nooit mee (ook niet via "klant per regel kiezen"). **Gekozen: A.**

**IN7 Wanneer.** A: v1.1, na v1.0.1, eigen branch `v1.1-uren`. **Gekozen: A.**

### Keuzes binnen A (bij het bouwen) — 1 en 3–16 AKKOORD (2026-10-12), 2 AKKOORD met helptekst (gebouwd)

1. **Opslag soort project**: eigen tabel `x_project` (project, soort klant/intern, kostenplaats); SL's `project`
   blijft ongewijzigd. Migratie 18 van de administratie-database (met `x_employee_rate`, `x_assignment.internalrate`
   en `x_jcitem_cost`); geen app-db-migratie.
2. **Bestaande projecten**: zonder rij in `x_project` geldt: met klant = **klantproject**, zonder klant = **niet
   ingedeeld**. "Niet ingedeeld" gedraagt zich zoals tot nu (verkooporders uit uren met klant per regel, in het rapport
   met verkoopwaarde); intern wordt een project alleen als je dat kiest. De projectlijst toont de kolom Soort.
   **Helptekst (gebruiker 2026-10-12):** bij een niet-ingedeeld project uitleg op het projectscherm ("nog niet
   ingedeeld … werkt zoals voorheen; kies Klantproject of Intern project"), een voetnoot onder de projectlijst en in
   het rapport Uren per project een regel dat niet-ingedeelde projecten meetellen zoals klantprojecten.
3. **Kiezen verplicht bij een nieuw project**; een bestaand, niet ingedeeld project kan ongewijzigd opgeslagen worden
   (blijft niet ingedeeld) tot je een soort kiest. De bibliotheek laat de soort ongewijzigd als die niet wordt
   meegegeven (import, Perl-vergelijking).
4. **Controles**: intern project met klant → geweigerd ("een intern project heeft geen klant"); klantproject zonder
   klant → geweigerd. Wisselen van soort mag ook als er al uren staan; uren die al op een order staan blijven daar.
5. **Kostenplaats**: keuzelijst met alleen afdelingen van het soort kostenplaats; ernaast "of nieuwe kostenplaats"
   (tekstveld): die wordt in dezelfde transactie als het project aangemaakt. Zonder JavaScript (beide velden zichtbaar).
6. **Kostenplaats niet op de tijdkaart**: de uren erven hem via het project (het rapport leest het actuele project);
   een afdeling die kostenplaats is van een project kan niet weg en staat als "in gebruik".
7. **Intern uurtarief medewerker**: nieuw scherm Uren > **Interne tarieven** (boekhouder): per medewerker regels met
   geldig-vanaf en tarief; dezelfde datum nog eens = overschrijven; een regel verwijderen mag (raakt vastgelegde
   tijdkaarten niet).
8. **Toewijzing**: nieuw veld **Intern tarief** (leeg = tarief van de medewerker), ook als kolom in de lijst.
9. **Vastleggen**: `x_jcitem_cost` (tijdkaart, tarief, bron toewijzing/medewerker/dienst). Bij een nieuwe tijdkaart
   altijd (ook via de maandstaat); bij wijzigen alleen opnieuw als medewerker, project, dienst of datum verandert.
   Niet met de hand te wijzigen; het tijdkaartformulier toont tarief en bron (alleen lezen).
10. **Tijdkaarten zonder vastgelegd tarief** (van vóór v1.1, of in SL ingevoerd): het rapport rekent ze met dezelfde
    volgorde op de datum van de uren en meldt in een informatieregel hoeveel dat er zijn.
11. Geen tarief te vinden (geen toewijzings- of medewerkerstarief, `lastcost` 0) → tarief 0.
12. **Verkooporders uit uren**: interne projecten staan niet in de lijst en de bibliotheek weigert ze ook als ze toch
    worden aangeboden (server, niet alleen het scherm). In de Perl-vergelijking bestaat `x_project` niet: gelijk.
13. **Label "intern"** (tag) op Mijn uren/Maandstaat bij het project, en in de lijst Tijdkaarten; in de projectkeuze
    van tijdkaart en toewijzing staat "(intern)" achter het project.
14. **Rapport** in het bestaande rapportkader: `/rapporten/uren-per-project`, module Afdelingen en projecten, menu
    Uren > **Uren per project** (alleen lezen en hoger; de urenschrijver niet). Filters soort (alle/klant/intern/niet
    ingedeeld), project, kostenplaats, klant, medewerker, periode; indeling **per project** of **per project en
    medewerker** (subtotaal per project); doorklik op het projectnummer naar Tijdkaarten met dezelfde filters.
15. **Kolommen**: project, omschrijving, soort, klant of kostenplaats, (medewerker), uren, gemiddeld intern tarief,
    kostwaarde, verkoopwaarde, uren op een order, uren nog niet op een order, marge. Verkoopwaarde = uren × prijs van
    de tijdkaart (standaardvaluta, zonder korting van de order); "op een order" = `allocated` (niet of de factuur er al
    is). Interne projecten: verkoopwaarde, order-uren en marge leeg. Niet ingedeelde projecten: zoals klantprojecten.
16. Het rapport en de tarieven zijn zichtbaar voor iedereen die rapporten mag zien (alleen lezen en hoger), zoals de
    andere rapporten; het tarievenscherm wijzigen alleen de boekhouder.
