# Fase 6 — parallel draaien, overstap, back-up inlezen en schema (analyse 2026-10-11)

**Status: F1–F9 en keuzes 1–7 AKKOORD gebruiker 2026-10-11; F6 = overstap 1-1-2027 (Q4 2026 parallel), eerste
administratie nog te kiezen; F7 = nu lokaal, later VPS (EU).** Oorspronkelijk: analyse gemaakt terwijl de gebruiker weg was (vooraf akkoord op het advies). De **technische** beslispunten
zijn volgens advies gekozen en ter keuring; de punten die echt bij de gebruiker liggen (overstapmoment, hosting) zijn
**open** en wachten op de gebruiker. Plan (2026-10-07): "6 parallel draaien, overstap, schema-migratie"; eis
2026-10-08: overstappers lezen hun SQL-Ledger-back-up in (ook oudere versies), uiterlijk in fase 6; todo 2026-10-11:
de conversie van SL voor oudere databaseversies nakijken.

## Wat SQL-Ledger doet

**Back-up** (admin.pl/am.pl → `AM::backup`): geen `pg_dump`, maar een eigen SQL-bestand (eventueel gzip, per mail of als
download): kopregel `-- SQL-Ledger Backup` met dataset, versie, host, login en datum; `DROP TABLE` van tabellen met
verwijzingen; het schema opnieuw uit `Pg-tables.sql` + de upgradescripts vanaf die versie; per tabel de rijen als
`INSERT`; sequences met `setval`; functies en triggers uit `Pg-functions.sql` (en eigen `custom_*`); audittrail-regel
`backup`. **Terugzetten** heeft SL 3.2.12 niet: dat doet de beheerder met `psql` (als superuser).

**Bijwerken** (`User::dbupdate`, bij inloggen op een oudere dataset): de 70 bestanden `sql/Pg-upgrade-A-B.sql` (1.2.6 →
3.2.4) op volgorde, alleen SQL (geen Perl), statement voor statement via `process_query` **zonder transactie**: een
fout wordt gemeld (`$form->info`) en het volgende statement draait gewoon door; de versie in `defaults` staat in het
laatste script. Gevolg: een half bijgewerkte database is mogelijk. SQL-Ledger 4.x heeft eigen scripts boven 3.2.4.

**Wat er bij ons al is:** de app opent een bestaande SL-database (koppelen, eerste start ES12: < 3.2.4 geweigerd met
melding), de compare-omgeving werkt oude datasets bij met de originele Perl-code (`compare/upgrade_dataset.pl`); de
tegel "Back-up inlezen" in de eerste start is grijs ("komt met stap 7"/fase 6).

## Beslispunten

**F1 Back-up inlezen — welke bestanden.** A: de **SL-back-up** (`.sql` of `.sql.gz` van admin.pl) én een **`pg_dump`
in platte SQL** (met `COPY … FROM stdin`); geen `pg_dump -Fc` (dan eerst zelf omzetten). B: alleen de SL-back-up.
**Gekozen volgens advies: A.**

**F2 Veilig terugzetten.** Een geüpload SQL-bestand is code. A: **niet** met `psql` als superuser, maar statement voor
statement door een eigen lezer die alleen toelaat wat in een SL-back-up/dump hoort (CREATE TABLE/SEQUENCE/INDEX/VIEW,
CREATE FUNCTION/TRIGGER in `plpgsql`/`sql`, ALTER TABLE/SEQUENCE, INSERT, COPY FROM stdin, `SELECT setval`, DROP TABLE,
SET van veilige sessie-instellingen); alles anders (COPY … TO/PROGRAM, CREATE EXTENSION, andere talen, GRANT/ROLE,
`\`-commando's van psql behalve `\.`) = weigeren met regelnummer. Uitvoeren in een **nieuwe, lege database** als de
gewone databasegebruiker van de app (geen superuser), in **één transactie**: mislukt er iets, dan gaat de database
weer weg. B: `psql` aanroepen. **Gekozen volgens advies: A.**

**F3 Oudere versies bijwerken.** A: **Python-versie van `dbupdate`**: dezelfde 70 SQL-bestanden meegeleverd
(ongewijzigd, `sqlledger/slschema/upgrade/`), dezelfde volgorde en versieberekening, maar in **één transactie** en
stoppen bij de eerste fout (met bestand en regel); getoetst door een oude dataset met Perl én met Python bij te werken
en de databases te vergelijken. Daarmee kan "koppelen" ook oudere databases aan (na bevestiging en met een back-up).
B: Perl blijven gebruiken. **Gekozen volgens advies: A.**

**F4 SQL-Ledger 4.x-databases.** A: herkennen (versie > 3.2.4) en **weigeren** met uitleg (de app kent dat schema niet);
B: ondersteunen. **Gekozen volgens advies: A** (overstappers van 4.x later, als er vraag is). Stand nu: een back-up van
SQL-Ledger 4.x inlezen wordt geweigerd; koppelen kan, maar wordt niet ondersteund (melding "gebruik op eigen risico");
er is geen 4.x-test meer.

**F5 Parallel draaien.** A: SL en de app op **dezelfde** database (kan al: zelfde schema 3.2.4) en een
**vergelijkingsrapport** per administratie dat de belangrijke cijfers van Perl en Python naast elkaar zet (proefbalans,
W&V, balans, openstaande posten debiteuren/crediteuren, btw per tijdvak) — alleen lezen, op een gekozen peildatum, met
de verschillen; draaien zo vaak als nodig tijdens de parallelle periode. B: aparte kopie per systeem. **Gekozen
volgens advies: A.**

**F6 Overstap.** Wanneer, welke administraties eerst, hoe lang parallel, wanneer SL alleen-lezen. **Open — aan de
gebruiker.** Advies: per administratie, eerst een kleine (bv. een holding), minstens één btw-tijdvak parallel, daarna SL
op alleen-lezen (Beheer > Afsluiten in SL of de afsluitdatum).

**F7 Hosting (H1).** Waar de app draait (eigen machine, VPS, hostingpartij). **Open — aan de gebruiker.** De app is al
lokaal én hostbaar (Docker, H3).

**F8 Eigen schema** (numeric i.p.v. float, constraints, eigen tabellen i.p.v. `x_`). A: **niet in fase 6**: zolang SL
parallel draait moet het schema 3.2.4 blijven; een eigen schema is een aparte fase ná de overstap van alle
administraties (eigen analyse). B: nu. **Gekozen volgens advies: A.**

**F9 Volgorde.** A: 6a back-up inlezen (F1/F2) + 6b bijwerken in Python (F3, F4) → 6c vergelijkingsrapport (F5) → 6d
overstaphandleiding + checklist (na F6/F7). Elk een eigen stap met Perl-vergelijking en browsercontrole. **Gekozen
volgens advies: A.**

## 6c Vergelijkingsrapport (F5) — beslispunten V1–V10 (analyse 2026-10-11, ter keuring)

**UITGESTELD (gebruiker 2026-10-11):** de gebruiker is al gestopt met SQL-Ledger, dus zelf parallel draaien heeft geen
zin. Bewaard voor later: als iemand anders (een andere SL-gebruiker) schaduw wil draaien, deze analyse dan voorleggen.
De parallelle periode uit F6 (Q4 2026) vervalt daarmee voor de eigen administraties.

Doel: tijdens de parallelle periode (Q4 2026, F6) per administratie zien dat SQL-Ledger (Perl) en de app (Python) op
**dezelfde database** dezelfde cijfers geven. Gevonden: de vergelijking bestaat al als ontwikkelgereedschap
(`compare/perl_driver.pl` roept SL's eigen `RP.pm`/`rp.pl` aan; `run_compare.py` legt de rijen naast de Python-rapporten
uit `sqlledger/reports.py` en `reports_arap.py`), maar alleen voor testdatabases en voor wie de repo heeft. De app zelf
kent geen Perl en geen SL-map.

**V1 Vorm.** A: scherm **Beheer > Vergelijken met SQL-Ledger** plus dezelfde functie op de opdrachtregel
(`sqlledger-web compare-sl --company … --date …`) voor wie het wekelijks wil inplannen. B: alleen opdrachtregel. C: alleen
scherm. **Advies: A.**

**V2 Hoe Perl draait.** A: een meegeleverde, kleine driver (alleen de rapportfuncties uit `perl_driver.pl`) als
subproces met SL's eigen code uit een ingestelde SL-map (`sl_dir` en `perl` in het configbestand, niet via een scherm:
het is een programmapad); de database alleen-lezen (`default_transaction_read_only`), tijdslimiet 5 minuten; zonder
`sl_dir` staat het scherm niet in het menu. B: de draaiende SL-webserver via HTTP aanspreken (inloggen, HTML lezen):
breekbaar en een SL-wachtwoord nodig. **Advies: A.**

**V3 Rapporten.** A: proefbalans (per rekening beginsaldo, debet, credit, eindsaldo; boekjaar t/m peildatum), W&V
(boekjaar t/m peildatum), balans op de peildatum, openstaande posten debiteuren en crediteuren per relatie
(ouderdomsoverzicht op de peildatum), btw per tijdvak (SL-btwrapport per btw-rekening, verkoop en inkoop, per maand of
kwartaal van het lopende boekjaar). B: ook de NL-btw-aangifte per rubriek: kan niet, SL kent die niet (wel afgeleid uit
dezelfde btw-rekeningen). **Advies: A.**

**V4 Waarop vergelijken.** A: op **cijfers** per rekening/relatie/btw-rekening (de ruwe uitkomst van `RP.pm` tegen de
Python-bibliotheek), exact op de cent; kopjes, subtotalen en opmaak niet (die verschillen bewust, bv. Perl's
"verouderde" subtotalen). B: het scherm regel voor regel (zoals `run_compare`): veel ruis voor de gebruiker. **Advies: A.**

**V5 Peildatum en perioden.** A: één peildatum (standaard vandaag); boekjaar uit de instellingen van de administratie;
btw-tijdvak (maand/kwartaal) uit Beheer > Instellingen, alle tijdvakken van het boekjaar t/m de peildatum. **Advies: A.**

**V6 Uitkomst.** A: per rapport groen "gelijk (n regels)" of rood "x verschillen" met de lijst (wat, SQL-Ledger, app,
verschil); download als xlsx met alle regels; logboek `sl_compare` (peildatum, uitkomst per rapport). Niets wordt in de
administratie opgeslagen (geen migratie). **Advies: A.**

**V7 Bekende verschillen.** A: verschillen die de README als bewuste afwijking noemt en die cijfers raken, worden
**getoond** als "verklaard (README nn)" en tellen niet als rood; de lijst staat in de code met een toets per regel.
B: ze verbergen. **Advies: A.**

**V8 Afbakening.** A: bedragen in de standaardvaluta, hele administratie (geen afdeling/project/GIFI-variant);
openstaande posten in vreemde valuta omgerekend zoals SL ze toont. **Advies: A.**

**V9 Rechten.** A: de **beheerder** van de administratie (alleen lezen, maar het start een programma op de server);
alleen als `sl_dir` is ingesteld. **Advies: A.**

**V10 Toetsen.** A: pytest met echte Perl (overgeslagen als Perl/SL ontbreekt) op de seed-administratie, plus een
opzettelijk aangebrachte afwijking die gevonden moet worden; draaien op een kopie van administraties A en C = 0 onverklaarde
verschillen; browsercontrole (ook 375 px); NL/EN/DE/FR. Bouwen op een eigen branch `fase6c`. **Advies: A.**

## 6d Ingebruikname — de app als enige systeem vanaf 1-1-2027 (analyse 2026-10-11)

**Keuzes gebruiker 2026-10-11: I1 = na 16-4-2026 is er niets meer geboekt (de back-up van 16-4 is definitief; de rest
van 2026 wordt in de app geboekt, = B); I2–I10 = A.** Bouwen op `fase6d`: 6d-1 back-up maken GEBOUWD en 6d-2
overstaplijst + controleblad GEBOUWD (keuzes binnen A elk 1–10 in README "Open punten" ter keuring); 6d-3 handleiding
(README "Overstappen uit SQL-Ledger") en proefdraai GEDAAN (verslag onderaan).

Doel: de gebruiker stapt zonder parallelle periode over (SL is al gestopt, 6c uitgesteld). Wat in F9 "overstap-
handleiding + checklist" heette, wordt een eigen **ingebruiknamechecklist**: alles wat moet kloppen voordat de app
het enige boekhoudsysteem is. Zonder parallelle periode vangt niets een fout op, dus de controle moet vóór
1-1-2027 af zijn.

**Wat er al is:**
- **Startlijst "Aan de slag"** op het dashboard (eerste start ES8/ES10, `dashboard.START_ITEMS`). Die staat aan bij een
  nieuwe administratie en bij koppelen of inlezen via de wizard, maar **niet** bij Systeem > Back-up inlezen. De
  punten zijn bedrijfsgegevens, huisstijl, btw-tijdvak, IBAN, beginbalans en eerste factuur, en worden automatisch
  afgevinkt of op "niet nodig" gezet. Voor een ingelezen administratie met historie passen ze maar half: "beginbalans"
  en "eerste factuur" staan meteen op klaar, omdat er al boekingen zijn.
- Back-up inlezen + bijwerken 2.8.37 → 3.2.4 (6a/6b), afsluitdatum (Beheer > Afsluiten), jaarafsluiting (Beheer >
  Jaarafsluiting), btw-aangifte + ICP, bankafschriften (CAMT/MT940), huisstijl, RGS-koppeling, gebruikers met 2FA.

**Wat ontbreekt:**
- **Een back-up maken vanuit de app.** De README raadt `pg_dump` aan ("of straks de eigen back-up"). De back-up van
  SL zelf neemt de `x_`-tabellen niet mee. Als enige systeem is dit het grootste gat.
- **Btw-rubrieken.** Een ingelezen SL-administratie heeft nog geen koppeling btw-rekening → rubriek (`x_vat_box`).
  Zonder die koppeling is de aangifte leeg of fout.
- **Een cijfercontrole na het inlezen**, nu er geen SL meer is om naast te leggen.

**Feiten over de eigen administraties** (back-upmap van de gebruiker): administratie A en administratie B, SL
**2.8.37**, de laatste back-up is van **16-4-2026**. Administratie C staat bij de back-ups van oude administraties
(2026), net als vier andere oude administraties (2024).

### Beslispunten

**I1 Gat tussen de laatste back-up en 1-1-2027.** De laatste SL-back-up is van 16-4-2026. **Aan de gebruiker:**
is er na 16-4 nog in SL geboekt, of ergens anders? Mogelijkheden:
- A: SL draait nog ergens. Dan maak je op de laatste dag een nieuwe back-up en die lees je in.
- B: na 16-4 is er niets geboekt. Dan lees je de back-up van 16-4 in en boek je de rest van 2026 in de app.
  Bankafschriften (CAMT) inlezen vult het meeste aan, inkoop via uploaden en verkoop via facturen.
- C: alleen een beginbalans per 1-1-2027 in een nieuwe administratie. Dan raak je de historie, openstaande posten en
  artikelen kwijt.

**Advies: A als het kan, anders B. Niet C.**

**I2 Welke administraties en in welke volgorde.** A: eerst **administratie B** als generale repetitie (klein, 0,8 MB),
daarna **administratie A**. Administratie C en de oude bv's lees je alleen in als archief, met de afsluitdatum op de
laatste boeking, en alleen als je ze in de app wilt kunnen inzien. B: alles tegelijk. **Advies: A.**

**I3 Proefdraai vóór de echte overstap.** A: nu de back-up van 16-4 lokaal inlezen en de hele checklist doorlopen.
Wat dan opvalt repareren we vóór december. De definitieve inleesronde volgt met de laatste back-up (I1); de
proefadministratie gaat daarna weg. B: alleen eind december. **Advies: A.**

**I4 Vorm van de checklist.** A: de bestaande **startlijst** uitbreiden met een **tweede soort, "Overstap uit
SQL-Ledger"**, voor administraties die uit een back-up zijn ingelezen. Die gaat bij **elk** inlezen aan, ook via
Systeem > Back-up inlezen, en heeft eigen punten (I5). Wat de app kan controleren, vinkt hij zelf af; de rest
markeer je met "gedaan" of "niet nodig". Zo nodig staat er ook een vaste plek onder Beheer > Ingebruikname, zodat
de lijst terug te vinden is nadat hij van het dashboard verdwijnt. B: alleen een handleiding (README/PDF). C: een
apart scherm zonder dashboard. **Advies: A.**

**I5 De punten** (volgorde zoals op het scherm; (auto) = de app vinkt zelf af):
1. Back-up ingelezen en bijgewerkt naar 3.2.4 (auto, met de versie en datum van de back-up).
2. **Cijfercontrole** na inlezen (I6). Gedaan na het bekijken van het controleblad.
3. Bedrijfsgegevens: naam, adres, KvK, btw-nummer (auto, zoals nu).
4. **Btw-rubrieken gekoppeld** (auto: elke btw-rekening heeft een rubriek, of staat op "geen aangifte"). Plus
   het btw-tijdvak (auto, zoals nu) en de afdrachtrekening (auto).
5. Bank: IBAN bij elke betaalrekening (auto, zoals nu) en **eerste bankafschrift ingelezen** (auto: er is een
   CAMT/MT940 ingelezen). Het beginsaldo van dat afschrift moet gelijk zijn aan het saldo in de boekhouding; zo niet,
   dan zie je een melding met het verschil.
6. Huisstijl, met de layout van de factuur, pakbon en creditfactuur (auto, zoals nu).
7. Nummering: het volgende factuur-, order- en relatienummer tonen ter controle. Gedaan met een vinkje, want de
   nummers lopen door vanuit SL.
8. Gebruikers: iedereen heeft een eigen login met 2FA (auto: geen gedeelde of SL-logins zonder 2FA).
9. **Back-up van de app ingesteld** (auto: er is een back-up gemaakt in de laatste 7 dagen, I7).
10. Afsluitdatum gezet (auto: `closedto` ingevuld), zie I8.
11. RGS-koppeling (alleen NL, "niet nodig" mag).

B: kortere lijst (alleen 1, 2, 4, 5, 9). **Advies: A.** Punt 2 en 9 zijn het belangrijkst.

**I6 Cijfercontrole zonder SL.** A: een **controleblad** (scherm + PDF) op de datum van de back-up. Daarop staan:
- de proefbalans, de balans en de W&V van het lopende boekjaar;
- de openstaande posten van debiteuren en crediteuren, met totalen;
- de saldi van de bankrekeningen;
- het aantal boekingen en facturen per jaar.

Daarnaast draait de app automatische **integriteitscontroles**, met rood/groen per controle:
- elke boeking is in evenwicht (debet = credit per `trans_id`);
- het bedrag van elke factuur sluit aan op het grootboek en op de debiteuren-/crediteurenrekening;
- betalingen zijn niet groter dan de factuur;
- er zijn geen boekingen op rekeningen die niet bestaan;
- de controles van het bijwerken waren in orde.

Jij legt het blad naast de laatste SL-cijfers die je hebt (jaarrekening 2025, btw-aangifte Q1 2026,
bankafschrift). Aanvullend draai ik **eenmalig** de bestaande ontwikkelvergelijking (`run_compare`, Perl tegen
Python) op de ingelezen echte back-up, buiten de app. B: alleen het controleblad. C: 6c alsnog bouwen.
**Advies: A.**

**I7 Back-up maken vanuit de app** (nieuw, nodig als enige systeem):
- A: **Systeem > Back-up maken** (per administratie, systeembeheerder). Dat is een `pg_dump` in platte SQL, gzip,
  inclusief alle `x_`-tabellen, als download. Dezelfde functie komt op de opdrachtregel
  (`sqlledger-web backup --company … --dir …`), zodat je hem kunt inplannen met launchd/cron (lokaal: in een map die naar een andere plek wordt gesynchroniseerd;
  later op de VPS: een map die buiten de server wordt gekopieerd). Het bestand is te lezen met de eigen
  Back-up inlezen; dat toetsen we met een rondje maken → inlezen → zelfde rijen. Het logboek krijgt `backup_made`,
  en het dashboard toont een herinnering als de laatste back-up ouder is dan 7 dagen.
  Bijlagen in de datamap komen mee als tweede bestand (zip), want die staan niet in de database (BB1–BB4).
- B: alleen een handleiding met het `pg_dump`-commando.

**Advies: A.** Om dit punt zou ik niet heen willen.

**I8 Oude perioden dichtzetten.** A: de afsluitdatum op het einde van het laatste **aangegeven** btw-tijdvak (bij
de overstap waarschijnlijk 30-9 of 31-12-2026). Jaarafsluiting 2026 doe je in de app na de jaarrekening; dat hoeft
niet vóór 1-1. Het punt staat op klaar zodra de afsluitdatum ná de datum van de back-up ligt. B: alles tot 1-1-2027
dichtzetten op 1 januari. Dan kun je 2026 niet meer corrigeren voor de jaarrekening. **Advies: A.**

**I9 Handleiding.** A: een korte pagina "Overstappen uit SQL-Ledger" (README + in de app achter de link op de
checklist), met per punt één alinea. Die is meteen een eerste stuk van de wens "helptekst per scherm" (punt 2 van de
open lijst). Er komt geen aparte website. **Advies: A.**

**I10 Bouwen en toetsen.** A: drie stappen op een eigen branch `fase6d`, elk ter keuring:
- **6d-1** back-up maken (I7);
- **6d-2** checklist + controleblad (I4–I6, I8);
- **6d-3** handleiding (I9) en de proefdraai met administratie B en administratie A van 16-4 (I3), met een verslag
  van wat opviel.

Toetsen: pytest (rondje back-up, elke integriteitscontrole met een opzettelijke fout, de checklistpunten), Perl-
vergelijking eenmalig op de echte back-ups, browsercontrole (ook 375 px), NL/EN/DE/FR. **Advies: A.**

### 6d-3 Proefdraai met de echte back-ups van 16-4-2026 (verslag, 2026-10-12)

Ingelezen op een lokale testserver (`f6d_adm_b`, `f6d_adm_a`; alleen lokaal, weg na keuring), zoals de gebruiker het
straks doet, met de lijst en het controleblad van 6d-2.

| | administratie B | administratie A |
|---|---|---|
| Back-up | SL 2.8.37, 0,8 MB, 3.849 opdrachten | SL 2.8.37, 97 MB, 422.411 opdrachten |
| Inlezen + bijwerken | 2.8.10 → 3.2.4, 0 overgeslagen (tijd niet gemeten, klein) | 54 s, 2.8.10 → 3.2.4 (19 scripts), 0 overgeslagen |
| Controles | alle in orde | alle in orde |
| Laatste boeking | 14-04-2026 | 11-04-2026 |
| Controleblad | direct (niet gemeten) | eerst 14,5 s (ouderdomsanalyse) → 0,7 s met de dashboardquery |
| Openstaand verkoop / inkoop | 0 / 1 (leverancier Y) | 0 / 20 facturen (deels vervallen per 11-4) |

**Wat er na het inlezen nog te doen is** (de lijst toont het, hier vooraf):
1. **Btw-rubrieken**: beide 6 btw-rekeningen, nog geen rubriek (SL kende geen aangifte); tijdvak kiezen.
2. **IBAN**: bij geen enkele betaalrekening ingevuld in de banktabel (het IBAN staat alleen in de omschrijving, bv.
   "ABN" gevolgd door het IBAN); invullen bij Beheer > Rekeningschema, dan bankafschriften inlezen **vanaf 12-4 (administratie A)
   resp. 15-4 (administratie B)** t/m 31-12-2026: zo komt de rest van 2026 in de app (I1).
3. **Afsluitdatum administratie A** staat op `20173112`: geen geldige datum (SL vergelijkt als getal, dus alles na 2017
   is open). Zetten op het einde van het laatst aangegeven btw-tijdvak (I8); administratie B heeft er geen.
4. **Nummering** loopt door met de SL-sjablonen (bv. `20<%yy%>-nnnnn`, `in0nnnnn`, `memnnnn`): de app vult `<%yy%>` in
   zoals SL. Nakijken en afvinken.
5. **Huisstijl**: de eigen SL-layouts (factuur, pakbon, creditfactuur) gaan niet mee; kiezen bij Beheer >
   Huisstijl.
6. **Opvallend** (vraag voor de gebruiker, geen fout): administratie A heeft in 2025 maar 1 verkoopboeking in SL (2024:
   729), en in 2026 geen; de omzet is dus elders geboekt (memoriaal of een ander systeem). Even nagaan of dat klopt
   vóór de jaarrekening 2026.
   **Beantwoord (gebruiker 2026-10-12): in 2025 en 2026 zijn er geen verkopen geweest; de cijfers kloppen.**
