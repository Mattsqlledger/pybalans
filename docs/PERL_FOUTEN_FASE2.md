# Fouten in de Perl-code, fase 2 (relaties, beheer, terugkerend, afletteren, import betalingen)

- Versie: SQL-Ledger 3.2.12 (bestand `VERSION`; `Form.pm` r122: `version` 3.2.12, `dbversion` 3.2.4)
- Datum: 2026-10-08
- Bron: `sql-ledger/SL/*.pm`, `sql-ledger/bin/mozilla/*.pl`, `sql-ledger/sql/*.sql`, plus het toegangsscript `sql-ledger/am.pl` op het hoogste niveau
- Werkwijze: elke fout is in de echte code nagelopen. Regelnummers kloppen met deze bron. "NIET BEVESTIGD" betekent dat de bewering niet klopt zoals ze geformuleerd was; wat er wél staat, is erbij beschreven.

Een algemene opmerking vooraf: de hulpfunctie voor het quoten van waarden in `Form.pm` bevat een beveiligingsfout die veel datumvelden raakt (zie E1). Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.

---

## Beveiliging — privé melden, niet openbaar

De punten hieronder zijn beveiligingsfouten in SQL-Ledger 3.2.12. Per punt staat alleen het onderdeel en wat PyBalans
doet; de technische details (plek in de code, velden, werkwijze) worden eerst privé aan de onderhouders gemeld.

### 1. SQL-injectie in CT::save (relaties opslaan) — BEVESTIGD (deels)
`SL/CT.pm`, opslaan van klanten/leveranciers. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### 2. SQL-injectie in CT::search en CT::get_history — BEVESTIGD
`SL/CT.pm`, zoeken en historie van relaties. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### 3. SQL-injectie in Form::add_shipto — BEVESTIGD
`SL/Form.pm`, afleveradres opslaan. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### 4. SQL-injectie in AM::save_account / move / save_department / save_sic — BEVESTIGD
`SL/AM.pm`, beheer van rekeningen, volgorde, afdelingen en branches. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### 5. SQL-injectie in AM: exchangerates, audit_log, closebooks, post_yearend, currency — BEVESTIGD
`SL/AM.pm`, koersen, audittrail, afsluiten, jaarafsluiting en valuta. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### 6. SQL-injectie in PE::save_project — BEVESTIGD
`SL/PE.pm`, projecten opslaan (zie ook E8). Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### 7. SQL-injectie in SL/RC.pm — BEVESTIGD
`SL/RC.pm`, afletteren (bankreconciliatie). Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst. De sleutels in het formulier worden gecontroleerd tegen de regels die de server zelf opbouwt.

### 8. SQL-injectie in recurring: Form::save_recurring, AM::update_recurring, AM::recurring_details — BEVESTIGD
`SL/Form.pm` en `SL/AM.pm`, terugkerende boekingen. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst. Eenheid uit een vaste lijst, start verplicht.

### 9. SQL-injectie in SL/IM.pm — BEVESTIGD
`SL/IM.pm`, import van betalingen. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### 10. Verwijderen alleen in het scherm geblokkeerd, niet op de server — BEVESTIGD
Toegangscontrole bij verwijderen van relaties, rekeningen, valuta, afdelingen en GIFI-codes (`ct.pl`/`CT.pm`, `am.pl`/`AM.pm`). Gevolg: gegevens met boekingen kunnen verwijderd worden. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: de controle of iets in gebruik is gebeurt in de bibliotheek op de server, niet alleen in het scherm.

### 11. Vergrendelen/ontgrendelen van de administratie zonder beheerderscontrole, plus XSS — BEVESTIGD
Toegangscontrole en XSS in `bin/mozilla/am.pl` (vergrendelen van de dataset). Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: vergrendelen is een vlag in de app-database, alleen voor de beheerder; alle uitvoer wordt geëscaped (Jinja-autoescape).

---

## Relaties

### 12. Relatie met boekingen te verwijderen — BEVESTIGD
`SL/CT.pm` `sub delete` r573 verwijdert de relatie direct. Er is geen controle op ar/ap/oe, dus facturen en orders blijven achter met een verwijzing naar een relatie die niet meer bestaat.

### 13. add_transaction wist enddate — BEVESTIGD
`bin/mozilla/ct.pl` `sub add_transaction` r3167: `$form->{enddate} = "" if $form->{enddate};`. Wie vanuit een relatie een boeking start, slaat de relatie eerst op zonder einddatum, waardoor een inactieve relatie stil weer actief wordt.

### 14. bank.dcn/rvc verloren bij opslaan — BEVESTIGD (beperkte impact)
`SL/CT.pm` `sub save`: `DELETE FROM bank WHERE id = $form->{id}` (r294-296), daarna `INSERT INTO bank (id, name, iban, bic, membernumber, clearingnumber...)` (r360-381) zonder `dcn`/`rvc`. De tabel heeft die kolommen wel (`sql/Pg-tables.sql` r674-675). Een waarde die op een andere manier in `dcn`/`rvc` van een relatiebank is gezet, gaat dus verloren. De relatieschermen zelf vullen deze kolommen nergens; ze worden alleen voor bankrekeningen in het rekeningschema gebruikt (AM::save_bank r2452-2466).

### 15. SELECT enddate zonder WHERE — BEVESTIGD
`SL/CT.pm` `sub save` r318: de query op `enddate` heeft geen WHERE. Dit leest de einddatum van een willekeurige (eerste) relatie in plaats van deze relatie. Het gebeurt alleen als `type` en `enddate` gezet zijn (r316).

### 16. Alleen de eerste contactpersoon wordt beheerd — BEVESTIGD
`bin/mozilla/ct.pl` `sub create_links` r64: `$form->{contactid} = $form->{all_contact}->[0]->{id};` (en r69). `CT::save` verwijdert en herschrijft alleen `contact WHERE id = $form->{contactid}` (CT.pm r285-287, r525-542). Andere contactpersonen zijn onzichtbaar en niet te beheren.

### 17. Persoon: name niet bijgewerkt — NIET BEVESTIGD VIA HET SCHERM
`SL/CT.pm` `sub save` r252: `$form->{name} ||= "$form->{lastname} $form->{firstname}";`. Na een eerste opslag is `name` gevuld, zodat een latere wijziging van voor- of achternaam de relatienaam niet meer bijwerkt.

**Correctie (nagelopen 2026-10-08):** bij soort persoon toont `ct.pl form_header` (r1915 e.v.) geen invoerveld `name` en `hide_form` (r2355) neemt `name` ook niet mee; bij opslaan is `name` dus leeg en wordt opnieuw opgebouwd. In de browserflow wordt de naam wél bijgewerkt (ook zo gevonden door de Perl-vergelijking `ct_ui`). Alleen een directe aanroep van `CT::save` met een gevulde `name` houdt de oude naam. Niet in de memo opnemen als gebruikersfout.

### 18. new_number hoogt de teller op zonder opslaan — BEVESTIGD
`bin/mozilla/ct.pl` `sub new_number` r3354-3360: `$form->{"$form->{db}number"} = $form->update_defaults(\%myconfig, "$form->{db}number");`. `Form::update_defaults` schrijft het nieuwe nummer weg en commit (Form.pm r4505-4512), ook als de relatie nooit wordt opgeslagen. Er ontstaan gaten in de nummering.

### 19. Inner JOIN op address/contact — BEVESTIGD
- `SL/CT.pm` `create_links` r53: `JOIN address ad ON (ct.id = ad.trans_id)`.
- `search` r725-726 (`JOIN contact ct ... JOIN address ad ...`), en ook r773-774, r813-814, r850-851, r887-888. `get_history` r1124.
- Een relatie zonder adres- of contactrij (bijvoorbeeld na een import) kan niet geopend worden en verschijnt niet in de zoeklijst.

### 20. Geen controle op dubbel nummer — BEVESTIGD
`SL/CT.pm` `sub save` r437 vult alleen een nummer in als het leeg is, en `Form::update_defaults` (r4357-4515) controleert niets. De index is niet uniek: `create index customer_customernumber_key on customer (customernumber);` (`sql/Pg-indices.sql` r31, vendor r66). Een handmatig ingevoerd of teruggezet nummer kan dus dubbel voorkomen.

### 21. Apostrof in e-mail breekt opslaan — BEVESTIGD
Dit hangt samen met beveiligingsfout 1 (CT.pm, opslaan relatie): een e-mailadres met bepaalde leestekens geeft een SQL-fout bij opslaan. In de contact-INSERT (r542) gaat hetzelfde veld wel goed.

### 22. Tikfout in chop taxaccount, prepayment ontbreekt — BEVESTIGD
`SL/CT.pm` `sub search`:
```perl
chop $ref->{taxaccount};                                           # r965  (moet taxaccounts zijn)
for (qw(arap payment discount)) { $ref->{"${_}_accno"} = $accno{$ref->{"${_}_accno_id"}} }   # r973
```
De spatie aan het eind van `taxaccounts` blijft staan, en `prepayment_accno` wordt niet gevuld. Bovendien bevat `%accno` alleen rekeningen met `link LIKE '%AR%'`/`'%AP%'` (r925-932), dus betaal- en kortingsrekeningen zonder die link blijven ook leeg.

### 23. Orphaned-filter negeert pay_trans; de controle in create_links doet niets — BEVESTIGD
- `search` r698-712 toetst alleen `oe` en `ar`/`ap`, niet `pay_trans`.
- `create_links` r95-104:
  ```perl
  if ($form->{status} eq "orphaned") { ... if (! $dbh->selectrow_array($query)) { $form->{status} = "orphaned"; }
  ```
  Dit zet `orphaned` opnieuw op `orphaned`. Bij gevonden `pay_trans` zou de status juist leeg moeten worden. Daardoor verschijnt de knop Delete ook bij leveranciers met betalingen.

### 24. batch_delete schrijft audittrail met lege of verkeerde naam — BEVESTIGD
`SL/CT.pm` `sub delete` r615-616 (`reference => $form->{name}`), aangeroepen vanuit `batch_delete` r1418-1423, dat alleen `$form->{id}` per regel zet. `name` is daar leeg of het zoekcriterium uit het lijstscherm, voor alle verwijderde relaties hetzelfde.

---

## Beheer

### 25. save_account zonder tax-vlag wist de tarifhistorie — BEVESTIGD
`SL/AM.pm` `sub save_account` r155-161:
```perl
} else {
  # remove tax
  if ($form->{id}) { $query = qq|DELETE FROM tax WHERE chart_id = $form->{id}|;
```
Eén keer opslaan zonder AR_tax/AP_tax/IC_tax* verwijdert alle btw-tarieven met hun `validto`-historie.

### 26. Btw-tarieven: geen controle op volgorde, dubbele datums of leeg tarief — BEVESTIGD
- `bin/mozilla/am.pl` `sub update_taxes` r2403-2438 sorteert en kopieert alleen.
- `sub save_taxes` r3222-3234 controleert alleen of de vorige regel van dezelfde rekening een `validto` heeft (r3227-3230).
- `AM::save_taxes` r1774-1787 doet `DELETE FROM tax` en voegt alles opnieuw in. Niet-oplopende of dubbele `validto`-datums en een leeg tarief (`parse_amount('')` = 0, r1782) worden geaccepteerd.

### 27. save_defaults wist alle sleutels; optional-lijst; yearend; race op closedto — BEVESTIGD
- `SL/AM.pm` `sub save_defaults` r1603: `$query = qq|DELETE FROM defaults|;`. Daarna komen alleen version, de vijf standaardrekeningen, precision en de sleutels uit `optional` terug (r1611-1629).
- `bin/mozilla/am.pl` r2711-2726: `optional` is een vaste lijst. Sleutels die daar niet in staan (bijvoorbeeld door andere modules of een latere versie) zijn na een opslag weg.
- `yearend` staat in `optional` (r2711), maar het scherm heeft er geen invoerveld voor en het wordt niet als verborgen veld meegestuurd. Een bestaande waarde gaat dus verloren.
- `closedto`/`revtrans`/`audittrail` gaan als verborgen velden mee (r2717 `@f = qw(closedto revtrans audittrail);`, r2732). Wordt intussen via Audit Control de boekperiode gesloten, dan zet een gelijktijdige opslag van Defaults de oude `closedto` terug.

### 28. AM::move verandert de basisvaluta — BEVESTIGD
De basisvaluta is de eerste in `curr ORDER BY rn` (`Form::get_currencies`, Form.pm r2541; gebruikt als `substr(...,0,3)`). Het valutascherm biedt omhoog/omlaag (am.pl r5858-5859), en `AM::move` (r2974-3046) wisselt `rn` zonder uitzondering voor rn 1.

### 29. delete_currency: in-gebruik-controle alleen op ar/ap/oe; basisvaluta te verwijderen — BEVESTIGD
`SL/AM.pm` `get_currency` r2744-2746 toetst alleen `ar`, `ap` en `oe` (niet `gl`, `customer`/`vendor.curr`, `exchangerate`, enzovoort) en sluit rn 1 niet uit. `delete_currency` r2801-2831 controleert helemaal niets. Bij een onbekende `curr` is `$rn` leeg en geeft r2811-2812 (`WHERE rn > $rn`) een SQL-fout.

### 30. AM::delete_account: omgedraaide logica — BEVESTIGD
`SL/AM.pm` r225-238:
```perl
$query = qq|SELECT count(*) FROM parts
            WHERE $_ = $defaults{$_}|;                             # r226-228
```
Dit telt artikelen op de *standaard*rekening in plaats van op de te verwijderen rekening (`$form->{id}`). Bij een lege default ontstaat ongeldige SQL (`WHERE inventory_accno_id = `). `selectrow_array` geeft dan stil `undef` en de controle wordt overgeslagen. Zie ook fout 10: er is geen controle op `acc_trans`.

### 31. Standaardrekeningen uit verborgen velden; cashovershort ontbreekt — BEVESTIGD
`bin/mozilla/am.pl` r440-444:
```perl
for (qw(inventory_accno_id income_accno_id expense_accno_id fxgainloss_accno_id)) {
  if ($form->{id} == $form->{$_}) { $form->error(...'Cannot delete default account!') }
```
De waarden komen uit verborgen velden (r132), niet uit de database, dus de controle is te omzeilen. `cashovershort_accno_id` ontbreekt in de lijst. Op de server (AM::delete_account) zit geen controle.

### 32. save_gifi/delete_gifi: id *= 1 op een tekstsleutel — BEVESTIGD
`SL/AM.pm` r348 `if ($form->{id} *= 1) {` + r352 `WHERE accno = '$form->{id}'`; `delete_gifi` r381 + r385. Een GIFI-code is tekst. `"0100"` wordt `100` en `"1000A"` wordt `1000`. Dan wordt de verkeerde code gewijzigd of verwijderd, of er volgt een INSERT in plaats van een UPDATE (bij een niet-numerieke code wordt id 0).

### 33. save_language vervangt dubbele spaties door een streepje — BEVESTIGD
`SL/AM.pm` `sub save_language` r1110: `$form->{$_} =~ s/ ( )+/-/g;`. Dit geldt voor `code` en `description`: "Nederlands  (NL)" wordt "Nederlands-(NL)". Elders in AM is de vervanging `/ /` (bijvoorbeeld r93, r345).

### 34. Schema-bestanden gebruiken fxgain/fxloss, de code fxgainloss — BEVESTIGD
- 41 van de 68 `sql/*-chart.sql` zetten `fxgain_accno_id`/`fxloss_accno_id` (bijvoorbeeld `Dutch-Standard-chart.sql` r271-272, `Default-chart.sql` r79-80). Geen enkele zet `fxgainloss_accno_id`.
- Het hernoemen gebeurt alleen in `sql/Pg-upgrade-3.2.0-3.2.1.sql` r2-3 (`update defaults set fldname = 'fxgainloss_accno_id' where fldname = 'fxgain_accno_id'; delete ... 'fxloss_accno_id'`).
- Een nieuwe administratie start echter op versie 3.2.4 (`sql/Pg-tables.sql` r73), dus dat upgradescript draait nooit. Nieuwe administraties hebben geen `fxgainloss_accno_id`. Gevolg: zie fout 55.

### 35. generate_yearend: resultaat 0 geeft "Nothing to do" — BEVESTIGD
`bin/mozilla/am.pl` r4236 `if ($ok && $earnings) {` → r4243 `$form->error('Nothing to do!');`. Is de winst precies 0, terwijl er wel saldi op opbrengst- en kostenrekeningen staan, dan worden die niet afgesloten.

### 36. delete_department zonder foutcontrole — BEVESTIGD
`SL/AM.pm` r666-668: `$query = qq|DELETE FROM department WHERE id = $form->{id}|; $dbh->do($query);`. Er is geen `|| dberror`, en de audittrail wordt daarna toch als "deleted" geschreven. Ook `reorder_rn` (r664) loopt al vóór de DELETE. (Hetzelfde patroon zit in `delete_sic` r1030-1033.)

### 37. clear_semaphores/remove_locks: DELETE FROM semaphore zonder controle — BEVESTIGD (met nuance)
`SL/AM.pm` `remove_locks` r2660: `my $query = qq|DELETE FROM semaphore|;`. Er is wel een `dberror`, maar geen beperking tot de eigen gebruiker en geen admin-controle in `am.pl` `clear_semaphores`/`remove_locks` (r5020-5056). Iedere gebruiker met toegang wist de locks van alle anderen, ook van transacties die op dat moment open staan.

---

## Terugkerende boekingen

### 38. update_recurring: maandeinde schuift op — BEVESTIGD
`SL/AM.pm` r1494: de nieuwe datum is de vorige `nextdate` plus het interval. Elke keer wordt vanaf de vorige `nextdate` gerekend, niet vanaf `startdate + n × interval`. 31-01 wordt zo 28-02, daarna 28-03, enzovoort.

### 39. Geen lock of transactie rond process_transactions — BEVESTIGD
`bin/mozilla/am.pl` `sub process_transactions` r4644-4900 boekt per regel, en daarna draait `AM->update_recurring` (r4893) op een eigen autocommit-verbinding (`dbconnect`, AM.pm r1487). Er is geen semafoor en geen gezamenlijke transactie. Twee gelijktijdige runs, of een afbreking tussen boeken en ophogen, leveren dubbele boekingen op.

### 40. Het printresultaat bepaalt $ok — BEVESTIGD
`bin/mozilla/am.pl` r4781: `$ok = &print_recurring(\%$pt, $defaultprinter);`, gevolgd door r4893 `AM->update_recurring(...) if $ok;`. Faalt het printen na een geslaagde boeking, dan wordt `nextdate` niet opgehoogd en boekt de volgende run dezelfde periode opnieuw. Dit geldt voor de AR/AP-tak; in de order-tak (r4843) wordt het resultaat niet aan `$ok` toegekend.

### 41. recurring_details haalt overdue/paid alleen uit ar — BEVESTIGD
`SL/AM.pm` r1430-1431: `ar.duedate - ar.transdate AS overdue, ar.datepaid - ar.transdate AS paid`. Bij AP-boekingen is dat NULL, dus de vervaldatum wordt gelijk aan de boekdatum (am.pl r4731) en de betaaldatum ook.

### 42. GL-kopie: debit/credit onopgemaakt, dus parse_amount ×100 — BEVESTIGD
`bin/mozilla/am.pl` r4874 `$form->{"debit_$j"} = $ref->{amount} * -1;` en r4876 `$form->{"credit_$j"} = $ref->{amount};`. Dit zijn ruwe getallen (bijvoorbeeld `12.34`). `GL::post_transaction` voert `parse_amount` uit (GL.pm r210-211), en dat haalt bij `1.000,00` alle punten weg (Form.pm r680): 12.34 wordt 1234 (×100), 12.5 wordt 125 (×10). De AR/AP-tak maakt de bedragen wel eerst op (r4704-4708).

### 43. Ingevulde reference geeft elke kopie hetzelfde nummer — BEVESTIGD
`bin/mozilla/am.pl` r4717 `$form->{invnumber} = $pt->{reference};` en r4757 `... update_defaults(...) unless $form->{invnumber};`. Hetzelfde gebeurt bij orders (r4813) en GL (r4854). Elke kopie krijgt het vaste referentienummer.

### 44. save_recurring benadert nextdate in dagen; voorbij de einddatum wordt nextdate = enddate — BEVESTIGD
`SL/Form.pm` r4236-4245: het aantal dagen sinds de startdatum en `int(($x / ($x + $y)) * $s{howmany} + 1) * $s{repeat}`. Dit is een lineaire benadering in dagen, onnauwkeurig bij maanden. r4249 `my $nextdate = $enddate;` blijft staan als `$advance >= repeat*howmany` (r4251), dus een schema dat al voorbij is, krijgt nextdate = enddate in plaats van leeg.

### 45. Geen validatie van howmany/repeat — BEVESTIGD
`bin/mozilla/arap.pl` `save_schedule` (r918-946) valideert niets. `Form::save_recurring` r4215 doet alleen `*= 1`. Bij `howmany = 0` wordt `$advance` negatief (r4218), met een einddatum vóór de startdatum. `repeat = 0` geeft een interval van 0 en daarmee een oneindige herhaling op dezelfde datum.

### 46. Ontbrekende koers: de oude koers wordt stil gebruikt — BEVESTIGD
`bin/mozilla/am.pl` r4724, r4820 en r4863: `$form->{exchangerate} = $form->format_amount(\%myconfig, $exchangerate) if $exchangerate;`. Zonder koers op de nieuwe datum blijft de koers van de oorspronkelijke boeking staan, zonder melding. Hetzelfde geldt voor de betaalkoers (r4743).

### 47. update_recurring: $interval in plaats van $advance — BEVESTIGD
`SL/AM.pm` r1498: `for (qw(PgPP Oracle)) { $interval{$_} = $interval{Pg} }`. `%interval` bestaat hier niet, dus `$advance{PgPP}`/`$advance{Oracle}` blijven leeg en r1501 wordt `SELECT  > enddate`, een SQL-fout bij die drivers.

### 48. Form::get_recurring zonder nextdate — BEVESTIGD
`SL/Form.pm` r4187: `for (qw(reference description startdate repeat unit howmany payment print email message)) { $self->{recurring} .= ... }`. `nextdate` zit niet in de string. De transactieschermen sturen alleen `recurring` mee als verborgen veld (aa.pl r649, is.pl r574, gl.pl r1321). Bij opnieuw boeken of opslaan van de bron-transactie herberekent `save_recurring` de `nextdate` met de benadering uit fout 44, en een handmatig gezette `nextdate` gaat verloren.

---

## Afletteren (bankreconciliatie)

### 49. rc.pl done zonder update schrijft niets — BEVESTIGD
`SL/RC.pm` r309: `if ($form->{"datecleared_$i"} ne $form->{"oldcleared_$i"}) {`. Beide zijn verborgen velden met de databasewaarde (rc.pl r350-351). Alleen `update` (r489) past `datecleared` aan. Vink je regels aan en klik je direct op Done, dan wordt niets afgeletterd, zonder melding.

### 50. Verschilcontrole op een verborgen, opgemaakt bedrag — BEVESTIGD
`bin/mozilla/rc.pl` r541: `$form->error(...'Out of balance!') if ($form->{difference} *= 1);`. `difference` is het opgemaakte bedrag uit een verborgen veld (r387, r435). Bij `1.000,00` wordt `"-0,50" * 1` gelijk aan `-0` en `"1.234,56"` gelijk aan `1.234`. Kleine verschillen worden zo niet gezien, en het veld is ook door de client te wijzigen.

### 51. select_all gebruikt een ongezette $i — BEVESTIGD
`bin/mozilla/rc.pl` r505: `$ref->{cleared} = ($form->{"datecleared_$i"}) ? $form->{"datecleared_$i"} : $form->{recdate};` staat in een `foreach $ref` zonder `$i++`. Bestaande afletterdatums worden genegeerd en alle regels krijgen `recdate`.

### 52. Deelstring-match op id — BEVESTIGD
`SL/RC.pm` r267: `$form->{PR}->[$i]->{id} .= " $ref->{id}" if $form->{PR}->[$i]->{id} !~ /$ref->{id}/;`. Id 12 wordt als aanwezig gezien in "112", waardoor dat id bij samenvoegen ontbreekt en de regel bij reconcile niet wordt bijgewerkt.

### 53. UNION in plaats van UNION ALL — BEVESTIGD
`SL/RC.pm` r192 en r204 (`UNION` tussen de gl/ar/ap-delen). Identieke rijen (zelfde datum, bron, bedrag, cleared, id, naam) worden samengevoegd. Twee gelijke bankregels in één boeking verschijnen dan als één regel en het saldo klopt niet.

### 54. Zonder tot-datum telt het afschriftsaldo ook niet-afgeletterde regels — BEVESTIGD
`SL/RC.pm` r100-116: `$transdate = ""; $cleared = "";` en alleen bij `todate` gevuld. Zonder tot-datum wordt de `endingbalance` dus de som van alle goedgekeurde regels, ook als ze niet afgeletterd zijn.

### 55. Zonder fxgainloss_accno_id een SQL-fout — BEVESTIGD
`SL/RC.pm` r137-138: `AND NOT ac.chart_id = $defaults{fxgainloss_accno_id}`. Bij een lege default ontstaat `AND NOT ac.chart_id = ` en een SQL-fout. Door fout 34 geldt dat voor elke nieuwe administratie.

### 56. Tweede deel: transdate < tot (strikt) — BEVESTIGD in de code, effect gering
`SL/RC.pm` r226: het tweede deel filtert op transdate strikt kleiner dan de tot-datum. Met een van-datum is dit overbodig (r223 is al `< fromdate`). Zonder van-datum is het echte probleem dubbeltelling (zie E9).

### 57. RC::paymentaccounts toont alle balansrekeningen — BEVESTIGD
`SL/RC.pm` r27-28: `WHERE c.charttype = 'A' AND c.closed = '0'`. Er is geen filter op `link` (bijvoorbeeld `%_paid`), dus elke open rekening is af te letteren.

---

## Import betalingen

### 58. IM::payment_links: bedragmatch zonder richting en zonder ORDER BY — BEVESTIGD
`SL/IM.pm` r1593-1612: een UNION van open AR en AP, elk met `(a.amount - a.paid) / a.exchangerate AS amount` (beide positief), zonder `ORDER BY`. De regel matcht op `credit - debit` (r1668) tegen `%amount` (r1619-1621). Een ontvangst kan daardoor een inkoopfactuur van hetzelfde bedrag koppelen, en welke kandidaat eerst komt, is willekeurig.

### 59. Onbekende dcn schuift een bedragkandidaat weg — NIET BEVESTIGD zoals geformuleerd
Wat er staat (r1681-1695): bij een gevulde dcn wordt altijd `$am = 0` (r1684), dus er volgt geen terugval naar matchen op bedrag. Is de dcn onbekend, dan is `$ref->{amount}` leeg, `format_amount` geeft dan een leeg of nulbedrag, en de `shift` (r1693) raakt hooguit een nul-sleutel. De regel blijft dan zonder koppeling. De echte fout zit bij een *gevonden* dcn: r1693 haalt de eerste kandidaat met hetzelfde bedrag uit `%amount`, en dat hoeft niet de dcn-factuur te zijn. Een andere factuur verdwijnt dan uit de kandidaten voor de volgende regels.

### 60. JOIN address sluit relaties uit; AP alleen met JOIN bank — BEVESTIGD
`SL/IM.pm` r1600, r1611, r1632 en r1641: `JOIN address ad ON (ad.trans_id = c.id)`, dus relaties zonder adres vallen weg. r1609: `JOIN bank b ON (b.id = a.bank_id)`: AP-facturen komen alleen mee als `bank_id` naar een rekening met een bankrecord wijst. `bank_id` wordt pas bij betalen gezet (AA.pm r298, IR.pm r1390), dus nog niet betaalde inkoopfacturen ontbreken meestal.

---

## Extra gevonden

### E1. SQL-injectie via de quoting-hulpfunctie in Form (beveiliging)
`SL/Form.pm`, gebruikt voor datumvelden in meerdere modules. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### E2. SQL-injectie in AM::save_defaults (beveiliging)
`SL/AM.pm`, Systeem > Standaarden. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### E3. SQL-injectie in AM::save_taxes (beveiliging)
`SL/AM.pm`, btw-tarieven opslaan. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### E4. Defaults omzeilt de admin-controle van Audit Control (beveiliging)
Toegangscontrole rond boekperiode sluiten en audittrail (`am.pl`/`AM.pm`). Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: afsluitdatum en audittrail alleen via het eigen scherm met rolcontrole op de server.

### E5. Padtraversal bij talen/sjabloonmappen in am.pl (beveiliging)
`bin/mozilla/am.pl`, beheer van talen. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: sjablonen staan in `<data_dir>/templates/<administratie-id>/`; namen worden gecontroleerd, geen paden uit het formulier.

### E6. Tabel- en kolomnaam uit het formulier in CT (beveiliging)
`SL/CT.pm`, relaties. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### E7. ORDER BY-injectie via de sorteerrichting (beveiliging)
`SL/Form.pm` en enkele modules, sorteren van lijsten. Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
- PyBalans: `sort` en richting accepteren alleen bekende waarden.

### E8. PE::save_job: SQL-injectie en ontbrekende NULL-standaard
`SL/PE.pm`, productie-opdrachten (jobs). Beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Daarnaast functioneel: een leeg klantveld geeft een SQL-fout (anders dan bij `save_project`).
- PyBalans: alle SQL is geparametriseerd; tabel-, kolom- en sorteernamen alleen uit een vaste lijst.

### E9. RC: zonder van-datum worden niet-afgeletterde regels dubbel getoond
`SL/RC.pm` r151-152 (eerste deel: alle regels met `transdate <= todate`, geen cleared-filter) plus r219-229 (tweede deel met `UNION ALL`: `cleared IS NULL AND transdate < todate`). Zonder `fromdate` komt elke niet-afgeletterde regel vóór de tot-datum twee keer in de lijst, met een verkeerd lopend saldo en dubbele afletter-checkboxen.

### E10. IM::payment_links zonder valutafilter
`SL/IM.pm` r1593-1612 selecteert open posten in alle valuta en deelt door `a.exchangerate`. Er is geen filter op `$form->{currency}`, dus een bankregel in EUR kan op bedrag matchen met een USD-factuur.

---

## Samenvatting

- Bevestigd: 59 van de 60. Bij 1 zijn `taxincluded`/`remittancevoucher` niet injecteerbaar (ze worden in r271 met `*1` numeriek gemaakt). 14 is bevestigd met beperkte impact, 37 met nuance, en 56 klopt in de code maar heeft weinig effect.
- Niet bevestigd zoals geformuleerd: 1 (nr. 59). De werkelijke fout zit bij een *gevonden* dcn.
- Extra gevonden: 10 (E1-E10). Daarvan gaan er 7 over beveiliging: E1-E7.
