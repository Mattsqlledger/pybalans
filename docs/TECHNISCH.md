# PyBalans

*boekhouding in Python op PostgreSQL · leest en verrijkt SQL-Ledger-administraties*

PyBalans is een webapp voor de boekhouding (pakket `pybalans`, versie 1.0.1) die SQL-Ledger-administraties inleest en
vervangt. De Python-modules heten nog `sqlledger` (de bibliotheek hieronder) en `sqlledger_web` (de webapp); de
opdracht is `pybalans` (de oude naam `sqlledger-web` blijft werken als alias). Over PyBalans (versie, licentie,
merken) staat in de app onderaan het menu.

PostgreSQL is een merk van de PostgreSQL Community Association of Canada; PyBalans is daar niet aan verbonden.
SQL-Ledger is een merk van DWS Systems Inc.; PyBalans is daar niet aan verbonden en leest SQL-Ledger-administraties
alleen in.

## Bibliotheek `sqlledger` — grootboekkern van SQL-Ledger in Python

Python-bibliotheek (zonder schermen) die de grootboeklogica van
[SQL-Ledger](https://sql-ledger.com) 3.2.12 overneemt. Werkt op het **bestaande
SQL-Ledger PostgreSQL-schema**, zodat Perl- en Python-versie naast elkaar op
dezelfde database kunnen draaien. Een database van SQL-Ledger 4.x (versie nieuwer dan 3.2.4) kan gekoppeld worden, maar wordt **niet ondersteund**
(bij "Verbinding testen" verschijnt een melding "gebruik op eigen risico"); een back-up van 4.x inlezen wordt geweigerd.

**Let op bij naast elkaar gebruiken:** eigen gegevens van deze versie staan in tabellen met `x_` (kostprijs,
bankafschriften, stuklijsten, orderkoppelingen, documentinstellingen …). De back-upfunctie van SQL-Ledger zelf
(3.2.12 én 4.x) neemt die niet mee en **wist ze bij terugzetten**. Maak back-ups met **Systeem > Back-up maken** of
`pybalans backup` (zie "Back-up maken" hieronder), of zelf met `pg_dump`.

Afgeleid werk van SQL-Ledger © DWS Systems Inc., licentie **GPL v2 of later**.

## Wat zit erin

| Python | Perl-origineel | Functie |
|---|---|---|
| `GeneralLedger.post_transaction` | `GL::post_transaction` + controles uit `gl.pl` | memoriaalboeking boeken of vervangen |
| `GeneralLedger.delete_transaction` | `GL::delete_transaction` | boeking verwijderen |
| `GeneralLedger.get_transaction` | `GL::transaction` | boeking ophalen (optioneel met record-lock) |
| `GeneralLedger.list_transactions` | `GL::transactions` + `gl.pl transactions` | journaaloverzicht over gl/ar/ap incl. tegenrekeningen; `display` = scherm (lopend saldo, per rekening, subtotalen) |
| `GeneralLedger.postable_accounts/departments/projects` | deel van `GL::transaction` | keuzelijsten |
| `ChartOfAccounts.all_accounts` | `CA::all_accounts` | rekeningschema met saldi |
| `ChartOfAccounts.account_transactions` | `CA::all_transactions` | mutaties per rekening met beginsaldo |
| `parse_amount`, `format_amount`, `round_amount` | `Form.pm` | bedragen |
| nummering, audittrail, locks, wisselkoers, referenties, herhaalschema | `Form.pm` | ondersteuning |
| `Reports.trial_balance` | `RP::trial_balance` + `rp.pl list_accounts` | proefbalans met begin-/eindsaldo, subtotalen per kop |
| `Reports.income_statement` | `RP::income_statement` + `rp.pl` | winst- en verliesrekening, per maand/kwartaal, vorig jaar, kas-/factuurstelsel, GIFI, valuta |
| `Reports.balance_sheet` | `RP::balance_sheet` + `rp.pl` | balans per datum, maand-/kwartaalkolommen, vorig jaar, resultaat lopend boekjaar |
| `Reports.aging` | `RP::aging` + `rp.pl aging` | ouderdomsanalyse debiteuren/crediteuren, klassen 0–90 dagen, per valuta |
| `Reports.reminders`, `save_reminder_levels` | `RP::reminder`, `RP::save_level` | vervallen facturen met aanmaanniveau; niveau opslaan |
| `Dunning` (`dunning.py`) | `rp.pl` reminder/statement print, `RP::save_level` | aanmaning per klant en valuta (`letters`, `record`, `set_levels`), rekeningoverzicht (`statements`, `record_statement`), sjabloonvariabelen (`letter_vars`, `statement_vars`) |
| `Reports.tax_accounts`, `tax_report` | `RP::get_taxaccounts`, `RP::tax_report` + `rp.pl` | btw-rapport verkoop/inkoop, samenvatting of detail, kasstelsel, onbelast |
| `Reports.payment_accounts`, `payments` | `RP::paymentaccounts`, `RP::payments` + `rp.pl` | ontvangsten/betalingen per betaalrekening met (sub)totalen |
| `Reports.earnings_accounts`, `yearend_preview`, `post_yearend` | `RP::yearend_statement`, `am.pl`, `AM::post_yearend` | jaarafsluiting berekenen en boeken |
| `ArapLedger` | `AA.pm` + `aa.pl`/`arap.pl` | verkoop-/inkoopboekingen zonder artikelen: boeken, laden, verwijderen, btw berekenen |
| `ArapLedger.list_transactions` | `AA::transactions` + `aa.pl transactions` | overzicht en openstaande posten verkoop/inkoop, samenvatting of per journaalregel, per valuta, subtotalen |
| `ArapLedger.find_names`, `all_names` | `Form::get_name`, `Form::all_vc` | klant/leverancier zoeken (actief op datum), keuzelijst (ook alleen met openstaande posten) |
| `Payments.open_vc`, `open_invoices` | `CP::get_openvc`, `CP::get_openinvoices` + `cp.pl` | relaties en posten die openstaan, met voorgestelde korting en betaalbedrag |
| `Payments.post_payment` | `CP::post_payment`, `OP::overpayment` + `cp.pl` | ontvangst/betaling over facturen verdelen, korting (met btw-correctie), koersverschil, vooruitbetaling |
| `Payments.void_payment` | `CP::void_payments` | betaling storneren (volledig, op een te kiezen datum) |
| `Payments.reissue_payment` | `CP::reissue_payment` | betaling opnieuw uitgeven met nieuw kenmerk en/of nieuwe datum |
| `RecurringTransactions.save`, `delete`, `list`, `process` | `Form::save_recurring`, `AM::recurring_transactions`, `am.pl process_transactions`, `AM::recurring_details`/`update_recurring` | terugkerende boekingen (memoriaal, verkoop/inkoop met en zonder artikelen, verkoop-/inkooporders; geen offertes): schema opslaan, lijst, kopie boeken/opslaan per schema in één transactie met lock (facturen via `InvoiceLedger.post_invoice`, orders via `OrderLedger.save_order`) |
| `Reconciliation.statement`, `reconcile`, `report`, `accounts` | `RC::paymentaccounts`, `RC::payment_transactions`, `RC::reconcile` + `rc.pl` | afletteren: scherm met beginsaldo, regels (samenvatting per kenmerk of detail, koersverschillen), afgeletterd saldo en verschil; opslaan bij verschil 0 (ronde in `x_reconciliation`); overzicht afgeletterde posten |
| `BankImport` (`bankimport.py`) | nieuw (SQL-Ledger kan alleen een CSV met betalingen inlezen, `im.pl`/`IM::payment_links`) | bankafschrift (CAMT.053/MT940, via `bankstatement.py`) inlezen in `x_bank_statement`/`x_bank_line`: rekening via IBAN (`bank`), dubbele regels overslaan, saldocontrole; voorstel per regel (kenmerk/dcn NL+RF, factuurnummer, IBAN relatie, bestaande boeking, vaste regel); verwerken als betaling (`Payments.post_payment`), memoriaal (met btw), koppelen of negeren, steeds direct afgeletterd; vaste regels (`x_bank_rule`) |
| `Contacts.get`, `new`, `choices` | `CT::create_links` | klant/leverancier met adres, contactpersoon, bank, btw-rekeningen, standaardrekeningen, verzendadres, referenties; keuzelijsten |
| `Contacts.save`, `next_number` | `CT::save`, `ct.pl new_number` | toevoegen/wijzigen (alleen gewijzigde velden), volgend relatienummer (ook alleen tonen) |
| `Contacts.delete`, `delete_many`, `set_inactive` | `CT::delete`, `CT::batch_delete` | ongebruikte relatie(s) verwijderen, inactief maken (einddatum) |
| `Contacts.search`, `countries` | `CT::search` + `ct.pl list_names` | relaties zoeken met openstaand en beschikbaar krediet |
| `admin.Admin` | `AM.pm` (get/save/delete_account, taxes, save_taxes, defaultaccounts, save_defaults, closedto, closebooks, audit_log, remove_locks) + controles `am.pl` | rekeningschema, btw-tarieven, instellingen (per sleutel), boekperiode afsluiten, audittrail, locks; `fix_fxgainloss` (A8) |
| `masterdata.MasterData` | `AM.pm` (currencies, save/delete_currency, move, get/save_exchangerate, department, business, paymentmethod, language) + `PE.pm` (projects, save/delete_project) | valuta (standaardvaluta expliciet), koersen, afdelingen, projecten, talen, branches, betaalmethoden |
| `Parts` (`parts.py`) | `IC.pm` (get_part, save, delete, all_parts, history, adjust_onhand) + `ic.pl` | artikelen, diensten en samengestelde artikelen (met stuklijst): lezen, opslaan (alleen gewijzigde kolommen), verwijderen (alleen ongebruikt), zoeken, prijshistorie, groepen, voorraadcorrectie (`x_stock_adjustment`) |
| `InvoiceLedger` (`invoice.py`) | `IS.pm`/`IR.pm` (post_invoice, retrieve_invoice, delete_invoice, retrieve_item, reverse_invoice, process_assembly) + `is.pl`/`ir.pl`/`io.pl` | verkoop-/inkoopfacturen en creditnota's met artikelen: doorrekenen (regelbedragen, korting, btw per regel en rekening, btw inbegrepen, valuta, afrondingsverschil), boeken/vervangen, laden, verwijderen; artikelen zoeken; voorraad; verkoop van samengestelde artikelen (onderdeelregels); laatste/gemiddelde inkoopprijs |
| `OrderLedger` (`order.py`) | `OE.pm` (save, retrieve, delete, transactions) + `oe.pl` (add/edit/update/save, sales_order/purchase_order/quotation/rfq, invoice, create_backorder) + `io.pl` create_form | verkoop-/inkooporders, offertes en offerteaanvragen: doorrekenen (zoals OE.pm), opslaan, laden, verwijderen, heropenen, omzetten (offerte -> order, order -> offerte, order/offerte -> factuur), lijst; bij het boeken van de factuur sluit de bron, `ship` = gefactureerd en het restant wordt een nabestelling (`close_for_invoice`); koppelingen in `x_oe_link` |
| `cogs.CogsEngine` (`cogs.py`) | `IS::cogs`, `cogs_returns`, `cogs_difference`, `IR::cogs`, `cogs_returns`, `reverse_invoice` | kostprijs (FIFO) met vastgelegde toewijzingen (`x_cogs_allocation`, kostprijs aanvoer in volle precisie in `x_invoice_cost`): toewijzen bij verkoop, alsnog bij latere inkoop, retouren tegen oorspronkelijke/FIFO-kostprijs, exact terugdraaien, kostprijsregels in andere boekingen alleen als verschil bijwerken; oude Perl-toewijzingen blijven tot een regel wijzigt |
| `assembly.Assemblies`, `assembly.Bom` | `IC.pm` (assembly_item, update_assembly, retrieve_assemblies, stock_assemblies, adjust_inventory) + `ic.pl` (stock_assembly, assembly_row) | stuklijst controleren (cyclus, max. 5 niveaus, elk onderdeel één keer), prijzen uit de onderdelen (per regel afgerond), assembleren/demonteren (`x_assembly_build`), hoeveel kan ik maken, stuklijst uitgeklapt, waar gebruikt, productiehistorie |

De rapporten leveren **data**, geen opmaak: de regels per rekening, de
subtotalen per kop, de totalen en het resultaat zoals `rp.pl` ze berekent, en
een lijst `display` met de regels in de volgorde van het SQL-Ledger-scherm.

Nog niet meegenomen: orders en offertes (`OE.pm`), het
betalingsregister en cheques/kwitanties printen (rest van `CP.pm`),
batch-beheer (`VR.pm`), bestandsuploads bij
referenties. (Inmiddels in de webapp en fase 3/4: zie de webtabel; aanmaningen en rekeningoverzichten in
`sqlledger/dunning.py`.)

## Gebruik

```python
from datetime import date
from decimal import Decimal
from sqlledger import (connect, UserConfig, GeneralLedger, ChartOfAccounts,
                       GLTransaction, JournalLine, TransactionSearch)

conn = connect("host=localhost dbname=mijnbedrijf user=sql-ledger")
cfg = UserConfig(login="admin", numberformat="1.000,00", dateformat="dd-mm-yy")
gl = GeneralLedger(conn, cfg)

trans_id = gl.post_transaction(GLTransaction(
    transdate=date(2026, 3, 2),
    description="Kasopname",
    lines=[JournalLine("1001", debit=Decimal("50.00")),
           JournalLine("1100", credit=Decimal("50.00"), source="BANK-12")],
))

view = gl.get_transaction(trans_id)
overzicht = gl.list_transactions(TransactionSearch(accno="1100", datefrom=date(2026, 1, 1)))
saldi = ChartOfAccounts(conn, cfg).all_accounts()
```

Rapporten:

```python
from sqlledger import Reports

rp = Reports(conn, cfg)
tb = rp.trial_balance(fromdate=date(2026, 1, 1), todate=date(2026, 6, 30))
wv = rp.income_statement(fromdate=date(2026, 1, 1), todate=date(2026, 12, 31),
                         includeperiod="quarter", previousyear=True)
bal = rp.balance_sheet(todate=date(2026, 6, 30))

for row in wv.display:          # kind: section/heading/account/subtotal/total/result
    print(row.kind, row.description, row.values)
resultaat = wv.result[("this", 0)]
```

Bedragen in de rapporten staan in weergaveteken (opbrengsten, kosten, activa,
passiva en eigen vermogen positief); `wv.columns` geeft de kolomvolgorde als
`(jaar, periode)` met jaar `'this'` of `'previous'` en periode 0 = hele periode.

Elke schrijfactie draait in één databasetransactie: bij een fout blijft er
niets half achter (ook geen opgehoogd boekingsnummer). Binnen een eigen
`with conn.transaction():` doet de bibliotheek mee in jouw transactie.

Fouten zijn exceptions: `OutOfBalanceError`, `ClosedPeriodError`,
`LockedTransactionError`, `ValidationError`, `NotFoundError` (allemaal
`LedgerError`).

## Webapp PyBalans (`sqlledger_web`)

FastAPI + server-side HTML (Jinja, HTMX lokaal meegeleverd). Stand: app-skelet
(stap 2) — inloggen, tweestapsverificatie, administratie kiezen, menu, rollen,
systeembeheer en logboek — plus alle rapporten (stap 3), memoriaal met
jaarafsluiting (stap 4), verkoop-/inkoopboekingen zonder artikelen (stap 5) en
ontvangsten/betalingen (stap 6); fase 2: terugkerende boekingen.
ontvangsten/betalingen (stap 6) en beheer (fase 2).

| Onderdeel | Hoe |
|---|---|
| App-database | eigen PostgreSQL-database (`app_dsn`): gebruikers, administraties, rechten, sessies, logboek; wordt bij het starten aangemaakt (als de databasegebruiker CREATEDB heeft) en bijgewerkt |
| Eerste start | EERSTE_START_ANALYSE.md ES1–ES9, keuzes 1–16 (`routes_start.py`, `templates/start/`, `static/dbname.js`). Zolang er geen gebruiker is, gaat elke pagina (behalve `/static/`) naar **`/welkom`**; daarna is die dicht. Stappen: (1) welkom in het Engels, taal kiezen (browsertaal voorgekozen; cookie `sl_lang` tot de beheerder bestaat), (2) **startcode** uit het serverlog (`docker compose logs app`; nieuw bij elke start, alleen de hash in `app_meta`) + login, naam, e-mail, wachtwoord → systeembeheerder met taal en notatie, (3) verplicht 2FA instellen (bestaand scherm), (4) **eerste administratie**: nieuw (formulier van Systeem; schema per taal: nl → Eenmanszaak/vof, en → leeg VK, fr → leeg FR, de → leeg DE; databasenaam uit de naam), **back-up inlezen** (grijs, komt met stap 7) of **bestaande database koppelen** (met "mij als beheerder toevoegen"), (5) **gebruikers uitnodigen** (naam, login, e-mail, rol; codes + activeringslink op één afdrukbare pagina, link "Toegang per administratie"), daarna het dashboard. Een systeembeheerder zonder administraties komt na inloggen weer bij stap 4. Elke stap is over te slaan ("Later via Systeem"). Systeem > Gebruikers: knop **Code mailen** zodra een administratie e-mail heeft ingesteld (alleen een geldige, ongebruikte code). Dashboard: blok **Aan de slag** (alleen beheerder; alleen bij administraties die in de app zijn aangemaakt of via de wizard gekoppeld, vlag `x_setting.start_list`, ES10): bedrijfsgegevens, **logo en huisstijl** (→ Beheer > Huisstijl, klaar zodra de huisstijl één keer is opgeslagen; HS10), btw-tijdvak, IBAN van de bankrekening (→ de eerste bankrekening zonder IBAN, bank vóór kas), beginbalans, eerste verkoopfactuur; automatisch afgevinkt, per punt "niet nodig", "Startlijst verbergen" (`x_task_done`, soort `start_<punt>`). Koppelen (wizard en Systeem) weigert een database van SQL-Ledger ouder dan 3.2.4, "Verbinding testen" waarschuwt (ES12, zie "Oude SQL-Ledger-versie"). Beheer > Rekeningschema: **Bankgegevens** (IBAN met controle, BIC, naam van de bank) bij een betaalrekening, in `bank` zoals SL (ES11). NL/EN/DE/FR |
| Administraties | tabel `company` met dbname/host/poort; databasewachtwoord alleen in config/omgeving |
| Nieuwe administratie | RS1 (`REKENINGSCHEMA_ANALYSE.md`; `sqlledger/newcompany.py`, `sqlledger/chartfiles.py`, `templates/system/company_create.html`). Systeem > Administraties > **Nieuwe administratie** (`/systeem/administraties/aanmaken`): naam, code, databasenaam (kleine letters/cijfers/_; een bestaande database wordt nooit overschreven), land en schema (keuzelijst per land; Nederland: Eenmanszaak/vof (MKB, 160 rekeningen, standaard), BV (MKB, 161), Basis (63 rekeningen) en leeg, alle drie met RGS 3.8-code per rekening en verlegde btw 2A/4A/4B; Duitsland: leeg schema met SKR03-nummers; België: MKB (MAR) (`be-mar`, 128 rekeningen, btw 21/12/6 % met rooster 54/59, nl/fr/en/de) en leeg; Frankrijk: PCG abrégé (`fr-pcg`, 175 rekeningen, nomenclatuur 2025, btw 20/10/5,5/2,1 % met CA3-regel) en leeg; Verenigd Koninkrijk: Limited company (`uk-ltd`, 107 rekeningen, Sage-banden, btw 20/5/0 % met VAT100-box 1/4) en leeg; Zwitserland: KMU/PME/PMI (`ch-kmu`, 109 rekeningen, Kontenrahmen KMU, btw 8,1/2,6/3,8 % met MWST-Ziffern 303/313/343/400) en leeg), taal van de rekeningnamen (= documenttaal; ook Italiaans, dan documenten in de taal van het scherm), standaardvaluta, laatste maand van het boekjaar, modules, "mij als beheerder toevoegen". Maakt de database (`CREATE DATABASE … TEMPLATE template0`, recht CREATEDB nodig), laadt het SL-schema + rekeningschema, instellingen, btw; mislukt er iets dan gaat de database weer weg. NL: btw-aangifte in het menu aan. "Bestaande database koppelen" = het oude formulier. **v1.0.1** (PB24/PB25): uitleg per schema wisselt mee met de keuze; Standaardvaluta als keuzelijst. NL/EN/DE/FR |
| Administratie wisselen | AW1–AW7 (`ADMINISTRATIE_WISSELEN_ANALYSE.md`; `templates/_switcher.html`, `static/switcher.js`, `Ctx.switcher`). De naam van de administratie in de kopregel is een keuzemenu (`<details>`, werkt zonder JavaScript): huidige administratie (code · database · rol), **Recent gebruikt** (laatste 5 van deze gebruiker, zonder de huidige; tabel `recent_company`, app-db migratie 9), vanaf 8 administraties een zoekveld "Andere administratie openen" (naam, code of database; Enter bij één treffer opent die, anders de keuzepagina met `?q=`), **Alle tonen** = `/administratie`. Kiezen = de bestaande POST `/administratie` (CSRF) → altijd het dashboard. Alleen administraties waar de gebruiker lid van is; vergrendeld = grijs en niet te kiezen (behalve voor een beheerder ervan). Half ingevuld formulier: eerst "Wijzigingen verlaten?". Korte **code** per administratie (`company.code`, hoogstens 12 tekens, uniek als ingevuld) in Systeem > Administraties, getoond in kopregel, keuzemenu en keuzepagina. NL/EN/DE/FR |
| Gebruikers | eenmalige code van de beheerder (24 uur, één keer) → zelf wachtwoord kiezen (argon2, min. 12 tekens); **v1.0.1** (PB14, PB23): per administratie keuzelijst **Medewerker** (medewerkers met login of "nieuwe medewerker aanmaken"); uitleg onder Login en Naam; Mijn uren zonder koppeling wijst de weg |
| 2FA | TOTP; verplicht voor systeembeheerders en beheerders van een administratie, optioneel voor anderen; code één keer bruikbaar |
| Rollen | systeembeheerder (app-breed) + per administratie beheerder / boekhouder / alleen-lezen; modules aan/uit per administratie |
| Koppeling SQL-Ledger | per gebruiker per administratie een medewerkerslogin (`employee.login`), gaat als `UserConfig.login` naar de bibliotheek |
| Beveiliging | server-side sessies (token gehasht, 1 uur inactief), nieuw token na elke inlogstap, blokkade na 5 mislukte pogingen per account én per IP (15 min), CSRF-token (formulier of `X-CSRF-Token`), `__Host-`/Secure/HttpOnly-cookies, strikte CSP, CSV-export zonder formules |
| Logboek | alle inlog-, beheer- en keuzeacties met IP; filter + CSV |
| Memoriaal | `/memoriaal/nieuw`, `/memoriaal/<id>` (`routes_gl.py`): kop + regels zoals `gl.pl`; rekening via zoekveld (alleen boekbare open rekeningen), totalen en verschil live (`static/gl.js`), altijd een lege regel onderaan; zonder JavaScript via "Bijwerken". Opslaan (bestaande boeking pas na bevestiging), Opslaan als nieuw, Nieuw nummer, Verwijderen (bevestiging). Leeg nummer = volgend nummer bij opslaan; `lock_glnumber` gevolgd. Valuta met voorgestelde koers, afdeling/project als ze bestaan, referenties (omschrijving/code). Lock bij openen (`semaphore`): een ander ziet "in gebruik door". Alleen-lezen bij rol alleen-lezen, `revtrans`, afgesloten periode of batch (dan "Kopiëren naar nieuwe boeking"). Herhaalschema wordt getoond en blijft bij opslaan staan (bewerken via "Herhalen"). Koersverschilboeking: `/memoriaal/nieuw?fx=1` (FX-vinkje per regel) |
| Verkoop/inkoop | `/verkoop/nieuw`, `/verkoop/<id>`, `/inkoop/...`, creditnota met `?creditnota=1` (`routes_arap.py`): scherm zoals `aa.pl`. Nieuwe boeking begint met relatie en afdeling van de laatste boeking (`lastname_used`); relatie via zoekveld (naam of nummer, actief op de datum), daarna laadt de server termijn, valuta, btw-rekeningen, rekeningen, korting en de rekeningen van de vorige boeking. Regels (bedrag, rekening uit AR_amount/AP_amount, omschrijving, project), btw per rekening met vinkje "berekenen" (zelf invullen zet het uit), btw inbegrepen; btw/totaal/openstaand rekent de server (`static/arap.js` vraagt ze op met `action=calc`). Betalingen op de boeking (met koers per datum), contante korting als de relatie of boeking die heeft, geblokkeerd, betaalkenmerk (inkoop invoer, verkoop berekend), referenties. Termijn ↔ vervaldatum; bij openen blijft de vervaldatum staan. Inkoop: waarschuwing bij een factuurnummer dat bij dezelfde leverancier al bestaat. Wijzigen pas na bevestiging; lock, alleen-lezen en "Kopiëren" zoals bij het memoriaal. Facturen met artikelen openen in het factuurscherm (fase 3) |
| Facturen met artikelen | `/verkoopfactuur/nieuw`, `/verkoopfactuur/<id>`, `/inkoopfactuur/...`, creditnota met `?creditnota=1` (`routes_is.py`, `templates/is/form.html`, `static/is.js`; fase 3 F1–F9). Kop zoals verkoop/inkoop (relatie via zoekveld, datums, termijn, valuta/koers, ordernummers, afdeling, verkoper, betaalmethode, betaalkenmerk, omschrijving, geblokkeerd). Regels: artikel via zoekveld (nummer, barcode of omschrijving; keuzelijst bij het typen, verversen bij een keuze: omschrijving, eenheid, prijs in de factuurvaluta en btw van het artikel), omschrijving, aantal, eenheid, prijs, korting %, bedrag, leverdatum, serienummer, project; altijd een lege regel onderaan. Regelbedragen, btw per rekening, subtotaal en totaal rekent de server (`action=calc`); btw inbegrepen; waarschuwing bij verkoop boven de voorraad (F4). Betalingen, contante korting, afleveradres en verzending, notities, referenties zoals bij verkoop/inkoop. Wijzigen na bevestiging; lock, afgesloten periode, batch, kassabon en alleen-lezen geven de leespagina met "Kopiëren naar nieuwe factuur". Bij openen een waarschuwing als het bedrag met de huidige btw-instellingen van artikel/relatie anders uitkomt dan geboekt. `/boekingen/<id>` en `/verkoop/<id>` van een factuur met artikelen gaan naar dit scherm. Boekhouder+, module Verkoop resp. Inkoop |
| Ontvangsten/betalingen | `routes_cp.py`, scherm zoals `cp.pl`. **Eén relatie** (`/ontvangst/nieuw`, `/betaling/nieuw`): relatie via zoekveld, standaard alleen relaties met open posten ("Alle relaties" voor een vooruitbetaling); posten aanvinken, bedrag en contante korting per post (korting voorgesteld binnen de termijn), totaalbedrag; wat niet aan posten is toegewezen wordt na bevestiging een vooruitbetaling; een totaal lager dan de posten en een bedrag hoger dan openstaand worden geweigerd. Kenmerk = volgend nummer van de betaalrekening. Filters vervaldatum, afdeling, branche (leveranciers), betaalmethode, debiteuren-/crediteurenrekening, valuta (met koers). **Meerdere relaties** (`/ontvangsten`, `/betalingen`): één regel per relatie, kenmerk loopt per relatie op, bedrag wordt verdeeld (creditnota's eerst, dan de oudste; korting alleen bij volledige betaling), alles of niets; "per post" opent het scherm voor één relatie. **Corrigeren** (`/ontvangsten/corrigeren`, `/betalingen/corrigeren`): betalingen per betaalrekening zoeken (relatie, kenmerk van/tot, datum, weergave), storneren op een te kiezen datum of opnieuw uitgeven met nieuw kenmerk/datum, na bevestiging. Geen locks bij het tonen: het scherm stuurt per post het getoonde openstaande bedrag mee en boekt niet als dat intussen veranderd is. Bedragen rekent de server (`static/cp.js`, `action=calc`); zonder JavaScript via "Bijwerken". Boekhouder+ en module Kas en bank |
| Orders en offertes | `/verkooporder/nieuw`, `/inkooporder/...`, `/offerte/...`, `/offerteaanvraag/...` (+ `/<id>`), lijsten `/verkooporders`, `/inkooporders`, `/offertes`, `/offerteaanvragen` (`routes_oe.py`, `templates/oe/`, fase 3 O1–O6, ON1–ON4). Eén scherm voor de vier soorten zoals `oe.pl`: relatie via zoekveld (nieuw: relatie en afdeling van de laatste order resp. offerte), nummer (leeg = volgend), datum, termijn (niet bij een aanvraag), leverdatum/geldig tot, inkoopordernummer, valuta/koers, afdeling, medewerker, omschrijving; regels zoals de factuur (artikel, omschrijving, aantal, eenheid, prijs, korting %, bedrag, leverdatum, serienummer, project; nieuw artikel krijgt de leverdatum van de order); btw en totaal rekent de server (`static/is.js`); notities, interne notities, afleveradres, referenties. **Opslaan en omzetten naar**: offerte → verkooporder, verkooporder → offerte, aanvraag → inkooporder, inkooporder → aanvraag, order/offerte → verkoop-/inkoopfactuur (`/verkoopfactuur/nieuw?order=<id>`: alle regels, deellevering door aantallen te verlagen of regels weg te halen). Een gefactureerde, gesloten of afgesloten order is een leespagina (gesloten zonder factuur: knop **Heropenen**) met koppelingen naar bron, factuur en nabestelling. Lijsten: nummer, relatie, periode, open/gesloten, meer opties, kolommen naar keuze, totaalregel, CSV/Excel; status open/gesloten/gefactureerd/nabestelling. Boekhouder+ (bekijken alle rollen), module Orders resp. Offertes |
| Terugkerende boekingen | `routes_recurring.py`. **Herhalen** op het memoriaal-, verkoop-, inkoop-, factuur- en orderscherm (opgeslagen boeking; niet bij offertes, batches en kassabonnen) opent `/herhalen/<id>` zoals `arap.pl schedule`: kenmerk, omschrijving voor de lijst, startdatum (standaard de boekdatum), elke n dagen/weken/maanden/jaren, aantal keer, betaling meenemen (verkoop/inkoop, ook facturen), volgende datum (bestaand schema); opslaan/verwijderen, terug naar de boeking. Controles: start verplicht, n 1–999, aantal 1–9999. **Lijst** `/terugkerend` (menu Terugkerend) zoals `am.pl recurring_transactions`, per soort: kenmerk, omschrijving, relatie, boeking, volgende datum (bewerkbaar), einde, bedrag, interval, status (te laat/vandaag/over n dagen/afgelopen); te late schema's staan aangevinkt. **Verwerken** boekt per aangevinkt schema één kopie (zie README 85–94) en meldt per schema het nummer met link of de reden van overslaan (koers ontbreekt, afgesloten periode, intussen al verwerkt, onvoldoende voorraad). Een gesloten of gefactureerde order kan als sjabloon dienen (alleen-lezen, wel Herhalen). **Mailen en PDF** (fase 4 stap 4d, SB12–SB13): op het schemascherm per documentsoort van de boeking (factuur: factuur/pakbon/picklijst; order: orderbevestiging/pakbon/picklijst; zonder artikelen: factuur, creditnota, inkoopboeking, debetnota; memoriaal: geen) de keuzes Mailen (+ bericht met plaatshouders) en PDF, opgeslagen als formnames zoals SL in `recurringemail`/`recurringprint`. Na het verwerken krijgt elk nieuw document een eigen mail (adres van de relatie + cc/bcc, standaardonderwerp; daarna gemaild, notitie `[email]`, audittrail) en komt de uitkomst per document onder het schema; de PDF-soorten van alle nieuwe documenten staan achter de knop "PDF van de nieuwe documenten (n)" (één samengevoegde PDF als download, daarna geprint + audittrail). Mislukt mailen of de PDF, dan blijven boeking en doorgeschoven schema staan (Perl-fout 23). Automatisch verwerken zonder knop is er niet (buiten SB12–SB13). Boekhouder+, module Terugkerend (en de module van de boeking) **v1.0.1** (PB16): schema dat meer dan één termijn achterloopt alleen met "toch verwerken"; Stoppen (`/terugkerend/<id>/stoppen`). |
| Afletteren | `/afletteren` (`routes_rc.py`, scherm zoals `rc.pl`): rekening (standaard de betaalrekeningen, vinkje "Alle balansrekeningen"), periode (standaard: dag na de vorige ronde t/m einde vorige maand), samenvatting per kenmerk of detail, koersverschillen mee. Regels met vinkje; aangevinkt, afgeletterd saldo en verschil met het afschriftsaldo rekent de server (`static/rc.js`, `action=calc`; zonder JavaScript "Bijwerken", "Alles aan-/uitvinken"). Opslaan alleen bij verschil 0, altijd met de vinkjes op het scherm; afletterdatum = afschriftdatum; al afgeletterde regels houden hun datum; regels van dezelfde boeking met dezelfde datum en hetzelfde kenmerk gaan samen; "deels" bij een samengevoegde regel die al voor een deel is afgeletterd. Elke ronde in `x_reconciliation` + audittrail + logboek; waarschuwing als het beginsaldo afwijkt van de vorige ronde. `/afletteren/rapport`: afgeletterde posten in de periode (rc.pl report), CSV/Excel. Afletteren: boekhouder+; rapport: alle rollen; module Kas en bank |
| Klanten/leveranciers | `routes_ct.py`, scherm zoals `ct.pl` (fase 2, K1–K9). **Lijst** (`/klanten`, `/leveranciers`): filters naam, nummer, contactpersoon, plaats en status alle/actief/inactief/ongebruikt; achter "Meer opties" e-mail, telefoon, adres, postcode, provincie, land, notities, medewerker, branche, startdatum, sortering en kolommen (alle `ct.pl`-kolommen, ook openstaand en beschikbaar krediet); CSV/Excel; 500 per pagina; bij "ongebruikt" meervoudig verwijderen na bevestiging. **Bewerkscherm** (`/klanten/nieuw`, `/klanten/<id>`): algemeen (bedrijf/persoon, nummer, naam, btw-nummer, branche, verkoper, taal, valuta, start-/einddatum), adres, contactpersoon (de eerste; andere blijven staan), voorwaarden en rekeningen met btw, bank (IBAN/BIC gecontroleerd), afwijkend verzendadres, notities, referenties. Opslaan (alleen gewijzigde velden), Opslaan als nieuw, Nieuw nummer (teller pas bij opslaan), Verwijderen (bevestiging; alleen als de relatie nergens gebruikt wordt, anders "Inactief maken"). Knoppen naar nieuwe verkoop/inkoop, creditnota en ontvangst/betaling met de relatie voorgevuld; doorklik "Boekingen"/"Openstaand" naar het overzicht met alleen deze relatie. Boekhouder+ bewerkt, alleen-lezen ziet; module Verkoop resp. Inkoop |
| Boekingen zoeken | `/rapporten/journaal`: alle opties van `gl.pl` (meer achter "Meer opties"), kolommen kiezen, per rekening met lopend saldo, subtotalen, doorklikken naar het bewerkscherm |
| Jaarafsluiting | `/beheer/jaarafsluiting` (beheerder): voorbeeld (precies wat geboekt wordt, ook als een bestaande afsluiting eerst vervalt) → bevestigen; standaard einde laatste afgelopen boekjaar; geen automatische afsluitdatum |
| Boekjaar | Bouwstap 2 (BEHEER_EXTRA_ANALYSE.md JA1–JA8, F5, F22). Laatste maand van het boekjaar in `defaults.yearend` (1–12, leeg = 12; oude SL-waarde `1/31` = januari), instelbaar bij aanmaken én op **Beheer > Instellingen** ("Boekjaar eindigt in", met waarschuwing als er al jaarafsluitingen zijn; wijziging in de audittrail, `formname` `yearend`). Hulpfuncties `fiscal_year`/`fiscal_dates`/`fiscal_label`/`fiscal_label_for`/`yearend_month` in `sqlledger/dates.py`, `_db.get_yearend`. Eindigt het boekjaar niet op 31-12: in de rapporten extra periodekeuze **Boekjaar** ("2025/2026", waarde = beginjaar; `period=fiscal&fyear=2025`), "Jaar" blijft het kalenderjaar; standaardperiode "Van" = begin van het lopende boekjaar; doorklik van een boeking naar rekeningmutaties over het boekjaar van de boekdatum; dashboard (resultaat, maandgrafiek in boekjaarvolgorde, banklinks), grafieklabel en controleblad Ingebruikname volgen het boekjaar. Btw-aangifte en ICP blijven per kalendertijdvak. NL/EN/DE/FR |
| GIFI-indeling | Bouwstap 3 (BEHEER_EXTRA_ANALYSE.md GF1–GF6). **Beheer > GIFI-indeling** (`/beheer/gifi`, onder Rekeningschema, alleen beheerder): lijst met kolom "Aantal rekeningen" (ook kopregels), toevoegen, wijzigen, verwijderen; bovenaan een signaal met rekeningen die naar een niet-bestaande code verwijzen, met link "Code toevoegen" (code alvast ingevuld). Verwijderen alleen als geen rekening de code gebruikt (ook op de server); een nieuwe code bij een bestaande neemt `chart.gifi_accno` mee in één transactie, code blijft tekst. Bibliotheek `Admin.gifi_codes`/`gifi_orphans`/`get_gifi`/`save_gifi`/`delete_gifi`/`clean_gifi`. Bij de rekening is GIFI een zoeklijst (`combo.js`) uit de tabel, met waarschuwing en link als de code er niet in staat (opslaan mag). Soort "GIFI-indeling" in Beheer > Importeren/Exporteren (`accno`, `description`; nieuw of omschrijving bijwerken). NL/EN/DE/FR |
| Bankafschrift | `routes_bank.py`, nieuw (geen Perl-scherm). **Inlezen** (`/bank/inlezen`): CAMT.053 of MT940 uploaden (max. 10 MB); rekening via het rekeningnummer, onbekend → rekening kiezen en het rekeningnummer wordt opgeslagen; uitslag met saldocontrole (begin + regels = eind verplicht), waarschuwingen (sluit niet aan op vorig afschrift of grootboek) en overgeslagen regels (al eerder ingelezen). **Verwerken** (`/bank/regels`): open regels per bankrekening met voorstel (a kenmerk, b factuurnummer, c rekeningnummer relatie, d bestaande boeking, e vaste regel); per regel uitklappen: voorstel boeken/koppelen, memoriaal tegen een rekening (btw optioneel, als vaste regel opslaan) of negeren; "Alle zekere voorstellen boeken" (a/b met precies het bedrag) na bevestiging. Een voorstel wordt alleen geboekt als het nog precies zo is als getoond. **Eén regel** (`/bank/regel/<id>`): relatie en facturen kiezen met bedragen (rest als vooruitbetaling), memoriaal, koppelen aan een bestaande boeking (±31 dagen), negeren/weer openzetten. **Vaste regels** (`/bank/vaste-regels`) en **ingelezen afschriften** (`/bank/afschriften`, per afschrift CSV/Excel). Alles wat geboekt of gekoppeld wordt is meteen afgeletterd (afletterdatum = boekdatum bank). Boekhouder+ en module Kas en bank |
| Betaalbestand (SEPA) | Bouwstap 5 (BEHEER_EXTRA_ANALYSE.md IM9 A1, IM10 A; keuzes SP1–SP13; `sqlledger/sepa.py`, `routes_sepa.py`, `templates/cp/sepa.html`, XSD's in `sqlledger/sepa_xsd/`). **Kas en bank > Betaalbestand maken** (`/betalingen/betaalbestand`, boekhouder+, module Kas en bank; ook een link op Betalingen): betaalrekening (alleen met IBAN), uitvoerdatum (standaard volgende werkdag), vervaldatum t/m (standaard de uitvoerdatum), leverancier (naam/nummer), versie pain.001.001.03 (standaard) of .09. Lijst: open inkoopfacturen in euro, goedgekeurd, niet geblokkeerd (zoals Betalingen, incl. drempel van de leverancier); vinkje, openstaand, contante korting op de uitvoerdatum, bedrag (voorstel = openstaand − korting, aan te passen), IBAN, omschrijving, opmerking. Niet aan te vinken: geen/ongeldige IBAN, IBAN buiten SEPA, geen adres bij een betaling buiten de EER. Een factuur die al in een betaalbestand zat staat uit ("In betaalbestand op …"). Creditnota's en andere valuta gaan niet mee (melding). **Betaalbestand maken**: controle tegen de database (openstaand zoals getoond), één overboeking per factuur, `BtchBookg` false, `ChrgBr` SLEV, EndToEndId = factuurnummer, omschrijving gestructureerd (SCOR, NL-kenmerk met uitgever CUR, of RF) of het factuurnummer; controle tegen de officiële XSD (`xmlschema`); vastgelegd in `x_sepa_export`/`x_sepa_item` (migratie 19) + audittrail per factuur (`ap`, formname `sepa`, actie `exported`) + logboek `sepa_export`; **niets geboekt** — Bankafschrift inlezen koppelt de afschrijvingen op kenmerk of factuurnummer. Downloaden en opnieuw downloaden onder "Eerder gemaakte betaalbestanden" (`/betalingen/betaalbestand/<id>.xml`). NL/EN/DE/FR |
| Artikelen | `routes_ic.py`, `templates/ic/` (fase 3, AT1–AT7). `/artikelen` lijst zoals `ic.pl search`: filters nummer, omschrijving, soort, groep, status (actief, vervallen, ongebruikt, op voorraad, onder bestelniveau), barcode; "Regels uit" verkocht/gekocht/orders/offertes (open/gesloten, periode) toont de factuur- en orderregels; kolommen naar keuze, CSV/Excel. `/artikelen/nieuw` (`?soort=dienst`) en `/artikelen/<id>`: artikel of dienst met nummer (leeg = volgend nummer, waarschuwing bij een dubbel nummer), omschrijving, eenheid, groep, barcode, vervallen, verkoop-/lijst-/laatste inkoopprijs (meer decimalen als de prijs die heeft), prijsdatum, voorraad (alleen tonen), bestelniveau, bak, gewicht, "voorraad controleren", rekeningen (vast zodra gebruikt) en btw; opslaan, als nieuw, nieuw nummer, verwijderen (alleen ongebruikt). Loon/overhead en kits: alleen lezen. Prijshistorie verkoop/inkoop (`/artikelen/<id>/historie`), voorraadcorrectie met reden (`/artikelen/<id>/voorraad`), artikelgroepen (`/artikelen-groepen`). Bekijken alle rollen, wijzigen boekhouder+, module Artikelen |
| Samengestelde artikelen | Fase 3 SA1–SA12. `/artikelen/nieuw?soort=samengesteld` en `/artikelen/<id>`: hetzelfde scherm met alleen opbrengstrekening en btw, een **stuklijst** (aantal, nummer, omschrijving, eenheid, voorraad, regelprijzen, vinkje "op documenten"; nieuw onderdeel = nummer of omschrijving in de lege regel + Bijwerken, bij meer treffers een keuzelijst; aantal leeg/0 haalt een regel weg) en berekende prijzen (verkoopprijs = som van de onderdelen, of "verkoopprijs zelf bepalen"; lijst-, kostprijs en gewicht altijd berekend). Zolang er voorraad van het product is liggen onderdelen en aantallen vast. **Assembleren** (`/artikelen/<id>/assembleren`, `routes_asm.py`): assembleren of demonteren met aantal, datum, kenmerk en reden; tabel met per onderdeel de beweging en de voorraad nu/daarna; te weinig voorraad → waarschuwing met "Ja, toch assembleren" (met "voorraad controleren" op het onderdeel: geweigerd); demonteren niet verder dan de productvoorraad. **Lijsten** (alle CSV/Excel): `/artikelen-assembleren` (hoeveel kan ik maken, beperkt door welk onderdeel), `/artikelen-stuklijsten` (alle stuklijsten uitgeklapt, ook "alleen eindproducten"), `/artikelen/<id>/stuklijst?aantal=n`, `/artikelen/<id>/waar-gebruikt` (direct en via sub-stuklijsten), `/artikelen-productie` (productiehistorie, doorklik naar de regels per onderdeel). Assembleren: boekhouder+; lijsten: alle rollen; module Artikelen |
| Beheer | `routes_am.py`, `templates/am/` (fase 2, A1–A8). **Beheerder:** `/beheer/instellingen` (bedrijfsgegevens, precisie, koersverschilrekening, opties, laatste nummers met 'vast'; alleen gewijzigde velden worden opgeslagen, vergeleken met wat het scherm toonde), `/beheer/rekeningschema` (lijst, toevoegen/wijzigen met koppelingen als vinkjes, opslaan als nieuw, verwijderen alleen zonder boekingen/verwijzingen en niet als standaardrekening), `/beheer/btw` (tarieven per btw-rekening met geldig-tot, regel toevoegen, gesloten), `/beheer/valuta` (toevoegen, decimalen, volgorde zonder de standaardvaluta te verschuiven, 'Standaardvaluta maken' met bevestiging, verwijderen als ongebruikt), `/beheer/koersen` (lijst per datum met filters, invoeren/wijzigen/verplaatsen/verwijderen per datum), `/beheer/talen`, `/beheer/branches`, `/beheer/betaalmethoden`, `/beheer/afsluiten` (afsluitdatum, alleen tegenboeken, audittrail aan/uit; administratie op slot met melding), `/beheer/audittrail` (filters medewerker, datum, actie, tabel, kenmerk; CSV), `/beheer/locks` (per lock of alle vrijgeven). **Boekhouder+ en module Afdelingen en projecten:** `/beheer/afdelingen`, `/beheer/projecten`. Verwijderen overal alleen als niet in gebruik (op de server); ongewijzigd opslaan laat de database ongewijzigd (getoetst op de echte administraties A en C). Bij het kiezen van een administratie wordt `fxgainloss_accno_id` rechtgezet als die ontbreekt (A8). Administratie op slot: kolommen `company.locked`/`lock_message` (app-database, migratie 3); wie geen beheerder is, gaat terug naar 'Administratie kiezen' |
| Rapporten | `/rapporten/<naam>` (`reportdefs.py`): rekeningschema, rekeningmutaties, proefbalans, W&V, balans, overzicht/openstaand verkoop en inkoop, ouderdomsanalyse (open bedrag per peildatum, afwijking 193), aanmaningen (lijst; werkscherm zie Aanmaningen), btw en onbelast, ontvangsten/betalingen. Formulier + resultaat op één pagina (GET, HTMX ververst het resultaat); doorklikken rapport → mutaties → leespagina boeking (`/boekingen/<id>`); CSV, Excel (openpyxl) en afdrukopmaak; lange lijsten per 500 regels. Laatst gebruikte instellingen per gebruiker/administratie/rapport (`report_pref`); een link met `run=1` is zelfstandig (geen persoonlijke instellingen). Alle rollen, binnen de modules die aan staan |
| Documenten (PDF) | Fase 4 stap 0 en 2 (D1–D8, DS1–DS10, DT2). `/document/<soort>/<id>.pdf` (`routes_docs.py`); soorten en bij welk scherm ze horen in `doctypes.py`: **verkoopfactuur** (factuur, pakbon, picklijst), creditfactuur, inkoopfactuur, debetnota, **verkooporder** (orderbevestiging, pakbon, picklijst), inkooporder, offerte, offerteaanvraag, verkoop/inkoop zonder artikelen (factuur, creditnota, inkoopboeking, debetnota; status-formname `transaction` zoals Perl). Een soort die niet bij het document hoort geeft 404. Knoppen **PDF** en **E-mail** op factuur-, order- en verkoop/inkoopscherm (meer soorten: keuzelijst), **Opslaan en PDF** bij een nieuw document (daarna de link "PDF openen"); na een wijziging zonder opslaan meldt het scherm "Sla eerst op" (`static/docs.js`); regel Geprint/Gemaild per soort. Veld **Sjabloonvariant** (talen uit Beheer > Talen + variantmappen; verborgen als er geen zijn), vooringevuld uit de relatie. Gegevens: `sqlledger/printdata.py` (`invoice_vars`, `order_vars` = OE::order_details, `transaction_vars` = arapprn.pl `print_transaction`; gedeelde regelopbouw, onderdeelregels van samengestelde artikelen); bedrag in woorden NL/EN/DE/FR (`num2text.py`). Opmaak: `documents.py`, Jinja-sandbox (autoescape, geen code) → WeasyPrint; alleen `data:`-URL's. Sjabloon gezocht in `<data_dir>/templates/<administratie-id>/<variant>/`, dan `<administratie-id>/`, dan de standaardset `doctemplates/standard/`. Documenttaal: variant/taal van het document (en…/nl…/de…/fr…), anders **Beheer > Instellingen > Documenttaal** (`company.doc_language`: NL/EN/DE/FR). Boekhouder+ zet `status.printed` + audittrail; alleen-lezen mag de PDF zien zonder status. Module van het scherm |
| Sjablonen | Fase 4 stap 2 (D5, DS11; `routes_tpl.py`, `documents.TemplateStore`, `templates/am/doctemplates.html`). **Beheer > Sjablonen** (`/beheer/sjablonen`, beheerder): per variant (tabbladen) alle documentsoorten en de gedeelde delen (`_base`, `_invoice`, `_order`, `_transaction`, `_parties`) met bron eigen/standaard; **downloaden** (eigen, anders de standaard als startpunt; ook vorige versies), **uploaden** (.html, UTF-8, max. 512 kB; eerst controle van de opbouw in de sandbox en een proef-PDF met het laatste document van die soort, bij een fout geweigerd met regelnummer), **terug naar standaard** (na bevestiging), laatste 5 versies **terugzetten**, **voorbeeld-PDF** (laatste document of `&nummer=`; zet geen 'geprint'), **logo** (PNG/JPEG/SVG, max. 1 MB, inhoud gecontroleerd; SVG zonder scripts of externe verwijzingen), **varianten** toevoegen (taal uit Beheer > Talen) en lege varianten verwijderen. Geen bewerken in de browser. Elke wijziging in het logboek (`doctemplate_*`) en de audittrail (tabel `templates`) |
| Huisstijl | HUISSTIJL_ANALYSE.md HS1–HS12, keuzes binnen A 1–9 (`sqlledger/docstyle.py`, `routes_style.py`, `docfonts.py`, `fonts/`, `templates/am/style.html`, `templates/am/fonts.html`, `static/style.js`, `doctemplates/standard/_base.html`). **Beheer > Huisstijl** (`/beheer/huisstijl`, bovenaan "Documenten en e-mail"; beheerder): **logo** uploaden/verwijderen (hetzelfde bestand als bij Sjablonen, meteen opgeslagen), grootte klein/middel/groot (max. 14 × 45, 22 × 70, 24 × 95 mm), ligging links/midden/rechts **naast** de bedrijfsgegevens (kop één rij van vaste hoogte, HS12); **accentkleur** (titel, lijn onder de kop, kolomkoppen, lijnen bij de totalen, rand betaalblok; kleurkiezer + hexcode + 6 snelkeuzes; contrast ≥ 4,5 : 1 tegen wit, anders niet op te slaan); **lettertype**: Standaard (systeem-Helvetica, zoals vroeger) of een van vijf meegeleverde OFL-lettertypen (Liberation Sans, Inter, Source Sans 3, Source Serif 4, Lora; onveranderd, in de PDF als subset ingesloten); **adresblok** links (venster NL/DE) of rechts (venster CH); **kop** (bedrijfsgegevens) en **voetregel** aan/uit (voorbedrukt briefpapier). Met een opgeslagen huisstijl staat het adresblok altijd op 45 mm van boven (vensterplek). **Live voorbeeld** (laatste verkoopfactuur, anders een voorbeeldfactuur in de documenttaal) in een kader met eigen CSP (`/beheer/huisstijl/voorbeeld`, niets opgeslagen of als geprint gemarkeerd), envelopvenster als stippellijn, waarschuwingen (kop te hoog, logo/voettekst niet getoond, KvK/btw-nummer op briefpapier); **PDF bekijken**; **Standaard herstellen**; waarschuwing bij verlaten. **Overnemen van …** (administraties waar de gebruiker ook beheerder is): laden → voorbeeld + balk "nog niet opgeslagen" → opslaan kopieert `style.*` en het logobestand (geen logo daar = eigen logo weg); de bron wordt alleen gelezen. Melding bij eigen sjablonen (eigen `_base` → huisstijl geldt nergens, per document met "(gebruikt de huisstijl)"); eigen sjablonen krijgen `style.set`, `style.color`, `style.font`, `style.font_face`, `style.logo_height`/`logo_width` (mm), `style.logo_pos`, `style.address_pos`, `style.show_head`/`show_footer`. Opslag in `x_doc_setting` (`style.*`, alleen wat afwijkt + `style.saved`; Documentinstellingen raakt die sleutels niet en omgekeerd); audittrail `docstyle` saved/copied, logboek `docstyle_saved`. Links vanaf Documentinstellingen en Sjablonen. **Lettertypen: licenties en makers** (`/lettertypen`, maker, copyright, bron en volledige OFL-tekst; `fonts/NOTICE.txt`). Geldt voor alle documenten van de standaardset; rapporten-PDF's blijven neutraal (HS9). NL/EN/DE/FR |
| Importeren en exporteren | FASE4_ANALYSE.md stap 7, D18–D20, keuzes 1–12 (`sqlledger/importer.py`, `routes_import.py`, `templates/am/import*.html`, `templates/am/export.html`). **Beheer > Importeren** (beheerder): soort kiezen (klanten, leveranciers, artikelen, diensten, artikelgroepen, rekeningschema, memoriaal) en een CSV- (`;` `,` of tab, aanhalingstekens met regeleinden, UTF-8 of Windows-1252) of xlsx-bestand uploaden (hoogstens 5.000 rijen); het bestand staat tijdelijk in de datamap (`import/`, alleen voor dezelfde administratie en gebruiker, na een dag weg). Kolommen worden gekoppeld op SL-veldnaam, op het label in elke schermtaal en op de bewaarde koppeling (`x_setting` `import_map.<soort>`); per kolom te wijzigen of "niet importeren", SL-velden die we niet kennen staan als "niet ondersteund". **Voorbeeld = proefrun** door dezelfde bibliotheek als de schermen in een transactie die wordt teruggedraaid: per rij nieuw / bijwerken (welke velden) / ongewijzigd / fout (waarom); vinkje per rij. **Importeren** in één transactie (alles of niets), resultaat + logboek `import`. Sleutel: relatie-/artikelnummer (leeg = volgend nummer), groepsnaam, rekeningnummer; memoriaal per referentie (ook niet-opeenvolgend), referentie die al bestaat = waarschuwing. Lege cel = veld ongewijzigd; alleen spaties eromheen = ongewijzigd; getallen en datums in de notatie van de gebruiker of 1234.56 / ISO. **Beheer > Exporteren**: dezelfde soorten (niet memoriaal) in precies het importformaat (kop = SL-veldnaam), CSV of xlsx (beheerder in het menu; klanten/leveranciers/artikelen ook voor wie die module mag zien, via het adres). NL/EN/DE/FR |
| Importeren facturen, orders, betalingen | Bouwstap 4 (BEHEER_EXTRA_ANALYSE.md §3, IM1–IM8 en IM10, alles A; herziet FASE4 D18). Vier soorten erbij in **Beheer > Importeren** (beheerder): **Verkoopfacturen** (`sales_invoice`), **Verkooporders** (`sales_order`), **Inkooporders** (`purchase_order`) en **Betalingen** (`payment`), met SL-veldnamen als kolomkop (NL/EN/DE/FR-labels werken ook). Facturen/orders: één rij per regel; rijen met hetzelfde nummer + relatie vormen één document, ook niet-opeenvolgend; een rij zonder nummer hoort bij het document erboven tenzij relatie of datum anders is, en krijgt het volgende nummer (IM2). Kopvelden uit de eerste niet-lege cel per kolom. Prijs, korting % (VW7), omschrijving, eenheid, termijn/vervaldatum, btw incl., debiteurenrekening, betaalrekening, valuta en koers zoals het scherm ze voorinvult, tenzij het bestand ze geeft; orders: leverdatum = datum + termijn zoals IM.pm. Boeken via `InvoiceLedger.post_invoice` / `OrderLedger.save_order` (IM4); `paid_1` = meteen betaald. Voorbeeld per document: valuta, netto, btw, totaal (IM5). Bestaand nummer (factuur in `ar`, order per soort) = fout, nooit vervangen (IM3); onbekende relatie of artikel = fout per document (IM6). Betalingen: één rij per betaling, koppelen op factuurnummer (+ klant-/leveranciersnummer), anders betalingskenmerk, nooit alleen op bedrag (IM7); alleen een openstaande post en niet meer dan openstaat; Credit = ontvangst, Debet = betaling; boeken via `Payments.post_payment`. Proefrun en alles of niets zoals de bestaande importer (IM8). Help verwijst voor bankbestanden naar Bankafschrift inlezen. NL/EN/DE/FR |
| Tijdkaarten (uren) | UREN_ANALYSE.md UR1–UR11, U-1 (`sqlledger/timecards.py`, `routes_jc.py`, `templates/jc/`). Menu Afdelingen en projecten > Uren: **Tijdkaarten** (lijst met filters project, medewerker, periode, status nog niet op een order / op een order; totaal uren en bedrag) en **Tijdkaart toevoegen** (boekhouder): datum, project (geen productie-opdracht), medewerker (leeg = ikzelf), dienst, omschrijving, begin- en eindtijd (geklokt, over middernacht), niet te declareren, uren, prijs, bedrag, notities; "Bijwerken" rekent zoals jc.pl (uren uit de tijden, prijs/omschrijving van de dienst), "Opslaan" rekent eerst. Uren die al op een verkooporder staan: niet lager, niet verwijderen. **Verkooporders uit uren** (U-2, boekhouder, module Orders): open uren (nog niet op een order) met filters project, medewerker, periode en "samenvatten per project, klant en dienst" (zoals pe.pl); aanvinken (project zonder klant: klant per regel kiezen) → per klant één verkooporder (prijs van de tijdkaart, project op de regel, leverdatum = datum van de uren, betalingstermijn/valuta/taal van de klant, btw van de dienst), `allocated` bijgewerkt, alles in één transactie; links naar de gemaakte orders. **Toewijzingen** (U-3, boekhouder): medewerker → project + dienst met periode, eigen prijs (anders die van de dienst) en projectleider (`x_assignment`, migratie 17; geen overlap, afsluiten met einddatum). **Mijn uren** (iedereen met de module, ook de nieuwe rol **urenschrijver**) en **Maandstaat** (boekhouder, elke medewerker): raster toegewezen project/dienst × dagen van de maand, uren per dag; opslaan raakt alleen gewijzigde dagen (één tijdkaart per dag, 0/leeg = weg), dagen met uren op een order, in een afgesloten periode of met meer tijdkaarten zijn vergrendeld; de medewerker van Mijn uren komt uit de login, nooit uit het formulier. Rol **urenschrijver** (app-db migratie 10): de server stuurt elke andere pagina door naar Mijn uren (403 bij posten). **Urenstaat (PDF)** (U-4) vanaf Mijn uren/Maandstaat, per maand of per project: documentengine (`doctemplates/standard/timesheet.html`, huisstijl, documenttaal), met de klant van het project als geadresseerde, uren per dag, totaal en een aftekenvak (naam, datum, handtekening); urenschrijver alleen de eigen staat. NL/EN/DE/FR |
| Back-up inlezen | FASE6_ANALYSE.md F1–F4, stap 6a/6b (`sqlledger/slbackup.py`, `sqlledger/slupgrade.py`, `sqlledger/slschema/upgrade/` = de 70 upgradescripts van SL 3.2.12, `routes_restore.py`, `templates/system/company_restore*.html`). **Systeem > Back-up inlezen** (`/systeem/back-up`, systeembeheerder) en dezelfde stap in de eerste start (de tegel was grijs): SL-back-up (`.sql`/`.sql.gz`) of `pg_dump` in platte SQL, tot 512 MB; regel voor regel gecontroleerd (alleen wat in een back-up hoort; eigenaar/rechten/DROP overgeslagen; COPY TO/PROGRAM, extensies, andere talen, rollen, psql-opdrachten geweigerd met regelnummer), in een nieuwe database als de gewone databasegebruiker, in één transactie (mislukt = niets); daarna bijwerken naar 3.2.4 zoals `User::dbupdate` (één transactie, overgeslagen statements getoond); een back-up van SQL-Ledger 4.x (nieuwer dan 3.2.4) geweigerd; daarna als administratie gekoppeld (mij als beheerder). Logboek `company_restored`. **v1.0.1** (PB1–PB5, PB14): twee stappen — uploaden met voortgang in % → controlescherm (soort, bedrijfsnaam, dataset, versie, datum, laatste boeking; naam/databasenaam vooringevuld; "Jij bent in deze administratie") → inlezen als achtergrondtaak (`restorejobs.py`) met voortgang per fase, venster dicht = loopt door, één tegelijk. NL/EN/DE/FR |
| Back-up maken | FASE6_ANALYSE.md 6d-1, I7 (`sqlledger/dbbackup.py`, `sqlledger_web/backups.py`, `routes_backup.py`, `templates/system/backup.html`, `cli.py` `backup`; app-db migratie 11 `company.last_backup`). **Systeem > Back-up maken** (`/systeem/back-up-maken`, systeembeheerder): per administratie de laatste back-up (datum, "nog nooit", "ouder dan 7 dagen") en twee knoppen: **Database (.sql.gz)** (`pg_dump` in platte SQL zonder eigenaar en rechten, gzip, met een eigen kop: administratie, versie, datum) en, als er eigen sjablonen of een logo zijn, **Sjablonen en logo (.zip)** (alles uit `<data_dir>/templates/<id>/`). Gemaakt in een tijdelijke map, na het downloaden weg; niets blijft op de server. Mislukt `pg_dump` (niet gevonden, geen verbinding, onvolledig), dan een melding en logboek `backup_failed`; gelukt = logboek `backup_made` (soort, bestand, grootte, versie) en `company.last_backup`. **Back-up inlezen** herkent deze back-up (soort `app`) en neemt optioneel als tweede bestand de zip met sjablonen: eerst helemaal gecontroleerd (zelfde controles als Beheer > Sjablonen en Huisstijl, vorige versies niet), pas na het inlezen van de database bij de nieuwe administratie gezet; fout in de zip = niets aangemaakt. Nieuwere `pg_dump`-versies zetten `\restrict`/`\unrestrict` om de dump: die regels slaat de lezer over. **Dashboard**, blok Te doen: beheerder en systeembeheerder zien "Nog geen back-up gemaakt met deze app" of "Laatste back-up is van …; maak een nieuwe" als de laatste ouder is dan 7 dagen (link alleen voor de systeembeheerder). Opdrachtregel `sqlledger-web backup` zie Gebruik. Docker-image met `postgresql-client`. **v1.0.1** (PB6): knop uit + "Bezig met maken…" tot de download er is (`static/backup.js`). NL/EN/DE/FR |
| Overstap uit SQL-Ledger | FASE6_ANALYSE.md 6d-2, I4–I6, I8 (`sqlledger/switchover.py`, `Dashboard.switch_list/switch_mark/next_numbers`, `routes_switch.py`, `templates/am/switch*.html`, `templates/dashboard/_switchlist.html`). Bij **elk inlezen** van een back-up (Systeem > Back-up inlezen en de wizard) legt de app de herkomst vast (`x_setting` `switch_list`: soort, dataset, versie, datum van de back-up, databaseversie vóór/na bijwerken, overgeslagen opdrachten, afsluitdatum en laatste boekdatum op dat moment) en zet daarmee de lijst **Overstap uit SQL-Ledger** aan: op het dashboard op de plek van de startlijst (alleen beheerder), met **Beheer > Ingebruikname** (`/beheer/ingebruikname`) als volledige pagina met uitleg per punt. Punten: back-up ingelezen (altijd klaar), cijfers controleren met het controleblad (zelf afvinken: "gedaan"), bedrijfsgegevens, btw-rubrieken + tijdvak + afdrachtrekening (klaar als elke open btw-rekening een rubriek of "geen aangifte" heeft, of tijdvak "geen"), IBAN + eerste bankafschrift (klaar als het eerste afschrift per rekening zonder saldowaarschuwing is ingelezen), huisstijl, nummering (laatst gebruikte nummers getoond; zelf afvinken), iedereen 2FA (app-db), back-up (niet ouder dan 7 dagen), afsluitdatum (later dan bij het inlezen) en RGS-codes (alleen als er een RGS-lijst is). Niet-automatische punten "niet nodig"; de lijst kan van het dashboard en weer terug. Een gekoppelde database: knop **Overstaplijst aanzetten**. **Controleblad** (`/beheer/ingebruikname/controleblad?datum=…`, scherm, afdrukken, PDF): peildatum standaard de laatste boekdatum bij het inlezen; **controles** (boekingen niet in evenwicht, 1-centverschillen bij vreemde valuta apart als informatie, regels op een niet-bestaande rekening, regels zonder boeking, meer betaald dan het factuurbedrag, factuur sluit niet aan op de debiteuren-/crediteurenrekening, overgeslagen opdrachten bij het bijwerken) met per bevinding de eerste 50 boekingen (doorklikbaar); aantal boekingen per jaar; kas en bank op de peildatum; proefbalans en winst en verlies van het boekjaar t/m de peildatum, balans op de peildatum (dezelfde rapportdefinities als Rapporten) en openstaande verkoop- en inkoopfacturen per relatie (aantal, openstaand, waarvan vervallen; standaardvaluta) met dezelfde query als het dashboard (per factuur gelijk aan de ouderdomsanalyse, maar 0,02 s in plaats van 15 s bij administratie A). Logboek `switch_list_enabled`. **v1.0.1**: controleblad met uitleg in 3 stappen en "Cijfers kloppen – akkoord" / "Er klopt iets niet" (PB8); balkje "Overstap: … – Terug naar de overstaplijst" (PB10, `switchbar.py`); punt "Terugkerende boekingen nakijken" (PB16). NL/EN/DE/FR |
| Tijdkaarten (uren) | UREN_ANALYSE.md UR1–UR11, U-1 (`sqlledger/timecards.py`, `routes_jc.py`, `templates/jc/`). Menu Afdelingen en projecten > Uren: **Tijdkaarten** (lijst met filters project, medewerker, periode, status nog niet op een order / op een order; totaal uren en bedrag) en **Tijdkaart toevoegen** (boekhouder): datum, project (geen productie-opdracht), medewerker (leeg = ikzelf), dienst, omschrijving, begin- en eindtijd (geklokt, over middernacht), niet te declareren, uren, prijs, bedrag, notities; "Bijwerken" rekent zoals jc.pl (uren uit de tijden, prijs/omschrijving van de dienst), "Opslaan" rekent eerst. Uren die al op een verkooporder staan: niet lager, niet verwijderen. **Verkooporders uit uren** (U-2, boekhouder, module Orders): open uren (nog niet op een order) met filters project, medewerker, periode en "samenvatten per project, klant en dienst" (zoals pe.pl); aanvinken (project zonder klant: klant per regel kiezen) → per klant één verkooporder (prijs van de tijdkaart, project op de regel, leverdatum = datum van de uren, betalingstermijn/valuta/taal van de klant, btw van de dienst), `allocated` bijgewerkt, alles in één transactie; links naar de gemaakte orders. **Toewijzingen** (U-3, boekhouder): medewerker → project + dienst met periode, eigen prijs (anders die van de dienst) en projectleider (`x_assignment`, migratie 17; geen overlap, afsluiten met einddatum). **Mijn uren** (iedereen met de module, ook de nieuwe rol **urenschrijver**) en **Maandstaat** (boekhouder, elke medewerker): raster toegewezen project/dienst × dagen van de maand, uren per dag; opslaan raakt alleen gewijzigde dagen (één tijdkaart per dag, 0/leeg = weg), dagen met uren op een order, in een afgesloten periode of met meer tijdkaarten zijn vergrendeld; de medewerker van Mijn uren komt uit de login, nooit uit het formulier. Rol **urenschrijver** (app-db migratie 10): de server stuurt elke andere pagina door naar Mijn uren (403 bij posten). **Urenstaat (PDF)** (U-4) vanaf Mijn uren/Maandstaat, per maand of per project: documentengine (`doctemplates/standard/timesheet.html`, huisstijl, documenttaal), met de klant van het project als geadresseerde, uren per dag, totaal en een aftekenvak (naam, datum, handtekening); urenschrijver alleen de eigen staat. **v1.1 interne uren** (UREN_ANALYSE.md IN1–IN7, migratie 18): op het projectscherm **Klantproject** (klant verplicht) of **Intern project** (kostenplaats verplicht, ook meteen een nieuwe; `x_project`), projectlijst met soort en kostenplaats (bestaand zonder klant = "niet ingedeeld", werkt zoals voorheen); **Interne tarieven** (boekhouder: intern uurtarief per medewerker met geldig-vanaf, `x_employee_rate`), **intern tarief** op de toewijzing; elke tijdkaart krijgt bij opslaan een vast intern tarief (toewijzing > medewerker > kostprijs dienst, `x_jcitem_cost`; zichtbaar op de tijdkaart); label "intern" op Mijn uren/Maandstaat en Tijdkaarten; Verkooporders uit uren neemt interne projecten nooit mee (ook op de server); rapport **Uren per project** (`/rapporten/uren-per-project`: filters soort, project, kostenplaats, klant, medewerker, periode; per project of per project en medewerker; uren, gem. intern tarief, kostwaarde, verkoopwaarde, uren op/nog niet op order, marge; CSV/Excel/PDF); geen boeking. NL/EN/DE/FR |
| Back-up inlezen | FASE6_ANALYSE.md F1–F4, stap 6a/6b (`sqlledger/slbackup.py`, `sqlledger/slupgrade.py`, `sqlledger/slschema/upgrade/` = de 70 upgradescripts van SL 3.2.12, `routes_restore.py`, `templates/system/company_restore*.html`). **Systeem > Back-up inlezen** (`/systeem/back-up`, systeembeheerder) en dezelfde stap in de eerste start (de tegel was grijs): SL-back-up (`.sql`/`.sql.gz`) of `pg_dump` in platte SQL, tot 512 MB; regel voor regel gecontroleerd (alleen wat in een back-up hoort; eigenaar/rechten/DROP overgeslagen; COPY TO/PROGRAM, extensies, andere talen, rollen, psql-opdrachten geweigerd met regelnummer), in een nieuwe database als de gewone databasegebruiker, in één transactie (mislukt = niets); daarna bijwerken naar 3.2.4 zoals `User::dbupdate` (één transactie, overgeslagen statements getoond); een back-up van SQL-Ledger 4.x (nieuwer dan 3.2.4) geweigerd; daarna als administratie gekoppeld (mij als beheerder). Logboek `company_restored`. NL/EN/DE/FR |
| Back-up maken | FASE6_ANALYSE.md 6d-1, I7 (`sqlledger/dbbackup.py`, `sqlledger_web/backups.py`, `routes_backup.py`, `templates/system/backup.html`, `cli.py` `backup`; app-db migratie 11 `company.last_backup`). **Systeem > Back-up maken** (`/systeem/back-up-maken`, systeembeheerder): per administratie de laatste back-up (datum, "nog nooit", "ouder dan 7 dagen") en twee knoppen: **Database (.sql.gz)** (`pg_dump` in platte SQL zonder eigenaar en rechten, gzip, met een eigen kop: administratie, versie, datum) en, als er eigen sjablonen of een logo zijn, **Sjablonen en logo (.zip)** (alles uit `<data_dir>/templates/<id>/`). Gemaakt in een tijdelijke map, na het downloaden weg; niets blijft op de server. Mislukt `pg_dump` (niet gevonden, geen verbinding, onvolledig), dan een melding en logboek `backup_failed`; gelukt = logboek `backup_made` (soort, bestand, grootte, versie) en `company.last_backup`. **Back-up inlezen** herkent deze back-up (soort `app`) en neemt optioneel als tweede bestand de zip met sjablonen: eerst helemaal gecontroleerd (zelfde controles als Beheer > Sjablonen en Huisstijl, vorige versies niet), pas na het inlezen van de database bij de nieuwe administratie gezet; fout in de zip = niets aangemaakt. Nieuwere `pg_dump`-versies zetten `\restrict`/`\unrestrict` om de dump: die regels slaat de lezer over. **Dashboard**, blok Te doen: beheerder en systeembeheerder zien "Nog geen back-up gemaakt met deze app" of "Laatste back-up is van …; maak een nieuwe" als de laatste ouder is dan 7 dagen (link alleen voor de systeembeheerder). Opdrachtregel `sqlledger-web backup` zie Gebruik. Docker-image met `postgresql-client`. NL/EN/DE/FR |
| Overstap uit SQL-Ledger | FASE6_ANALYSE.md 6d-2, I4–I6, I8 (`sqlledger/switchover.py`, `Dashboard.switch_list/switch_mark/next_numbers`, `routes_switch.py`, `templates/am/switch*.html`, `templates/dashboard/_switchlist.html`). Bij **elk inlezen** van een back-up (Systeem > Back-up inlezen en de wizard) legt de app de herkomst vast (`x_setting` `switch_list`: soort, dataset, versie, datum van de back-up, databaseversie vóór/na bijwerken, overgeslagen opdrachten, afsluitdatum en laatste boekdatum op dat moment) en zet daarmee de lijst **Overstap uit SQL-Ledger** aan: op het dashboard op de plek van de startlijst (alleen beheerder), met **Beheer > Ingebruikname** (`/beheer/ingebruikname`) als volledige pagina met uitleg per punt. Punten: back-up ingelezen (altijd klaar), cijfers controleren met het controleblad (zelf afvinken: "gedaan"), bedrijfsgegevens, btw-rubrieken + tijdvak + afdrachtrekening (klaar als elke open btw-rekening een rubriek of "geen aangifte" heeft, of tijdvak "geen"), IBAN + eerste bankafschrift (klaar als het eerste afschrift per rekening zonder saldowaarschuwing is ingelezen), huisstijl, nummering (laatst gebruikte nummers getoond; zelf afvinken), iedereen 2FA (app-db), back-up (niet ouder dan 7 dagen), afsluitdatum (later dan bij het inlezen) en RGS-codes (alleen als er een RGS-lijst is). Niet-automatische punten "niet nodig"; de lijst kan van het dashboard en weer terug. Een gekoppelde database: knop **Overstaplijst aanzetten**. **Controleblad** (`/beheer/ingebruikname/controleblad?datum=…`, scherm, afdrukken, PDF): peildatum standaard de laatste boekdatum bij het inlezen; **controles** (boekingen niet in evenwicht, 1-centverschillen bij vreemde valuta apart als informatie, regels op een niet-bestaande rekening, regels zonder boeking, meer betaald dan het factuurbedrag, factuur sluit niet aan op de debiteuren-/crediteurenrekening, overgeslagen opdrachten bij het bijwerken) met per bevinding de eerste 50 boekingen (doorklikbaar); aantal boekingen per jaar; kas en bank op de peildatum; proefbalans en winst en verlies van het boekjaar t/m de peildatum, balans op de peildatum (dezelfde rapportdefinities als Rapporten) en openstaande verkoop- en inkoopfacturen per relatie (aantal, openstaand, waarvan vervallen; standaardvaluta) met dezelfde query als het dashboard (per factuur gelijk aan de ouderdomsanalyse, maar 0,02 s in plaats van 15 s bij administratie A). Logboek `switch_list_enabled`. NL/EN/DE/FR |
| Documentinstellingen | Fase 4 stap 3 (DI1–DI4; `sqlledger/docsettings.py`, `routes_docsettings.py`, `templates/am/docsettings.html`, `doctemplates/standard/_lines.html`). **Beheer > Documentinstellingen** (`/beheer/documentinstellingen`, beheerder), per administratie in de tabel `x_doc_setting` (migratie 8; alleen wat afwijkt van de standaard, niet in `defaults` omdat SQL-Ledger daar onbekende sleutels wist): **btw-nummer** (in de voet); **bank**: rekening van de boeking (zoals Perl) of één vaste rekening met bankgegevens (IBAN, BIC en kenmerksjabloon van die rekening), banknaam bij het IBAN, betaalzin aan/uit; **teksten per documenttaal** (NL/EN/DE/FR; leeg = standaard): retouradresregel aan/uit of eigen tekst, voettekst, per documentsoort slottekst en opmerking onder de totalen, met lichte opmaak `**vet**`, `*cursief*`, `[tekst](https://…)` (ge-escaped, alleen http(s)/mailto); **kolommen** per groep (verkoopfacturen, inkoopfacturen, orders/offertes, pakbon, picklijst): per veld kolom / kolom als ingevuld / regel onder de omschrijving / niet tonen, eigen naam per taal (kolomkop en voorvoegsel), velden artikelnummer, EAN, goederencode, land van herkomst, project, leverdatum, serienummer, verpakking, locatie, eenheid, korting, nettoprijs, gewicht, nettogewicht (omschrijving, aantal, prijs, bedrag altijd); meer dan 8 kolommen = compacte tabel; **gewichtsoverzicht** onder de totalen; **voorbeeld-PDF** per soort in de gekozen taal. De standaardset leest alles uit `doc` (`documents.doc_context`); eigen geüploade sjablonen kunnen hetzelfde gebruiken (`doc.taxnumber`, `doc.columns`, `doc.notes`, `doc.closing`, `doc.note`, `doc.footer`, `doc.return_text`, `doc.bankname`, `doc.weights`, `doc.payinfo`) of de gedeelde macro's in `_lines.html`. Geldt voor PDF, mailbijlage en voorbeeld. Opslaan: audittrail (`x_doc_setting`) + logboek `docsettings_saved`. `compare/voorbeeld_pdf.py --instellingen <json>` probeert een instelling zonder iets op te slaan |
| Mailen | Fase 4 stap 1 (D8, D9; `mailer.py`, `routes_mail.py`, `templates/mail/`). **Beheer > E-mail** (`/beheer/email`, beheerder): verzenden via SMTP (STARTTLS, SSL of zonder versleuteling; inloggen zonder versleuteling alleen naar een server op dezelfde computer), uit, of **map** (testmodus: `.eml` in `<data_dir>/mail/<administratie-id>/`, niets verstuurd); afzendernaam/-adres, antwoordadres; wachtwoord versleuteld (Fernet, sleutel `secret_key` of `<data_dir>/secret.key`), nooit getoond; standaardonderwerp en -bericht per documentsoort (alle 14) en documenttaal (NL/EN/DE/FR) met plaatshouders `{nummer}`, `{datum}`, `{vervaldatum}`, `{bedrag}`, `{valuta}`, `{relatie}`, `{bedrijf}`, `{kenmerk}`, `{ordernummer}`, `{referentie}` (Engelse namen ook); alleen afwijkende teksten worden bewaard; **Testmail naar mijzelf**. **Mailscherm** (`/document/<soort>/<id>/mail`, knop **E-mail** op het factuurscherm, boekhouder+): aan/cc/bcc uit de relatie (meerdere adressen met komma), onderwerp en bericht uit de standaardtekst + handtekening van de gebruiker (Mijn instellingen), PDF als bijlage (voorbeeldlink zet geen 'geprint'). Na verzenden: `status.emailed`, notitie `[email]` onder de interne notities, audittrail `emailed`, logboek (`document_emailed`, mislukt: `document_email_failed`); een fout laat niets achter en houdt de ingevulde velden. Onder de knoppen van het factuurscherm: Geprint/Gemaild ja/nee |
| Aanmaningen en rekeningoverzichten | Fase 4 stap 4a (D13, SB1–SB6, SB15; `sqlledger/dunning.py`, `routes_dunning.py`, `templates/dunning/`, `doctemplates/standard/_reminder.html`, `reminder1..3.html`, `statement.html`). **Verkoop > Aanmaningen** (`/aanmaningen`): peildatum, klant, klantnummer, minimaal dagen vervallen, afdeling; per klant en valuta een blok met de vervallen facturen (vinkje, dagen, openstaand, niveau nu, wordt) en de te verrekenen negatieve posten; **Voorbeeld** (PDF, legt niets vast, alle rollen), **PDF** (alle aangevinkte brieven als één PDF; legt per factuur het niveau vast met "geprint"), **Mailen** (overzicht wie gemaild wordt en wie afvalt zonder e-mailadres → per klant een mail met de brief als PDF, tekst uit Beheer > E-mail in de taal van de klant; vastleggen alleen na verzending), **Niveau opslaan** (0–3 per factuur); de lijst `/rapporten/aanmaningen` (CSV/Excel) blijft. **Verkoop > Rekeningoverzichten** (`/rekeningoverzichten`): peildatum, filters, ouderdomskolommen, vanaf vervaldatum; per klant en valuta een regel met vinkje; Voorbeeld/PDF/Mailen; vastleggen = audittrail. Maximaal 200 mails per keer. Teksten per niveau in Documentinstellingen (inleidende tekst, slottekst, opmerking), sjablonen in Beheer > Sjablonen (`_reminder` gedeeld). Boekhouder en hoger voor PDF/mailen/niveau |
| Betalingsspecificatie en betalingsregister | Fase 4 stap 4b (D15, SB7–SB8, SB15; `sqlledger/paymentspec.py`, `routes_payspec.py`, `templates/cp/payregister.html`, `templates/cp/_specs.html`, `doctemplates/standard/_payment.html`, `receipt.html`, `check.html`). Document **betalingsspecificatie** per geboekte betaling per relatie: `receipt` bij een ontvangst (klant), `check` bij een betaling (leverancier); per factuur nummer, datum, factuurbedrag, korting en betaald, de vooruitbetaling als eigen regel, totaal, omschrijving. Een betaling = relatie + betaalrekening + datum + kenmerk (zoals Corrigeren). **Kas en bank > Betalingsregister** (`/betalingsregister`): soort (ontvangsten/betalingen), betaalrekening, relatie, kenmerk van/tot, periode (standaard vorige en lopende maand); per betaling een vinkje; **Voorbeeld** (legt niets vast, alle rollen met de module), **PDF** (aangevinkte specificaties als één PDF) en **Mailen** (per betaling een mail aan de relatie, overzicht vooraf, maximaal 200) voor boekhouder en hoger. Dezelfde knoppen in de melding na het boeken (één relatie direct; meerdere relaties met per relatie een vinkje) en op Ontvangsten/Betalingen corrigeren. PDF en Mailen schrijven een audittrailregel per factuur (`receipt`/`check`, printed/emailed); geen statusregel. Teksten in Documentinstellingen (inleidende tekst, slottekst, opmerking), mailtekst in Beheer > E-mail, sjablonen in Beheer > Sjablonen (`_payment` gedeeld) |
| Batch | Fase 4 stap 4c (D10, SB9–SB11, SB14, SB15; `sqlledger/batch.py`, `routes_batch.py`, `templates/batch/batch.html`). **Documenten > Batch** (`/documenten/batch`, zichtbaar als verkoop, inkoop, orders of offertes aan staat): soort (alle 14 documentsoorten; pakbon en picklijst uit verkoopfacturen én verkooporders), datum van/tot, relatie (naam of nummer), nummer, status (alle/open/gesloten), geprint en gemaild (alle/ja/nee; eerste keer: vorige en lopende maand, niet geprint en niet gemaild). Lijst (maximaal 1.000 regels) met vinkjes, "alles selecteren", status geprint/gemaild en e-mailadres; **Voorbeeld** (één samengevoegde PDF, legt niets vast, alle rollen met de module), **PDF** (één samengevoegde PDF; daarna per document geprint + audittrail) en **Mailen** (overzicht vooraf met wie afvalt zonder e-mailadres; onderwerp en bericht optioneel één keer voor de hele reeks, met plaatshouders per document; per document een eigen mail; daarna gemaild, notitie `[email]` en audittrail) voor boekhouder en hoger. Maximaal 200 documenten per keer; één fout stopt de rest niet |
| Inkoopfactuur uploaden en bijlagen | Fase 4 stap 4e (IU1–IU8, D17; `sqlledger/apupload.py`, `sqlledger/attachments.py`, `routes_apupload.py`, `routes_attach.py`, `uploads.py`, `templates/arap/upload.html`, `templates/_attach.html`, `static/upload.js`, `static/attach.js`). **Inkoop > Inkoopfactuur uploaden** (`/inkoop/uploaden`, boekhouder+, module Inkoop): een PDF of foto (pdf/jpg/png, gecontroleerd op inhoud, tot de maximale uploadgrootte: standaard 10 MB, stap 4f) slepen of kiezen; daarna staat hij naast het formulier (vanaf 1000 px breed, op de telefoon eronder). Invullen: leverancier, factuurdatum, factuurnummer (verplicht) en totaalbedrag inclusief btw. Voorgesteld en aan te passen: kostenrekening (vaste kostenrekening van de leverancier, anders die van de laatste boeking zonder artikelen; meer kostenregels → de grootste met melding), btw-rekening (die van de laatste boeking voor zover nog gekoppeld, anders de gekoppelde; bij twee zonder historie kiezen; "geen btw"), btw-bedrag en netto live van de server (netto = totaal / (1 + tarief); een ander btw-bedrag van de factuur invullen kan, het netto past zich aan), vervaldatum uit de termijn, crediteurenrekening, valuta (+ koers), afdeling van de laatste boeking, omschrijving. **Boeken** boekt via het gewone inkoopformulier (`routes_arap.do_post`, zelfde controles, waarschuwing bij een dubbel factuurnummer) met het bestand als bijlage en geeft een leeg scherm met een link naar de boeking; **Volledig scherm** opent het gewone inkoopscherm met alles ingevuld en het bestand als nog niet opgeslagen bijlage. Bestand vervangen of weghalen met "ander bestand". **Vaste kostenrekening** per leverancier op het leveranciersscherm (eigen tabel `x_vendor_default`, migratie 9). **Bijlagen** op het verkoop-, inkoop- en memoriaalscherm (`reference`/`archive`/`archivedata` met de codering van SL): bij een opgeslagen boeking een blok onder het formulier met bekijken (pdf/jpg/png in een nieuw tabblad), downloaden, verwijderen (met bevestiging) en toevoegen (slepen of kiezen, omschrijving en vertrouwelijk optioneel), meteen en zonder de boeking opnieuw te boeken; bij een nieuwe boeking gaat een gesleept bestand mee met Opslaan (tot dan tijdelijk in `<data_dir>/uploads`, na een dag opgeruimd). `/bijlage/<id>` en `/bijlage/tijdelijk/<token>` serveren het bestand met een eigen CSP (alleen in een frame van de app); andere bestandssoorten (uit SL) alleen als download. Referenties zonder bestand (zoals SL) staan ingeklapt onder "Referentiedocumenten" |
| Bijlagen op andere schermen | Fase 4 stap 6a (RB1, RB2, RB6, RB7). Het blok **Bijlagen** (zoals 4e) ook op **facturen met artikelen** (verkoop/inkoop, ook credit/debet), **orders en offertes** (verkooporder, inkooporder, offerte, offerteaanvraag), **klanten en leveranciers** en **artikelen** (artikel, dienst, samengesteld, loon/overhead, kit). Factuur en order/offerte: bij een nieuwe ook slepen, het bestand gaat mee met Opslaan; relatie en artikel: na het opslaan ("Bijlagen kun je toevoegen nadat je hebt opgeslagen."). Omzetten offerte → order en order/offerte → factuur neemt de bijlagen mee als verwijzing naar hetzelfde archiefstuk (lijst "gaat mee bij Opslaan", weghalen kan); kopiëren en "Opslaan als nieuw" nemen niets mee. Onder elke bijlage "ook bij …" met een link als hetzelfde archiefstuk aan een ander record hangt. Toevoegen/verwijderen: boekhouder+ (ook bij een gesloten order of in een afgesloten periode); alleen-lezen bekijkt en downloadt; de module van het scherm moet aan staan. Formname zoals SQL-Ledger: `ar_invoice`/`ar_credit_invoice`/`ap_invoice`/`ap_debit_invoice`, het ordertype, `customer`/`vendor`, het soort artikel. |
| Foto's verkleinen en dubbele bestanden | Fase 4 stap 6b (RB4, RB5; `sqlledger/images.py`, `attachments.prepare/duplicates/ensure_hashes`, migratie 11 `x_archive_hash`, `templates/attach_confirm.html`). Elke bijlage (blok Bijlagen, nieuwe boeking, uploadscherm): jpg/png met de lange kant boven 2.400 px verkleind tot 2.400 px (jpg kwaliteit 85), eerst rechtop gezet volgens de EXIF-oriëntatie, **alle metadata weg** (EXIF met GPS en toestel, XMP, IPTC, commentaar, png-tekst; kleurprofiel blijft); kleine foto's zonder opnieuw comprimeren opgeschoond; PDF nooit aangeraakt; meer dan 120 miljoen beeldpunten geweigerd. Het tijdelijke bestand is al de verkleinde versie (voorbeeld klopt). Per archiefstuk sha256 (in `archive.hash` als die kolom er is, SQL-Ledger 4.x, anders `x_archive_hash`); oude stukken krijgen hem bij de eerste controle. Hangt hetzelfde bestand al bij een andere boeking/order/relatie/artikel: waarschuwing "Dit bestand hangt al bij: Inkoopboeking 2026-0815 (leverancier Y, EUR 121,00)" met link (vertrouwelijk van een ander: zonder plek; los SL-document: zo genoemd). Opgeslagen record: bevestigingspagina "Ja, toch toevoegen" (bestand blijft tijdelijk staan); nieuwe boeking en uploadscherm: vinkje "toch toevoegen", anders weigert Opslaan/Boeken. Na bevestigen een **nieuw** archiefstuk. Hetzelfde bestand twee keer bij hetzelfde record (ook twee keer slepen): geweigerd. |
| Overzicht bijlagen | Fase 4 stap 6c (RB3; `Attachments.overview/delete_loose`, migratie 12 `x_archive_added`, `routes_attach.overview`, `templates/attach_overview.html`). **Rapporten > Bijlagen** (`/bijlagen`, module Rapporten): alle bijlagen die je mag zien, nieuwste eerst: toegevoegd (datum, tijd, gebruiker; vastgelegd vanaf stap 6c), bestand (bekijken, vertrouwelijk, omschrijving, downloaden), gekoppeld aan (soort, nummer, naam, met link naar boeking/order/relatie/artikel). Zoeken op omschrijving of bestandsnaam, soort (verkoop, inkoop, memoriaal, orders, offertes, klanten, leveranciers, artikelen, los document, overig uit SQL-Ledger), gekoppeld aan (nummer of naam) en periode toegevoegd; hoogstens 1.000 regels. Bijlagen bij een module die uit staat en vertrouwelijke van een ander blijven weg. Losse archiefstukken zonder koppeling (SQL-Ledger "Document toevoegen") staan erin en zijn te openen; weggooien alleen door de beheerder. Geen los toevoegen en geen "Koppelen aan" (slepen op het scherm volstaat). |
| Rapporten en lijsten als PDF | Fase 4 stap 5 (D14, RP1–RP10; `reportpdf.py`, `templates/reports/pdf.html`). Knop **PDF** naast CSV/Excel op alle rapporten (`/rapporten/<naam>?…&export=pdf`), de lijsten klanten/leveranciers, artikelen, orders/offertes/aanvragen, afgeletterde posten, bankafschrift, assembleren/stuklijsten/productiehistorie/waar gebruikt en de leespagina van een boeking; opent in een nieuw tabblad. Zelfde tabel als scherm/CSV/Excel (`cell_text`), dus dezelfde bedragen, in taal en notatie van de gebruiker; **alle** regels (niet alleen de schermpagina) tot 5.000 (`MAX_ROWS`), daarboven een melding met link terug. Kop op elke pagina (titel, periode/filters, administratie), kolomkoppen herhaald, voet "Afgedrukt op" + "Pagina x / y"; staand of liggend en lettergrootte (8 → min. 6,5 pt) uit de breedte van de inhoud (`choose_layout`). Geen links, niets bewaard; logboek `report_exported` met `format=pdf`. Rechten als het scherm (ook alleen-lezen). Knop Afdrukken (browser) blijft. |
| Grafiek bij Winst en verlies | v1.1 (GR1–GR5 = A; `plchart.py`, `templates/reports/_plchart.html`, `static/plchart.js`). Boven het resultaat van **Rapporten > Winst en verlies** de keuze **Tabel / Grafiek / Beide** (links met `weergave=`; per gebruiker en administratie onthouden in `report_pref`, regel `winst-en-verlies:weergave`; standaard Tabel). De grafiek volgt de kolommen: per maand, per kwartaal of één groep bij "Geheel"; per periode staven **opbrengsten** en **kosten** naast elkaar en een **lijn resultaat**; met "Vorig jaar ernaast" een lichtere staaf vorig jaar naast elke staaf en een stippellijn resultaat vorig jaar. Grafiekteken in de tabel bij elke rekening, kop (groep) en rubriek (bij Beide) en een keuzelijst "Toon in grafiek" bij de grafiek: dan staven van alleen die rekening/groep (`grafiek=<rekening>`, `I`/`E`), met "terug naar opbrengsten en kosten". Zelfde bibliotheekrapport als de tabel, dus dezelfde filters (afdeling, project, valuta, stelsel) en bedragen. Server-side SVG (geen JS-bibliotheek): ook in de **PDF** (boven de tabel, of alleen de grafiek bij Grafiek) en bij **Afdrukken**; `<title>`/`<desc>`, legenda in tekst, tooltip per staaf (`<title>`, met JS direct en bij aanraken) en "Cijfers bij de grafiek" als tabel; licht/donker via CSS-variabelen. Vaste breedte per periode: op een smal scherm scrolt de grafiek binnen zijn kader. Geen grafiek bij de Balans (GR5). NL/EN/DE/FR |
| Dashboard | Fase 4 stap 5b (DB1–DB13; `sqlledger/dashboard.py`, `routes_dashboard.py`, `templates/dashboard/`). Startscherm `/` na inloggen/kiezen van de administratie, menu-item **Dashboard** bovenaan; de vroegere startgegevens onder "Over deze administratie". Pagina meteen, elk blok los via HTMX (`/dashboard/blok/<naam>`): **tegels** (bank en kas, te ontvangen, te betalen, resultaat t/m vandaag vs vorig jaar; klik → balans/ouderdomsanalyse/W&V), **bank en kas** per betaalrekening in de balans-activa (saldo = balans per vandaag, afschrift t/m, open bankregels; rekeningen zonder saldo/regels/mutaties in 365 dagen verborgen), **te ontvangen/te betalen** (SVG-staafgrafiek per ouderdom na de vervaldatum: niet vervallen, 1–30, 31–60, > 60), **resultaat** (omzet/kosten/resultaat dit jaar en deze maand vs vorig jaar = `Reports.income_statement`, grafiek per maand, klik → W&V van die maand), **te doen** (herinnering btw-aangifte van het laatst afgesloten tijdvak met Afgehandeld, vervalt na de termijn, bankregels, vervallen verkoopfacturen, inkoopfacturen vervallen/binnen 7 dagen, terugkerende boekingen, artikelen onder bestelniveau, verkooporders met verstreken leverdatum). Blokken volgen modules; alleen-lezen ziet getallen, geen actieknoppen of Afgehandeld. Geen boekingen in het lopende jaar: resultaat over het laatste jaar met boekingen (melding). Beheer > Instellingen: **Btw-aangifte per** kwartaal/maand/jaar/geen (`x_setting`, migratie 10; afgevinkt in `x_task_done`). NL/EN/DE/FR. |
| Btw-aangifte | BTW_ANALYSE.md BA1–BA9 (`sqlledger/vatreturn.py`, `routes_vat.py`, `templates/vat/`, `static/vatform.css`; migratie 13 `x_vat_box`, `x_vat_customer_box`, `x_vat_return`; app-db migratie 7 `company.vat_return`). **Btw > Btw-rubrieken** (beheerder): per btw-rekening de rubriek van de Nederlandse aangifte (1A–1E, 2A, 3A–3C, 4A, 4B, 5B of niet in de aangifte; voorstel uit naam en tarief), afdrachtrekening en rekening afrondingsverschillen (voorstel uit de omschrijving). **Btw > Btw-aangifte** (alleen als er rubrieken zijn en het tijdvak niet "geen" is): tijdvak volgens Beheer > Instellingen (laatste 12) of een andere periode; overzicht per rubriek in hele euro's afgerond in uw voordeel; klik op een rubriek → de boekingen met exacte bedragen; meldingen (omzet zonder btw, btw past niet bij netto, niet-gekoppelde btw-rekeningen, afdrachtboekingen niet meegeteld); Excel (met exacte bedragen per rekening en rubriek) en PDF. **Aangifteformulier** (`/btw/aangifte/formulier`): opmaak Belastingdienst (1a–5g, 5e/5f, te betalen/terug te vragen), "Proefafdruk" tot de btw-boeking, daarna de vastgelegde bedragen; PDF op één A4. **Btw-boeking maken** (boekhouder+, module Grootboek): opent het memoriaal ingevuld (btw-rekeningen tegen, 5G op de afdrachtrekening, centen op afrondingsverschillen, datum einde tijdvak); na Opslaan is het tijdvak vastgelegd en de dashboardtaak afgehandeld; wijkt de boekhouding later af, dan een melding met het verschil per rubriek en de boekingen die het veroorzaken. Klantscherm: **btw-rubriek omzet zonder btw** (1E/3A/3B). Dashboardtaak btw linkt naar de aangifte. NL/EN/DE/FR |
| Btw-aangifte België | BTW_LANDEN_ANALYSE.md BE1–BE6 (`sqlledger/vatforms.py` `BE`, `sqlledger/vatlisting.py`, `templates/vat/_form_be.html`, `listing.html`; migratie 21 `x_vat_account_box`). *Aangifte volgens* België: periodieke aangifte kader II–VI in hele euro's; verkoop-btw op 01/02/03 (btw 54), aftrek 59, verlegde paren 86/88 (55), 87 (56), 57; omzet zonder btw via de klant (47 standaard, 44/45/46/00); inkoop per kosten-/activarekening in 81/82/83 (Btw-rubrieken); creditnota's in 48/49, 84/85, 63, 64; formulier in nl of fr met PDF; teruggaafmelding bij 72; btw-boeking op 451900; termijn 20e/25e. **Jaarlijkse klantenlisting** (`/btw/klantenlisting`, drempel > € 250, mod-97-controle, PDF/Excel). Schema's `be-mar`/`leeg-be` met verlegde paren. NL/EN/DE/FR |
| ICP-opgaaf | ICP_ANALYSE.md ICP1–ICP11 (`sqlledger/icp.py`, `routes_icp.py`, `templates/vat/icp.html`; migratie 14 `x_icp_customer`, `x_icp_return`; app-db migratie 8 `company.icp`). **Btw > ICP-opgaaf** (zodra een btw-rekening of een klant op rubriek 3B staat): precies de verkoopboekingen uit rubriek 3B van de aangifte, per afnemer (btw-nummer; zelfde nummer bij meer klanten samengevoegd) landcode, nummer, leveringen en diensten, daaronder de ABC-leveringen; per regel rekenkundig afgerond op hele euro's; totaal naast rubriek 3B met melding bij boekingen in 3B zonder afnemer; klanten zonder geldig btw-nummer apart gemeld (link naar de klant); doorklik per afnemer; PDF, Excel, CSV. Tijdvak: Beheer > Instellingen **ICP-opgaaf per** kwartaal/maand (standaard het btw-tijdvak; btw per jaar → kwartaal). Leveringen/diensten: factuur met artikelen per regel (artikel of samengesteld = levering, dienst = dienst), anders de **ICP-soort** van de klant (leveringen/diensten/ABC). Vastleggen samen met de btw-boeking als het tijdvak gelijk is, anders knop **ICP-opgaaf vastleggen**; later verschil per afnemer. Klantscherm: ICP-soort; een klant in 3B (rubriek of btw-rekening) en een leverancier met een btw-rekening op 4B moeten een geldig EU-btw-nummer hebben (anders opslaan geweigerd); de aangifte meldt relaties in 3B/4B zonder geldig nummer. Dashboard: eigen regel bij de btw-taak tot de opgaaf is vastgelegd. NL/EN/DE/FR |
| Talen | Nederlands, Engels, **Duits** en **Frans** (fase 4 stap 2b, DT1/DT3/DT4, TL1–TL8), per gebruiker bij Mijn instellingen (`app_user.language`); de taal wordt per verzoek gezet (`main.py`). Zonder sessie (inloggen, tweestapscode, activeren, foutpagina's) volgt de taal de browser (`Accept-Language`, `i18n.browser_language`), anders Nederlands; er wordt niets opgeslagen. Een **nieuwe** gebruiker krijgt bij het activeren de browsertaal met de notatie die daarbij past (`i18n.FORMATS`: DE `1.000,00`/`dd.mm.yy`, FR `1000,00`/`dd/mm/yy`, EN `1,000.00`/`dd/mm/yy`); bestaande gebruikers houden hun instellingen. Alle teksten staan in het Nederlands in de code met `_()` (meteen vertalen) of `N_()` (markeren, vertalen bij tonen), zinnen met variabelen als `_("… {naam} …").format(naam=…)`; Engels in `i18n_en.py` (terminologie van de Engelse SQL-Ledger), Duits in `i18n_de.py`, Frans in `i18n_fr.py` (Duitsland/"Sie" en Frankrijk/"vous"; vaktermen volgens de goedgekeurde woordenlijst `dev/woordenlijst.py`, SQL-Ledger `locale/de`/`fr` alleen als naslag). Elk catalogusbestand heeft `CONTEXT` (menu-item dat anders heet dan hetzelfde veld), `LIBRARY` (vaste bibliotheekmeldingen); DE/FR daarnaast `LIBMSG`/`TERMS`/`FRAGMENTS` voor de meldingssjablonen. Meldingen met variabelen uit `sqlledger/` staan als sjabloon met benoemde plaatshouders in `libmsg.py`; `lib_message(exc)` (in sjablonen `libmsg(tekst)`) herkent de melding en zet haar om, vaste termen in de waarden gaan mee. Een taal toevoegen (DT6): één catalogusbestand zoals `i18n_de.py`, registreren in `i18n.py`, plus documentteksten (`documents.py`), mailteksten (`mailer.py`) en bedrag in woorden (`num2text.py`). Toetsen: `tests/web/test_libmsg.py` (elke f-string-melding heeft een sjabloon), `tests/web/test_i18n.py` (elke tekst vertaald in EN/DE/FR, plaatshouders en begin-/eindspaties gelijk, niets onvertaald behalve een korte lijst gelijke woorden, browsertaal, activeren, en een renderproef van alle 84 GET-pagina's in DE en FR zonder Nederlandse schermtekst). Overzicht ter beoordeling: `python dev/vertalingen.py <pad.xlsx>` (NL/EN/DE/FR per scherm + meldingen). Lange woorden breken af volgens `<html lang>` (CSS `hyphens`, vanaf 10 tekens). Datum-/getalnotatie staan los van de taal. **Eigen woorden** (TL9, `overrides.py`): per installatie `<data_dir>/vertalingen/<taal>.txt` (nl/en/de/fr) met per regel `Nederlandse tekst = eigen tekst` (`[menu] Valuta = …` voor een context, `#` = commentaar, `\n` = nieuwe regel); sleutel is altijd de Nederlandse tekst uit de code, voor bibliotheekmeldingen de Nederlandse tekst van het sjabloon in `libmsg.py`. Gaat vóór de ingebouwde vertaling en blijft bij een update staan (zoals `locale/` in SQL-Ledger, maar buiten de code). Het bestand wordt opnieuw gelezen zodra het verandert; een regel met een onbekende tekst, andere plaatshouders of meer dan 2000 tekens wordt overgeslagen en gelogd; bestanden > 1 MB of geen UTF-8 worden niet gebruikt. `sqlledger-web translations <taal>` controleert het bestand, `--starter` drukt alle teksten af als commentaarregels (startpunt) |
| Helpteksten | HELP_ANALYSE.md HL1–HL12 (alles A). Op elk scherm onder de titel een uitklapblok **Hoe werkt het?** (altijd dicht, zonder JavaScript, ook op 375 px) met vaste kopjes *Waarvoor*, *Zo doet u het*, *Velden* (alleen niet-vanzelfsprekende velden) en *Let op* (valkuilen, één regel "Anders dan in SQL-Ledger" waar dat verrast). Eén bestand per scherm in `templates/help/`, ingevoegd met `{% include %}` in het schermsjabloon (ook bewerkschermen zonder menu-item). Zinnen via `ht("… {b} …", b=…)`, knop-/veldnamen via `hl("Opslaan")` (vertaald, tussen de aanhalingstekens van de taal: “…”, „…“, « … »), menupaden via `hm("Verkoop", "Verkoopfactuur")` (`helptext.py`); zo loopt de help mee als een label verandert, en eigen woorden (`vertalingen/<taal>.txt`) gelden ook voor de help. Toetsen: `tests/web/test_i18n.py` (helpzinnen vertaald; elke `hl`/`hm`-naam bestaat ook op een scherm of in het menu), `tests/web/test_web_help.py` (elk scherm van de golf toont het blok in NL/EN/DE/FR, aanhalingstekens per taal, opmaak veilig). **Golf 1** (dagelijks werk, 2026-10-13): verkoop-/inkoopfactuur, verkoop-/inkoopboeking en creditnota, inkoopfactuur uploaden, ontvangst/betaling, bankafschrift inlezen, bankregels verwerken (ook één bankregel), vaste bankregels, afletteren, memoriaal, klanten/leveranciers zoeken en bewerken, artikel/dienst, btw-aangifte, ICP-opgaaf, aanmaningen, dashboard. **Golf 2** (overzichten en afsluiten, 2026-10-13): alle rapporten onder Verkoop, Inkoop, Btw, Kas en bank, Grootboek en Rapporten (via `Report.help`/`REPORT_HELP` in `reportdefs.py`, include in `reports/page.html`, met een vaste laatste regel over de rapportknoppen `help/_report_common.html`), afsluiten, jaarafsluiting, ontvangsten/betalingen voor meerdere relaties en corrigeren, koersverschilboeking (eigen blok in plaats van dat van het memoriaal), betalingsregister, afgeletterde posten, documenten in batch, rekeningoverzichten. Meegenomen (V3): *Openstaand* begint met een lege "Van" (ook facturen van eerdere jaren), de jaarafsluiting stelt de resultaatrekening van de vorige afsluiting voor, titel *Documenten in batch*. Golf 3–4 volgen (beheer en systeem, rest + pagina *Help*). Schermteksten spreken de gebruiker overal aan met "u" (V1, 2026-10-13). NL/EN/DE/FR |
| v1.0.1 schermen | V101_ANALYSE.md PB11, PB13, PB17, PB19, PB20, PB22 + PB26, PB27 (alles A; keuzes binnen A onder Open punten). **Zoeklijst** (`static/combo.js`, PB17): elk veld met `list=` (relaties, rekeningen, artikelen, bankkandidaten) krijgt een eigen ARIA-combobox: begint-met vóór bevat, getypte tekst vet, "nummer – naam" (`data-n`/`data-name`), ~10 regels, max 50 + "nog n – typ verder", toetsenbord/muis/aanraken, groepskoppen (`data-group`); serverlijsten (`data-remote`) geven 50 opties + `data-more`; labels met spaties in het nummer worden op de server exact herkend. **Datumvelden** (`static/dates.js`, PB19): eigen melding bij een onvolledige datum, ×-knopje bij niet-verplichte velden. **Logo** (PB11): sleepvak op Beheer > Huisstijl, meteen opslaan via fetch (JSON bij `Accept: application/json`). **RGS-lijst** (PB13): link naar de Kennisbank + waarschuwing bij alfa/bèta (`rgs.prerelease_signs`). **Menu productie** (PB20, `sqlledger/production.py`, `sqlledger_web/production.py`): alleen met samengestelde artikelen of schakelaar in Instellingen (`x_setting.show_production`). **W&V/balans** (PB22, `reportdefs.statement_columns`): periodes oud → nieuw, totaal als laatste. **Bankregels** (PB26/PB27): tegenrekening "Eerder gebruikt", na boeken "Voortaan zo boeken?" (vaste regel of rekeningnummer bij de relatie) |
| v1.0.1 naam PyBalans | NB1–NB5 (alles A; keuzes binnen A onder Open punten). De app heet **PyBalans**: browsertitel, kopregel, inlog/2FA/activeren, wizard ("Welkom bij PyBalans"), e-mail met code, startbanner in het serverlog, `X-Mailer`, PDF-maker (`Creator` = `PyBalans <versie>` via `reportpdf.write_pdf`), back-upkop `-- PyBalans back-up` (oude kop `-- SQL-Ledger (Python) back-up` blijft leesbaar), TOTP-issuer `PyBalans`. Ondertitel op inlogscherm en wizard (`templates/_tagline.html`). **Over PyBalans** (`/over`, `templates/about.html`, link onderaan het menu): versie, licentie GPL-2.0-or-later en de merkregels. Pakket `pybalans` 1.0.1, opdrachten `pybalans` en `sqlledger-web` (alias); modules `sqlledger`/`sqlledger_web` ongewijzigd. Waar het over de oude software gaat (back-up uit SQL-Ledger, overstap, koppelen, `-- SQL-Ledger Backup`) blijft SQL-Ledger staan. Kleine fixes: grootte op Back-up controleren in kB/MB; vertaalde bestandsknop bij Importeren en RGS-lijst. |
| Pictogram (favicon) | Optie B (2026-10-13): twee gelijke stapels (wit en goud) op een wip in evenwicht, op het blauw van de app. `static/favicon.svg` (bron), `favicon-32.png`, `favicon.ico` (16/32/48) en `apple-touch-icon.png` (180) zijn daaruit getekend met Pillow; gelinkt in `base.html`, en `/favicon.ico` werkt ook zonder inloggen |

```bash
pip install -e ".[web]"                                # of zonder -e: pip install ".[web]"
pybalans --config app.toml serve                       # http://127.0.0.1:8000
# eerste start: de server maakt de app-database en zet een startcode in het log;
# open http://127.0.0.1:8000/welkom (taal, beheerder, 2FA, administratie, gebruikers)
# noodroute (beheerder kwijt, scripts): pybalans --config app.toml create-admin <login>
```

De opdracht heette tot v1.0.1 `sqlledger-web`; die naam blijft werken (zelfde opdracht), zodat bestaande
launchd-, cron- en systemd-regels niet hoeven te veranderen. Nieuwe voorbeelden gebruiken `pybalans`.

**Oude SQL-Ledger-versie** (ES12): de app koppelt alleen databases van SQL-Ledger 3.2.4 of hoger; een database van SQL-Ledger 4.x kan
gekoppeld worden, maar wordt **niet ondersteund** (melding "gebruik op eigen risico").
Een back-up van een oudere versie (bv. 2.8) leest u het eenvoudigst in met **Systeem > Back-up inlezen** (fase 6a/6b:
werkt hem meteen bij naar 3.2.4). Een draaiende oude database koppelen kan pas na bijwerken: de back-up
terugzetten (`createdb <naam>` en `psql -d <naam> -f <back-up>.sql`; de foutmeldingen over `DROP TABLE` mogen) en dan
één keer inloggen in SQL-Ledger 3.2.x op die database (die werkt het schema bij met `sql/Pg-upgrade-*.sql`), of met
`perl compare/upgrade_dataset.pl <sql-ledger-map> <naam>` (zelfde code, zonder inloggen). Daarna koppelen via de
wizard of Systeem > Administraties.

Instellingen (TOML of `SQLLEDGER_<NAAM>`, wachtwoord ook via `SQLLEDGER_DB_PASSWORD_FILE`):
`app_dsn`, `db_host`, `db_port`, `db_user`, `db_password`, `secure_cookies` (standaard aan;
uit voor http buiten localhost), `session_timeout`, `lockout_attempts`, `lockout_minutes`,
`setup_code_hours`, `password_min_length`, `data_dir` (sjablonen en logo's per administratie, mailmap in testmodus,
standaard `~/.local/share/sqlledger`), `secret_key` (Fernet-sleutel voor het SMTP-wachtwoord; leeg = `<data_dir>/secret.key`), `pg_dump` (programma voor
Back-up maken, standaard `pg_dump` uit het zoekpad; dezelfde of een nieuwere hoofdversie dan de PostgreSQL-server). Voorbeeld: `dev/dev.toml` (testomgeving).
Docker: `Dockerfile` + `compose.yaml` (app + PostgreSQL, alleen op 127.0.0.1; voor hosting
een https-proxy ervoor en `secure_cookies` aan).

**Back-up maken** (FASE6_ANALYSE.md 6d-1, I7). Per administratie in het scherm **Systeem > Back-up maken**, of alles
tegelijk op de opdrachtregel (om dagelijks in te plannen met cron, launchd of een systemd-timer):

```bash
pybalans --config app.toml backup --dir /srv/sqlledger-backup --keep 30
# alleen één administratie (id, naam, code of database; mag vaker):  --company adm_a
# ook administraties die uit staan: --inactive;  zonder app-database: --no-app-db
```

Per administratie `<database>-JJJJMMDD-UUMM.sql.gz` (de database, ook alle `x_`-tabellen en de bijlagen) en, als er
eigen sjablonen of een logo zijn, `<database>-JJJJMMDD-UUMM-sjablonen.zip`; daarnaast de app-database (gebruikers,
rechten, 2FA, logboek). `--keep N` laat per soort de laatste N staan. Bij een fout gaat de opdracht door met de rest
en eindigt met code 1 (zichtbaar in cron-mail). Bewaar de map ook buiten de server. **Terugzetten**: een administratie
met Systeem > Back-up inlezen (database + eventueel de zip met sjablonen). De app-database bij een nieuwe installatie
met psql, vóór de eerste start: `createdb sqlledger_app` en `gunzip -c sqlledger_app-….sql.gz | psql -d sqlledger_app`;
daarna de administraties inlezen of opnieuw koppelen. Niet in de back-up (per installatie, zelf bewaren): het
configbestand, `<data_dir>/secret.key` (zonder die sleutel opnieuw het SMTP-wachtwoord invullen), eigen woorden
(`<data_dir>/vertalingen/`) en de RGS-lijst (`<data_dir>/rgs/`, opnieuw in te lezen).

**Terug naar SQL-Ledger** (alleen als het echt moet). Lees deze back-up **niet** in met Restore in SQL-Ledger: SL
wist daar eerst alle tabellen en kan een pg_dump niet lezen, zodat een lege administratie overblijft. Daarom heeft de
kop `-- App-Dataset:`/`-- App-Version:` en weigert SL het bestand ("Not a SQL-Ledger backup!"). Wel kan het zo: de
back-up met psql in een nieuwe database zetten en die in SQL-Ledger (Beheer, gebruiker bewerken) als dataset
koppelen. De database blijft in de vorm van SQL-Ledger 3.2.4; de eigen `x_`-tabellen laat SL links liggen (alles wat
alleen daarin staat, zoals bankafschriften, bijlagen in de app en btw-rubrieken, ziet SL niet).

```bash
createdb -E UTF8 -T template0 adm_a_terug
gunzip -c adm_a-20270105-0300.sql.gz | psql -v ON_ERROR_STOP=1 -d adm_a_terug
```

**Overstappen uit SQL-Ledger** (FASE6_ANALYSE.md 6d, I9). Stappenplan voor wie de app als enige systeem gaat
gebruiken; dezelfde punten staan in de app op **Beheer > Ingebruikname** (en op het dashboard) met een vinkje zodra de
app ze zelf kan controleren.

1. **Laatste back-up uit SQL-Ledger**: Systeem > Back-up in SQL-Ledger (ook oudere versies, bv. 2.8). Bewaar dat
   bestand: het is de stand op de overstapdatum. Boek daarna niets meer in SQL-Ledger.
2. **Inlezen**: Systeem > Back-up inlezen (of de tegel in de eerste start). Een oudere versie wordt bijgewerkt naar
   3.2.4; overgeslagen opdrachten staan op het resultaat en op het controleblad.
3. **Controleblad** (Beheer > Ingebruikname > Controleblad, peildatum = laatste boeking in de back-up): leg het naast
   de laatste cijfers die u uit SQL-Ledger hebt: jaarrekening of proefbalans, de laatste btw-aangifte, het
   bankafschrift op die datum en de openstaande posten. Kijk de controles na ("nakijken" = bekijk de boekingen; het kan
   een oude eigenaardigheid in de data zijn). Vink af.
4. **Btw**: Btw > Btw-rubrieken (elke btw-rekening een rubriek of "geen aangifte", afdrachtrekening) en het
   aangiftetijdvak bij Beheer > Instellingen. SQL-Ledger had geen aangifte; de app wel.
5. **Bank**: IBAN bij elke bankrekening (Beheer > Rekeningschema) en het eerste afschrift inlezen (Kas en bank >
   Bankafschrift inlezen) **vanaf de dag na de laatste boeking in de back-up**. Sluit het beginsaldo niet aan, dan meldt het
   inlezen het verschil. Wat na de back-up nog niet geboekt is (bijvoorbeeld de rest van het jaar), komt zo via de
   bankregels en Inkoop > Inkoopfactuur uploaden alsnog in de administratie.
6. **Huisstijl**: de documentlayouts van SQL-Ledger (`.tex`/`.html` in `templates/`) worden niet overgenomen; kies
   logo, kleur en lettertype bij Beheer > Huisstijl (of eigen sjablonen bij Beheer > Sjablonen).
7. **Nummering**: de nummers lopen door (laatst gebruikt staat op de lijst); controleer ze bij Beheer > Instellingen.
8. **Gebruikers**: iedereen een eigen login met tweestapsverificatie (Systeem > Gebruikers); geen gedeelde logins
   zoals in SQL-Ledger.
9. **Back-up**: Systeem > Back-up maken, en dagelijks inplannen (zie "Back-up maken" hierboven). Lokaal op een Mac
   bijvoorbeeld met cron (`crontab -e`): `0 2 * * * /pad/naar/pybalans --config /pad/naar/app.toml backup --dir
   /pad/naar/back-upmap --keep 30` (kies een map die vanzelf buiten de computer bewaard wordt, bv. een
   gesynchroniseerde cloudmap).
10. **Afsluitdatum** (Beheer > Afsluiten): op het einde van het laatst aangegeven btw-tijdvak. Het jaar van de
    overstap blijft open tot de jaarrekening; de jaarafsluiting (Beheer > Jaarafsluiting) doet u daarna in de app.
11. **RGS** (Nederland, optioneel): Systeem > RGS-lijst inlezen en Beheer > RGS-koppeling.

## Bewuste afwijkingen van Perl

Alles is gelijk aan het origineel (aangetoond, zie hieronder), behalve:

**Gekozen bij de opdracht**
1. **Afronding** met `Decimal` en ROUND_HALF_UP. Perl telt bij floats 0.501 op
   en kapt af, waardoor bv. 1.00499 → 1.01 wordt; hier 1.00. Optellingen in de
   database gebeuren als `numeric` (geen 244.82999999999998).
   Let op: de kolommen zelf blijven `float` (bestaand schema).

**Controles die in Perl alleen in het scherm zaten, nu in de kern**
2. Debet = credit, niet debet én credit op één regel, boekdatum verplicht,
   geen boeking in afgesloten periode (`closedto`).
3. Bestaande boekingen niet wijzigen/verwijderen bij `revtrans` of als ze in een
   afgesloten periode vallen. Perl liet via het scherm toe dat je een boeking
   uit een afgesloten periode naar een open datum verplaatste; hier niet.
4. Alleen boeken op bestaande, open boekrekeningen (charttype `A`). Perl boekt
   op kop-rekeningen en maakt bij een onbekende rekening een regel met
   `chart_id NULL` (bedrag verdwijnt uit elk rekeningoverzicht).
5. Negatieve debet/credit-bedragen worden geweigerd.

**Bugs in het origineel die hier opgelost zijn**
6. `GL::delete_transaction` met batch: typefout `$mount` → batchtotaal werd
   nooit bijgewerkt.
7. `CA::all_transactions` met afdeling: beginsaldo telde alleen het gl-deel mee
   (lus overschreef de query); hier gl + ar + ap.
8. `CA::all_transactions` met onbekende rekening: Perl crasht (lege SQL);
   hier een lege lijst.
9. `GL::transactions` tegenrekeningen:
   - zonder AR/AP-hoofdregel in de selectie zette Perl een tegenrekening op
     `$form->{GL}[undef]` = de eerste regel van het overzicht;
   - bij gelijke bedragen (volledige betaling) hing de keuze welke regel de
     bankrekening als tegenrekening krijgt af van Perl's willekeurige
     hash-volgorde — twee runs konden verschillen. Hier: altijd de betaalkant;
   - de 'al gezien'-administratie was globaal; hier per boeking (en elke
     rekening staat maar één keer in een tegenrekening);
   - bij meerdere regels met precies het tegenbedrag koos Perl via de
     hash-volgorde, soms de eigen rekening; Python kiest bij voorkeur een
     andere rekening.
10. Nieuwe boeking met `fx_transaction`-regel: Perl's `$cleared` lekte door naar
    alle volgende regels; hier per regel.
11. `update_defaults` deed een tussentijdse `COMMIT` als het nummerveld nog
    niet bestond; hier blijft alles in één transactie.
12. Alle SQL is geparametriseerd (beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna).
    `sort` accepteert alleen bekende kolommen.

**Rapporten**
13. Een sectie zonder eigen kop (bv. Passiva, Eigen vermogen) kreeg in `rp.pl`
    een subtotaalregel met de naam van de laatste kop uit een vórige sectie;
    op de balans werd het resultaat lopend boekjaar daarin opgeteld (en voor
    vorig jaar zelfs het resultaat van dít jaar, `rp.pl` regel 1442). Python
    toont subtotalen alleen onder een echte kop.
14. Proefbalans: koppen en beginsaldi werden vertaald via `language_code`, de
    rekeningen in de periode via `$form->{countrycode}` (in de praktijk leeg).
    Python gebruikt overal dezelfde taal (`language_code`, standaard de taal
    van de gebruiker).
15. Subtotalen worden ook berekend als koppen niet getoond worden
    (`l_heading=False`); Perl toonde dan nullen.
16. `decimalplaces` standaard = valuta-precisie. Perl gebruikte zonder waarde
    0 decimalen, waardoor rekeningen met een saldo onder 0,50 uit de balans en
    W&V verdwenen.

**Debiteuren, crediteuren, btw, betalingen, jaarafsluiting**
17. Ouderdomsanalyse en aanmaningen joinden op `contact`: een relatie met twee
    contactpersonen kreeg elke openstaande factuur twee keer (dubbele
    bedragen). Python neemt één contactpersoon per relatie.
18. Aanmaningen: bij meerdere aanmaanregels in `status` kwam een factuur
    meermaals in de lijst, met het niveau van de láágste. Python: één regel,
    niveau = hoogste + 1.
19. Betalingenoverzicht: `RP::payments` riep per betaalrekening
    `$form->sort_order` aan, die de sorteerrichting telkens omdraait — de
    rekeningen stonden afwisselend oplopend en aflopend. Python sorteert overal
    in de gevraagde richting.
20. Jaarafsluiting: Perl verwijderde latere afsluitingen al bij het berekenen,
    buiten de boekingstransactie; het resultaat werd opgeteld uit onafgeronde
    bedragen terwijl de regels wél werden afgerond (boeking kon een fractie uit
    balans zijn; in echte data staan zulke regels). Python doet alles in één
    transactie, met resultaat = som van de afgeronde regels, en accepteert
    alleen een eigen-vermogenrekening als resultaatrekening.
21. De aanmaanlijst gebruikt een peildatum (`asof`, standaard vandaag) in
    plaats van de klok van de databaseserver.

**Verkoop- en inkoopboekingen (fase 1a, `sqlledger/arap.py`)**
22. Bij openen en opnieuw opslaan liet Perl btw-regels weg op btw-rekeningen die niet
    (meer) aan de relatie gekoppeld zijn — het factuurbedrag daalde stil. In de echte
    data betrof dat honderden boekingen. Python behoudt de btw.
23. Perl gebruikte de veldnaam `cleared_$i` voor zowel betaling i als regel i: bij opnieuw
    opslaan kreeg regel i de afletterdatum van betaling i en verloor een afgeletterde
    regel zijn eigen datum. Python houdt ze gescheiden.
24. Regelvolgorde bij laden: Perl sorteerde alleen op datum (volgorde binnen een datum
    willekeurig); Python op datum en oorspronkelijk regelnummer.
25. Batches/vouchers worden geweigerd (buiten de scope).
25a. Boeking op een debiteuren-/crediteurenrekening die in het rekeningschema
    niet (meer) als AR/AP gekoppeld is: bij opnieuw opslaan boekte Perl die
    regels met `chart_id NULL` (bedragen verdwijnen uit elk overzicht). Python
    behoudt de oorspronkelijke rekening. (Komt voor in de echte data.)

25b. Regels op rekeningen zonder AR/AP-koppeling (bv. een oude bankrekening
    waarvan de betaalkoppeling later is weggehaald, of een kostenrekening zonder
    `AP_amount`): Perl liet ze bij openen en opnieuw opslaan stil vallen —
    betalingen verdwenen (factuur weer open) of kostenregels verdwenen (factuur
    lager). In de echte data: ruim 2.000 regels in ±2.000 boekingen. Python
    behoudt ze: een regel met een eigen betaalrij, op een andere datum dan de
    boekdatum, of op een rekening die overwegend als betaling wordt gebruikt is
    een betaling; anders een kosten-/opbrengstregel.
25c. Kostenregel op een als betaalrekening gekoppelde rekening (bv. een
    tussenrekening) op de boekdatum: Perl telde die als negatieve betaling,
    waardoor het factuurbedrag daalde (tot zelfs 0). Python houdt hem als regel
    als het nettobedrag van de factuur dat bevestigt.
    Toets: alle 16.797 (A) en 8.921 (C) verkoop-/inkoopboekingen zonder
    artikelen geopend en opnieuw opgeslagen met Python → per boeking saldo per
    rekening, factuurbedrag en betaald bedrag gelijk aan het origineel (C: 4
    boekingen in vreemde valuta waar één afrondingscent tussen twee kostenregels
    schuift door de willekeurige regelvolgorde in oude data).

**Betalingen (fase 1b, `sqlledger/payments.py`)**
26. Contante korting met btw-correctie (`defaults.cdt`): Perl telde voor het
    totaaltarief álle tarieven van een btw-rekening op die op of na de
    factuurdatum aflopen. Bij een tariefwijziging (bv. 19% tot 30-09-2012 en
    daarna 21%) werd dat 40%: het factuurbedrag (`amount`) daalde met 40% van de
    korting, terwijl de btw-regels wél met het juiste tarief werden geboekt —
    de factuur bleef daardoor schijnbaar te veel betaald. Python rekent per
    rekening met het tarief dat op de factuurdatum gold. (Komt voor in de echte
    data: `cdt` staat aan en er zijn tariefwijzigingen in 2012 en 2019.)
27. Een betaling waarvan het totaal lager is dan het aan facturen toegewezen
    bedrag gaf in Perl een negatieve vooruitbetaling; Python weigert dat.
28. Vooruitbetaling zonder gekozen debiteuren-/crediteurenrekening (relatie
    zonder standaardrekening): Perl boekte die regel met `chart_id NULL`, zodat
    hij uit elk rekeningoverzicht verdwijnt. Python neemt de standaardrekening
    van de relatie, anders de eerste AR- resp. AP-rekening.
29. Factuur zonder koers (`exchangerate` leeg): Perl rekende met koers 0
    (betaling kwam niet op debiteuren terecht maar op koersverschil). Python
    gebruikt 1. In de echte data heeft elke boeking een koers.
30. Drempelbedrag leverancier (`vendor.threshold`) in de relatiekeuze: Perl
    telde het openstaande bedrag per journaalregel in plaats van per factuur.
31. Extra controles: de facturen horen bij de gekozen relatie, staan in de
    valuta van de betaling, zijn goedgekeurd, niet door een ander gelockt en
    niet in een batch; betaal- en kortingsrekening bestaan en zijn als zodanig
    gekoppeld; koerswinst/-verliesrekening aanwezig als die nodig is. Perl
    vertrouwde op het scherm.
32. Bedragen worden op `precision` afgerond, zoals het scherm ze toont.
33. Storneren (`void_payment`): Perl boekte alleen de bankregel terug, zonder
    datum (de dag van de databaseserver) en zonder tegenregel — de boeking
    raakte uit balans — en verhoogde bij klanten het betaalde bedrag in plaats
    van het te verlagen. Koersverschil, koerswinst/-verlies en korting bleven
    staan. Python boekt de héle betaling tegen op een te kiezen datum (standaard
    vandaag, niet in een afgesloten periode): bank, koersverschil,
    debiteuren/crediteuren, koerswinst/-verlies en contante korting met
    btw-correctie. `paid` daalt, het factuurbedrag krijgt de btw-correctie
    terug, `datepaid` wordt de laatste overgebleven betaaldatum, en de
    stornoregel krijgt een `payment`-rij met de koers van de betaling.
34. Opnieuw uitgeven (`reissue_payment`): Perl zette alleen de bankregel op
    het nieuwe kenmerk en op de datum van vandaag; de andere regels van de
    betaling bleven op de oude datum (per dag uit balans, eventueel over een
    jaargrens). Python verplaatst alle regels van de betaling naar de gekozen
    datum (standaard blijft de datum gelijk) en weigert verplaatsen in of uit
    een afgesloten periode. De koers blijft die van de oorspronkelijke betaling.
35. Een betaling wordt herkend aan kenmerk + betaalrekening (+ betaaldatum als
    het kenmerk op meerdere dagen voorkomt). Regels zonder kenmerk
    (debiteuren/crediteuren, koerswinst/-verlies) worden gezocht op de bedragen
    die het boeken ervan oplevert; is dat niet eenduidig, dan volgt een fout.

**Overzicht verkoop/inkoop (`sqlledger/arap_list.py`)**
36. Zoeken op memo: Perl voegde `OR lower(i.description) LIKE ..` zonder haakjes
    toe (alle andere filters, ook "goedgekeurd", vielen weg) en gaf in de
    samenvatting altijd een lege lijst (`a.id = 0`). Detailweergave: Perl telde
    factuurbedrag, betaald en openstaand per journaalregel op (een factuur met
    vier regels vier keer). Subtotaal openstaand per valuta: typefout `{mount}`
    in `aa.pl subtotal` gaf min het betaalde bedrag (zichtbaar in de echte
    data). Python lost alle drie op; bij gelijke sorteerwaarden volgt Python het
    boekingsnummer (Perl: willekeurige databasevolgorde).

**Rapporten in de webapp (stap 3)**
37. Betalingenoverzicht: bij gelijke sorteerwaarden (datum, relatie, medewerker)
    gaf PostgreSQL de regels in willekeurige volgorde, ook tussen twee Perl-runs.
    Python sorteert daarna op boekingsnummer, kenmerk en bedrag.
38. Rekeningmutaties (`ca.pl`): een beginsaldo dat exact 0 is maar als float-som
    een restje heeft (bv. -8,6e-12) gaf in Perl een regel "0,00"; Python telt
    exact en laat de regel weg (zie punt 1). Komt voor in beide echte administraties.

**Memoriaal in de webapp (stap 4)**
39. Journaaloverzicht (`gl.pl transactions`) met "per rekening" (splitledger) én
    subtotalen: Perl sloot de subtotaalgroep niet af bij een nieuwe rekening, zodat
    een subtotaal regels van twee rekeningen optelde en onder de verkeerde rekening
    stond. Python begint bij elke rekening een nieuwe groep.
40. Bedragen met meer decimalen dan `precision` (bv. 0,004) worden geweigerd
    (`GeneralLedger.validate`). Perl sloeg ze ongewijzigd op terwijl de
    balanscontrole afrondde, zodat een boeking een fractie uit balans kon raken.
41. Jaarafsluiting (`Reports.post_yearend`) in een afgesloten periode wordt
    geweigerd. Perl controleerde dat niet en verwijderde daarbij ook latere
    afsluitingen, waardoor afgesloten jaren veranderden.
42. Extra controles bij boeken: een boeking die door een andere gebruiker
    geopend (gelockt) is, kan niet opgeslagen worden; bij een vreemde valuta is
    een koers verplicht (in Perl alleen een rood sterretje op het scherm).
43. Herhaalschema bij opnieuw opslaan: Perl gaf de volgende herhaaldatum niet
    mee (`Form::get_recurring` laat `nextdate` weg) en rekende die opnieuw uit
    vanaf vandaag. Een boeking met achterstallige herhalingen verloor die zo
    stil bij elke wijziging. Python behoudt de volgende datum.
44. Journaaloverzicht: regels met gelijke sorteerwaarden (zelfde boeking,
    rekening en datum) kwamen in wisselende volgorde uit PostgreSQL, ook tussen
    twee keer hetzelfde scherm openen; het lopend saldo halverwege sprong dan.
    Python sorteert daarna op bedrag, kenmerk en memo.
45. Verkoop-/inkoopscherm (`aa.pl`): bij openen van een bestaande boeking werd
    `oldterms` niet geladen, waardoor `update` de vervaldatum altijd opnieuw
    uitrekende (boekdatum + termijn). Wie een boeking alleen opende en opsloeg,
    verloor een met de hand gezette vervaldatum. Python behoudt hem
    (`aa.pl create_links`/`update`; scenario "aa.pl-scherm: vervaldatum ...").
46. Verkoop-/inkoopscherm: het veld "Internal Notes" toont de notities van de
    relatie (`AA::get_name` overschrijft `intnotes`) maar wordt nergens opgeslagen;
    wat je er typt gaat verloren. De webapp toont de relatienotities alleen-lezen.
47. Verkoop-/inkoopboekingen: bedragen met meer decimalen dan `precision`
    worden geweigerd, net als bij het memoriaal (40).
48. Betaalkenmerk (dcn) bij verkoop: `AA::post_transaction` rekent het bij elke
    keer opslaan opnieuw uit met het sjabloon van de bankrekening. Heeft die
    rekening geen sjabloon (meer), dan werd een bestaand kenmerk leeggemaakt,
    terwijl het al op de verstuurde factuur stond (C: 52 boekingen bij
    alleen openen en opslaan). Python behoudt dan het bestaande kenmerk.

49. Betalingen (`Payments.post_payment`): een bedrag (plus korting) hoger dan
    wat op een post openstaat wordt geweigerd; Perl liet een factuur
    overbetalen (betaald > bedrag). Het meerdere hoort een vooruitbetaling te zijn.
50. Betaalscherm zonder locks bij het tonen (`cp.pl` lockt elke getoonde post
    en laat die locks staan als je het scherm verlaat of als boeken mislukt).
    De webapp lockt pas bij het boeken (`SELECT ... FOR UPDATE`) en weigert als
    het openstaande bedrag van een post afwijkt van wat het scherm toonde
    (`PaymentLine.expected_due`). Posten die een ander gelockt heeft (ook vanuit
    Perl tijdens parallel draaien) blijven buiten de lijst.
51. Vooruitbetaling zonder aangevinkte post (`cp.pl post_payment`): Perl boekte
    zonder kenmerk, ook als er een was ingevuld, maar hoogde het nummer van de
    betaalrekening (`check_<rekening>`) wél op. De webapp boekt het ingevulde of
    voorgestelde kenmerk (scenario "vooruitbetaling zonder posten en zonder kenmerk").
52. Scherm voor meerdere relaties (`cp.pl post_payments`): (a) de contante korting
    werd ook bij een deelbetaling gegeven en van het betaalde bedrag afgetrokken,
    terwijl het ingevoerde totaal geboekt bleef; (b) dat verschil werd een
    vooruitbetaling bij de **eerste relatie in de lijst** (`$form->{vc_id}` wordt
    vóór de lus op `vc_id_1` gezet), dus bij een andere klant/leverancier;
    (c) zonder debiteuren-/crediteurenrekening (`chart_id` NULL, het scherm heeft
    geen keuze). De webapp verdeelt met `Payments.allocate`: creditnota's eerst,
    dan de oudste; korting alleen als een post helemaal betaald wordt; wie het
    volle bedrag zonder korting betaalt, heeft geen korting genomen.
53. Scherm voor meerdere relaties: Perl boekte bij een deelbetaling ook een
    betaling van 0 (bank- en debiteurenregel, `payment`-regel) op posten die
    niets kregen en zette daar `datepaid` en `bank_id`; de laatste betaaldatum
    van zo'n post klopte daarna niet meer. Python slaat die posten over.
54. Betalingenlijst voor corrigeren (`CP::payment_register`): één regel per
    betaling (kenmerk + rekening + datum + relatie) in plaats van per factuur;
    kenmerk van/tot vergelijkt getallen als getal en tekst als tekst (Perl
    vergeleek altijd als getal, kenmerken met letters vielen eruit of erin).
    Een stornering staat met haar eigen datum in de lijst.
55. Na storneren/opnieuw uitgeven via het scherm: zie 33–35 (hele betaling in
    plaats van alleen de bankregel).

**Terugkerende boekingen** (fase 2; `sqlledger/recurring.py`, scenario's "terugkerend: ...")
85. Volgende datum van een nieuw schema: Perl schatte die lineair vanaf vandaag
    (`Form::save_recurring`, Form.pm 4228–4262: `int(x/(x+y)·aantal + 1)·n`), wat bij een
    start in het verleden termijnen oversloeg of de verkeerde koos (start 15-01, maandelijks
    ×12, vandaag 08-10 → 15-11, oktober overgeslagen), en bij start = vandaag de tweede termijn
    gaf. Python: de eerste termijn ná de boekdatum van de boeking (start = boekdatum → de
    tweede termijn, want de boeking zelf is de eerste; start later → de start). Een opgegeven
    volgende datum na de einddatum maakt het schema afgelopen, zoals Perl.
86. Datums: termijn k = start + k·interval, steeds vanaf de start gerekend; een maandeinde
    blijft een maandeinde (31-01 → 28/29-02 → 31-03 → 30-04, 29-02 jaarlijks → 28-02 en in een
    schrikkeljaar weer 29-02). Perl telde het interval op bij de vorige datum
    (`AM::update_recurring`, AM.pm 1494: `nextdate + interval`): 31-01 → 28-02 → 28-03 → 28-04.
    Na verwerken wordt de volgende datum de eerste termijn ná de geboekte datum én de vorige
    volgende datum: een in de lijst aangepaste (latere) boekdatum verschuift het schema niet
    (Perl: aangepaste datum + interval, het schema schoof blijvend op); een datum verder vooruit
    slaat de termijnen daartussen over.
87. Dubbel boeken: `am.pl process_transactions` (4644) las het schema zonder lock en hoogde de
    datum pas na het boeken op in een eigen verbinding: twee gebruikers of een dubbele POST
    boekten dezelfde termijn twee keer; mislukte het afdrukken (`print_recurring`, 4781), dan
    stond de boeking er wel maar bleef de datum staan → de volgende keer nogmaals. Python: per
    schema één databasetransactie met `SELECT … FOR UPDATE` op `recurring`, controle dat de
    volgende datum nog gelijk is aan wat de lijst toonde (anders overslaan met melding), boeking
    + ophogen samen; afdrukken/mailen staat los (fase 4). Een fout bij één schema (afgesloten
    periode, koers) laat de andere door. Perl boekte ook in een afgesloten periode (de controle
    op `closedto` zit alleen in de boekingsschermen, niet in `AA::post_transaction` of
    `process_transactions`); in echte data staat nog een laatste termijn uit 2015 open bij een
    afsluitdatum in 2017. Python slaat zo'n termijn over met melding.
88. Nummer: Perl gaf elke kopie het kenmerk van het schema als nummer (am.pl 4717 `invnumber`,
    4854 `reference`): alle kopieën hetzelfde factuurnummer. Python gebruikt het kenmerk alleen
    als dat nummer nog niet bestaat (memoriaal/verkoop in de hele tabel, inkoop bij dezelfde
    leverancier), anders het volgende nummer.
89. Memoriaalkopie bij getalnotatie 1.000,00: am.pl (4874) zet de bedragen ongeformatteerd in het
    formulier (`5100.5`), `GL::post_transaction` leest ze in de notatie van de gebruiker: de punt
    is een duizendtal-scheiding → 51005 geboekt (×10 of ×100). Verkoop/inkoop werden wel
    geformatteerd. Python geeft bedragen als getal door.
90. Koers: zonder koers op de nieuwe datum hield Perl stil de koers van het origineel (am.pl
    4722–4725, 4741–4744) en schreef die als dagkoers weg. Python slaat het schema over met de
    melding welke koers ontbreekt (ook voor de datum van een meegenomen betaling); de volgende
    datum blijft staan.
91. Meerdere schema's in één verwerkronde: de kopie van een verkoop/inkoop zonder betaling kreeg
    in Perl een betaalregel van de vorige kopie (scenario: 100 op rekening NULL, met
    koersverschilregel en `payment`-rij; `ar.paid` bleef 0). `process_transactions` hergebruikt
    het formulier en de schermcode van ar.pl/ap.pl tussen de schema's. Python bouwt elke kopie
    uit haar eigen origineel.
92. Inhoud van de kopie: Perl nam als omschrijving de schema-omschrijving (am.pl 4718/4855; het
    schemascherm vult die niet voor, dus standaard een kopie zónder omschrijving), verloor bij het
    memoriaal de afdeling (`GL::transaction` levert `department_id`, `GL::post_transaction` leest
    `department`) en kenmerk/memo per regel (4870–4880 neemt alleen rekening, project en bedrag
    over). Python: omschrijving, notities, afdeling, kenmerk/memo van het origineel; de
    schema-omschrijving is alleen de naam in de lijst. Bij verkoop wordt het betaalkenmerk (dcn)
    opnieuw berekend (niet dat van het origineel).
93. Vervaldatum en betaling: `AM::recurring_details` (AM.pm 1430–1431) haalt de afstand tot de
    vervaldatum en de betaaldatum uit `ar`, ook voor inkoop: een inkoopkopie verviel op de
    boekdatum en een meegenomen betaling kreeg de boekdatum. Alle betalingen kregen bovendien
    dezelfde afstand (die van de laatste betaling). Python: vervaldatum en elke betaling (en
    een contante korting) schuiven met hetzelfde aantal dagen als bij het origineel mee, niet
    afgeletterd; zonder "betaling meenemen" ook geen korting. Schema opslaan/verwijderen komt
    in de audittrail (`formname` recurring) en in het logboek van de webapp; verwerken via de
    gewone audittrail van de boeking.
94. **Beveiliging:** beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Python: alles geparametriseerd; eenheid
    uit een vaste lijst, start verplicht, elke n en aantal ≥ 1 (Perl accepteerde aantal 0 of
    negatief: einddatum vóór de start). Kleine Perl-fout zonder gevolg voor PostgreSQL:
    `update_recurring` (1498) vult `%interval` in plaats van `%advance` voor PgPP/Oracle.
**Afletteren (fase 2, `SL/RC.pm`, `bin/mozilla/rc.pl`)**
95. **Beveiliging:** beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Alles is geparametriseerd; de sleutels in het formulier worden
    gecontroleerd tegen de regels die de server zelf opbouwt.
96. "Klaar" zonder eerst "Bijwerken" sloeg de vinkjes niet op: `RC::reconcile` (r309) werkt
    alleen regels bij waarvan `datecleared_$i` ≠ `oldcleared_$i`, en `datecleared_$i` wordt pas
    door `rc.pl update` (r482–497) gevuld. De webapp slaat altijd de vinkjes op die op het
    scherm staan.
97. De controle "Out of balance" (`rc.pl done` r541: `$form->{difference} *= 1`) gebruikte een
    verborgen, geformatteerd veld: bij notatie 1.000,00 is "0,50" × 1 = 0 en werd een ronde met
    verschil opgeslagen; het veld was ook te wijzigen. De webapp rekent het verschil op de
    server uit de vinkjes en het afschriftsaldo en weigert bij ≠ 0 (ook in de bibliotheek).
98. "Alles selecteren" (`rc.pl select_all` r504–505, `$i` ongezet) gaf álle regels de nieuwe
    afletterdatum, ook regels die al eerder waren afgeletterd. De webapp: al afgeletterde
    regels houden hun datum; uitvinken haalt alleen datums weg van regels die op het scherm
    staan (niet van regels in het beginsaldo) en een latere ronde blijft staan.
99. Samenvatting per kenmerk: `RC::payment_transactions` (r267, `id !~ /$ref->{id}/`) zocht
    een boekingsnummer als deelstring: staat 123 al in de regel, dan werd boeking 12 niet
    toegevoegd en dus niet afgeletterd. Nu een verzameling boekingen.
100. `UNION` i.p.v. `UNION ALL` (RC.pm r192/r204, en de `UNION ALL` ertussen werd door de
    volgende `UNION` weer ontdubbeld): identieke regels (zelfde boeking, datum, kenmerk, bedrag)
    stonden maar één keer op het scherm, waardoor saldo en verschil niet klopten (echt
    voorbeeld: C, 1100, mei 2011). Ook vielen regels weg van boekingen buiten gl/ar/ap of
    met een ontbrekende relatie (inner join). De webapp toont alle goedgekeurde regels.
101. Zonder koersverschilrekening (`fxgainloss_accno_id`) gaf het scherm een SQL-fout
    (RC.pm r137–138, `AND NOT ac.chart_id = `); de voorwaarde had ook geen zin (`chart_id` is
    altijd de afgeletterde rekening). Koersverschilregels doen gewoon mee (vinkje
    "Koersverschillen meenemen").
102. Zonder tot-datum was het voorgestelde afschriftsaldo de som van álle regels, ook niet
    afgeletterde (RC.pm r100–116). Nu alleen afgeletterde regels.
103. Een samengevoegde regel (zelfde datum en kenmerk; leeg kenmerk voegt alle regels van die
    dag samen) waarvan maar een deel is afgeletterd, kreeg in `rc.pl` (r288–293) de status van
    de eerste regel: het verschil klopte dan al bij openen niet (C, 1100, 22-3-2011:
    −2.547,00) en bij "Klaar" bleven de andere regels ongewijzigd. De webapp toont "deels":
    het afgeletterde deel telt mee en blijft staan, aanvinken lettert de rest af.
104. Gekozen (L1–L5): elke ronde wordt vastgelegd in `x_reconciliation` (rekening, datum,
    beginsaldo, afschriftsaldo, aantal regels, gebruiker) met audittrail en logboek, met een
    waarschuwing als het beginsaldo afwijkt van het afschriftsaldo van de vorige ronde; regels
    worden aangewezen met (boeking, rekening, datum, kenmerk) zoals `RC::reconcile` — regels
    met dezelfde sleutel worden dus samen afgeletterd en staan op het scherm in één groep
    (één vinkje, ook in detail; de koersverschilregel volgt haar betaling); de rekeningkeuze
    toont standaard de betaalrekeningen (Perl: alle open rekeningen, ook W&V), met een vinkje
    voor alle balansrekeningen; van-datum na tot-datum en een afschriftsaldo met meer decimalen
    dan de administratie worden geweigerd; regels met dezelfde datum en hetzelfde kenmerk staan
    in een vaste volgorde (bedrag, afletterdatum, boeking) — in Perl willekeurig, wat in de
    samenvatting de samenvoeging kon veranderen.
*Bankafschrift inlezen (fase 2, I1–I10; nieuw — SQL-Ledger heeft alleen `im.pl` "Import → Betalingen" voor een CSV):*

105. Nieuwe functie, dus geen Perl-vergelijking. Wat geboekt wordt is wel
     precies wat het (tegen Perl bewezen) betaalscherm boekt: een betaling gaat
     via `Payments.post_payment` (test: `test_payment_identical_to_payment_screen`,
     journaalregels, `ar`/`ap` en `payment` gelijk op de afletterdatum na).
106. **Fout in Perl, voor de memo** — `SL/IM.pm` `payment_links` (r1593–1620,
     r1704–1709): koppelt een CSV-regel op "eerste open post met hetzelfde
     geformatteerde bedrag" over AR én AP samen, zonder richting. Het bedrag is
     credit − debit en het openstaande bedrag van een inkoopfactuur is positief:
     een betaling aan een leverancier (debet) wordt dus nooit gevonden, maar een
     ontvangst kan wél aan een inkoopfactuur gekoppeld worden. Hier: richting uit
     het teken (bij = klant of creditnota leverancier, af = leverancier of
     creditnota klant).
107. **Fout in Perl** — `IM::payment_links` r1610: de query voor inkoopposten
     heeft `JOIN bank b ON (b.id = a.bank_id)`: alleen inkoopfacturen die al eens
     (deels) betaald zijn via een rekening met bankgegevens kunnen gekoppeld
     worden; nieuwe facturen vallen weg (de verkoopquery heeft die join niet).
108. **Fout in Perl** — `IM::payment_links` r1625–1642 en r1696: zoeken op `dcn`
     controleert niet of de factuur nog openstaat (een betaalde factuur wordt
     opnieuw betaald) en haalt daarna de *eerste* post met hetzelfde bedrag uit
     de lijst — vaak een andere factuur, die daarna niet meer gevonden wordt.
     Hier: alleen open posten, en een post wordt per afschrift maar aan één
     regel voorgesteld.
109. **Beveiliging** — beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Python: alles geparametriseerd.
110. Kenmerk van een betaling uit het afschrift = de bankreferentie (uniek per
     bankrekening, anders `<ref>/<regel>` of `BANK-<regel>`); de nummerteller van
     het betaalscherm (`receipt_<rekening>`/`check_<rekening>`) blijft staan
     (`post_payment` zou hem anders op de bankreferentie zetten).
111. Alles wat via het afschrift geboekt of gekoppeld wordt, krijgt meteen de
     afletterdatum = boekdatum van de bank op de bankregel(s) (I8); in SQL-Ledger
     is afletteren (`rc.pl`) een aparte stap.
112. Valuta van een bankrekening: SQL-Ledger legt die nergens vast. Hier: de
     valuta van eerder ingelezen afschriften, anders de meest gebruikte valuta
     van de laatste 200 boekingen op de rekening, anders de standaardvaluta. Een
     afschrift in een andere valuta wordt geweigerd (I4).
113. Volgorde voorstellen a–e zoals I6, met één veiligheidsklep: staat een
     factuur open (a/b/c) terwijl er al een niet-afgeletterde bankboeking met
     hetzelfde bedrag binnen 7 dagen is, dan krijgt het voorstel een
     waarschuwing en telt het niet als "zeker" (anders staat het geld twee keer
     op de bank, bv. als de betaling ooit als memoriaal is geboekt).
114. Contante korting wordt niet voorgesteld (alleen volledige of gedeeltelijke
     betaling, creditnota's die in de omschrijving genoemd worden gaan mee); wie
     korting geeft gebruikt het betaalscherm. Storno's (`is_reversal`) worden
     alleen gesignaleerd (I9); koppelen aan een bestaande boeking kan per regel
     aan één boeking (één overboeking voor meerdere al geboekte posten: zelf
     verwerken).
115. **Gezien in Perl, niet gewijzigd** — `SL/CP.pm` `post_payment` r947 zet
     `datepaid` op de datum van déze betaling, ook als de factuur al een latere
     betaling heeft. Wie een ouder afschrift na een nieuwer verwerkt, ziet dus
     een te vroege laatste betaaldatum. Python doet hetzelfde (gelijk aan het
     betaalscherm); kandidaat voor de memo.
**Artikelen (fase 3, `sqlledger/parts.py`, `ic.pl`/`IC.pm`)**
116. Opslaan schrijft alleen gewijzigde kolommen; ongewijzigd opslaan schrijft niets (ook geen
     audittrail). Perl (`IC::save` r213–565) schreef alle kolommen opnieuw (leeg werd 0) en gooide
     `partsvendor`, `makemodel`, `partstax` en `partscustomer` weg om ze uit het scherm opnieuw op te
     bouwen; velden die het scherm niet toont gingen zo verloren. Hier blijven afbeelding, tekening,
     merk/model, leveranciers- en klantprijzen, douane, lot en vertalingen staan (AT2). Getoetst:
     alle 196 artikelen van A en 735 van C ongewijzigd opslaan via de webapp → 0 verschillen
     (`compare/ic_keeptest.py`, ook met `--touch`). Bij het vergelijken telt tekst zonder witruimte
     eromheen en met `\n` als regeleinde (een browser stuurt CRLF; Perl sneed het nummer bij).
117. De voorraad (`onhand`) wijzigt nooit via het artikelscherm (AT4). Perl telde bij elk opslaan
     `onhand - oldonhand` op (IC.pm r540; daarnaast een beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna), en zette bij loon/overhead de voorraad bij elk
     opslaan op 0 (er is geen invoerveld, wel de optelling). Voorraadcorrectie is een eigen scherm
     met reden, vastgelegd in `x_stock_adjustment` (migratie 4); geen grootboekboeking, zoals Perl
     (`IC::adjust_onhand` schreef alleen `parts.onhand`, zonder spoor).
118. Rekeningen en soort van een gebruikt artikel kunnen niet meer wijzigen; de server weigert
     (AT5). Perl blokkeerde dat alleen in het scherm (ic.pl r318–330). Bestaande artikelen waarvan
     een rekening (intussen) niet meer gekoppeld is of ontbreekt, mogen zo opgeslagen worden.
119. Verwijderen alleen als het artikel niet in facturen, orders, samenstellingen of uren staat
     (AT6). `IC::delete` (r1007) controleerde dat niet; alleen de knop ontbrak.
120. Omschrijving verplicht bij een nieuw artikel of als die er was (bestaande artikelen zonder
     omschrijving: C 46, mogen zo blijven); prijzen/hoeveelheden maximaal 6 decimalen en niet
     negatief (gewicht mag negatief); eenheid maximaal 5 tekens. Perl controleerde niets.
121. Dubbel artikelnummer mag (A 12 en C 98 dubbelen), het scherm waarschuwt (AT3).
     "Opslaan als nieuw" met hetzelfde nummer geeft het volgende nummer; Perl
     (`ic.pl save_as_new` r4041) maakte er een dubbel nummer van.
122. Zoeken: het open/gesloten-filter werkt ook voor offertes en offerteaanvragen; Perl schreef
     het in de variabele van de orders (IC.pm r1543–1544), waardoor het genegeerd werd.
123. **Beveiliging** — beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Python: alles geparametriseerd, geen upload (fase 4).
124. Overige Perl-fouten IC voor de memo: marge met verkeerd teken in `io.pl` r1008 en deling
     door nul bij marge −100 (r1002); leveranciersregels zonder prijs en klantprijzen van 0
     verdwijnen bij opslaan (IC.pm r497, r520); leverancier op naam zonder Bijwerken geeft een
     SQL-fout, klant op naam wordt klant 0; import (`IM.pm`) zet ontbrekende kolommen op NULL,
     wist leveranciers zonder `vendornumber`, telt de voorraad op i.p.v. te vervangen en commit
     per artikel (`IC.pm` r560 commit ook binnen de transactie van de aanroeper).

**Samengestelde artikelen (fase 3, `sqlledger/assembly.py`, `ic.pl`/`IC.pm`)**
125. Prijzen (SA3): lijstprijs, kostprijs en gewicht van een samengesteld artikel worden altijd uit
     de onderdelen berekend (over alle niveaus), de verkoopprijs ook tenzij "verkoopprijs zelf
     bepalen" aan staat (`x_assembly_price`, migratie 5). Per regel afgerond op de standaard-
     decimalen, zoals `ic.pl assembly_row` (r3476). Wijzigt een onderdeel, dan worden de opgeslagen
     prijzen van alle producten waar het (ook indirect) in zit opnieuw berekend. Perl gaf een
     wijziging door als verschil (float-drift) en deed dat door een omgedraaide voorwaarde
     (`IC::save` r281: alleen als het gewijzigde artikel zelf samengesteld is) nooit voor gewone
     onderdelen. Perl haalde bij het toevoegen van een onderdeel de lijstprijs niet op
     (`IC::assembly_item` r1120 selecteert geen `listprice`): een nieuw samengesteld artikel kreeg
     lijstprijs 0. `adj` blijft in de tabel (nieuwe regels 1, zoals Perl) maar wordt niet gebruikt.
126. Stuklijst (SA2/SA8): cycli (A in B in A, ook via sub-stuklijsten) en meer dan 5 niveaus worden
     geweigerd; Perl controleerde niets en liep bij een cyclus oneindig door (`update_assembly`
     r569 hield alleen het directe pad bij). Elk onderdeel één keer, aantal > 0 (max. 6 decimalen),
     geen kit, niet het product zelf, een nieuw onderdeel niet vervallen. Wijzigen mag zolang de
     voorraad van het product 0 is, ook als het al verkocht is (Perl: alleen zolang ongebruikt,
     daarna "Changeup" dat het artikel laat vervallen; niet gebouwd). Perl verwijderde en schreef de
     stuklijst bij elk opslaan opnieuw (nieuwe id's); Python alleen bij een wijziging, het vinkje
     "op documenten" (`bom`) apart. De soort kan niet van of naar samengesteld wijzigen.
127. Assembleren/demonteren (SA4/SA5): vastgelegd in `x_assembly_build` + `x_assembly_build_line`
     (aantal, voorraad ervoor, kenmerk, reden, medewerker), geen grootboekboeking (zoals Perl).
     Demonteren is een eigen keuze en kan niet verder dan de voorraad van het product; Perl kon
     met een negatief aantal onbeperkt demonteren en schreef dan ook action `stock` in de
     audittrail (Python: `unstock`). Diensten in de stuklijst bewegen niet (zoals Perl).
128. Onvoldoende onderdelen (SA6): waarschuwing met bevestiging; met "voorraad controleren" op een
     onderdeel geweigerd. Perl controleerde alleen als het vinkje in het zoekscherm aan stond en
     vergeleek met een verborgen veld (`restock_assemblies` r4494, vervalsbaar).
129. "Hoeveel kan ik maken" (SA9) rekent per product met de voorraad van de directe onderdelen
     (negatieve voorraad = 0) en noemt het beperkende onderdeel. Perl verdeelde de voorraad over
     alle producten in de lijst en telde bij een tekort de al toegewezen aantallen verkeerd terug
     (`retrieve_assemblies` r860), en gaf zonder magazijnen niets terug als een onderdeel geen
     `inventory`-regels had (r822, `LEFT JOIN` met `SUM` = NULL).
130. Verwijderen van een samengesteld artikel alleen zonder voorraad en zonder productiehistorie;
     een onderdeel van een stuklijst kan niet verwijderd worden (eigen melding).
131. **Beveiliging** — beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Python: alles geparametriseerd.

**Facturen met artikelen (fase 3, `sqlledger/invoice.py` + `sqlledger/cogs.py`, `is.pl`/`ir.pl`/`io.pl`, `IS.pm`/`IR.pm`)**
132. Kostprijs (F3): elke FIFO-toewijzing wordt vastgelegd in `x_cogs_allocation` (afnemende regel,
     aanvoerregel, aantal, bedrag; migratie 6) en bij wijzigen of verwijderen precies teruggedraaid.
     Perl wees toe op volgorde van boeking (FIFO) maar draaide terug op LIFO (`reverse_invoice`
     r2127/2160, `ORDER BY trans_id DESC`), boekte bij opnieuw opslaan van een inkoop
     tegenboekingen in de verkopen op de inkoopdatum (soms vóór de verkoopdatum) en daarna opnieuw,
     en nam bij opnieuw opslaan van een verkoop willekeurige vrije inkoop. Hier: ongewijzigd opnieuw
     opslaan verandert niets; een gewijzigde regel houdt wat kan (alleen het verschil wordt bij- of
     teruggeboekt); een prijswijziging van een inkoop past de kostprijs in de verkopen aan op hun
     eigen datum. Kostprijsregels in andere boekingen worden alleen als verschil bijgewerkt; ligt
     zo'n regel in een afgesloten periode, dan wordt de wijziging geweigerd. Lock op de artikelen
     tijdens het boeken (Perl: twee gelijktijdige verkopen konden dezelfde inkoop nemen).
133. Kostprijsregels: één regel kosten/voorraad per verkoopregel en datum, met `acc_trans.id` = de
     verkoopregel (Perl: één regel per inkoop met id = de inkoopregel, en bij een latere inkoop het
     artikel-id, `IR::cogs` r1719). Een verkoop zonder voorraad krijgt de kostprijs bij de volgende
     aanvoer, geboekt in de verkoop op de datum van die aanvoer (zoals Perl, F4); alleen verkoop-
     regels (Perl nam ook retouren aan leveranciers mee). Afrondingsverschillen van de factuur (btw
     inbegrepen, valuta) komen op de grootste omzetregel, nooit op een kostprijsregel (Perl sorteerde
     de kostprijsregels mee).
134. Inkoopprijs in volle precisie: de kostprijs van een aanvoerregel staat in `x_invoice_cost` en wordt
     cumulatief verdeeld (3 voor 1,00 = 0,33 + 0,34 + 0,33, niets blijft in de voorraad). De opgeslagen
     nettoprijs (`invoice.sellprice`) van inkoop houdt de decimalen van de prijs (minstens de
     standaard), zoals bij verkoop; `IR.pm` rondde af op 2 decimalen (r939), waardoor bv. 12.600 stuks
     à 0,05316 als 0,05 de kostprijs in gingen (A heeft zulke regels uit een oudere versie).
135. Retouren (F5): een retour van een klant boekt de omzet gewoon terug en zet de voorraad terug tegen
     de oorspronkelijke kostprijs (toewijzingen van de laatste verkopen van dat artikel aan die klant;
     anders laatste, dan gemiddelde inkoopprijs); het teruggekomen stuk gaat later tegen die kostprijs
     weer de deur uit. Perl boekte de omzetregel op de voorraadrekening tegen verkoopprijs en een
     verschil via `cogs_difference`, en het teruggekomen stuk ging later tegen de verkoopprijs de FIFO
     in. Een retour aan een leverancier haalt de voorraad weg tegen de FIFO-kostprijs, het verschil
     met de retourprijs gaat naar de kostprijsrekening (Perl: voorraad eraf tegen de retourprijs);
     zonder voorraad tegen de retourprijs. Oude retouren uit Perl blijven zoals ze geboekt zijn zolang
     de regel niet wijzigt.
136. Gegevens uit Perl zonder vastlegging (oude toewijzingen, tegenboekingen) blijven met hun
     kostprijsregels staan zolang de regels van de factuur niet wijzigen (de Perl-kostprijs op de
     factuurdatum verhuist mee met een nieuwe factuurdatum). Wijzigt er een regel, dan worden alle
     oude toewijzingen van die factuur op de manier van Perl teruggedraaid en daarna vastgelegd
     opnieuw toegewezen.
137. Btw (F2): tarieven altijd uit de database (btw van het artikel ∩ btw van de relatie, geldig op de
     factuurdatum), nooit uit het formulier (Perl nam `${accno}_rate` uit verborgen velden). Btw-
     rekeningen die al op de opgeslagen factuur staan tellen mee, ook als de relatie ze niet meer heeft
     (zoals README 22 bij verkoop/inkoop; Perl liet de btw dan weg en het factuurbedrag daalde). Zijn de
     btw-vinkjes van een artikel na het boeken gewijzigd, dan rekent opnieuw opslaan met de huidige
     (zoals Perl); het scherm waarschuwt daar vooraf voor.
138. Btw inbegrepen + vreemde valuta: de opgeslagen nettoprijs trekt de btw in de valuta van de factuur
     af; Perl trok de btw in standaardvaluta af van de prijs in vreemde valuta (`IS.pm` r1076, `IR.pm`
     r908) en de kostprijs werd fout. Volledig betaald met btw inbegrepen: betaald = factuurbedrag
     (Perl vergeleek met som regels + btw en liet 0,01 open, r1285).
139. Afgesloten periode op de oude én de nieuwe datum en bij verwijderen, op de server (F6); `revtrans`,
     lock van een ander, batch en kassabon (`till`, POS volgt later) geven de leespagina. Perl
     controleerde alleen de nieuwe datum in het scherm. Verwijderen zet een gekoppelde order weer open
     (`oe.aa_id`, zoals Perl).
140. `lastcost`/`avgcost` worden binnen de transactie bijgewerkt, ook bij verwijderen van een inkoop
     (F8); Perl deed het na de commit en niet bij verwijderen, en deelde bij een som van aantallen
     die door float-ruis net niet 0 was (A: avgcost −9·10¹⁶). `parts.bin` en gewicht worden niet
     overschreven (Perl: `bin` uit het formulier bij elk boeken); een vervallen artikel blijft
     vervallen (Perl zette het bij opnieuw opslaan weer actief, `reverse_invoice` r2095).
141. Inkoop: de standaard betaalrekening (`ap.bank_id`) is de rekening van de laatste betaling, anders
     de gekozen rekening; `ir.pl post` verloor hem (lusvariabele buiten de lus, r1248) en schreef
     NULL. Niet vergeleken in `compare/`.
142. Samengestelde artikelen (SA7): verkopen zoals Perl — voorraad van het product omlaag, per onderdeel
     (ook uit sub-stuklijsten) een regel `assemblyitem` met FIFO-kostprijs van het onderdeel, vóór de
     regel van het product. Een creditnota zet de onderdelen terug tegen hun oorspronkelijke kostprijs
     (F5); Perl weigerde dat in het scherm ("Cannot return assembly!") en gebruikte in de bibliotheek
     `cogs` met een negatief aantal (`IS.pm` r1672). Inkopen van een samengesteld artikel en kits worden
     geweigerd.
143. Factuurregels: ongewijzigde regels van een bestaande factuur worden herkend aan hun regel-id
     (verborgen veld), zelfde artikel en teken; Perl schreef alle regels opnieuw. Regels met aantal 0
     vallen weg (zoals Perl). Leverdatums die Python niet kan lezen (A: jaar 20121) worden als tekst
     bewaard en het scherm vraagt om een geldige datum. Prijs/aantal/korting maximaal 6 decimalen,
     korting 0–100%.
144. Niet gebouwd (F8): kostprijs/leverancier per verkoopregel tonen
     (wordt wel bewaard: nieuwe regel = laatste inkoopprijs omgerekend naar de factuurvaluta, zoals
     Perl), gewicht/vracht (`cargo` blijft staan), magazijnen, bulk "facturen genereren" en
     "samenvoegen", kits, kassa.
145. **Beveiliging** — beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Python: alles geparametriseerd.
146. Overige Perl-fouten IS/IR voor de memo: `voucher`-koppelingen raken los bij opnieuw boeken en
     betaal-id's worden hernummerd (Python: batches geweigerd; betaal-id's zoals verkoop/inkoop);
     `checkinventory` alleen in het scherm en tegen een verborgen voorraadveld; `fxdiff = 0 if
     rowcount == 2` telt lege regels mee; `datepaid` wordt de datum van de contante korting als die er
     is (de korting gaat als laatste betaling mee; Python doet hetzelfde, zoals verkoop/inkoop).
147. Contante korting met btw-correctie (`cdt`): de btw die terugkomt is het aandeel van de korting in het
     factuurbedrag inclusief btw (2% korting op 100 + 21 btw = 0,42 btw terug). Perl deelde de korting door
     het bedrag zonder btw (`IS.pm` r1342, `IR.pm` r1149) en boekte 0,51 terug.
**Orders en offertes (fase 3, `sqlledger/order.py`, `oe.pl`/`OE.pm`/`io.pl`)**
148. Voorraad (O3): opslaan, omzetten en verwijderen van een order raken `parts.onhand` niet en schrijven
     geen `inventory`-regels; alleen de factuur beweegt de voorraad. Perl verlaagde de voorraad al bij het
     opslaan van een order met verzonden aantallen, schreef magazijnbewegingen en liet de factuur dan de
     voorraad overslaan; na verwijderen van zo'n factuur bleef de voorraad verlaagd (scenario oe 8).
149. Omzetten (O2): de bron sluit pas als het doel opgeslagen (order) of geboekt (factuur) is, in dezelfde
     transactie. Perl sloot de offerte/order al bij de klik, ook als de order of factuur nooit opgeslagen
     werd. "Order → offerte" sluit de order niet meer (Perl wel). Koppelingen staan in `x_oe_link`.
150. Deellevering (O4, ON2): geen kolom "Verzonden" in het orderscherm; in het factuurscherm aantallen
     verlagen of regels weghalen. Bij het boeken krijgt `orderitems.ship` het gefactureerde aantal (nooit
     meer dan besteld) en wordt het restant een nabestelling (zelfde nummer en datum, `backorder`), zoals
     Perl. Perl maakte de nabestelling vóór de factuur (andere volgorde van id's).
151. Factuur verwijderen (F6, ON3): de order gaat weer open met verzonden aantallen 0, en een nabestelling
     die bij deze factuur ontstond verdwijnt als hij nog open en onaangeroerd is (vingerafdruk bij
     ontstaan); anders blijft hij staan met een melding. Perl liet hem staan (restant dubbel open). Een
     offerte die direct gefactureerd is (ON4) gaat ook weer open.
152. Wijzigen (O6, ON1): een gefactureerde of gesloten order en een order in een afgesloten periode kunnen
     op de server niet opgeslagen of verwijderd worden; een gesloten order zonder factuur kan heropend
     worden. Perl liet gesloten orders opslaan (keuze Open/Gesloten) en controleerde "gefactureerd" alleen
     in het scherm. Verwijderen van een order uit een offerte zet de offerte weer open.
153. Btw (F2): tarieven uit de database (btw van het artikel ∩ btw van de relatie op de orderdatum); Perl
     nam de tarieven uit het formulier. Rekenwijze verder als `OE.pm save` (ook btw inbegrepen).
154. Opslaan van een bestaande order: lege tekstvelden blijven zoals ze stonden ('' of NULL), kostprijs en
     leverancier per regel blijven NULL als ze dat waren (Perl schreef 0), de medewerker blijft ook als
     die niet meer in de keuzelijst staat (Perl zette 0), `parts.weight` wordt niet overschreven en
     vrachtgegevens (`cargo`) blijven bij hun regel. Perl liet na opslaan een lock op ar/ap staan.
155. Aanbetalingen op orders (O5) worden niet ondersteund: een order met grootboekregels is een
     leespagina (C en A: geen). Niet gebouwd (O5): inkooporders genereren uit verkooporders,
     orders samenvoegen, verzenden/ontvangen met vracht, pak-/picklijsten; printen/mailen fase 4.
156. Samengestelde artikelen (SA11) mogen op verkooporders en offertes, zonder voorraadbeweging; op een
     inkooporder geweigerd (zoals de inkoopfactuur).
157. Audittrail: zoals Perl `saved` bij opslaan (ook bij omzetten en het ontstaan van een nabestelling);
     het sluiten bij factureren staat bij de factuur (`posted`), niet als extra regel bij de order.
158. **Beveiliging** — beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Alle SQL in `order.py` is geparametriseerd.
159. Overige Perl-fouten OE voor de memo: aanbetaling verdwijnt uit het grootboek bij omzetten en wordt
     dubbel geboekt bij "opslaan als nieuw" en samenvoegen; opnieuw opslaan wist de verzendhistorie
     (`inventory`); verzenden boekt het niet-afgekapte aantal; samenvoegen hangt alle referenties aan elke
     nieuwe order en neemt kopvelden van de laatste order; `lastname_used` en `get_name` zetten de notities
     van de relatie in de interne notities van een nieuwe order.

**Herhalen van facturen en orders (fase 3 stap 4, R1, RH1–RH6; `sqlledger/recurring.py`)** — de afspraken van
fase 2 (85–94: datums, lock, nummer, koers, vervaldatum, betaling, omschrijving) gelden ook hier.
160. Inhoud van de kopie (RH1, RH2): prijzen, korting, omschrijving, eenheid, project, kostprijs/leverancier per
     regel van het origineel (zoals Perl: `invoice_links`/`prepare_invoice`, `order_links`/`prepare_order`); btw
     zoals een nieuwe factuur of order op de nieuwe datum (btw-instellingen van artikel en relatie op dat
     moment, zoals Perl `all_taxaccounts`); btw-rekeningen die alleen nog op het origineel staan tellen niet
     mee. Voorraad, kostprijs (FIFO) en samengestelde artikelen zoals bij een nieuwe factuur.
161. Voorraad (RH3): een verkoopkopie met een artikel met "voorraad controleren" en te weinig voorraad wordt
     overgeslagen met de melding welke regel; de volgende datum blijft staan. Perl (`IS::post_invoice`)
     controleert dat niet (alleen het scherm, `io.pl validate_items`) en boekte de kopie met negatieve voorraad.
162. Leverdatums (RH4): de leverdatum op factuur- en orderregels schuift evenveel dagen mee als de boekdatum,
     net als de leverdatum in de orderkop. Perl schoof alleen de kop (`reqdate`, am.pl 4824) en hield de oude
     datums op de regels.
163. **Fout in Perl** — orderkopie (RH5): `prepare_order` laadt het geleverde aantal (`ship`) en de
     nabestelvlag; `OE::save` schreef die in de kopie, zodat een nieuwe order deels geleverd of als
     nabestelling leek. Python: een nieuwe open order, geleverd 0, geen nabestelvlag, geen koppeling met een
     factuur, ook als het origineel gefactureerd of gesloten is (dat mag als sjabloon). Herkomst via de
     audittrail.
164. Offertes en aanvragen (RH6) krijgen geen schema (zoals Perl: knop alleen bij orders, `AM::recurring_
     transactions` slaat ze over); de server weigert het ook bij een zelfgemaakte POST, en een schema op een
     offerte uit oude data wordt niet verwerkt. Een factuur die uit een order is gemaakt wordt gekopieerd
     zonder koppeling (`order_id`): de order blijft zoals hij is (alleen het ordernummer staat als tekst op
     de kopie). Kassabonnen (POS) worden niet herhaald.

**Documenten (fase 4 stap 0, `sqlledger/printdata.py`, io.pl `print_form`, IS/IR `invoice_details`)**
165. Printen wijzigt de boeking niet. Perl schreef bij elke afdruk `ar.dcn` en `ar.bank_id` weg
     (IS::invoice_details); het betaalkenmerk wordt al bij het opslaan berekend.
166. **Fout in Perl** — `part`/`service` per regel zijn leeg in plaats van de tekst "NULL" (Perl duwde het kale
     woord NULL in de lijst, waardoor `<%if service%>` altijd waar was).
167. `<accno>_tax` is de btw van die rekening (Perl gaf daar de btw na contantkorting, meestal 0);
     `cd_<accno>_tax` en `cd_available` zijn opgemaakt als bedrag (Perl: kaal getal).
168. Bedragen met Decimal, pas aan het eind afgerond (Perl: drijvende komma): geen "-0" of "****", en het
     bedrag in woorden mist geen cent meer (Perl kapte 12,36 af tot 35 cent). Gewicht en volume per regel
     altijd opgemaakt en zonder e-notatie (Perl gaf ze deels kaal uit verborgen velden en telde "4.14" in de
     notatie 1.000,00 als 414 mee in het totaalgewicht).
169. De lijsten `discount` en `projectnumber` per regel bestaan (Perl duwde de korting in een symbolische array,
     omdat `discount` al de klantkorting bevatte, en verloor het projectnummer).
170. `fdy`/`ldy` (±n) zijn de eerste en laatste dag van het jaar (Perl gaf de maand van de factuurdatum).
171. Betalingen op een rekening zonder koppeling "betaling" staan in de lijst, zoals op het factuurscherm
     (README 25b/c); Perl liet ze weg.
172. **Fout in Perl** — bedrag in woorden in het Nederlands: spelfouten verbeterd (twalf → twaalf, achtien →
     achttien, "honderdnul" weg) (`num2text.py`).
173. Printen = PDF in de browser (D7): geen printercommando, geen spoolmap, geen LaTeX. Het vinkje "geprint"
     wordt bij elke PDF van een boekhouder gezet (Perl deed dat alleen bij een printer of de wachtrij).
     Sjablonen zijn HTML (Jinja) in plaats van SL-sjablonen met `<%veld%>`; de variabelen hebben dezelfde
     namen, zodat een eigen sjabloon ze 1-op-1 kan gebruiken. Lege regels in het bedrijfsadres (Beheer) worden
     overgeslagen.
Getoetst met de Perl-driverstap `print_ui` + `compare/compare_print.py` (alle sjabloonvariabelen van is.pl/ir.pl
print, ook met groeperen/sorteren): A 300 + C 600 verkoop en A 218 + C 211 inkoop, 0 onverklaarde
verschillen; overige verschillen staan per soort in `explained()`.
Stap 2: dezelfde vergelijking voor orders en offertes (`compare_print.py --oe`, oe.pl print: orderbevestiging,
pakbon, picklijst, inkooporder) en verkoop/inkoop zonder artikelen (`--arap`, ar.pl/ap.pl print): C 300
verkooporders (900 documenten) en 200 inkooporders, A 22 inkooporders, C 400 + 300 en A 600 + 300
boekingen zonder artikelen, A 300 pakbonnen en 150 picklijsten vanaf de factuur, vreemde valuta apart,
offerte/offerteaanvraag en samengestelde artikelen op testdata: **0 onverklaarde verschillen**; met
`--missing` geen sjabloonvariabele die Perl wel en Python niet kent.

**Mailen (fase 4 stap 1, `sqlledger_web/mailer.py`, io.pl `e_mail`/`print_form`, `Mailer.pm`)**
174. Verzenden via SMTP per administratie in plaats van het programma `sendmail` op de server; afzender is de
     administratie (Beheer > E-mail) in plaats van naam/e-mail van de gebruiker. Antwoorden gaan naar het
     antwoordadres van de administratie of anders naar het e-mailadres van de gebruiker (Reply-To).
175. Adressen en onderwerp worden gecontroleerd: alleen "adres" of "Naam <adres>", geen regeleinden, max. 20
     ontvangers (beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna).
176. De handtekening staat zichtbaar in het berichtveld (Perl plakte haar er bij verzenden onzichtbaar onder).
     Het bericht heeft een standaardtekst per documentsoort en documenttaal; Perl begon leeg met onderwerp
     "<label> <nummer>" (bv. "Invoice 7").
177. Altijd PDF als bijlage; geen inline HTML/tekst en geen keuze voor PostScript (D1/D9).
178. De notitie in de interne notities heeft de datum in de notatie van de gebruiker (Perl: `localtime`, Engels)
     en zonder de handtekening; de opbouw `[email]`/Datum/Aan/Cc/Bcc/Onderwerp/Bericht is gelijk.
179. Status "geprint"/"gemaild" staat als tekst onder de knoppen; Perl had vinkjes die je zelf kon aanzetten en
     die bij opslaan werden bewaard. Hier zet alleen de handeling (PDF, mailen) ze.

**Overige documenten (fase 4 stap 2, `printdata.order_vars`/`transaction_vars`, `doctypes.py`, OE::order_details,
arapprn.pl `print_transaction`)**
180. Een document wordt gemaakt van de **opgeslagen** order/boeking. Perl printte het formulier zoals het op
     het scherm stond (ook niet opgeslagen) en trok bij printen zonder nummer een nieuw nummer uit de teller.
     Na een wijziging zonder opslaan zegt het scherm "Sla eerst op".
181. Soorten zoals D2/DS1: geen bin list, werkorder, barcode, remittance voucher, cheque of kwitantie; geen
     pakbon/picklijst bij een retour aan een leverancier (Perl bood die bij een debetfactuur).
182. Orders: de lijstprijs per regel is gevuld (Perl laadde hem bij een opgeslagen order niet: leeg).
183. **Fout in Perl** — onderdeelregels van samengestelde artikelen (`assembly_details`): Perl voegde ze maar
     aan een deel van de lijsten toe (o.a. `sell`, `part`, `service`, `deliverydate`, `lot`, `make`), zodat
     kolommen verschoven. Hier zijn alle lijsten even lang; nieuwe lijst `bom` ("1" = onderdeelregel). Alleen
     onderdelen met "op documenten" en één niveau (werkorder met alle niveaus is niet gebouwd).
184. Verkoop/inkoop zonder artikelen: de interne notities zijn die van de boeking (**fout in Perl**:
     `AA::get_name` verving ze door de notities van de relatie, die zo op het document konden komen). Een
     geboekte btw-rekening die de relatie niet meer heeft blijft op het document (**fout in Perl**: weggelaten,
     dus een verkeerd totaal, zoals F2 bij facturen). Het btw-tarief staat in de getalnotatie van de
     gebruiker (Perl: `$myconfig` in plaats van `\%myconfig`, dus "17.5"). De betaalde korting wordt goed
     gelezen (**fout in Perl**: "52.42" in de notatie 1.000,00 werd 5.242). Zoals Perl: alle btw-rekeningen
     van de relatie staan erin, ook met 0,00; een creditnota/debetnota heeft geen contantkorting.
185. Btw-nummer en omschrijving per btw-rekening komen van de tariefregel die gebruikt is (Perl nam de eerste
     regel; bij twee tarieven zonder einddatum willekeurig).
186. **Fout in Perl** — bedrag in woorden Duits (`locale/de/Num2text`): boven 999 "zwanzigdrei" in plaats van
     "dreiundzwanzig" (1.023 = "eintausendzwanzigdrei"); Frans (`locale/fr/Num2text`): 2.000 = "Mille" (de
     "deux" viel weg), 91 = "Quatre-vingt Onze". Hier correct (Duits als één woord, Frans traditionele
     spelling, kleine letters). In de webapp volgt het bedrag in woorden de taal van het document (variant,
     anders Beheer > Instellingen > Documenttaal); Perl nam de taalcode of de landcode van de gebruiker.
187. De picklijst is gesorteerd op locatie (`bin`), daarna regelvolgorde; Perl volgde de gekozen sortering
     (standaard regelvolgorde).
188. Nieuwe sjabloonvariabelen (niet in Perl): `toship` per orderregel (besteld − geleverd, voor de pakbon),
     `amountdue` bij boekingen zonder artikelen (te betalen, ook 0 opgemaakt), `customer`/`vendor` = naam
     van de relatie (Perl: formulierveld), `exchangerate` alleen bij vreemde valuta.
189. Sjablonen beheren: downloaden/uploaden met controle en vorige versies in plaats van bewerken in de browser
     (am.pl `edit_template`; beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna); logo per administratie.
190. Documentinstellingen (fase 4 stap 3): wat in SQL-Ledger vast in de .tex-sjablonen stond (kolommen, eigen
     kolomnamen, vaste teksten, bankgegevens, btw-nummer) of per gebruiker in `users/<naam>.conf` (SL 2.8), stelt
     de beheerder per administratie in zonder sjabloon te bewerken. Opslag in `x_doc_setting`, niet in `defaults`:
     `AM::save_defaults` (3.2.12 r1603) doet `DELETE FROM defaults` en schrijft alleen bekende
     sleutels terug.
191. Vaste bankrekening op het document (instelling): IBAN, BIC en kenmerk van die rekening in plaats van de
     rekening van de laatste betaling (AA::company_details); zonder instelling zoals Perl. Bij verkoop zonder
     artikelen geeft `transaction_vars` (zoals arapprn.pl) geen IBAN, dus daar staat de betaalzin alleen met een
     vaste rekening.
192. Offerteaanvraag (zonder prijzen) met een subtotaalregel (groeperen): de standaardset liet het subtotaal
     zien; nu niet meer. Komt in de app niet voor (er wordt niet gegroepeerd geprint).
193. Ouderdomsanalyse, aanmaningen en rekeningoverzicht rekenen met het **openstaande bedrag op de peildatum**
     (fase 4 stap 4a, SB5): huidig openstaand + wat ná de peildatum op de debiteuren-/crediteurenrekening van de
     boeking is afgeboekt (`reports_arap.open_asof`). Perl (RP.pm r1253/1288/1504) gebruikt het huidige `paid`,
     waardoor een overzicht met een oude datum latere betalingen al als betaald toont. Met een peildatum op of na
     de laatste betaling is de uitkomst gelijk aan Perl.
194. Ouderdomsanalyse met één gekozen kolom: Perl (RP.pm r1331) gebruikt ook dan `<`, waardoor met alleen
     "lopend" de boekingen van de peildatum zelf wegvallen; hier `<=`.
195. Aanmaning per klant en valuta met alle vervallen facturen (SB1) in plaats van één brief per factuur (rp.pl
     `do_print_reminder`); het niveau blijft per factuur in `status.reminderN`, de brief krijgt het hoogste
     volgende niveau van de opgenomen facturen (sjabloon `reminder1..3`). Perl mailt één aanmaning per keer.
196. Negatieve posten (creditnota's, vooruitbetalingen) zijn geen aanmaning maar staan op de brief onder "te
     verrekenen" en gaan van het totaal af; klant met een totaal ≤ 0 krijgt geen aanmaning (SB2). Perl maakte
     ook voor een creditnota een aanmaning.
197. Aanmaanniveau wordt vastgelegd bij definitief printen (PDF) of geslaagd mailen (SB3), met het vinkje
     geprint/gemaild in de statusregel `reminderN` en een audittrailregel; Perl legt alleen vast met "Save
     Level" en zet bij printen/mailen van aanmaningen en overzichten niets. Na niveau 3 blijft het 3 (Perl: 4,
     de keuzelijst toont dan 1 en opslaan zet het terug naar 1). Handmatig niveau 0 wist het (Perl: alleen
     1–3 in de keuzelijst).
198. Rekeningoverzicht per klant **en valuta**: rp.pl `do_print_statement` (r3450) slaat bij een klant met
     posten in twee valuta het tweede overzicht over als ze samen geprint worden. Bankgegevens: van de nieuwste
     openstaande factuur (Perl: geen), een vaste rekening uit Documentinstellingen gaat voor.
199. Meerdere brieven in één PDF (aanmaningen, overzichten): elk document apart opgemaakt met eigen
     paginanummering, daarna samengevoegd (`DocumentRenderer.render_many`, WeasyPrint) in plaats van één
     printeropdracht per document of `pdftk` (bp.pl combine).
200. Betalingsspecificatie van een **geboekte** betaling (SB7/SB8): na het boeken, vanaf Corrigeren en vanuit het
     betalingsregister, als PDF of per mail. SQL-Ledger drukt alleen af vanaf het betaalscherm vóór het boeken
     (cp.pl `print_payment`/`print_payments`, alleen PostScript/PDF naar printer of scherm, geen mail) en legt niets
     vast; hier een audittrailregel per factuur.
201. Een betaling wordt herkend aan relatie + betaalrekening + datum + kenmerk (zoals `payment_register`); oude
     betalingen zonder kenmerk van dezelfde relatie op dezelfde dag en rekening staan samen op één specificatie.
202. Inhoud: per factuur het factuurbedrag (Perl: het openstaande bedrag vóór de betaling, `due`), alleen posten
     waarop in deze betaling iets betaald of gekort is (Perl `print_payments` zet ook posten met 0 erop) en de
     korting ook bij het scherm voor meerdere relaties (Perl-fout 25). Het bedrag in woorden (`text_amount`) is
     beschikbaar voor eigen sjablonen; het standaardsjabloon is geen cheque en toont het niet.
203. Btw-tarieven (BT1): een tarief waarin al geboekt is, kan in Beheer > Btw niet meer van percentage veranderen,
     verdwijnen of eerder eindigen dan de laatste boeking met die btw-rekening (journaalregel op de rekening, of een
     factuur-/orderregel met een artikel dat de rekening heeft, zodat ook 0% telt); het nummer mag wel. Een nieuw
     tarief gaat via "Nieuw tarief per datum" (percentage + ingangsdatum; het lopende tarief krijgt de dag ervoor als
     einddatum). SQL-Ledger (AM::save_taxes) wist en herschrijft de tarieven zonder controle, waarna oude facturen
     bij afdrukken en opnieuw opslaan met het nieuwe tarief rekenen. BT2: documenten blijven rekenen met het tarief
     op de factuurdatum (zoals SL). Proef op A en C (teruggedraaid): per btw-rekening lukt een nieuw tarief
     per morgen en wordt overschrijven geweigerd; ongewijzigd opslaan (`am_keeptest`) 0 verschillen.
204. Batch (SB9): één scherm Documenten > Batch voor alle documentsoorten in plaats van bp.pl per soort met een
     wachtrij. Ook verkoop/inkoop zonder artikelen (BP.pm `get_spoolfiles`: alleen `invoice = '1'`) en creditfactuur/
     debetnota als eigen soort (BP.pm: `formname LIKE '%invoice'`, factuur en creditfactuur door elkaar). Geprint/
     gemaild als keuze alle/ja/nee; SL toont zonder vinkje bij (niet) geprint/gemaild niets (`a.id = 0`). Geen
     spoolmap of wachtrij (D8) en geen printer (D7). Filters geparametriseerd (beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna).
205. Status en logboek in de batch (SB10): `printed` pas na een gelukte samengevoegde PDF, `emailed` en de notitie
     `[email]` pas na verzending, audittrail per document. SQL-Ledger zet de status vóór het printen/versturen
     (io.pl r1829/1859, bp.pl r529), schrijft in een batch geen audittrail (io.pl r1838) en zet bij printen uit de
     wachtrij niets (bp.pl r423) — Perl-fout 22.
206. Mailen in batch (SB11): synchroon, maximaal 200 per keer, overzicht vooraf (wie afvalt zonder e-mailadres),
     onderwerp/bericht optioneel voor de hele reeks met plaatshouders per document, resultaat per document; een fout
     (adres of opmaak) stopt de rest niet. SL (bp.pl `email`) roept per document `print_form` aan zonder overzicht en
     zonder resultaat per document. Samenvoegen zoals 199 (SB14).
207. Herhalen met mailen en PDF (SB12): mailen en de PDF gebeuren ná het boeken van alle aangevinkte schema's; een
     mislukte mail (geen of fout adres, e-mail niet ingesteld, opmaakfout) laat de boeking en het doorgeschoven schema
     staan, staat met reden in het resultaat en in het logboek (`recurring_email_failed`/`letter_email_failed`) en zet
     `emailed` niet; opnieuw mailen kan vanaf de boeking of via Documenten > Batch ("niet gemaild"). De PDF is één
     samengevoegde PDF via een knop op de resultaatpagina en zet pas daarna `printed`; een printer wordt niet gebruikt
     (D7). SQL-Ledger (am.pl `process_transactions`, r4784/4846/4893/4917) schuift het schema niet door als printen
     mislukt (de volgende keer wordt dezelfde termijn opnieuw geboekt: dubbele factuur), mailt orders ook als opslaan
     mislukte, schuift bij een mislukte mail wel door zonder melding bij de boeking, en gebruikt `$myconfig->{printer}`
     dat altijd leeg is — Perl-fout 23.
208. Schemascherm mailen/PDF (SB13): de keuzes zijn de documentsoorten van het scherm van de boeking
     (`doctypes.SCREEN_KINDS`), ook bij verkoop/inkoop zonder artikelen; arap.pl `schedule` biedt daar alleen
     afdrukken aan (`type !~ /transaction/`), maar am.pl `email_recurring` voert een mailregel wel uit. Opgeslagen met
     de formname zoals SL en formaat `pdf` (SL: html/pdf/postscript); een printernaam blijft bewaard en wordt genegeerd.
     Soorten die bij ons niet bestaan (werkbon, bin list, barcode …) blijven in een Perl-schema bewaard en worden op het
     scherm genoemd, maar niet uitgevoerd.
209. Inkoopfactuur uploaden (IU1–IU5) is nieuw (geen SL-scherm). De boeking zelf gaat door het gewone inkoopformulier:
     één kostenregel met het netto, het btw-bedrag als vast bedrag op de gekozen btw-rekening (niet "berekenen"),
     `taxincluded` uit; vergeleken met ap.pl (`compare/compare_attach.py`). Het factuurnummer is hier verplicht (op het
     gewone scherm niet). De vaste kostenrekening per leverancier staat in `x_vendor_default` (SL kent hem niet).
210. Bijlagen (D17/IU6): alleen pdf, jpg en png tot de maximale uploadgrootte (stap 4f: standaard 10 MB), herkend aan de inhoud (SL `rd.pl`: elk bestand); de bestandsnaam krijgt zo nodig de extensie van de inhoud, zodat SL hem ook goed toont.
     Opslag en codering als `Form::save_reference` (blokken van 512 bytes, `pack 'u'`), heen en terug vergeleken met
     `Form::get_reference`/`save_reference`. Bestanden die SL wel toeliet maar die geen pdf/jpg/png zijn, toont de app
     niet in de browser maar alleen als download (beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna).
211. Bijlagen bij een **opgeslagen** boeking worden meteen toegevoegd of verwijderd, zonder de boeking opnieuw te boeken
     (SL: via Opslaan van de boeking of in rd.pl), ook in een afgesloten periode; het grootboek verandert niet. Een
     archiefstuk verdwijnt pas met de laatste verwijzing ernaar, ook bij opnieuw opslaan of verwijderen van een boeking
     (Perl-fout 28). Kopiëren en "Opslaan als nieuw" nemen bijlagen niet mee (aa.pl nam de verwijzing mee).
212. Maximale uploadgrootte (stap 4f, MU1–MU5): Beheer > Instellingen, sleutel `defaults.max_upload_size` in MB zoals
     SQL-Ledger 4.x (SL 3.2.12 kent hem niet en wist hem bij opslaan van Systeem > Standaarden; dan weer 10 MB). Leeg = 10 MB,
     instelbaar 1–25 MB; door SQL-Ledger 4.x geschreven waarden: 0 = leeg, breuken naar beneden, boven 25 =
     25. Geldt voor alle bijlagen (uploadscherm en blok Bijlagen, ook bij het boeken van een nog niet opgeslagen
     bijlage); bankafschrift (10 MB), sjablonen (512 kB) en logo (1 MB) houden hun vaste grens. Gecontroleerd op de
     server (bibliotheek: `attachments.max_size`, ook in `store`) én in de browser vóór het versturen.
     1 MB = 1.048.576 bytes.
213. Rapporten als PDF (stap 5, D14/RP1–RP10) is nieuw: SL 3.2.12 kon dat niet (`rp.pl print_report` doet alleen
     `$form->debug`; de keuze PDF bij "Print Report" gaf niets). Alleen W&V en balans hadden "Sjabloon gebruiken"
     (`income_statement.html`/`balance_sheet.html`, HTML op het scherm, ook eigen versies voor A en C); die optie en
     sjablonen zijn niet nagebouwd (RP6): één vaste opmaak, alle kolommen van het scherm.
214. Startscherm is een dashboard (stap 5b, DB1–DB13); SL 3.2.12 had geen startscherm met cijfers. De open posten
     rekent het dashboard met één query (`Dashboard.open_items`, zelfde `open_asof` als de ouderdomsanalyse) in plaats
     van `Reports.aging` per relatie en valuta (A 5–9 s → 0,02–0,13 s); bedragen in de standaardvaluta. Btw-
     aangiftetijdvak en afgevinkte aangiften zijn nieuw (eigen tabellen, SL kent ze niet).

215. Bijlagen op facturen met artikelen, orders/offertes, relaties en artikelen (stap 6a, RB1/RB2/RB6/RB7). Verwijderen
     van een boeking, order, relatie of artikel haalt **alle** verwijzingen weg, ook vertrouwelijke van andere
     gebruikers (`Form::delete_references` wiste alleen eigen en openbare en liet de rest als wees achter); het
     archiefstuk pas bij de laatste verwijzing. Een archiefnummer uit het formulier wordt alleen gekoppeld als de gebruiker dat stuk mag zien, of als het een los
     stuk zonder verwijzing is (rd.pl "Document toevoegen") (beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna). Omzetten neemt de verwijzingen mee zoals io.pl `create_form`; kopiëren en
     "Opslaan als nieuw" (is.pl/oe.pl/ct.pl) niet. Toevoegen controleert dat de soort bij het record past (een offerte
     is geen order, een dienst geen artikel). Naast de rd.pl-namen kent de app de formnames die SL zelf schrijft
     (`ar_credit_note`, `ap_debit_invoice`, `kit` …): een bijlage op een creditnota kreeg na opnieuw opslaan
     `ar_credit_note` en was in 4e daarna niet meer te verwijderen (gerepareerd).

216. Foto's verkleinen en dubbele bestanden (stap 6b, RB4/RB5, BB3/BB4). SL 3.2.12 bewaart het bestand zoals
     het binnenkomt; wij verkleinen jpg/png tot 2.400 px en halen alle metadata weg (pdf ongewijzigd), dus een foto die
     de app opslaat is niet byte voor byte het origineel. Bij een identieke upload
     waarschuwen wij en maken na bevestigen een nieuw
     archiefstuk. Hash = sha256 hex van het opgeslagen bestand, in `archive.hash` als die kolom bestaat (SQL-Ledger 4.x),
     anders `x_archive_hash` (SQL-Ledger 3.2.12 kent hem niet).

217. Overzicht bijlagen (stap 6c, RB3) in plaats van rd.pl "Documenten zoeken": geen "Document toevoegen" (los, zonder
     koppeling) en geen "Koppelen aan"/"Ontkoppelen"; mappen (`reference.folder`) niet als filter. Nieuw: datum en
     gebruiker van toevoegen (`x_archive_added`, SL legt dat niet vast). Een los archiefstuk mag iedereen met Rapporten
     openen (zoals rd.pl); weggooien alleen de beheerder en alleen als het nergens meer aan hangt.
218. Menu (MN1–MN7): een rubriek **Btw** die SL 3.2.12 niet kent (daar: AR/AP > Reports > Tax collected/paid en
     Non-taxable, System > Taxes): btw-rapporten, btw-tarieven en straks btw-aangifte en -rubrieken bij elkaar;
     tussenkopjes in Verkoop, Inkoop, Kas en bank, Btw en Beheer; Batch (in SL een eigen hoofdmenu) staat als
     "Documenten in batch" onder Verkoop, Inkoop, Orders en Offertes. Verder dezelfde rubrieken als SL.
219. Btw-aangifte (BTW_ANALYSE.md BA1–BA9): SQL-Ledger kent geen aangifte en geen koppeling btw-rekening → rubriek.
     Nieuw: rubrieken per btw-rekening en per klant, overzicht, aangifteformulier, btw-boeking en vastleggen per tijdvak,
     alles in eigen tabellen (`x_vat_box`, `x_vat_customer_box`, `x_vat_return`, `x_setting`); SL ziet alleen het
     gewone memoriaal. De btw telt op de datum van de btw-regel (`acc_trans.transdate`), het oude btw-rapport op de
     factuurdatum (verschilt bij 5 facturen van A); boekingen op de afdrachtrekening tellen niet mee.
220. ICP-opgaaf (ICP_ANALYSE.md ICP1–ICP11): SQL-Ledger kent geen ICP-opgaaf. Nieuw, op basis van rubriek 3B van de
     aangifte; ICP-soort per klant en vastgelegde opgaven in eigen tabellen (`x_icp_customer`, `x_icp_return`,
     `x_setting` `icp_period`). Nieuwe controle bij het opslaan van een relatie: een klant in rubriek 3B (rubriek bij de
     klant of een btw-rekening op 3B) en een leverancier met een btw-rekening op 4B moeten een geldig btw-nummer uit
     een ander EU-land hebben (vorm per land, EL/XI; geen VIES); SL accepteert elk btw-nummer.
221. Administratie wisselen (ADMINISTRATIE_WISSELEN_ANALYSE.md AW1–AW7): in SL hoort een login bij één dataset
     (`login@dataset`, opnieuw inloggen om te wisselen). Nieuw: keuzemenu in de kopregel met recent gebruikt per
     gebruiker (`recent_company`) en een korte code per administratie (`company.code`), beide in de app-database; de
     SL-database verandert niet.
222. Nieuwe administratie (REKENINGSCHEMA_ANALYSE.md RS1, RS9, RS10): SL maakt een dataset in admin.pl
     (`User::dbcreate`) met een van ±70 meegeleverde `sql/*-chart.sql` (verouderde tarieven, geen btw-rubrieken).
     Hier: Systeem > Administraties > **Nieuwe administratie** met hetzelfde SL-schema (`sqlledger/slschema`, ongewijzigd
     uit 3.2.12, databaseversie 3.2.4) en een eigen standaardschema uit `sqlledger/charts/*.csv` (land, taal van de
     omschrijvingen, actuele btw-tarieven, in NL de rubrieken van de aangifte en de afdracht-/afrondingsrekening).
     Daarnaast in `defaults` `yearend` en `audittrail = 1` (SL zet die niet) en de medewerker voor de aanmaker; de
     oude SL-schema's worden niet aangeboden (RS11). Land en schema staan in `x_setting` (`country`, `chart`).
223. NL-rekeningschema's en verlegde btw (REKENINGSCHEMA_ANALYSE.md RS3, deelstap 2): SL kent negatieve
     btw-tarieven (IS/IR/AA rekenen in twee rondes, `rate * ml > 0`: eerst de positieve, dan de negatieve tarieven),
     maar Python weigerde ze in Beheer > Btw-tarieven. Nu toegestaan zoals Perl, begrensd op -100 % tot 100 %. Een
     paar −21 % (te betalen, rubriek 2A/4A/4B) en +21 % (te vorderen, 5B) bij de leverancier geeft op de inkoop
     netto geen btw en in de aangifte beide rubrieken; de grondslag wordt per helft van het paar verdeeld
     (`vatreturn.split_base`), anders telde het nettobedrag dubbel. Schemabestanden: negatief tarief alleen op
     2A/4A/4B en alleen met een 5B-partner. De RGS-code per rekening staat in een eigen tabel `x_account_rgs`
     (rekening, RGS-versie, code; migratie 15), gevuld bij een nieuwe administratie; SL merkt er niets van.
224. Eerste start (EERSTE_START_ANALYSE.md ES1–ES9): SL begint met `admin.pl` en een leeg `root login`-wachtwoord
     dat wie het eerst komt instelt. Hier maakt de server de app-database zelf aan en leidt zolang er geen gebruiker
     is alles naar de wizard `/welkom`, die een **startcode** uit het serverlog vraagt (alleen de hash in `app_meta`,
     nieuw bij elke start, foute code telt mee voor de IP-blokkade). `init-db` en `create-admin` blijven als noodroute.
     Ook: een schema mag geen rekening "0" hebben (`chartfiles.check`): Perl behandelt "0" als onwaar en laat zo'n
     rekening/kop in proefbalans en balans weg (gevonden met `newcompany_check` bij `leeg-de`).
     Koppelen van een database ouder dan SQL-Ledger 3.2.4 wordt geweigerd (ES12; SL werkt hem bij het inloggen bij,
     hier eerst bijwerken tot Back-up inlezen dat doet). Bankgegevens van een betaalrekening staan in Beheer >
     Rekeningschema (ES11; SL 3.2.12 heeft er een apart scherm voor, `am.pl bank_accounts`).

225. Huisstijl (HUISSTIJL_ANALYSE.md HS1–HS12): SL kent alleen de LaTeX-sjablonen (`letterhead.tex`: één kopregel met bedrijf links, logo in het midden, contact rechts); kleur, lettertype of ligging wijzigen betekent daar een sjabloon bewerken. Hier een scherm zonder HTML, opgeslagen in `x_doc_setting` (`style.*`). Zonder opgeslagen huisstijl is elk document byte-gelijk aan vroeger; met huisstijl wordt de kop één rij van vaste hoogte (logo naast de bedrijfsgegevens, bedrijfsgegevens 9 pt) en staat het adresblok vast op 45 mm, zodat het in een vensterenvelop valt (gemeten: met logo boven de bedrijfsnaam viel het adres in de oude opmaak op 67–77 mm). Lettertypen worden meegeleverd en ingesloten in plaats van die van de server.
226. Zwitsers rekeningschema (REKENINGSCHEMA_ANALYSE.md deelstap 3, CH1–CH3): SL 3.2.12 heeft alleen
     `Swiss-German-chart.sql`. Hier één bestand `ch-kmu.csv` op basis van een Zwitsers KMU/PME/PMI-schema uit
     2012 (GPL; **oude** nummering en btw 2,5/3,8/8,0 %), met die selectie en namen (de/fr/it, tikfouten hersteld), **hernummerd naar de huidige
     Kontenrahmen KMU** (gecontroleerd met RMACH van Run my Accounts) en btw 2024; namen ook en/nl. Eigen vermogen als
     categorie Q, koersverschil één rekening 6940. De MWST-Ziffer
     per btw-rekening staat in `x_vat_box` met land CH (migratie 16: kolom `country`, sleutel rekening + land); de
     Nederlandse aangifte en ICP lezen alleen land NL. Italiaans kan als taal van de rekeningnamen (`chartfiles.LANGUAGES`),
     niet als schermtaal.
227. Brits rekeningschema (REKENINGSCHEMA_ANALYSE.md deelstap 4, UK1–UK5): SL's `UK_General-chart.sql` heeft geen
     kopjes, btw 17,5 %/5 % op één rekening voor verkoop én inkoop, de creditcard als schuld tussen de activa (1240),
     dividenden als kosten (8500) en koerswinst/-verlies apart (4920/5220). Hier `uk-ltd.csv`: dezelfde selectie en
     Sage-banden met kopjes en de nummers van `leeg-uk` (x001), btw 20/5/0 % apart voor verkoop en inkoop met VAT100-box
     (1 en 4, land GB in `x_vat_box`), creditcard 2120, dividenden 3300 (eigen vermogen), één koersverschilrekening 7906.
     Ook `leeg-uk` en `leeg-ch` brengen nu hun box/Ziffer mee (UK5).
228. Frans rekeningschema (REKENINGSCHEMA_ANALYSE.md deelstap 5, FR1–FR5): SL's `France-chart.sql` (2001, 927
     rekeningen zonder accenten, btw 19,6 %, koersverschil op het niet-PCG-nummer 999000, voorraad als standaard op
     681730) wordt niet gebruikt. Hier `fr-pcg.csv`: eigen PCG-abrégé-selectie (175 rekeningen) volgens de nomenclatuur
     voor boekjaren vanaf 2025 (ANC 2022-06, gecontroleerd tegen de ANC-lijst 1-1-2025: geen 79, cessions op 657/757,
     subsidiequote 747, koersverschil op klanten/leveranciers 656), de nummers van `leeg-fr`, CA3-regel per
     btw-rekening (land FR in `x_vat_box`, ook in `leeg-fr`; formulier 2026: 2,1 % op T6), 445620 TVA sur
     immobilisations zonder btw-koppeling.
229. Belgisch rekeningschema (REKENINGSCHEMA_ANALYSE.md deelstap 6, BE1–BE4): SL's `Belgium-chart.sql` (2006, ±420
     regels, btw 6/21 %, vóór het WVV en de niet-recurrente rubrieken 66/76) wordt niet gebruikt. Hier `be-mar.csv`:
     eigen MKB-selectie uit het minimumrekeningschema (128 rekeningen, gecontroleerd tegen de MAR-tekst van de CBN,
     bijlage KB 21-10-2018) met de nummers van `leeg-be`, afschrijvingen op
     2X9 als contra, btw 21/12/6 % met rooster 54 (verschuldigd) en 59 (aftrekbaar), land BE in `x_vat_box` (ook in
     `leeg-be`); geen 69/79 (resultaatverwerking).
230. Importeren (FASE4_ANALYSE.md stap 7): SL's im.pl zet rijen klaar in `report`/`reportvars` en slaat ze per rij op; een lege cel overschrijft het veld, `dataline` kan geen regeleinde binnen aanhalingstekens, memoriaal groepeert alleen opeenvolgende rijen, boekt ook op een kop en schrijft voor de eigen valuta een koers 1 (geen `defaultcurrency`), een nieuwe relatie krijgt geen startdatum of soort. Hier: proefrun als voorbeeld, alles of niets, lege cel laat staan, nieuwe relatie en boeking zoals het scherm (startdatum vandaag, soort bedrijf, medewerker op de boeking), nummer dat in de administratie dubbel staat = fout (SL neemt er stilzwijgend één), onbekende artikelgroep wordt aangemaakt (zoals SL) met een melding.
231. Tijdkaarten (UREN_ANALYSE.md U-1, UR9): SL's `JC::save` maakt een nieuwe regel met een tijdstempel als tijdelijke omschrijving en zoekt hem daarop terug; een tijdkaart met uren op een order mag lager worden en verwijderd worden. Hier nieuw id via `RETURNING`, aantal niet lager dan `allocated`, niet verwijderen met `allocated` > 0; `allocated` is op het scherm niet te wijzigen (SL: wel). Bij een andere dienst houdt het scherm een zelf ingevulde omschrijving (jc.pl overschrijft altijd met die van de dienst; in de Perl-vergelijking zoals jc.pl).
232. Verkooporders uit uren (UREN_ANALYSE.md U-2, UR9): pe.pl `generate_sales_orders` neemt de btw van een regel via een regex op de tekst met btw-rekeningen: een dienst zonder btw-rekening krijgt álle btw-rekeningen van de klant, een dienst met twee rekeningen geen; de orders worden per klant apart opgeslagen (mislukt de tweede, dan staat de eerste er al) in de willekeurige volgorde van een Perl-hash. Hier: btw van de dienst zoals bij een gewone order, alle orders in één transactie, op volgorde van klant-id. Samenvatten werkt zoals SL (opeenvolgende uren met hetzelfde project, dezelfde klant en dienst; prijs van de eerste tijdkaart).
233. Back-up inlezen en bijwerken (FASE6_ANALYSE.md F1–F4): SL 3.2.12 kan een back-up niet terugzetten (dat doet de beheerder met psql als superuser) en werkt een oude database statement voor statement bij zonder transactie (een fout wordt gemeld en het volgende statement draait door). Hier: een eigen lezer met een witte lijst in plaats van psql, terugzetten als de gewone databasegebruiker in één transactie, bijwerken met dezelfde 70 scripts in één transactie (een falend statement wordt zoals SL overgeslagen, maar via een savepoint en zichtbaar gemaakt).
234. RGS-koppeling (REKENINGSCHEMA_ANALYSE.md RS13, keuze A 2026-10-11): SQL-Ledger kent geen RGS. Hier: de systeembeheerder leest het officiële RGS-bestand in (Systeem > RGS-lijst, niet meegeleverd), de code per rekening en per RGS-versie staat in `x_account_rgs`; Beheer > RGS-koppeling met tot drie voorstellen, niets automatisch. SL ziet hier niets van.
235. Back-up maken (FASE6_ANALYSE.md 6d-1): SL's `AM::backup` schrijft een eigen SQL-bestand met de tabellen uit `Pg-tables.sql` en de upgradescripts; eigen tabellen (`x_`) gaan niet mee en een terugzetting wist ze. Hier: `pg_dump` van de hele database (zonder eigenaar en rechten) met een eigen kop, plus de sjablonen en het logo als zip (bij SL staan die in `templates/<login>/` en zitten ze ook niet in de back-up). Mailen van de back-up (SL: optie "per e-mail") niet: de opdrachtregel in een geplande taak vervangt dat.
236. Korting % van de klant/leverancier (BEHEER_EXTRA_ANALYSE.md VW7): zoals SL (`is.pl`/`ir.pl`/`oe.pl` `discount_$i ||=`) de regelkorting van een nieuw gekozen artikel op verkoop-/inkoopfactuur, order, offerte en offerteaanvraag, als die regel nog geen korting heeft (leeg of 0). Bestaande regels en opgeslagen facturen veranderen niet. Kasafronding (`roundchange` van de betaalmethode) werkt zoals SL op PDF en e-mail (`printdata.py`).
237. Stelsel in rapporten (VW1): de beginstand komt zoals SL (`Form.pm`) uit de instelling "Rapporten standaard op kasbasis" (`defaults.method`), in elk rapport met het rondje Stelsel (balans, winst en verlies, btw-overzichten); de laatst gebruikte keuze van de gebruiker gaat voor.
238. Velden die in SL iets doen maar hier niet, van het scherm gehaald; de waarde blijft in de database (back-ups en terug naar SL ongemoeid): "Map/url referentiedocumenten" (`referenceurl`, VW3: PyBalans heeft echte bijlagen), "Korting (%)" bij Branches (`business.discount`, VW5: SL verlaagde er de verkoopprijs mee; korting per relatie werkt wel, zie 236). "Kosten" bij Betaalmethoden (`paymentmethod.fee`, VW6) weg: ook in SL zonder effect.
239. Decimalen per valuta (`curr.prec`, VW4): alleen-lezen met uitleg; PyBalans rekent in elke valuta met de algemene precisie (SL rekende per valuta). Een nieuwe valuta krijgt 2, de SL-standaard.
240. Boekjaar (BEHEER_EXTRA_ANALYSE.md JA1–JA8): in SL 3.2.12 is `defaults.yearend` een overblijfsel zonder invoerveld dat niets leest en dat bij Opslaan van de instellingen verdwijnt (`AM::save_defaults`); "Jaar" in rapporten is 12 maanden vanaf de gekozen maand. Hier: `yearend` = laatste maand van het boekjaar, instelbaar en gebruikt door de periodekeuze "Boekjaar" (alleen bij een gebroken boekjaar), de standaardperiode, het dashboard, de doorklik vanaf een boeking, het controleblad Ingebruikname en het voorstel bij de jaarafsluiting. "Jaar" blijft 1 januari t/m 31 december; btw en ICP blijven kalendertijdvakken. Een SL-database zonder `yearend` werkt als december: daar verandert niets.
241. GIFI-indeling (BEHEER_EXTRA_ANALYSE.md GF1–GF6): SL `AM::save_gifi`/`delete_gifi` doen `$form->{id} *= 1` op de oude code: "020" wordt 20, zodat wijzigen en verwijderen niets raken en toch "GIFI saved!" melden, en een code met letters wordt 0. Verwijderen keek alleen naar boekingen, zodat rekeningen zonder boekingen een dode verwijzing hielden; een gewijzigde code liet `chart.gifi_accno` op de oude code staan. Hier: de code blijft tekst, verwijderen kan alleen als geen rekening de code gebruikt, en een gewijzigde code neemt de rekeningen mee in één transactie. Omschrijving is verplicht. Geen knop *Copy to COA*; in plaats daarvan een zoeklijst bij de rekening. GIFI is ook te importeren/exporteren (SL: niet).
242. Importeren van facturen, orders en betalingen (BEHEER_EXTRA_ANALYSE.md §3): SL's im.pl groepeert alleen opeenvolgende rijen, **vervangt** een bestaande openstaande factuur met hetzelfde nummer, zoekt een bestaande order alleen op nummer (`IM.pm:703`, niet gequote en zonder soort: een inkooporder vervangt een verkooporder met hetzelfde nummer — aangetoond in de Perl-vergelijking), koppelt een betaling op `dcn` of anders op de **eerste open post met precies hetzelfde bedrag** (van wie dan ook; `invnumber` wordt niet gebruikt) en slaat per document op (een fout halverwege laat de eerdere staan). Het AP-deel van de betalingenimport sluit inkoopfacturen zonder `bank_id` uit (`IM.pm:1609`), en het volgende betaalkenmerk komt onder sleutel `<rekening>` in plaats van `receipt_<rekening>`. Hier: groeperen op nummer + relatie ook niet-opeenvolgend, bestaand nummer = fout (nooit vervangen), orders per soort, betalingen op factuurnummer of betalingskenmerk en nooit op bedrag alleen, niet meer dan openstaat, proefrun en alles of niets; de factuur krijgt de standaard betaalrekening (`ar.bank_id`) zoals het factuurscherm. Geen creditnota's, offertes of inkoopfacturen via de import (IM1).
243. Audittrail bij een nieuwe afdeling, branche of betaalmethode (HELP_ANALYSE.md F19): SL `AM::save_department`/`save_business`/`save_paymentmethod` geven bij iets nieuws een leeg id mee en `Form::audittrail` schrijft dan niets (`if ($audittrail->{id} *= 1)`), zodat alleen wijzigen en verwijderen in de audittrail kwamen. Hier krijgt ook het aanmaken een regel `saved` met het nieuwe id. Perl-vergelijking: verwacht verschil in `compare/scenarios_am.py`.
244. Betaalbestand (BEHEER_EXTRA_ANALYSE.md IM9 A1): SL 3.2.12 maakt alleen een Zwitsers DTA-bestand en een CSV van geboekte betalingen. Hier een SEPA-bestand (pain.001.001.03/.09) van **te betalen** inkoopfacturen, zonder te boeken; het bankafschrift lettert later af. Zie de rij Betaalbestand (SEPA).

**Klanten en leveranciers (fase 2, `sqlledger/contacts.py`, `ct.pl`/`CT.pm`)**
56. Verwijderen: `CT::delete` (CT.pm r561) en `ct.pl delete` (r3338) controleren niet of
    de relatie gebruikt wordt; alleen de knop ontbreekt in het scherm (`form_footer`
    r2269). Een zelfgemaakte POST `action=delete` (of `yes__delete` voor de lijst,
    `CT::batch_delete` r1413) verwijdert een klant mét boekingen; die boekingen wijzen
    daarna naar een niet-bestaande relatie (scenario "verwijderen van een relatie met
    boekingen"). Python weigert op de server als de relatie in verkoop/inkoop, orders,
    `pay_trans` of `acc_trans` voorkomt (de `pay_trans`-controle in `CT::create_links`
    r96–105 zocht op het relatie-id in plaats van het boekings-id). Bij meervoudig
    verwijderen schreef Perl de audittrail zonder naam (`batch_delete` heeft geen
    `name`); Python neemt de opgeslagen naam.
57. Opslaan (`CT::save` r238–557) gooide adres, contactpersoon, bank, btw en
    verzendadres weg en schreef ze opnieuw uit de formuliervelden. Gevolgen: telefoon,
    fax en e-mail van de relatie worden overschreven door die van de eerste
    contactpersoon (`ct.pl create_links` r69 toont alleen die), het veld `contact`
    wordt opnieuw "voornaam achternaam", een relatie zonder contactpersoon krijgt een
    lege, lege waarden worden '' of 0 in plaats van NULL, en kolommen die het scherm
    niet kent gaan verloren (`contact.parent_id`, `shipto.shiptorecurring`). Een
    medewerker die geen verkoper (meer) is staat niet in de keuzelijst
    (`all_employees(..., sales)` r199) en wordt bij opslaan weggehaald; hetzelfde voor
    een rekening die niet (meer) in de keuzelijst staat; een lege valuta wordt de
    eerste uit de lijst. Python vergelijkt met de opgeslagen relatie en schrijft per
    tabel alleen gewijzigde velden (telefoon/e-mail: die van de contactpersoon, anders
    die van de relatie). Toets: alle 803 (A) en 1.235 (C) relaties via het
    webscherm geopend en ongewijzigd opgeslagen → 0 verschillen en 0 schrijfacties;
    met alleen de notities gewijzigd → alleen die kolom anders (`compare/ct_keeptest.py`).
58. Relatienummer: Perl controleerde niet op dubbele nummers en "Opslaan als nieuw"
    (`ct.pl save_as_new` r3302) bewaarde de kopie met hetzelfde nummer (behalve met
    `lock_<vc>number`). Python weigert een nummer dat al bij een andere relatie hoort
    (bestaande dubbelen in de data mogen blijven zolang het nummer niet verandert;
    A 13, C 11) en geeft bij "Opslaan als nieuw" met ongewijzigd nummer het
    volgende nummer. `lock_customernumber`/`lock_vendornumber` wordt ook op de server
    gecontroleerd.
59. "Nieuw nummer" (`ct.pl new_number` r3354) hoogde de teller in `defaults` meteen op,
    ook als je daarna niet opsloeg. Python toont het volgende nummer en hoogt de
    teller pas op bij opslaan (is het nummer intussen door een ander gebruikt, dan
    krijgt de relatie het daaropvolgende).
60. Extra controles in de bibliotheek (Perl controleerde alleen of er een naam was;
    te lange waarden gaven een databasefout): veldlengtes uit het schema, IBAN
    (vorm + mod 97) en BIC-vorm, kortingspercentage 0–100, einddatum niet vóór de
    startdatum, bestaande valuta/taal/branche/medewerker/betaalmethode/rekening.
    Controles op IBAN, BIC en korting gelden alleen voor een gewijzigde waarde: oude
    data met bv. een rekeningnummer of "INVOICES SENT BY EMAIL ONLY" als IBAN of
    333% korting (komt voor in de echte data) blokkeert het opslaan van iets anders
    niet. Een gewijzigde IBAN/BIC wordt zonder spaties in hoofdletters bewaard.
61. Bank (`CT::save` r277–433): `bank.dcn` en `bank.rvc` gingen bij elk opslaan
    verloren (scenario "betaalkenmerk/rvc van de bank blijven staan"); naam en adres
    van de bank werden in hoofdletters gezet (`uc`). Python behoudt dcn/rvc en de
    schrijfwijze; een bankrij met dcn/rvc blijft bestaan als de bankvelden leeg
    worden gemaakt.
62. Zoeken/lijst (`CT::search` r629, `ct.pl list_names` r716): inner JOIN op
    `contact` en `address` (r725–726) maakte relaties zonder adres of contactpersoon
    onzichtbaar en toonde een relatie met twee contactpersonen twee keer (testdata);
    "actief" was alleen `enddate IS NULL`, zodat een relatie met een einddatum in de
    toekomst actief noch inactief was (r691–696); "ongebruikt" keek niet naar
    betalingen; typefout `chop $ref->{taxaccount}` (r965, de btw-rekeningen houden een
    spatie) en de vooruitbetalingsrekening ontbrak in de kolommen (r973). Python: één
    rij per relatie met het eerste adres en de eerste contactpersoon (LEFT JOIN),
    actief = geen of een toekomstige einddatum, openstaand en beschikbaar krediet
    altijd berekend. Boekingen/orders als extra rijen in de lijst zijn vervangen door
    de doorklik "Boekingen"/"Openstaand" (overzicht verkoop/inkoop met de relatie).
63. Elke boekingsknop op het relatiescherm (`ct.pl add_transaction` r3167) sloeg het
    formulier op én wiste de einddatum: een inactieve relatie werd zo ongemerkt weer
    actief. De webapp opent het boekings-/betaalscherm met de relatie voorgevuld
    (`?relatie=<id>`) zonder iets op te slaan.
64. Dode code met een fout: `CT::save` r316–321 haalt `SELECT enddate, current_date
    FROM <customer|vendor>` zonder WHERE op (een willekeurige rij); de voorwaarde
    wordt nooit waar omdat `add_transaction` de einddatum eerst leegmaakt. Niet
    overgenomen.
65. **Beveiliging**: beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna.
    In Python is alle SQL geparametriseerd.
66. Persoon (soort "persoon"): de naam is achternaam + voornaam en wordt opnieuw
    opgebouwd als voor-/achternaam of soort wijzigen (zoals Perl in het scherm); een
    afwijkende opgeslagen naam blijft staan zolang die niet wijzigen.
**Beheer (fase 2, `sqlledger/admin.py`, `sqlledger/masterdata.py`)**
70. Btw-koppeling van een rekening uitzetten (`AM::save_account`, r.154–161: `DELETE FROM
    tax WHERE chart_id = ...`) wiste de hele tariefhistorie; oude boekingen en de btw-rapporten
    verloren daarmee hun tarief. Python bewaart de tarieven; ze blijven op het btw-scherm
    staan met "geen btw-koppeling meer".
71. Rekening verwijderen (`am.pl delete_account` / `AM::delete_account`): de server controleerde
    niet of er op de rekening geboekt is of dat een relatie/artikel ernaar verwijst (alleen de
    knop ontbrak); de standaardrekening-controle stond alleen in am.pl en miste
    `cashovershort_accno_id`, `fxgain_accno_id`, `fxloss_accno_id`; en `AM::delete_account`
    (r.225–240) zocht artikelen met `WHERE inventory_accno_id = <standaardrekening>` in plaats
    van `= <deze rekening>` en zette artikelen stil om naar de standaardrekening. Python weigert
    bij boekingen, elke `defaults.*_accno_id` en verwijzingen uit klant/leverancier/artikel/
    loonregels (scenario "rekening met boekingen/in gebruik verwijderen").
72. Btw-scherm (`am.pl save_taxes` / `AM::save_taxes`): Perl gooide bij elk opslaan de hele tabel
    `tax` weg en schreef alles opnieuw; een leeg tarief werd 0%; de volgorde van 'geldig tot'
    werd niet gecontroleerd (alleen 'ontbreekt' bij een tussenregel, waardoor testdata met twee
    huidige tarieven niet meer op te slaan was). Python: tarief verplicht, geldig-tot strikt
    oplopend, alleen de laatste regel zonder einddatum, en alleen rekeningen waarvan iets
    veranderd is worden herschreven (een lege nieuwe regel telt niet mee).
73. Instellingen (`AM::save_defaults`, r.1603 `DELETE FROM defaults`): Perl gooide de hele tabel
    weg en schreef alleen de schermvelden terug. Daardoor verdwenen `yearend`,
    `fxgain_accno_id`/`fxloss_accno_id` en andere sleutels die niet op het scherm staan, werd
    `cashovershort_accno_id` de eerste rekening uit de keuzelijst (er is geen lege keuze), werd
    `revtrans = '0'` weggelaten, en overschreef een scherm dat al open stond een intussen gezette
    afsluitdatum of opgehoogde nummerteller. Python schrijft per sleutel, alleen wat op het
    scherm gewijzigd is ten opzichte van wat het toonde; andere sleutels blijven altijd staan.
74. Valuta-volgorde (`AM::move` met `db=curr`): omhoog/omlaag schoof elke valuta naar of van de
    eerste plek, en de eerste valuta ís de standaardvaluta — die veranderde zo ongemerkt (met
    alle gevolgen voor koersen en bedragen). Ook liep de volgorde rond (laatste ↔ eerste).
    Python: de standaardvaluta blijft bovenaan; wisselen alleen via "Standaardvaluta maken".
75. Standaardvaluta kiezen (nieuw, eis gebruiker): kan alleen zolang de administratie geen
    boekingen heeft (`acc_trans`, `ar`, `ap`, `gl`, `oe` leeg) en er geen koersen zijn — bedragen
    en koersen staan in de standaardvaluta en zouden anders fout worden. Relaties en
    artikelprijzen in de oude standaardvaluta gaan na bevestiging mee naar de nieuwe.
76. Valuta verwijderen (`am.pl delete_currency` / `AM::delete_currency`): geen controle op de
    server; ook de standaardvaluta en valuta's die in gebruik zijn konden weg (`get_currency`
    keek alleen naar ar/ap/oe, niet naar gl, relaties, koersen). Een valutacode wijzigen in
    het scherm maakte een nieuwe valuta. Python weigert de standaardvaluta en elke valuta die
    ergens gebruikt wordt; de code is niet te wijzigen.
77. Koersen (`am.pl save_exchangerate`): bij een andere datum bleef de koers op de oude datum
    staan (`oldtransdate` werd niet gebruikt). Python verplaatst de koersen. Leeg = koers weg.
78. Afdeling verwijderen (`AM::get_department`/`delete_department`): Perl keek alleen in
    `dpt_trans` (en alleen voor de knop); een afdeling die in gl/ar/ap/oe gebruikt wordt, werd
    verwijderd. Branches en betaalmethoden hadden altijd een verwijderknop
    (`$form->{orphaned} = 1`), projecten alleen een knop-controle. Python controleert op de
    server alle verwijzingen (ar/ap/gl/oe/dpt_trans; relaties; boekingen/betalingen;
    acc_trans/invoice/orderitems/jcitems/parts). Volgorde omhoog/omlaag loopt niet meer rond.
79. Talen (`AM::save_language`): dubbele spaties in de omschrijving werden een '-'
    (`s/ ( )+/-/g`: "Frans  (FR)" → "Frans-(FR)"); een taalcode die in gebruik is (relaties,
    boekingen, vertalingen) werd hernoemd zonder de verwijzingen bij te werken. Python maakt er
    één spatie van en weigert het hernoemen van een gebruikte code.
80. `IC_taxpart`/`IC_taxservice` aan- of uitzetten (`AM::save_account` r.164–192) zette de
    btw-rekening bij álle artikelen resp. diensten aan of uit (keuzes per artikel gingen
    verloren). Python laat `partstax` ongemoeid; dat volgt met de artikelen (fase 3).
81. Administratie op slot (`am.pl lock_dataset`: bestand `users/<db>.LCK`) is nu een vlag in de
    app-database (`company.locked`, met melding). Wie geen beheerder is, kan de administratie
    niet kiezen of openen.
82. **Beveiliging:** beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen daarna. Python: alles
    geparametriseerd; tabel- en kolomnamen alleen uit vaste lijsten.
83. Audittrail: Perl bood "Remove Audit trail up to" (`AM::closebooks`) aan; Python niet (A4).
    Locks: Perl kon alleen alle locks tegelijk weghalen (`AM::remove_locks`); Python ook per lock.
84. Nieuwe administratie uit het SQL-Ledger-sjabloon heeft `fxgain_accno_id`/`fxloss_accno_id`
    maar geen `fxgainloss_accno_id`, terwijl de code (CP.pm, AA.pm) alleen die laatste leest:
    koersverschillen konden niet geboekt worden. Python zet hem bij het kiezen van de
    administratie (`admin.fix_fxgainloss`) als beide gelijk zijn of er één gevuld is; zijn ze
    verschillend, dan verandert er niets (kiezen bij Instellingen).
85. Back-up inlezen, bijwerkscript `Pg-upgrade-2.9.1-2.9.2.sql` (ADM-15; Perl 3.2.12):
    de koersentabel werd teruggebracht tot één koers en nam alleen `buy` over; koersen met alleen een verkoopkoers
    (`buy` = 0) verdwenen (administratie C (UK): 104 van 248). Python neemt `buy`, anders `sell` (`slupgrade.PATCHES`, het
    .sql-bestand zelf blijft ongewijzigd; een toets bewaakt dat het oorspronkelijke statement er nog staat).
    Boekingen raken het niet: elk stuk heeft zijn eigen koers. Al ingelezen administraties blijven zoals ze zijn.
86. Andere server/poort voor een administratie (Perl: admin.pl *Change Host*, ADM-16; databasenaam daar niet te
    wijzigen): Perl slaat op zonder verbinding te testen, logt niets en meldt "Lockfile removed!". Python (F24)
    eist een bereikbare database die niet al bij een andere administratie hoort, toont eerst oud → nieuw met de
    bedrijfsnaam uit beide databases en zet de oude waarden in het logboek.
87. Vertrouwelijke bijlagen en de medewerkerlogin (DOC-16; Perl 3.2.12): Perl verhuist bij
    een andere login van een medewerker (HR) wel de opgeslagen rapporten (`report.login`) maar niet
    `reference.login`, en laat ze bij verwijderen staan → wees, of zichtbaar voor wie later die login krijgt.
    Python (F42): twee actieve gebruikers in één administratie kunnen niet dezelfde medewerker zijn; krijgt een
    gebruiker een andere medewerker, dan toont Systeem > Gebruikers het aantal vertrouwelijke bijlagen onder de oude
    login en verhuist ze na een vinkje (logboek `attachments_moved`).

**Bewust overgenomen eigenaardigheden** (om bestaande nummering/data gelijk te houden)
- `<%CURR%>` in het boekingsnummer is leeg bij de standaardvaluta.
- Datumdelen in sjablonen (`<%YYMMDD%>`) komen altijd in volgorde dd-mm-yy.
- Het eerste getal in een nummer wordt opgehoogd (`2026-0001` → `2027-0001`).
- Een betaling op de boekdatum krijgt altijd de koers van de boeking (aa.pl post).
- Betalingen: een leeg betaalkenmerk wordt het voorgestelde volgende nummer
  van de betaalrekening (`receipt_<rekening>`/`check_<rekening>` in defaults);
  de regel voor contante korting krijgt als regelnummer het boekingsnummer;
  de koersverschilregel van een vooruitbetaling krijgt de betaaldatum als
  afletterdatum; de vooruitbetaling krijgt geen koers (`exchangerate` leeg).
- `closedto` wordt net als in Perl als getal jjjjmmdd vergeleken. In echte data
  komt bv. `20173112` voor (geen geldige datum); dat werkt als "t/m eind 2017
  afgesloten" en mag geen fout geven.
- Maandrekenen zoals Perl's `add_date`: 30-06 min 11 maanden = 31-07 (Perl telt
  de dagen van de maanden op); bepaalt de kolomdatums van de balans.
- Rekeningen waarvan het saldo op `decimalplaces` afgerond 0 is, vallen weg uit
  W&V en balans; daardoor kan het resultaat lopend boekjaar een fractie van
  een cent verschillen van het W&V-resultaat (op het scherm gelijk).
- De proefbalans laat rekeningen zonder mutaties in de periode weg, tenzij
  `all_accounts=True`.

## Gelijkheid aangetoond

`compare/` draait 55 scenario's (boeken, wijzigen, verwijderen, vreemde valuta,
batches, herhaalschema's, referenties, locks, alle zoekfilters, rekeningsaldi,
vertalingen, getalnotaties, foutgevallen, 36 rapportvarianten, verkoop-/
inkoopboekingen en betalingen) **zowel door de
originele Perl-code als door deze bibliotheek**, elk op een eigen kopie van
dezelfde database, en vergelijkt:

- de volledige inhoud van 17 grootboektabellen na afloop (alleen tijdstempels
  genegeerd),
- alle leesresultaten (velden, volgorde, saldi, foutgevallen), en
- voor de rapporten: de ruwe data uit `RP.pm` én elke regel van het HTML-scherm
  dat het originele `rp.pl` maakt (labels, bedragen, subtotalen, totalen,
  resultaat), in de getalnotatie van de gebruiker. Zo is ook de rekenlogica
  uit het scherm aangetoond gelijk.

Betalingen gaan in Perl door het echte betaalscherm (`cp.pl`): de driver
gedraagt zich als een browser — elke stap is een los request en krijgt precies
de velden mee die in de HTML van het vorige scherm staan (relatie en datum
kiezen, betaalrekening, posten aanvinken, posten). Zo worden ook de
voorgestelde korting en betaalbedragen per factuur vergeleken. Ook het scherm
voor meerdere relaties (`cp.pl payments`) wordt zo nagespeeld. De Python-kant
gaat door de formuliercode van de webapp (`routes_cp`: doorrekenen, verdelen,
boeken; storneren/opnieuw uitgeven via de betalingenlijst).

Waar Perl zelf niet-deterministisch is, wordt Perl met 8 verschillende
hash-seeds gedraaid en moet Python met één daarvan overeenkomen.

Resultaat laatste run: **0 onverwachte verschillen**; alle verschillen zijn de
gedocumenteerde punten hierboven. Zie `compare/LAATSTE_RUN.txt`.

### Documenten zonder instellingen ongewijzigd (fase 4 stap 3)

`dev/doc_html_dump.py` maakt de HTML van documenten met de code van een bepaalde versie (PYTHONPATH) en vergelijkt
twee versies (witruimte naast tags en het stijlblok tellen niet). Standaardsjablonen vóór en na stap 3, zonder
instellingen: A, C en web_demo, per soort de laatste 15 documenten in NL/EN/DE/FR: **1.580 documenten,
0 verschillen**; aangevuld met groepskop, subtotaal, stuklijstregel, locatie, korting, alle regelvelden en
offerte(aanvraag)/pakbon/picklijst uit een order (`--synthetic`): 22 documenten, 1 verschil (afwijking 192).

### Aanmaningen en rekeningoverzichten (fase 4 stap 4a)

`compare/compare_dunning.py <db>`: Perl-driverstap `rp_print_ui` (rp.pl `generate_reminder` / `generate_ar_aging`
→ print per factuur resp. per klant en valuta, `Form::parse_template` onderschept) tegen `sqlledger.dunning`.
Vergeleken: welke facturen, voorgesteld niveau, factuur- en klantgegevens, bank; per overzicht regels, kolommen en
totalen. Testdata (ook `--synthetic`: creditnota, klant met twee valuta, niveau 3, klant met alleen tegoed;
kolommen 4 en 7, vanaf vervaldatum, één kolom), C (peildatum 29-06-2016 en vandaag) en A (peildatums
30-06-2024 t/m 31-01-2025): **0 onverklaarde verschillen**; verklaard: Perl dubbel bij twee contactpersonen
(README 17), klant met totaal ≤ 0 (196), niveau na 3 (197), later betaald (193). `dev/doc_html_dump.py` oud vs
nieuw: 1.244 bestaande documenten (A, C, web_demo) ongewijzigd.

### Betalingsspecificatie (fase 4 stap 4b)

`compare/compare_payspec.py [<db>]`: Perl-driverstappen `cp_post`/`cp_payments` met `print` (cp.pl
`print_payment`/`print_payments` op het betaalscherm, `Form::parse_template` onderschept; daarna boekt Perl de
betaling) tegen `sqlledger.paymentspec` op de door Perl geboekte betaling. Zes schermen voor één relatie (twee
facturen met omschrijving, deelbetaling met vooruitontvangst, betaling met vooruitbetaling, deelbetaling inkoop,
contante korting, USD) en twee keer het scherm voor meerdere relaties (elk op een eigen kopie), in `1.000,00` en
`1,000.00`: **0 onverklaarde verschillen**; verklaard: bedrag in woorden met de verbeterde Nederlandse spelling
(fase 4), posten met 0 betaald (202), vooruitontvangst bij een andere klant (Perl-fout 26). Echte data
`compare/payspec_keeptest.py`: elke betaling uit het register is als specificatie te vinden met hetzelfde totaal —
C 2.665 ontvangsten en 4.592 betalingen, A 14.002 en 10.554 (waarvan 23 met korting, 89 met
vooruitbetaling), web_demo 9: 0 verschillen. `dev/doc_html_dump.py` oud vs
nieuw: 1.244 bestaande documenten ongewijzigd; run_compare standaardset 123/0, gelijk aan `LAATSTE_RUN.txt`.
Btw (vraag gebruiker): `tests/test_tax_rates.py` — btw 0 en tariefwijziging met een periode in orde; overschrijven
van een gebruikt tarief wordt geweigerd (BT1, afwijking 203).

### Batch (fase 4 stap 4c)

Batch is volgens SB16 niet zinvol met Perl te vergelijken (bp.pl werkt met een spoolmap, printers en `pdftk`); wel
pytest: `tests/test_batch.py` (selectie per soort, filters, geprint/gemaild per `formname`, open/gesloten ook met
`paid` NULL, pakbon uit factuur en order, SQL-tekst in filters) en `tests/web/test_web_batch.py` +
`test_web_batch_items.py` (menu en modules, voorbeeld legt niets vast, PDF zet geprint + audittrail alleen voor de
aangevinkte documenten, mislukte PDF legt niets vast, maximum, rollen, mailen in mapmodus met eigen onderwerp en
bericht, één fout adres en één opmaakfout stoppen de rest niet, samengevoegde PDF = som van de pagina's van de losse
documenten). `dev/doc_html_dump.py` oud (9c1f67a) vs nieuw: 2.488 bestaande documenten (A, C, web_demo;
NL/EN/DE/FR) ongewijzigd. run_compare standaardset **123 scenario's, 0 onverwacht**; scenario 81 (btw-tarieven)
is sinds BT1 een verwacht verschil (afwijking 203: Python weigert het overschrijven van een gebruikt tarief).

### Herhalen met mailen en PDF (fase 4 stap 4d)

Herhalen is volgens SB16 niet zinvol met Perl te vergelijken (am.pl start per document een subproces naar een
printer of sendmail); wel pytest: `tests/test_recurring.py` (opslaan zoals SL in `recurringemail`/`recurringprint`,
`keep_output`, het resultaat geeft soorten en bericht door, niet bij een overgeslagen schema, ongeldige formname) en
`tests/web/test_web_recurring_output.py` (keuzes per boeking, opslaan/wissen, Perl-soorten bewaard, mailen in mapmodus
met eigen bericht + gemaild/notitie/audittrail, mislukte mail en geen e-mailadres: boeking blijft en schema schuift door,
e-mail niet ingesteld, opmaakfout, PDF-knop zet pas na de PDF geprint, mislukte PDF legt niets vast, rollen, factuur
met mail én PDF van factuur + pakbon, memoriaal zonder knop). Regressie: pytest **753 groen**, run_compare
standaardset **123 scenario's, 0 onverwacht** (gelijk aan `LAATSTE_RUN.txt`), `dev/doc_html_dump.py` 58e5ece vs
nieuw: 2.488 bestaande documenten (A, C, web_demo; NL/EN/DE/FR) ongewijzigd.

### Inkoopfactuur uploaden en bijlagen (fase 4 stap 4e)

`compare/compare_attach.py` (kopie van `sl_template`): 9 inkoopfacturen via het uploadscherm (voorstel, splitsen,
gewone inkoopformulier, `do_post`) en dezelfde factuur in ap.pl als browser (`aa_ui`) geven gelijke grootboekregels en
kop (bedrag, netto, vervaldatum, valuta, afdeling): 21%, afronding 82,64/17,36, eigen btw-bedrag 17,35, 9% bij twee
gekoppelde rekeningen, negatief, groot bedrag, zonder btw. Bijlagen heen en terug met SL's eigen code (driverstappen
`ref_save`/`ref_get` = `Form::save_reference` met tmpfile en `Form::get_reference`): Python-pdf/-jpg byte voor byte in SL
met bestandsnaam en contenttype, SL ziet onze verwijzingen (ook vertrouwelijk verborgen voor een ander), een SL-bijlage
opent in Python, opnieuw boeken in ap.pl laat onze bijlagen staan en opnieuw boeken in Python die van SL: **16
vergelijkingen, 0 verschillen**. Echte data (`compare/apupload_hitrate.py`, kopieën): kostenrekening-voorstel raak A
90,3% (12.402 van 13.727), C 72,8% (4.440 van 6.098); btw raak A 93,4%, C 86,5%. pytest **825 groen**
(`tests/test_attachments.py`, `tests/test_apupload.py`, `tests/web/test_web_apupload.py`, `tests/web/test_web_attach.py`);
run_compare standaardset **123 scenario's, 0 onverwacht** (gelijk aan `LAATSTE_RUN.txt`); `dev/doc_html_dump.py`
6125c8b vs nieuw: 2.488 bestaande documenten ongewijzigd.

### Bijlagen op andere schermen (fase 4 stap 6a)

`compare/compare_attach.py` deel 3 (kopie van `sl_template` + testartikelen van blok "is"): Python zet een bijlage bij
een inkoop- en verkoopfactuur met artikelen (gemaakt in ir.pl/is.pl), een verkooporder en offerte (oe.pl), klant,
leverancier, artikel en dienst; SL (`Form::get_reference` + `all_references`) opent elk bestand byte voor byte en ziet de
verwijzing met dezelfde formname; opnieuw opslaan in is.pl/ir.pl, oe.pl, ct.pl en ic.pl laat ze staan; een bijlage die
SL bij een order zet ziet Python en blijft bij opnieuw opslaan in Python; omzetten offerte → order in Python deelt het
archiefstuk en SL opent het bij de nieuwe order: totaal **34 vergelijkingen, 0 verschillen**. (Testdata: `sl_template`
heeft geen rekening met `IC_income`, dan opent ic.pl geen enkele dienst; de toets zet die koppeling op 8520.)
pytest: `tests/test_attachments.py` (soort past bij record, "ook bij", verwijderen ook vertrouwelijk van anderen,
alleen zichtbare archiefstukken koppelen) en `tests/web/test_web_attach_6a.py` (12: factuur nieuw met bestand en blok,
opnieuw opslaan, opslaan als nieuw/kopiëren zonder bijlage, creditnota, offerte → order → factuur met gedeeld
archiefstuk en "ook bij", meegekomen bijlage weghalen, inkooporder, gemanipuleerd archiefnummer, klant/leverancier,
artikel/dienst, rechten en modules); totaal pytest **930 groen**.

### Foto's verkleinen en dubbele bestanden (fase 4 stap 6b)

Geen Perl-tegenhanger (SL verkleint niet en kent geen hash). `tests/test_images.py` (11: groot + gedraaid, klein met GPS
zonder opnieuw comprimeren — beelddata byte voor byte gelijk, kleurprofiel, png, alleen kleiner gebruiken, te veel
beeldpunten, pdf onaangeroerd); `tests/test_attachments.py` (hash in eigen tabel én in `archive.hash`, oude stukken,
hier/elders/vertrouwelijk/los, foto verkleind bij opslaan);
`tests/web/test_web_attach.py` (bevestigingspagina, zelfde record geweigerd, vinkje bij nieuwe boeking, twee keer
slepen) en `tests/web/test_web_apupload.py` (zelfde factuur twee keer uploaden); totaal pytest **954 groen**. `compare/compare_attach.py` blijft
**34/0**.

### Overzicht bijlagen (fase 4 stap 6c)

`tests/web/test_web_attach_6c.py` (4: lijst met links, alle filters, los document openen, alleen de beheerder gooit een
los stuk weg en niet een gekoppeld, vertrouwelijk/modules/alleen-lezen, SL-bijlage zonder datum); totaal pytest **958 groen**. `compare/compare_attach.py`
**34/0** en run_compare "referentiedocumenten" 0 na het vastleggen van datum/gebruiker bij opslaan.

### Maximale uploadgrootte (fase 4 stap 4f)

pytest **852 groen** (27 nieuw: `tests/test_attachments.py` grens uit `defaults`, waarden van SQL-Ledger 4.x, ook bij het boeken
en boven 10 MB; `tests/test_admin.py` opslaan/weigeren/4.x-waarden ongewijzigd laten; `tests/web/test_web_am.py`,
`tests/web/test_web_attach.py` (sleepvak, server, verzoek boven 25 MB, Engels), `tests/web/test_web_apupload.py`,
`tests/web/test_libmsg.py`).
`compare/compare_attach.py` **16 vergelijkingen, 0 verschillen**; run_compare standaardset **123 scenario's, 0
onverwacht** (gelijk aan `LAATSTE_RUN.txt`); `dev/doc_html_dump.py` b98f509 vs nieuw: 1.244 documenten (A 498, C
652, web_demo 94) ongewijzigd.

### Rapporten als PDF (fase 4 stap 5)

pytest **874 groen** (22 nieuw in `tests/web/test_web_reportpdf.py`, plus PDF-controles in `test_web_rc.py` en
`test_web_bank.py`), ook op het 4.x-testschema (103 rapport-/lijsttoetsen); run_compare standaardset **123 scenario's,
0 onverwacht**; `dev/doc_html_dump.py` fase4 vs stap 5: 1.244 documenten (A 498, C 652, web_demo 94) ongewijzigd.
Geen Perl-vergelijking (SL kan het niet, afwijking 213). De PDF gebruikt dezelfde celteksten als het scherm, dat tegen
Perl is bewezen; `tests/web/test_web_reportpdf.py` haalt de tekst uit de PDF (pypdf) en eist dat elk bedrag van het
scherm erin staat, voor alle 22 rapporten, drie getalnotaties, alle regels bij een schermpagina van 3, W&V per maand
met vorig jaar (liggend), en de lijsten (ook `test_web_rc.py`, `test_web_bank.py`). Voorbeelden en tijden op echte data
(`compare/voorbeeld_rapport_pdf.py`, uitvoer niet in git: `dev/voorbeeld-pdf/stap5/`): A rekeningmutaties 1000 heel
2017 3.580 regels = 103 blz. in 12 s, C 2600 heel 2012 3.538 regels = 67 blz. in 9 s; balans per maand met vorig
jaar (26 bedragkolommen) liggend 6,5 pt op 2 blz.; journaal heel jaar (16–18 duizend regels) → melding.

### Dashboard (fase 4 stap 5b)

pytest **910 groen** (21 in `tests/test_dashboard.py`, 15 in `tests/web/test_web_dashboard.py`), dashboardtoetsen ook op
het 4.x-testschema; run_compare **123 scenario's, 0 onverwacht**; `dev/doc_html_dump.py` 1.244 documenten ongewijzigd.
Open posten = ouderdomsanalyse per factuur: `compare/dashboard_check.py` op web_demo, A en C, peildatums vandaag,
A 30-06-2024 en 31-03-2017, C 30-06-2013: **0 verschillen** (ook de facturenverzameling); `tests/test_dashboard.py`
op testdata bij vier peildatums. Bank = balans per vandaag en resultaat = W&V (zelfde bibliotheekfunctie, ook getoetst).
Tijden per blok op A/C: bank ≤ 0,2 s, open posten ≤ 0,13 s, resultaat ≤ 0,11 s, te doen ≤ 0,08 s.
`tests/web/test_web_dashboard.py`: tegels en doorkliklinks, blokken, modules uit, alleen-lezen, btw afvinken (logboek),
te laat, instelling, laatste jaar met boekingen, foutblok, EN/DE/FR.

### Btw-aangifte

pytest **991 groen** (13 in `tests/test_vatreturn.py`, 7 in `tests/web/test_web_vat.py`, menu en dashboard).
Echte data `compare/vat_check.py` (op een kopie van A, rubrieken volgens het voorstel, afdracht 1840): per jaar
2011–2026 en per btw-rekening de mutaties uit verkoop/inkoop **gelijk aan het btw-rapport** in 11 jaren; de 5 andere
(1831, € 0,62–1,76) zijn volledig verklaard door 5 facturen waarvan de btw-regel een andere datum heeft dan de factuur
(aangifte: datum btw-regel, btw-rapport: factuurdatum). Tegen de eigen 163 maandelijkse afdrachtboekingen van A: 24 maanden
gelijk op de afronding per rekening, 12 maanden twee aan twee verschoven, 127 met verschil; de aangifte telt precies de
boekhouding van nu (bv. mei 2024 voorbelasting 1835 € 6.879,18, afgedragen op € 7.599, zoals al in de analyse). A heeft
geen audittrail, dus welke boekingen later zijn ingevoerd of gewijzigd is niet aan te wijzen (BA7 meldt dat voortaan zelf).
Grondslag × tarief ≈ btw per jaar (1A/1B vanaf 2019): op 1 na (2019 1A, kassabonnen) binnen 1%; 16 meldingen
"btw past niet bij het nettobedrag" in 16 jaar. Tijd: ≤ 0,1 s per maand, ≤ 0,3 s per jaar (A).
Browsercontrole (tijdelijke beheerder op een kopie van A): rubrieken, overzicht, doorklik, Excel/PDF, formulier,
btw-boeking maart 2026 (teruggave € 973) → vastgelegd → later ingevoerde inkoop geeft de verschilmelding; 375 px.

### ICP-opgaaf

pytest **1019 groen** (22 in `tests/test_icp.py`, 7 in `tests/web/test_web_icp.py`; `tests/web/test_web_vat.py`
aangepast: klant op 3B zonder EU-nummer wordt geweigerd). Echte data heeft vrijwel geen ICP (A: geen EU-klanten;
C: Brits, na Brexit geen ICP), dus getoetst met eigen gegevens. Browsercontrole op testadministratie `icp_test`
(kopie `sl_template` met DE-klant: factuur 2 machines + 4× installatie en een creditnota; FR-klant diensten zonder
artikelen; BE-klant ABC met machine + installatie; binnenlandse verkoop 21%): 3e kwartaal 2026 DE 2.250/380, FR 0/1.841,
BE 0/96 en ABC 1.250; totaal exact = rubriek 3B (5.816,25); doorklik, aangiftelink, klant zonder nummer geweigerd,
PDF/CSV/Excel, 375 px zonder horizontaal scrollen.

### Nieuwe administratie (rekeningschema's, deelstap 1)

pytest **1062 groen** (23 in `tests/test_newcompany.py`, 10 in `tests/web/test_web_newcompany.py`), run_compare **123/0**.
`compare/newcompany_check.py`: per meegeleverd schema een nieuwe administratie, dan inbreng, verkoop met btw en kosten
plus rekeningen, proefbalans, W&V en balans door Perl en Python: **5 schema's, 0 verschillen** (SL 3.2.12 opent en
gebruikt de nieuwe databases). Browsercontrole: "Bakkerij Proef" aangemaakt via het scherm, dashboard, rekeningschema,
btw-rubrieken (1A/1B/5B, afdracht 1590, afronding 4900), 375 px zonder horizontaal scrollen.

### NL-rekeningschema's (deelstap 2)

pytest **1073 groen** (o.a. `test_dutch_charts`, `test_dutch_company_with_rgs_and_reverse_charge`,
`test_split_base_reverse_charge_pair`, `test_taxes_negative_rate_for_reverse_charge`), run_compare **123/0**. `compare/newcompany_check.py`:
**8 schema's, 0 verschillen**; bij de NL-schema's met een EU-inkoop bij een leverancier met het btw-paar 4B −21 % /
5B +21 % (Perl en Python boeken 1570 +42, 1571 −42, crediteuren 200). Alle RGS-codes bestaan in RGS 3.8
(`dev/rsnl/gen.py` toetst tegen `RGS 3.8-def.xlsx`). Browsercontrole (server 8769 vanuit de worktree): "Bakkerij
Proef NL" aangemaakt met Eenmanszaak/vof (standaard in de keuzelijst), dashboard, btw-rubrieken (2A/4A/4B met −21 %,
paren op 5B), Beheer > Btw-tarieven ongewijzigd opgeslagen met de negatieve tarieven, rekeningschema (183 regels,
375 px zonder horizontaal scrollen).

### Samenvoeging na keuring (2026-10-11)

CH/UK/FR/BE-schema's, import/export, fase Uren, fase 6 (6a/6b) en de RGS-koppeling fast-forward in `fase4`, na de
correcties FR B1–B7 en BE B1–B6: pytest `tests/` **646 groen** (2 overgeslagen), `tests/web` **551 groen**, run_compare
**133 scenario's, 0 onverwacht**, `newcompany_check` **13 schema's, 0 verschillen**.

### Zwitsers rekeningschema (deelstap 3)

pytest **1128 groen**, run_compare **123/0**. `ch-kmu`: 147 regels, 109 rekeningen, gemaakt met `dev/rsch/gen.py` uit `dev/rsch/master.txt` + de
`Swiss-*-chart.sql` van het KMU/PME/PMI-schema uit 2012 (GPL, niet in git); controle-Excel `dev/rekeningschema_ch_controle.xlsx` (per regel nieuw/oud nummer, wat
er veranderde, herkomst van de tekst; tabblad niet overgenomen: 3950/3955). pytest: `test_swiss_chart`,
`test_create_swiss_company_in_italian` (Ziffern met land CH, NL-aangifte niet aan, NL-rubriek en CH-Ziffer naast
elkaar op één rekening), `test_create_swiss_chart_in_italian` (web: documenttaal = schermtaal).
`compare/newcompany_check.py`: **10 schema's, 0 verschillen** (Perl boekt en rapporteert ch-kmu: 193 rapportregels gelijk).
Browsercontrole (server 8770 vanuit de worktree, eigen app-db `sqlledger_app_rsch`, testbeheerder `testrsch`):
"Ticino Prova SA" met Italiaanse rekeningnamen (documenttaal nl = schermtaal), "Zürich Test AG" met schermtaal DE en
rekeningnamen van het land (documenttaal de), Beheer > Rekeningschema en Btw-tarieven (8,1/2,6/3,8 %), formulier in het
Frans op 375 px zonder horizontaal scrollen.

### Belgisch rekeningschema (deelstap 6)

pytest **1134 groen**, run_compare **123/0**. `be-mar`: 169 regels, 128 rekeningen (na B1–B6: 166/126), gemaakt met `dev/rsbe/gen.py` uit `dev/rsbe/master.txt` (niet in git; controleert
dat elke rekening van `leeg-be` erin zit); controle-Excel `dev/rekeningschema_be_controle.xlsx`. pytest:
`test_belgian_chart`, `test_create_belgian_company_in_french`. `compare/newcompany_check.py`: **13 schema's, 0
verschillen**. Browsercontrole FR + BE (server 8772 vanuit de worktree `~/sqlledger-test/rsfr`, schermtaal Frans):
keuzelijst "Belgique – PME (PCMN) · 126 comptes" en "France – PCG abrégé (PME) · 178 comptes", "Lyon Essai SARL"
(fr-pcg, 226 regels op 375 px zonder horizontaal scrollen, kopjes 4456/4457 op hun plek) en "Gent Proef BV" (be-mar, nl)
aangemaakt.

### Frans rekeningschema (deelstap 5)

`fr-pcg`: 223 regels, 175 rekeningen (na B1–B7: 226/178), gemaakt met `dev/rsfr/gen.py` uit `dev/rsfr/master.txt` (niet in git; de generator
controleert dat elke rekening van `leeg-fr` erin zit met dezelfde koppeling); controle-Excel
`dev/rekeningschema_fr_controle.xlsx`. pytest: `test_french_chart`, `test_create_french_company` (rekeningnamen in het
Nederlands, 8 CA3-regels met land FR). `compare/newcompany_check.py --only fr-pcg`: 0 verschillen (269 rapportregels; `leeg-fr` 0 verschillen).

### Brits rekeningschema (deelstap 4)

pytest **1130 groen**, run_compare **123/0**. `uk-ltd`: 115 regels, 107 rekeningen, gemaakt met `dev/rsuk/gen.py` uit `dev/rsuk/master.txt` (niet in git); controle-Excel
`dev/rekeningschema_uk_controle.xlsx` (per regel nummer in UK_General, wat er veranderde; tabblad "niet overgenomen":
4920/5220). pytest: `test_uk_chart`, `test_create_uk_company`, `test_language_and_currency` (lege schema's met box/Ziffer
en landcode, NL-aangifte niet aan). `compare/newcompany_check.py`: **11 schema's, 0 verschillen**. Browsercontrole
(server 8771 vanuit de worktree `~/sqlledger-test/rsuk`, schermtaal Engels): "Bristol Test Ltd" aangemaakt (documenttaal en,
boekjaar tot maart), rekeningschema 115 regels op 375 px zonder horizontaal scrollen, Btw-tarieven 20/5/0 % ongewijzigd
opgeslagen.

### Eerste start (wizard)

pytest **1100 groen** (o.a. `tests/web/test_web_start.py` 22: alles naar `/welkom` zonder gebruikers ook voor HTMX,
dicht zodra er een gebruiker is, startcode alleen als hash en nieuw per start, foute code geweigerd en IP-blokkade,
CSRF, beheerder met taal/notatie, 2FA verplicht vóór stap 4, stap 4–5 alleen voor systeembeheerders, hervatten na
onderbreken, schema per taal, nieuwe administratie + uitnodigen met één foute regel, koppelen, startlijst met
"niet nodig"/verbergen en niet voor de boekhouder, code mailen). `compare/newcompany_check.py`: **9 schema's,
0 verschillen** (nieuw: `leeg-de`). run_compare **123/0** (standaardset, na de bouw).

### RGS-koppeling (RS13)

Volledige set: pytest **1197 groen** (646 + 551 web). pytest: `tests/test_rgs.py` (inlezen van een klein bestand in de vorm van `RGS 3.8-def.xlsx` met MKB-blad en
versus-blad, foutmeldingen, opslaan en de nieuwste versie laden (3.10 na 3.8), eerste voorstel voor debiteuren,
crediteuren, bank, kas, omzet, huur en een kop, nooit een balanscode voor een W&V-rekening en omgekeerd, oude koppeling
via het versus-blad als eerste voorstel), `tests/web/test_web_rgs.py` (inlezen + logboek, rechten, scherm zonder lijst,
kiezen/andere code/geen code/onbekende code, filter, zoeken, kolom RGS op het rekeningschema). Geen Perl-vergelijking
(SL kent geen RGS). **Echte lijst:** `RGS 3.8-def.xlsx` ingelezen: 4.979 codes, 3.704 MKB, omzetting vanaf 3.0–3.7.
**Kwaliteit voorstellen** gemeten op onze eigen NL-schema's (de codes die daar al in staan als juiste antwoord):
eenmanszaak 62% eerste voorstel / 77% bij de drie, bv 65% / 77%, basis 60% / 75%. Browser: NL-proefadministratie uit
het SL-sjabloon (204 rekeningen, nog zonder codes), kiezen en opslaan, 375 px zonder horizontaal schuiven.

### Back-up inlezen en oude versies bijwerken (fase 6a/6b)

Volledige set na 6a/6b: pytest **1181 groen**, run_compare **133 scenario's, 0 onverwacht**.
pytest: `tests/test_slbackup.py` 14 (splitsen met aanhalingstekens/regeleinden/`$$`/commentaar/COPY, acht gevaarlijke
statements geweigerd, overslaan van eigenaar/rechten/DROP/commentaar, psql-opdracht geweigerd, geen back-up herkend,
versieberekening en volgorde van de 70 scripts, pg_dump (gzip) van de testadministratie terugzetten = zelfde rijen, fout =
geen database, 4.x-versie geweigerd), `tests/web/test_web_restore.py` 3 (inlezen + koppelen + logboek, dubbele database
geweigerd, geen back-up/gevaarlijke regel geweigerd zonder database, wizardtegel actief). **Echte back-ups**
(`compare/slupgrade_check.py --restore-py`, alleen lokaal): vier andere administraties, administratie B,
administratie C (161.284 statements) en administratie A (422.411 statements), alle SL 2.8.37 met databaseversie 2.8.10: met de Python-lezer
ingelezen en in Python bijgewerkt naar 3.2.4 (19 scripts, 0 overgeslagen) = **schema en alle gegevens gelijk** aan psql +
Perl `User::dbupdate`; per back-up alleen de 54–57 `DROP TABLE`-regels overgeslagen.

### Back-up van Run my Accounts / ledger123 inlezen (RMA_IMPORT_ANALYSE.md §9–§10, RI1–RI7)

Herkend aan een RMA-tabel (`invoicetax`, `acc_trans_log`, …) of versie 2.8.11 … 2.8.x (`slbackup.rma_family`); alleen in
dat pad mogen `CREATE DATABASE`/één `\connect` (`pg_dump -C`) en ENUM-typen, de extensie fuzzystrmatch wordt overgeslagen.
`sqlledger/rmaimport.py`: referentie = het originele SL-2.8.10-schema (`slschema/ref-2.8.10.txt`, gemaakt met
`compare/make_ref_2810.sh` uit RMA-commit 6d3c94f); alles daarbuiten gaat vóór de keten naar schema `rma` (extra
tabellen/sequences/ENUM's verhuizen; volledige kopie van tabellen met extra kolommen, van wat de keten herbouwt,
`defaults` en `inventory`; triggers/functies/foreign keys als tekst in `rma.object`), versie → 2.8.10, dan de gewone
keten. Terugzetten (RI5): kostenrekening per leverancier → `x_vendor_default`, eindmaand boekjaar → `defaults.yearend`;
de rest wordt geteld (zonder plek: blijft in `rma`). Controle (RI6): proefbalans per rekeningnummer en openstaand per
factuur vóór = na (2 decimalen), anders afbreken en database weg; rijen per brontabel terug te vinden. Rapport in
`rma.import_report`, getoond na het inlezen, op het foutscherm en bij Beheer > Run my Accounts-gegevens (menu via
`company.rma`, app-db migratie 12). Gewone `inventory` blijft leeg tot RM4 (RM11). Een PyBalans-back-up neemt `rma` mee
en is weer in te lezen (BK7). Gevonden: de originele upgrade 3.2.0→3.2.1 gooit een aparte verliesrekening
koersverschillen stil weg (findings ADM-17); het rapport waarschuwt. pytest: `tests/test_rmaimport.py` (referentie,
extra's van de vier dumps, inlezen van `rma_test_ch/de/oud` + `ledger28` (optioneel) met gelijke proefbalans en
openstaand, afbreken bij verschil, terugzetten, back-up van deze app), `tests/web/test_web_restore.py` (controlescherm,
rapport, Beheer-pagina en menu, foutscherm met verschil).

### Back-up maken (fase 6d-1)

pytest: `tests/test_dbbackup.py` 5 (`\restrict`/`\unrestrict` overgeslagen en andere psql-opdrachten nog steeds
geweigerd, kop herkend als soort `app`, rotatie laat de nieuwste N staan en raakt andere bestanden niet, back-up van een
administratie met `x_`-tabellen en daarna inlezen = **elke tabel (md5 van alle rijen), sequences, functies, triggers,
indexen en constraints gelijk**, geen bijwerkstappen; fout in pg_dump of pg_dump niet gevonden = geen bestand),
`tests/web/test_web_backup.py` 7 (scherm, download, kop, `last_backup`, logboek en weer inlezen; zip met sjablonen,
varianten en logo, inlezen met zip = 3 overgenomen en de vorige versie overgeslagen; foute zip of geen zip = geen
database en geen administratie; pg_dump-fout getoond en gelogd; alleen systeembeheerder; herinnering op het dashboard
bij nooit/8 dagen, niet bij 6 dagen en niet voor de boekhouder; opdrachtregel: alle actieve + app-database, `--keep 2`,
`--company`, mislukte administratie gaat door met code 1, logboek `via=cli`). **Echte administraties** (alleen lokaal):
A (`adm_a`, 422.403 rijen, 4,3 MB gzip, 2,7 s) en `web_demo` na bijwerken van de `x_`-tabellen: back-up → inlezen =
alle 95 resp. 92 tabellen, sequences, functies, triggers en indexen gelijk.

### Overstap uit SQL-Ledger (fase 6d-2)

pytest: `tests/test_switchover.py` 9 (schone testadministratie = alle controles in orde; elke soort bevinding met een
opzettelijke fout gevonden: niet in evenwicht +5,00, 1-centkoersregel als informatie, onbekende rekening, regels zonder
boeking, meer betaald, factuur sluit niet aan −10,00, overgeslagen opdrachten; aantallen per jaar = tabeltellingen;
saldi kas/bank = dashboard (= balans); herkomst vastleggen/lezen, lijst met punten per module, afvinken, niet nodig,
afsluitdatum, btw "geen", verbergen; btw-punt pas klaar als élke btw-rekening een rubriek heeft; bankpunt pas klaar met
IBAN en een aansluitend eerste afschrift; aanzetten voor een gekoppelde database; openstaand per relatie = dashboard,
verkoop en inkoop), `tests/web/test_web_switchover.py` 4
(inlezen zet de lijst aan in plaats van de startlijst; gedaan/niet nodig/verbergen/weer tonen; aanzetten bij een
gekoppelde administratie, 2FA-punt, alleen beheerder; controleblad met alle onderdelen, peildatum, bevinding "nakijken"
en PDF). `tests/web/test_i18n.py`: het controleblad toont rekeningnamen (gegevens) en staat daarom bij `DATA_PAGES`.
**Echte data** (alleen lokaal, de echte back-ups): controles op A (0 bevindingen, 0,27 s) en C (6× 1-cent
koersverschil 2014–2015 als informatie, 2 inkoopfacturen meer betaald, 1 verkoopfactuur sluit niet aan: echte
eigenaardigheden in de oude data); de back-up van administratie B van 16-4-2026 (SL 2.8.37) ingelezen op testserver 8776: lijst
op het dashboard, controleblad per 14-04-2026 met alle controles in orde, PDF 37 kB. **Proefdraai administratie A**
(back-up 16-4-2026, 97 MB, 422.411 opdrachten): ingelezen in 54 s, bijgewerkt 2.8.10 → 3.2.4 (19 scripts, 0
overgeslagen), alle controles in orde, laatste boeking 11-04-2026, controleblad eerst 14,5 s (ouderdomsanalyse) →
na de dashboardquery onder 1 s.

### Interne uren (v1.1)

Volledige set: pytest **1239 groen** (2 overgeslagen). `tests/test_timecards.py` 4 nieuw (soort project en
kostenplaats incl. controles en alles-of-niets, intern tarief vast bij opslaan in de volgorde toewijzing > medewerker >
kostprijs dienst, interne projecten nooit naar een verkooporder ook niet via de server, rapportfunctie met filters en
berekend tarief), `tests/web/test_web_jc.py` 2 nieuw (projectscherm, Interne tarieven, toewijzing, maandstaat met label,
tijdkaart toont tarief, Verkooporders uit uren zonder intern, rapport met bedragen en CSV/Excel/PDF; alleen lezen).
Perl-vergelijking: `--scenarios jc` **5 scenario's, 0 onverwacht**, `--scenarios am` **15 scenario's, 0 onverwacht**.
Browsercontrole (server 8779, `v11u_demo`): intern project met nieuwe kostenplaats, tarief medewerker, toewijzingen,
Mijn uren met label "intern", verkooporder alleen voor het klantproject, rapport per project en medewerker; 375 px
zonder horizontaal scrollen.

### Urenstaat als PDF (fase Uren U-4)

Volledige set na U-4 (fase Uren af): pytest **1164 groen**, run_compare **133 scenario's, 0 onverwacht**.
`tests/web/test_web_jc.py` `test_timesheet_pdf` (PDF met project en klant, bestandsnaam, inhoud via dezelfde renderer in
nl en en: titel, aftekenvak). Voorbeeld `dev/voorbeeld-pdf/uren/urenstaat-lyon-fr.pdf` (niet in git): "Relevé d'heures"
van Test Rsch, oktober 2026, project P-LYON met klant, 3 dagen, totaal 19,0, "Bon pour accord".

### Toewijzingen, maandstaat en rol urenschrijver (fase Uren U-3)

Volledige set na U-3: pytest **1163 groen**, run_compare **133 scenario's, 0 onverwacht**.
pytest: `tests/test_timecards.py` `test_assignments_and_month_sheet` (overlap geweigerd, aansluitend mag, productie-opdracht
geweigerd, prijs van de toewijzing, opslaan alleen wijzigingen, 0 = weg, dag op een order vergrendeld, niet-toegewezen
combinatie geweigerd), `tests/web/test_web_jc.py` `test_assignments_and_month_sheet_for_hours_role` (boekhouder wijst toe;
urenschrijver: dashboard en andere schermen → Mijn uren, posten elders geweigerd, menu alleen Mijn uren, opslaan met de
medewerker uit de login ook als het formulier een andere stuurt). Browsercontrole (server 8774): toewijzing Test Rsch →
P-LYON/CONSEIL, Mijn uren oktober 2026 (dag 11 grijs: al op order), 7,5 en 8 uur opgeslagen ("2 wijzigingen", totaal
19,00), 375 px: alleen de tabel scrolt, de pagina niet.

### Verkooporders uit uren (fase Uren U-2)

Volledige set na U-1 + U-2: pytest **1161 groen**, run_compare **133 scenario's, 0 onverwacht**.
`tests/web/test_web_jc.py` `test_sales_orders_from_hours` (samengevat, project zonder klant → klant kiezen of fout, één
order per klant, `allocated` = qty, daarna geen open uren). Perl-vergelijking: driverstap `pe_so` (pe.pl
`project_jcitems_list` → `generate_sales_orders`) vs `compare_jc.pe_so_py`, scenario's per regel (alle uren, twee
klanten) en samengevat (deel van de regels): tabellen `oe`, `orderitems`, `jcitems`, `audittrail` gelijk; plus een
scenario met een dienst zonder btw-rekening (verwacht verschil, afwijking 232). `--scenarios jc` **5 scenario's, 0
onverwacht**. Browsercontrole (server 8774): open uren van "Lyon Essai SARL" → verkooporder 1 (332,50) in de lijst
Verkooporders.

### Tijdkaarten (fase Uren U-1)

pytest: `tests/test_timecards.py` 3 (geklokte tijd incl. over middernacht, opslaan/lezen/zoeken, uit-datum de dag erna,
niet lager/niet weg met uren op een order, controles: productie-opdracht, geen dienst, afgesloten periode, medewerker van
de gebruiker), `tests/web/test_web_jc.py` 3 (nieuw met tijden + bijwerken + opslaan, wijzigen, lijst, verwijderen met
bevestiging, rechten, menu). Perl-vergelijking: driverstap `jc_ui` (jc.pl als browser: add/edit → update → save, resave;
delete) vs `compare/compare_jc.py` (`timecards.form_update`), `--scenarios jc` **2 scenario's, 0 onverwacht** (tijden,
over middernacht met niet te declareren, eigen aantal en prijs, wijzigen van eindtijd en dienst, verwijderen).
Browsercontrole (server 8774 vanuit `~/sqlledger-test/uren`, "Lyon Essai SARL"): tijdkaart 09:00–12:30 → 3,50 uur × 95,00 =
332,50, opslaan, lijst met totaal, 375 px zonder horizontaal scrollen.

### Betaalbestand SEPA (bouwstap 5)

pytest: `tests/test_sepa.py` 12 (SEPA-tekenset en kenmerken, volgende werkdag, omschrijving NL/RF/vrij, betaalrekeningen met IBAN, standaardselectie op vervaldatum, leverancierfilter, geen/ongeldige/niet-SEPA IBAN, adres buiten de EER, creditnota en andere valuta als melding, korting op de uitvoerdatum, bestand .03 en .09 geldig tegen de XSD met bedragen/kenmerken/BIC, vastlegging en audittrail en niets geboekt, adresregels en geen BIC, weigeringen laten niets achter, dubbele factuurnummers, rondgang betaalbestand → camt.053 → voorstel (a)/(b) exact), `tests/web/test_web_sepa.py` 6 (menu en rechten, zonder IBAN, maken en downloaden + logboek, versie .09 en filter, weigeringen, Bijwerken houdt keuzes). Geen Perl-vergelijking: SL 3.2.12 heeft geen SEPA.

### Importeren facturen, orders en betalingen (bouwstap 4)

pytest: `tests/test_import_docs.py` 8 (groeperen op nummer + relatie, facturen proefrun → import met netto/btw/totaal, standaarden van klant en artikel, korting van de klant, btw inclusief, meteen betaald, vreemde valuta zonder/met koers, fouten per document en alles of niets, verkoop- en inkooporders met hetzelfde nummer, betalingen op nummer en kenmerk, deelbetalingen in één bestand, niet meer dan openstaat, Credit/Debet, dubbel nummer in ar en ap), `tests/web/test_web_import.py` +3 (facturen- en betalingenflow met de bedragkolommen, Engels). Perl-vergelijking: driverstap `im_ui` uitgebreid met de vier soorten (alle verborgen velden van im.pl mee, `paymentaccount`/`currency` zoals het importscherm), `--scenarios im` **9 scenario's, 0 onverwacht**: facturen gelijk op `ar.bank_id` na (zoals het scherm), orders gelijk (termijn en leverdatum zoals IM.pm), Perl-fout IM.pm:703 aangetoond (inkooporder vervangt verkooporder met hetzelfde nummer), betaling op kenmerk gelijk op de sleutel van het volgende betaalkenmerk en het logboek na. Browsercontrole (server 8765 vanuit `~/sqlledger-test/importen`, web_demo, alleen proefruns): facturen met een bestaand nummer en een onbekende klant, document zonder nummer krijgt het volgende nummer; betalingen met deelbetaling, te hoog bedrag en onbekend kenmerk.

### Importeren en exporteren (fase 4 stap 7)

pytest: `tests/test_importer.py` 15 (CSV-varianten incl. regeleinden en Windows-1252, xlsx, koppelen op naam/label/bewaarde
koppeling, getallen in beide notaties, proefrun verandert niets, alles of niets, lege cel laat staan, dubbele sleutel,
artikelen/diensten/groepen/rekeningen/memoriaal, export → import = ongewijzigd per soort), `tests/web/test_web_import.py` 7
(rechten en menu, hele flow met koppeling wijzigen en onthouden, niets bij een fout, verlopen bestand, export CSV/xlsx terug
inlezen, Engelse koppen). Perl-vergelijking: driverstap `im_ui` (im.pl als browser met hetzelfde bestand) vs `compare/compare_im.py`,
`--scenarios im` **5 scenario's, 0 onverwacht** (verklaard: startdatum/soort van een nieuwe relatie, medewerker en koers bij het
memoriaal, boeking op een kop); standaardset **128 scenario's, 0 onverwacht**. Echte data `compare/im_keeptest.py adm_a adm_c`:
elke soort als CSV én xlsx (notatie 1.000,00 / dd-mm-jj) geëxporteerd, teruggelezen en geïmporteerd = **alles ongewijzigd, 0
verschillen in de tabellen**, behalve rijen waarvan het nummer in de administratie dubbel staat (A 27 klanten, 9 leveranciers,
28 artikelen; C 21 klanten, 4 leveranciers, 26 artikelen, 443 diensten): die geven terecht een fout. Gevonden en opgelost:
spaties achter groepsnamen (A) en verkopersnamen (C). Browsercontrole (server 8773 vanuit `~/sqlledger-test/imp`):
klanten importeren met een foute rij, bijwerken met "Gewijzigd: Plaats", 375 px zonder horizontaal scrollen, exportscherm.

### Huisstijl

pytest: `tests/test_docstyle.py` 13 (opslag alleen afwijkingen + `style.saved`, contrast WCAG incl. de grens #767676/#777777, alle snelkeuzes donker genoeg, Documentinstellingen en huisstijl laten elkaars sleutels staan, audittrail, **zonder huisstijl byte-gelijk aan het basissjabloon van `fase4`** voor 9 documentsoorten, kleur/lettertype/logo/adres in de HTML, variabelen voor eigen sjablonen, en met WeasyPrint gemeten: adresblok op 45,0 mm bij logo links/groot, midden/middel en rechts/klein met 4 adresregels), `tests/web/test_web_style.py` 11 (rechten, menu, links, voorbeeld met eigen CSP en lettertype-URL, PDF, niets 'geprint', voorbeeldfactuur DE/EN, opslaan en contrast, logo, overnemen incl. logo kopiëren en weigeren zonder beheerrol, melding eigen sjablonen, lettertypen/licenties, startlijst). `dev/doc_html_dump.py` oud (`fase4`) vs nieuw zonder huisstijl: **1.244 documenten** (A 498, C 652, web_demo 94) **byte-gelijk** (`diff -r`, ook stijlblok en witruimte). Gemeten tijdens het bouwen: alle 18 combinaties ligging × grootte × 2/4 adresregels → adres op 45,0 mm.

### Getest op een echte administratie

Daarnaast is vergeleken op een echte SQL-Ledger-administratie (back-up uit
2.8.37, met de originele `User::dbupdate` bijgewerkt naar 3.2.4; 184.415
journaalregels over 2011–2025). 18 scenario's: per jaar proefbalans (ook GIFI),
W&V per maand/kwartaal/vorig jaar/kasstelsel, balans per kwartaal met vorig
jaar, journaaloverzichten, mutaties van alle 149 rekeningen en boeken/wijzigen/
verwijderen. Resultaat: **0 onverwachte verschillen**. Elk resterend verschil
is per regel rekenkundig herleid tot een bekende Perl-bug (zie
`reconcile()` in `compare/run_compare.py`); die tellen mee in de uitvoer.

Gevonden in de echte data:
- `closedto = 20173112` (geen geldige datum) — zie hierboven.
- 131 journaalregels met float-ruis (bv. `-491231.730000001`), allemaal op
  31-12 van 2011–2023: de jaarafsluitingen die Perl onafgerond wegschreef.
  Sommen in Python zijn exact op basis van die opgeslagen waarden.
- Gelijke sorteersleutels in overzichten komen uit PostgreSQL in wisselende
  volgorde (ook tussen twee Perl-runs); de vergelijking koppelt regels op inhoud.

Betalingen op echte data: in een kopie van twee administraties zijn álle
openstaande posten via het betaalscherm afbetaald (Perl) en via
`Payments.post_payment` (Python): 9 inkoopposten (EUR) en 64 verkoop- plus
186 inkoopposten in GBP, EUR en USD, inclusief creditnota's (527
journaalregels). Resultaat: databases identiek.

Gebruik: `python compare/run_compare.py --sldir … --scenarios real --template <db>`.

Bankafschrift inlezen op echte data (`compare/bank_realdata.py`): op een kopie
wordt uit alle boekingen op de bankrekening in een periode een CAMT.053-afschrift
gemaakt (kenmerk/factuurnummer, IBAN waar bekend), de betalingen worden verwijderd
alsof ze nooit geboekt waren, het afschrift wordt ingelezen en alle voorstellen
worden verwerkt. A 1000 Q4 2024 (219 regels): 100% goed gekoppeld; C 1100
oktober 2015 (98 regels): 99% (1 regel = één overboeking voor meerdere al
geboekte posten, README 114). Daarna `ar`/`ap` en het saldo van elke rekening
gelijk aan het origineel, op de laatste betaaldatum van 4 posten van C na (README 115).

Memoriaalscherm (stap 4): de driver speelt `gl.pl` na als browser (toevoegen,
wijzigen met bevestiging, als nieuw, verwijderen, nieuw nummer, vreemde valuta,
koersverschil, foutgevallen) en Python speelt hetzelfde na via de formuliercode
van de webapp (`routes_gl`): databases gelijk op de gedocumenteerde Perl-fouten
na (README 10, 43). Het journaalscherm (`gl.pl transactions`) is vergeleken op de
testdata en op beide echte administraties.

Relatiescherm (fase 2): de driver speelt `ct.pl` na als browser (`ct_ui`: toevoegen,
wijzigen, bedrijf/persoon wisselen, opslaan als nieuw, nieuw nummer, verwijderen) en
Python hetzelfde via de formuliercode van de webapp (`routes_ct`); de relatietabellen
(`customer`, `vendor`, `address`, `contact`, `bank`, `shipto`, `customertax`,
`vendortax`) worden mee vergeleken. Gelijk op de gedocumenteerde Perl-fouten na
(README 56–61). Echte data: `compare/ct_keeptest.py adm_a adm_c [--touch]` opent elke
klant en leverancier in het webscherm en slaat ongewijzigd (of met alleen gewijzigde
notities) op: 0 verschillen met het origineel.

Webschermen (stap 3): de tabellen rekenen niets; ze tonen `display`/`rows` uit
de bibliotheek. `tests/web/test_web_reports.py` en `compare/web_check.py --db <db>`
vergelijken elk bedrag op het scherm met de functies uit `run_compare.py` die tegen
rp.pl/ca.pl/aa.pl zijn bewezen. Echte data: A 2024–2025 (34 schermen) en C
2014–2016 (45 schermen, meerdere valuta) — 0 verschillen. Het mutatiescherm van
`ca.pl` (lopend saldo, subtotalen) is apart tegen Perl vergeleken (scenario
"mutatiescherm"), ook op beide echte administraties.

Beheer (fase 2): de driver speelt `am.pl` na als browser (rekening toevoegen/wijzigen/als
nieuw/verwijderen, btw-scherm met 'Update', instellingen, afsluiten, valuta toevoegen/
wijzigen/verplaatsen/verwijderen, koersen, afdelingen, branches, betaalmethoden; talen en
projecten via `AM.pm`/`PE.pm`) tegen de formuliercode van de webapp (`compare/compare_am.py`);
15 scenario's (`--scenarios am`, ook in de standaardset), 0 onverwacht. Echte data:
`compare/am_keeptest.py <db> [--web]` opent op een kopie elke rekening, het btw-scherm, de
instellingen, afsluiten, elke valuta, elke koersdatum en elke afdeling/project/taal en slaat ze
ongewijzigd op (met `--web` via de HTML-formulieren van de webapp, als browser): A (163
rekeningen) en C (266 rekeningen, 4 valuta, 139 koersdatums, afdelingen, projecten, talen)
— 19 tabellen identiek aan het origineel.

Facturen met artikelen (fase 3): de driver speelt `is.pl`/`ir.pl` na als browser (`is_ui`:
toevoegen, relatie kiezen, artikelen een voor een in de lege regel + verversen, aantallen/prijzen/
korting/projecten, betalingen met eigen koers, opslaan met herbevestiging, opslaan als nieuw,
verwijderen) en Python hetzelfde via de formuliercode van de webapp (`compare/compare_is.py`,
`routes_is`); `invoice` wordt mee vergeleken. Kostprijsregels worden per boeking, rekening, datum en
project opgeteld (Perl: één regel per inkoop) en verschillen alleen in datum tellen als verklaard als
ze per boeking en rekening gelijk zijn (Perl boekt terug en opnieuw, README 132). 9 scenario's
(`--scenarios is`, ook in de standaardset): inkoop/verkoop/opnieuw opslaan, verkoop zonder voorraad
en latere inkoop, btw inbegrepen met twee tarieven en korting, vreemde valuta met betalingen, volledig
betaald + prijswijziging inkoop + als nieuw, samengesteld artikel, inkoop verwijderen met projecten,
btw inbegrepen in valuta (README 138), retouren (README 135) — 0 onverwacht. Echte data:
`compare/is_keeptest.py adm_a adm_c [--web]` opent op een kopie elke factuur met artikelen (A 11.982,
C 3.689) en slaat haar ongewijzigd op (met `--web` via de formuliercode van het scherm): grootboek
per boeking, factuurregels, `allocated` en voorraad identiek aan het origineel, behalve facturen
waarvan de btw-vinkjes van artikel of relatie na het boeken gewijzigd zijn (A 47, C 59; vóór het
opslaan al ander bedrag, README 137) en kopvelden die Perl bij opnieuw opslaan ook zo zet (laatste
betaaldatum, betaalkenmerk volgens sjabloon, `paid` uit de werkelijke betalingen); `avgcost` waar Perl
door float-ruis deelde (README 140). Via het scherm: 11 facturen van A met een leverdatum als
20121-04-25 vragen om een geldige datum; 1 kassabon (POS) is alleen-lezen.

Orders en offertes (fase 3 stap 3): de driver speelt `oe.pl` na als browser (`oe_ui`: toevoegen,
relatie kiezen, artikelen in de lege regel + verversen, aantallen/prijzen/korting/projecten/afdeling/
valuta, opslaan met herbevestiging, opslaan als nieuw, verwijderen, omzetten met de knoppen Order/
Offerte/Aanvraag, factuur maken met verzonden aantallen en daarna `is.pl`/`ir.pl` boeken) en Python
hetzelfde via de formuliercode van het webscherm (`compare/compare_oe.py`, `routes_oe`/`routes_is`);
`oe`, `orderitems` (zonder id), `cargo` en `inventory` worden mee vergeleken. Als nabestelling en factuur
alleen in volgorde van aanmaken verschillen, krijgen de Python-id's die van Perl (`oe_remap`, README 150).
8 scenario's (`--scenarios oe`, ook in de standaardset): verkooporder met korting/btw inbegrepen/opnieuw
opslaan, inkooporder/offerte/aanvraag/als nieuw/verwijderen, offerte → order → factuur, inkooporder met
deellevering, verkooporder deels gefactureerd, aanvraag → inkooporder en order → offerte, valuta + project +
afdeling + samengesteld artikel, factuur met nabestelling verwijderen — 0 onverwacht (verwacht: README
148, 149, 151, achtergebleven Perl-lock). Echte data: `compare/oe_keeptest.py adm_c adm_a [--web]` opent op een
kopie elke order/offerte (C 5.116, A 22) en slaat haar ongewijzigd op (gesloten en gefactureerde orders,
die het scherm weigert, via een interne testroute): kop, regels, afleveradres, afdeling, status,
referenties, vracht, magazijn, voorraad en tellers identiek aan het origineel, ook via het webscherm. 510
orders van C rekenen met de huidige btw-instellingen van relatie/artikel anders uit dan opgeslagen; een
steekproef van 25 daarvan via `oe.pl` opnieuw opgeslagen (eigen orderscenario voor C) geeft
in Perl dezelfde nieuwe bedragen (verschil alleen de medewerker, README 154). Koerstabel: 1 datum waarop
een order en een factuur een andere koers hebben; opslaan schrijft de koers van de order, zoals Perl.

Herhalen van facturen en orders (fase 3 stap 4): de driver maakt de factuur/order als browser (`is_ui`,
`oe_ui`), opent hem en zet het schema via de knop Schedule (`recurring_ui` met module `is`/`ir`/`oe`), en
verwerkt via `am.pl recurring_transactions` → `process_transactions`; Python via `routes_recurring`.
8 scenario's (`--scenarios rh`, ook in de standaardset): verkoopfactuur met korting en dienst (2 termijnen,
nieuwe artikelprijs geldt niet), inkoopfactuur die de kostprijs van een open verkoop levert, samengesteld
artikel, btw-instelling klant gewijzigd, leverdatum op de regel, te weinig voorraad, verkooporder met
leverdatum, inkooporder deels geleverd — 0 onverwacht (verwacht: README 161–163, O3, achtergebleven
Perl-lock). In A en C heeft geen factuur met artikelen of order een herhaalschema (echte-datatoets
niet van toepassing).

### Zelf draaien

Benodigd: PostgreSQL, Perl met DBI + DBD::Pg, de uitgepakte
`sql-ledger-3.2.12.tar.gz`.

```bash
compare/make_template.sh /pad/naar/sql-ledger
python compare/run_compare.py --sldir /pad/naar/sql-ledger
python -m pytest
```

Standaardverbinding: `127.0.0.1:55432`, gebruiker `postgres`
(aan te passen met `--dsn`, `PGHOST/PGPORT/PGUSER` en `SQLLEDGER_TEST_DSN`).

## Open punten

- **Pakket 3 open punten na v1.2.0 (OPEN_PUNTEN_ANALYSE.md F27a, F27b, F42; alles A, F27b alleen ontkoppelen,
  2026-10-13): keuzes binnen A — 1–12 AKKOORD (gebruiker 2026-10-14), in `main`.**
  1. **F27a "nooit ingelogd"** = `app_user.last_login IS NULL` (wordt pas gezet na volledig inloggen, dus ook na
     wachtwoord kiezen zonder in te loggen nog verwijderbaar). Geen migratie.
  2. **F27a verwijderen** via een bevestigpagina (knop "Gebruiker verwijderen" onderaan de gebruiker, alleen als het
     mag; anders een korte uitleg). Lidmaatschappen, codes, sessies, rapportvoorkeuren gaan mee (CASCADE); het
     logboek houdt de login (`user_deleted` met naam, e-mail en administraties).
  3. **F27a uitzetten** = vinkje Actief uit: ook alle lidmaatschappen, sessies en ongebruikte codes weg; het logboek
     noemt de weggehaalde administraties (`removed`). Weer aanzetten = toegang opnieuw instellen (helptekst).
  4. **F27a filter**: Systeem > Gebruikers toont standaard alleen actieve gebruikers, link "Ook uitgezette gebruikers
     tonen (N)" (`?uit=1`); de link staat er alleen als er uitgezette zijn.
  5. **F27a laatste systeembeheerder**: geldt voor uitzetten, systeembeheer afnemen en verwijderen (in de praktijk
     dekt "niet jezelf" dit al, want alleen een actieve systeembeheerder komt op het scherm; de controle blijft als
     vangnet).
  6. **F27a hergebruik**: alleen een login die ooit verwijderd is (`user_deleted` in het logboek) geeft een
     waarschuwing met datum en een verplicht vinkje "Ja, deze login opnieuw gebruiken"; het logboek krijgt
     `reused`. Bestaat de login nog als uitgezette gebruiker, dan melding "staat uit, zet hem weer aan".
  7. **F27b ontkoppelen**: bevestigpagina met database en gebruikers met toegang; naam exact overtypen
     (hoofdlettergevoelig). Geweigerd als de administratie aan staat, als er een inlezen loopt, of als er nog een
     inleesmarkering met dezelfde databasenaam is (anders zou `recover` die database later kunnen weggooien).
  8. **F27b naam in het logboek**: vóór het weggooien krijgen alle logregels van de administratie
     `detail.unlinked_company`; het logboek toont die naam als de administratie niet meer bestaat. Nieuwe regel
     `company_unlinked` met code, database, server/poort en gebruikers. Weg: rij `company` (CASCADE: membership,
     company_mail, report_pref, recent_company), `<data>/templates/<id>` en `<data>/mail/<id>`.
  9. **F42 zelfde medewerker**: vergeleken zonder `@…` en zonder hoofdletters; alleen tussen **actieve**
     gebruikers (een uitgezette heeft geen lidmaatschappen meer). Wordt gecontroleerd vóór een nieuwe medewerker
     wordt aangemaakt.
  10. **F42 verhuizen**: alleen bij een bestaand lidmaatschap waarvan de medewerker wijzigt en er bijlagen onder de
     oude login staan; tussenpagina met per administratie "N vertrouwelijke bijlagen onder login X meeverhuizen naar
     Y" (vinkje standaard aan). Zonder vinkje blijven ze staan. Niet bereikbare administratie = 0 (geen vraag).
     Bij uitzetten of toegang weghalen wordt niets verhuisd (B "wezen vrijgeven" later).
  11. **F42 label**: "Vertrouwelijk (alleen voor medewerker {login})" bij Bijlage toevoegen; kolom Vertrouwelijk bij
     referentiedocumenten heeft dezelfde tekst als tooltip. Helpteksten bijlagen en relatie bijgewerkt.
  12. **Perl**: F27a/b komen in Perl niet voor. F42 → **DOC-16** (al gepusht 17ac526, telling 199);
     README-afwijking 87. **Geen migratie** (hoogste blijft 21; app-db 11).

- **Pakket 2 open punten na v1.2.0 (OPEN_PUNTEN_ANALYSE.md F23–F26; alles A, 2026-10-13): keuzes binnen A — 1–10
  AKKOORD (gebruiker 2026-10-14), in `main`.**
  1. **F23 vinkje**: "Mij als beheerder van deze administratie toevoegen" bij *Bestaande database koppelen* (Systeem
     en eerste start), standaard aan. Bij een bestaande administratie alleen zichtbaar als niemand toegang heeft, dan
     standaard **uit** (anders maakt een gewone wijziging u ongemerkt beheerder). Logboek: `admin=<login>`.
  2. **F23 "niemand heeft toegang"** telt alleen **actieve** gebruikers (een uitgezette gebruiker met lidmaatschap
     telt niet). Lijst: oranje achter het aantal in de kolom Gebruikers; scherm: gele melding bovenaan. Een
     systeembeheerder zonder lidmaatschap telt ook niet (C was niet gekozen).
  3. **F23/F24 één database per administratie**: vergeleken op databasenaam + server + poort, waarbij leeg = de
     standaard uit de instellingen (dus `127.0.0.1:55432` uitgeschreven = leeg). Geldt bij koppelen én wijzigen;
     melding "De database X hoort al bij de administratie Y." Back-up inlezen vergelijkt zoals voorheen alleen de naam.
  4. **F24 bevestigstap** alleen als databasenaam, server of poort verandert (naam/code/modules: gewoon opslaan).
     Nieuwe database moet bereikbaar zijn ("Geen verbinding met de nieuwe database: …"); de oude mag onbereikbaar zijn
     (dan "niet bereikbaar" in de tabel). Bevestigen = verborgen velden + `confirm=1`; de controles lopen bij het
     bevestigen opnieuw. Logboek `company_updated` met `old_<veld>` voor elk gewijzigd veld (naam, code, databasenaam,
     server, poort). Koppelen van een nieuwe database mag nog steeds onbereikbaar (zoals voorheen; dashboard meldt het).
  5. **F25 Documenttaal** op Back-up controleren (nl/en/de/fr), voorinvullen: `x_setting.doc_language` uit een
     back-up van deze app, anders de taal van de gebruiker, anders nl. Ontbreekt het veld, dan die voorinvulwaarde;
     een onbekende waarde → "Onbekende documenttaal."
  6. **F25 taal in de administratie**: `x_setting` `doc_language` (geen migratie; `newcompany.save_setting`), gezet bij
     Nieuwe administratie, Back-up inlezen en bij opslaan van *Beheer > Instellingen*. De app leest nog steeds
     `company.doc_language`; `x_setting` dient alleen om de taal in een back-up mee te nemen. Bij **koppelen** van een
     bestaande database wordt de taal (nog) niet uit `x_setting` overgenomen — kan later als het nodig blijkt.
  7. **F25 btw/ICP-vlaggen** meteen na inlezen via `routes_vat.company_flags` (zelfde regel als `sync_flag`: rubrieken
     ingesteld + tijdvak niet "geen", per aangifteland; ICP = 3B en NL). Een SQL-Ledger-back-up heeft geen rubrieken →
     beide uit, zoals voorheen. Bij koppelen niet (zoals voorheen; gaat aan bij opslaan van de btw-instellingen).
  8. **F26**: *Variant X verwijderen* toont eerst een bevestiging ("De vorige versies … (n) gaan ook weg.", alleen als
     er versies zijn); met een eigen `*.html` in de variant blijft het geweigerd (ook bij de bevestigknop). Weg gaat de
     hele variantmap (`shutil.rmtree`). Toets zonder omweg.
  9. **Perl**: F23, F25, F26 komen in 3.2.12 niet voor (bij *Delete language* ruimt Perl de sjabloonmap
     van de taal zelf op). F24 wel in andere vorm → **ADM-16** in sql-ledger-findings (02ce5ad, telling 198,
     gepusht 2026-10-14); README-afwijking 86.
  10. **Geen migratie** nodig (hoogste blijft 21).

- **Pakket 1 open punten na v1.2.0 (OPEN_PUNTEN_ANALYSE.md W1, AD1, K1–K4, F39–F41, F43, F44, PF1, PF2; alles A,
  2026-10-13): keuzes binnen A — 1–12 AKKOORD (gebruiker 2026-10-13), in `main`.**
  1. **AD1 koersen** (ADM-15): `slupgrade.PATCHES` (script + exact oorspronkelijk statement → vervanging), het
     .sql-bestand blijft ongewijzigd. Nieuw: `buy`, anders `sell` (ook als `buy` leeg is); een koers met beide 0 of
     leeg valt nog steeds weg (zoals SL). Een toets faalt als het oorspronkelijke statement niet meer in het script
     staat. Al ingelezen administraties blijven zoals ze zijn (AD2: administratie C (UK) zo laten).
  2. **K1 menu**: *Systeem > Back-up inlezen* direct onder *Back-up maken*; de knop op Administraties blijft. Teksten en
     help die "Systeem > Administraties > Back-up inlezen" zeiden, zeggen nu "Systeem > Back-up inlezen" (EN/DE/FR).
  3. **K2 nummering**: "volgende: verkoopfactuur 2026-14338 (Instellingen: 20<%yy%>-14337), memoriaal GL-0100" —
     ingevuld met de datum van vandaag en uw datumnotatie (`numbering.preview`, slaat niets op); het sjabloon alleen
     als er een `<%…%>` in staat. Sjablonen die een relatie of artikel nodig hebben (`<%name%>` e.d.) blijven leeg in
     het voorbeeld. Uitleg bij het punt: "Controleer de volgende nummers".
  4. **K3 controlescherm**: gele waarschuwing bovenaan met per bestaande administratie één regel: zelfde dataset én
     back-updatum → "Deze back-up is al ingelezen als X"; zelfde dataset, andere datum → "Er is al een administratie
     uit dataset Y: X (back-up van D)"; anders gelijke databasenaam of naam. Herkomst uit `x_setting` van elke
     gekoppelde administratie (alleen lezen, 3 s per administratie; niet te openen = overslaan). Naam en databasenaam
     blijven vooringevuld; *Inlezen* weigert gelijke naam/databasenaam zoals voorheen.
     **Lege naam/databasenaam bij Cookie (13-10) niet te reproduceren**: de Cookie-back-up geeft in de voorbeeldlezer
     dataset `adm_a` en bedrijfsnaam "Administratie A B.V."; het controlescherm vult die altijd
     in (sinds v1.0.1 PB2, ook in v1.2.0), in Chromium ook bij een tweede inlezen van dezelfde back-up; het logboek
     van 13-10 toont de gewone weg (upload → controlescherm → Annuleren). Geen code gevonden die de velden leegmaakt.
     Zie je het weer, graag een schermafdruk (browser + welk bestand).
  5. **K4**: docstring `vatreturn.py` (elke aangifte in hele bedragen).
  6. **F41**: voorraadcorrectie, assembleren én demonteren weigeren een datum t/m de afsluitdatum, met dezelfde melding
     als tijdkaarten ("Deze datum valt in een afgesloten periode."); help Voorraadcorrectie en Assembleren.
  7. **F43**: alleen bij een datumfilter: "Bijlagen zonder datum (uit SQL-Ledger) niet getoond: N. *Tonen*"; Tonen
     zet `zonderdatum=1` (blijft staan bij een volgende zoekopdracht) met "… worden ook getoond. *Verbergen*".
  8. **F44**: *Annuleren* is een knop (POST `/bijlagen/annuleren`): tijdelijk bestand weg, terug naar het record (het
     adres wordt op de server bepaald, niet uit het formulier). Mislukt het koppelen, dan gaat het tijdelijke bestand
     ook weg (`finally`). Bij het starten van de server gaan tijdelijke bijlagen ouder dan een dag weg (zelfde grens
     als bij een volgende upload). Andere tijdelijke mappen (importen, back-ups) ongewijzigd.
  9. **F39 één prijsbasis** (prijshistorie en *Regels uit*): ingevoerde prijs vóór korting × koers van het stuk,
     afgerond op de decimalen van de ingevoerde prijs (minstens de administratie-precisie, zoals IS.pm); een oude
     SL-factuurregel zonder `fxsellprice` toont `sellprice` (al in standaardvaluta, na korting). Kolomkop "Prijs (EUR)";
     *Korting %* en *Valuta* (valuta van het stuk) blijven. Bij een stuk "inclusief btw" is de ingevoerde prijs inclusief
     btw (zoals ingevoerd; factuur en order nu gelijk).
  10. **F40**: nummer van een order/offerte linkt naar `/verkooporder/{id}` e.d. als de module Orders/Offertes aanstaat
      (dat scherm regelt alleen-lezen en locks zoals vanuit de orderlijst); facturen blijven via `/boekingen/{id}`.
  11. **PF1/PF2** (sql-ledger-findings, lokaal commit 5a8b7ef, **niet gepusht**): INV-22 rechtgezet (factuur-`sellprice`
      wordt vóór opslaan naar de standaardvaluta omgerekend: 3.2.12 IS.pm 1117/IR.pm 939);
      nieuw **DOC-15** "abandoned attachment uploads are never removed" (Form.pm 81–96 schrijft, alleen
      `save_reference` ruimt op); telling 197. Het pad-probleem met `referencetmpfile_N` stond al in SEC-21.
  12. **W1**: geen code; werkwijze in OVERDRACHT (volledige pytest in de eigen worktree; niet in `main` samenvoegen
      terwijl daar een run loopt).

- **Btw-aangifte per land, deelstap 3 België (BTW_LANDEN_ANALYSE.md BE1–BE6, BL3/BL5/BL6, alles A, 2026-10-13):
  keuzes binnen A — 1–13 AKKOORD (gebruiker 2026-10-13), in `main`.**
  1. **Bron** (gecontroleerd 2026-10-13): Intervat-help van de FOD Financiën (eservices.minfin.fgov.be, kader II–V)
     bevestigt de roosters uit de analyse; 91 bestaat niet meer. financien.belgium.be heeft een botcontrole (niet
     omzeild). Eén nuance uit de officiële tekst: 81/82/83 zijn "rekening houdend met de ontvangen creditnota's".
  2. **Koppelingen per btw-rekening**: verkoop-btw → het rooster van de grondslag **01/02/03** (btw in 54) in plaats
     van "54"; aftrek → 59; verlegde paren (negatief tarief) → **86** of **88** (btw 55), **87** (btw 56), **57**
     (invoer: btw 57, grondslag in 87). Een bestaande Belgische koppeling "54" zet migratie 21 om naar 01/02/03 op het
     tarief. Klant (omzet zonder btw): **47** standaard, 44, 46 en ook **45** (medecontractant) en **00** (bijzondere
     regeling), twee meer dan BL4 noemde.
  3. **Creditnota's** (BE1), per boeking: negatieve bedragen gaan naar 48 (van 44/46) of 49 (de rest), verschuldigde
     btw naar 64; ontvangen: aftrekbare btw naar 63, grondslag 86/88 naar 84, 87 en de rest naar 85, en **81/82/83
     worden verlaagd** (officiële tekst). Zo staat elk rooster positief. Zou 81–83 voor een tijdvak in totaal negatief
     worden (alleen creditnota's), dan blijft het negatief staan (niet afgevangen).
  4. **81/82/83** (BL3/BE2): de grondslag van **elke** inkoopboeking (ook zonder btw en ook verlegd) wordt naar rato
     verdeeld over de kosten- en activaregels op de boekingsdatum (zonder btw-, debiteuren-, crediteuren- en
     betaalrekeningen, dus betalingen tellen niet); memoriaal met aftrekbare btw: de kostenregels aan de aftrekkant.
     Keuze per rekening op Btw-rubrieken (tabel "Inkoop per kostenrekening"), opgeslagen in `x_vat_account_box`
     (migratie 21). Voorstel: klasse 60 en voorraden 81, klasse 2 → 83, de rest 82. Niet-aftrekbare btw staat op de
     kostenrekening en zit dus in de grondslag ("aftrekbare btw niet inbegrepen").
  5. **Afronding** (BL6, besluit 2026-10-13): hele euro's — grondslagen en verschuldigde btw (54–57, 63) omlaag,
     aftrekbare btw (59, 64) omhoog; XX, YY en 71/72 uit de afgeronde bedragen; centen naar afrondingsverschillen.
     Intervat zelf kent centen (dan ",00" invullen).
  6. **Schema's** (BE3): `be-mar` en `leeg-be` krijgen de koppeling 01/02/03 en vijf rekeningen: **411100** aftrek
     verlegd 21 %, **451186** IC-verwerving goederen, **451188** IC-diensten, **451187** verlegd binnenland, **451157**
     invoer (alle −21 %). 81/82/83 staan in de kolom `rubriek` bij de kosten- en activarekeningen (60 → 81, 61–64 met
     AP_amount → 82, klasse 2 → 83, voorraden → 81); `chartfiles` staat dat alleen voor BE toe. Alleen 21 % (geen
     verwerving tegen 6/12 %). Bestaande administraties worden niet aangepast.
  7. **Formulier** (BL10): kader II–VI in één kolom, blauw-grijs, "Bedragen in euro, zonder centen"; de officiële tekst
     in het **Frans voor een Franstalige gebruiker, anders in het Nederlands**, met de vertaling eronder als de
     schermtaal anders is; 61 en 62 met "niet berekend: zelf invullen". PDF, "Proefafdruk" tot de btw-boeking.
  8. **Btw-boeking** (BL9): het saldo XX − YY op de afdrachtrekening (schema: 451900; tegoed = debet), memo "saldo";
     verschilmelding "Saldo (71 min 72) toen … nu …", correctie via 61/62 in een volgende aangifte.
  9. **Teruggaaf** (BE6): bij rooster 72 een melding: vanaf het minimum (€ 400, in december/4e kwartaal € 50) "vink in
     Intervat de teruggaaf aan (kader I)", anders "het tegoed wordt overgedragen". Niets geboekt.
  10. **Termijn** op het dashboard: maand de 20e, kwartaal de 25e van de volgende maand (de termijnfunctie krijgt nu
      het soort tijdvak). "Per jaar" is voor BE niet geblokkeerd.
  11. **Jaarlijkse klantenlisting** (BE5): `/btw/klantenlisting?jaar=…` (`sqlledger/vatlisting.py`), knop
      "Klantenlisting" op de aangifte (alleen BE); **geen menu-item** (dat vraagt een nieuwe vlag in de app-database).
      Drempel **meer dan € 250** (bronnen niet eensluidend: "meer dan" of "minstens"; wettekst niet nagelezen).
      Belgisch nummer: "BE…" of 10 cijfers beginnend met 0/1, met controle modulo 97; ongeldige nummers als melding.
      Alle verkoopboekingen van de klant (creditnota's eraf), hele euro's omlaag, nihil-melding, PDF en Excel; geen
      XML (zoals BE4). Status: nog verplicht (afschaffing vanaf 2026 is alleen een voorstel).
  12. **Niet gebouwd**: Intervat-XML (BE4), de intracommunautaire opgave (deelstap 7, dan ook controle btw-nummer bij
      44/46), bijzondere regelingen (winstmarge, vrijstelling kleine ondernemingen), kasstelsel (BL8), 61/62.
  13. **Toetsen**: `tests/test_vatreturn_be.py` (15), `tests/test_vatlisting.py`, `tests/web/test_web_vat_be.py` (5);
      NL en VK ongewijzigd. Browsercontrole op een verzonnen Belgische administratie (be-mar, poort 8771): rubrieken,
      overzicht (met de hand nagerekend), formulier, 375 px, klantenlisting, btw-boeking sluitend.
  Helpteksten Btw-rubrieken en Btw-aangifte: één regel over België (EN/DE/FR). Alle nieuwe teksten EN/DE/FR.

- **Btw-aangifte per land, deelstap 1 (kern) en 2 (VK) (BTW_LANDEN_ANALYSE.md BL1–BL14, UK1–UK6, alles A,
  2026-10-13): keuzes binnen A — 1–13 AKKOORD (gebruiker 2026-10-13), in `main`.**
  1. **Opzet** (BL1): `sqlledger/vatforms.py` met per land de vakken (`Box`), de **koppelingen** (`Target`: waar btw en
     grondslag van een btw-rekening of klant terechtkomen), afronding, totalen, te betalen vak, voorstel en termijn;
     `vatreturn.py` rekent per koppeling. NL is het eerste formulier; oude namen (`BOXES`, `round_boxes`, …) blijven.
     **Administratie A: alle 256 maanden en kwartalen 2011–2026 byte-gelijk** aan de oude kern (snapshot, meldingen, tellingen).
  2. **Land** (BL2): instelling `vat_country` op *Btw > Btw-rubrieken* ("Aangifte volgens", knop "Land kiezen");
     zonder instelling het land van het schema (nieuwe administratie), anders Nederland — bestaande NL-administraties
     merken niets. Nog niets opgeslagen: voorstel uit schema of standaardvaluta (GBP → VK). Koppelingen, rubriek bij de
     klant en vastgelegde aangiften per land (migratie 20); wisselen verliest niets. Afdracht- en afrondingsrekening
     zijn gedeeld (niet per land).
  3. **Nieuwe VK-administratie** (`uk-ltd`, `leeg-uk`): de aangifte staat meteen aan (de boxes zitten in het schema).
  4. **Box 6/7** (UK1): alle verkoop- en inkoopboekingen, ook zonder btw (inkoop zonder btw → 7, verkoop zonder btw →
     6 of de keuze bij de klant); memorialen alleen als ze een btw-regel hebben, dus lonen tellen niet mee.
  5. **Afronding** (BL6): **elke aangifte in hele bedragen** (besluit gebruiker 2026-10-13, ook al kent HMRC pence in
     box 1–5): box 1 en 2 omlaag, box 4 omhoog, box 6–9 afgekapt (ook negatief richting nul), 3 en 5 uit de afgeronde
     bedragen; de centen gaan bij de btw-boeking naar de rekening afrondingsverschillen; CSV met ".00".
  6. **Koppelingen**: btw-rekening → box 1 (grondslag 6), **1R** verlegd binnenland/uitgestelde invoer-btw (btw in 1,
     waarde alleen in 7), 2 verwerving EU in Noord-Ierland (grondslag 9), 4 voorbelasting (grondslag 7); klant → 6
     (standaard) of 8 (goederen Noord-Ierland → EU, ook in 6). Voorstel uit tarief, naam en koppeling (negatief tarief
     → 1 of 1R; kosten-/opbrengstrekening of "payments/HMRC/liability/control" → niet in de aangifte).
  7. **Schema's** (UK5): `uk-ltd` en `leeg-uk` + 6 rekeningen: paren 20 % voor diensten uit het buitenland
     (2204/2214), verlegd binnenland (2205/2215) en uitgestelde invoer-btw (2206/2216); `chartfiles` staat het negatieve
     tarief per land toe (`COUNTRY_REVERSE`). Bestaande administraties worden niet aangepast.
  8. **Tijdvak** (BL7/UK3): "Kwartaal eindigt in" maart/juni/sep/dec, april/juli/okt/jan of mei/aug/nov/feb (alleen VK);
     zo'n kwartaal heet "februari – april 2026"; dashboardtermijn 1 maand + 7 dagen.
  9. **Formulier** (BL10): HMRC-opmaak (zwart-wit, één kolom, £), officiële Engelse boxteksten met de vertaling
     eronder (niet in het Engels), box 5 als bedrag met vinkje te betalen/terug te vragen; PDF; "Proefafdruk" tot de
     btw-boeking.
  10. **CSV voor bridging software** (UK2): `VRN`, `Period start/end`, dan `Box,Description,Value` (punt als
      decimaalteken, box 5 als absoluut bedrag zoals de HMRC-API) en `5 direction,pay|reclaim`. Er is geen standaard
      formaat; bridging tools laten je de kolommen aanwijzen. Op scherm en formulier: overtypen mag niet.
  11. **Btw-boeking** (BL9): box 5 op de afdrachtrekening (schema: 2202), vastgelegd met landcode, verschilmelding met
      "Box 5 toen … nu …". Een btw-rekening die op "niet in de aangifte" staat mag de afdrachtrekening zijn (administratie C: 1520
      "Payments to/from HMC&E" heeft in SQL-Ledger een btw-koppeling) — geldt ook voor NL.
  12. **ICP** blijft Nederlands: alleen bij de NL-aangifte (menu, klantscherm "ICP-soort", link uit 3B).
  13. **Echte data administratie C** (UK6, `compare/vat_check_gb.py`, kopie `vatcmp_c`, afdracht 1520): 12 kwartalen 2013 Q2 –
      2016 Q1 met een eigen "VAT RETURN"-boeking; **0 tot op de penny gelijk** (verschillen £ 3,75 – £ 1.963 per
      kwartaal, per rekening in beide richtingen), maar **sluitend**: de som van de verschillen (£ 26,00) = wat er na de
      laatste eigen aangifte nog op de btw-rekeningen staat. Elke btw-mutatie telt dus precies één keer; de verschillen
      zijn boekingen die na de eigen aangifte van C zijn ingevoerd, gewijzigd of van datum veranderd (zoals administratie A). Box 1 van
      2016 Q1 is exact gelijk (£ 52.718,69). Audittrail pas vanaf oktober 2014, en elke betaling schrijft er ook in, dus
      per boeking niet aan te wijzen.
  Helpteksten Btw-rubrieken en Btw-aangifte: één regel over het VK erbij (EN/DE/FR). Niet gebouwd: MTD-koppeling
  (UK2, apart besluit). Teaser (buiten de repo): btw-scène NL + UK VAT Return + kaart "BE · FR · CH · DE coming soon".

- **Bouwstap 5 SEPA-betaalbestand (BEHEER_EXTRA_ANALYSE.md IM9 A1, IM10 A; SP1–SP13 alles A, 2026-10-13):
  keuzes binnen A — 1–13 AKKOORD (gebruiker 2026-10-13), in `main`.**
  1. **Plaats** (SP1): menu Kas en bank > "Betaalbestand maken" direct onder "Betalingen (meerdere leveranciers)",
     plus een link bovenaan Betalingen (alleen bij leveranciers). Rechten zoals Betalingen.
  2. **Selectie** (SP2): dezelfde open posten als Betalingen (goedgekeurd, niet geblokkeerd, drempel van de leverancier,
     niet gelockt door een ander), alleen `curr = 'EUR'`. Zonder vervaldatum telt de factuurdatum. Het
     leveranciersfilter zoekt in naam en nummer. Volgorde: vervaldatum, leverancier, factuurnummer.
  3. **Bedrag en korting** (SP3): voorstel = openstaand − contante korting als die op de uitvoerdatum nog geldt (zelfde
     berekening als Betalingen). Let op: Bankafschrift inlezen herkent zo'n betaling als *gedeeltelijke* betaling; de
     korting boekt u dan zelf bij het verwerken (of via Betaling). Korting herkennen bij het afletteren is niet gebouwd
     (zou een kleine uitbreiding van `bankimport` zijn; eventueel later).
  4. **Creditnota's** (SP4): niet in het bestand; melding per leverancier die op het scherm staat ("… heeft een open
     creditnota (nummer (bedrag)); die gaat niet mee"). Open posten in een andere valuta: alleen het aantal als melding.
  5. **Omschrijving** (SP5): geldig NL-kenmerk → `Strd` SCOR met uitgever CUR; RF-kenmerk → `Strd` SCOR zonder
     uitgever; anders `Ustrd` = factuurnummer, **en als de factuur een ander (ongestructureerd) betaalkenmerk heeft,
     dat erachter** (max. 140 tekens) — kleine uitbreiding op SP5, omdat leveranciers dat kenmerk vaak vragen.
  6. **Uitvoerdatum** (SP6): standaard volgende werkdag (alleen weekend overgeslagen, geen feestdagen); in het verleden
     geweigerd. Wie de datum wijzigt en meteen op "Betaalbestand maken" klikt krijgt de vraag de bedragen eerst te
     controleren (de korting kan veranderen).
  7. **IBAN en adres** (SP7): SEPA-landen volgens de EPC-lijst inclusief AL, MD, ME, MK, RS (sinds 2025). Bij een
     land buiten de EER (CH, GB, AD, MC, SM, VA, GG, JE, IM, AL, MD, ME, MK, RS) is een adres met plaats verplicht: de
     eerste adresregel(s) van de leverancier gaan als `AdrLine` mee met `Ctry` = land van de IBAN, en het eigen adres
     uit Instellingen als adres van de betaler. Een ongeldige BIC van de leverancier wordt weggelaten.
  8. **Eigen rekening** (SP8): zonder BIC staat er `NOTPROVIDED` (toegestaan binnen SEPA). Naam = bedrijfsnaam uit
     Instellingen (verplicht).
  9. **Versie** (SP9): keuze per keer, niet onthouden; in de link na het maken blijft hij wel staan.
  10. **XSD** (SP10): afhankelijkheid `xmlschema` (pyproject); de twee officiële XSD's staan ongewijzigd in
     `sqlledger/sepa_xsd/` met bronvermelding (`BRON.txt`). Bij de GitHub-publicatie nog in NOTICE.md opnemen.
  11. **Vastleggen** (SP11) — **afwijking van "zonder schemawijziging"**: naast audittrail en logboek twee eigen
     tabellen `x_sepa_export` (bestand, bericht-id, rekening, datum, totaal, de XML zelf) en `x_sepa_item` (factuur,
     bedrag, end-to-end-id), migratie 19. Reden: de audittrail kan in Beheer uit staan, en dan zou "In betaalbestand
     op …" (bescherming tegen dubbel betalen) verdwijnen; bovendien kan het bestand zo opnieuw gedownload worden.
     Een factuur die al in een bestand zat kan bewust opnieuw aangevinkt worden (bijv. als de bank het bestand weigerde).
  12. **Kenmerken** (SP12): bericht-id `PB-<jjjjmmdduummss>-<nr>`, EndToEndId = factuurnummer in de SEPA-tekenset
     zonder spaties (dubbel in één bestand → `-2`, `-3`). `bankimport` herkende het factuurnummer in de end-to-end-id
     al (stap b) en het kenmerk (stap a); daar is niets aan veranderd.
  13. **Toetsen** (SP13): zie Getest. Geen Perl-vergelijking (SL heeft geen SEPA).

- **Bouwstap 4 importen facturen/orders/betalingen (BEHEER_EXTRA_ANALYSE.md §3 IM1–IM8 en IM10, alles volgens advies):
  keuzes binnen A — 1–12 AKKOORD (gebruiker 2026-10-13), in `main`.**
  1. **Soorten en kolommen.** Vier soorten erbij in Beheer > Importeren: Verkoopfacturen, Verkooporders, Inkooporders,
     Betalingen. Kolomkop = SL-veldnaam (`invnumber`, `customernumber`, `partnumber`, `qty`, `sellprice`, `paid_1` …);
     de NL/EN/DE/FR-labels werken ook. Velden uit SL die we niet inlezen (afleveradres `shipto*`, `shippingpoint`,
     `shipvia`, `waybill`, `warehouse`, `serialnumber`, tweede betaling `paid_2`) staan als "niet ondersteund".
     Geen export van deze soorten (export betalingen = SEPA, bouwstap 5).
  2. **Kopgegevens** (klant, datum, valuta, notities …) mogen op de eerste rij van een document staan: per kolom telt
     de eerste niet-lege cel van het document (SL: de eerste rij).
  3. **Groeperen** (IM2): een rij met nummer maar zonder relatie hoort bij het laatste document met dat nummer; hetzelfde
     nummer met een andere relatie is een tweede document en krijgt de fout "Komt ook voor in rij …".
  4. **Bestaand nummer** (IM3): een factuurnummer moet uniek zijn in alle verkoopfacturen (ongeacht de klant: dubbele
     factuurnummers mogen niet); een ordernummer per soort (verkooporder / inkooporder; offertes tellen niet mee).
     Strenger dan "per soort en relatie" uit de analyse, omdat een factuurnummer wettelijk uniek moet zijn.
  5. **Standaarden zoals het scherm**, tenzij het bestand ze geeft: prijs (verkoop: verkoopprijs, inkoop: laatste
     inkoopprijs, omgerekend bij vreemde valuta), korting % van de relatie (VW7), omschrijving en eenheid van het
     artikel, aantal 1, termijn en vervaldatum, btw inclusief, debiteurenrekening, betaalrekening op de factuur
     (`bank_id`), valuta van de relatie, koers uit de koersentabel (ontbreekt die: fout per document), medewerker van
     de relatie, contante korting en betaalmethode. Orders: leverdatum = orderdatum + termijn (of de termijn uit een
     opgegeven leverdatum) en op elke regel, zoals SL's import en het orderscherm.
  6. **Niet via de import**: creditnota's, offertes, inkoopfacturen (IM1 noemt verkoopfacturen; een inkoopfactuur
     uploadt u als PDF). Een negatief aantal is een retourregel, zoals op het scherm.
  7. **Meteen betaald** (`paid_1`, `datepaid_1`, `AR_paid_1`, `source_1`, `memo_1`): één betaling per factuur; datum
     standaard de factuurdatum, rekening standaard de betaalrekening van de klant of de eerste betaalrekening. Een
     hoger bedrag dan de factuur wordt geboekt zoals op het factuurscherm.
  8. **Betalingen: één soort voor beide kanten.** De kant volgt uit de kolom klant- of leveranciersnummer, anders uit
     Credit (ontvangst van een klant) of Debet (betaling aan een leverancier), anders uit waar het nummer staat; staat
     het in verkoop én inkoop, dan is het een fout met de vraag het relatienummer in te vullen. Bedrag altijd positief.
  9. **Betalingen: strenger dan SL.** Alleen op een openstaande post en niet meer dan wat openstaat (geen
     vooruitbetaling/overbetaling via de import); geen contante korting. Meerdere rijen op dezelfde factuur mogen
     (deelbetalingen, in volgorde verwerkt). Betaalrekening: kolom `accno`, anders die van de relatie, anders de enige
     betaalrekening; anders een fout. Zonder `source` krijgt de betaling het voorgestelde volgende betaalkenmerk van
     die rekening, zoals op het betaalscherm.
  10. **Voorbeeld**: per document valuta, netto, btw en totaal in de valuta van het document (facturen/orders); bij
     betalingen bedrag en openstaand vóór deze betaling. Ook op het resultaatscherm. Bedragen in foutmeldingen in de
     notatie van de gebruiker.
  11. **Verplichte kolommen** worden vooraf gecontroleerd: facturen/orders relatienummer, datum en artikelnummer;
     betalingen betaaldatum, een bedragkolom en factuurnummer of betalingskenmerk.
  12. **Helptekst** Importeren en de uitleg "Hoe moet het bestand eruitzien?" bijgewerkt (verwijst voor bankbestanden
     naar Bankafschrift inlezen); EN/DE/FR mee. README rij, afwijking 242, Perl-vergelijking 9 scenario's.

- **Bouwstap 3 GIFI-indeling (BEHEER_EXTRA_ANALYSE.md GF1–GF6, alles volgens advies): keuzes binnen A — 1–10 AKKOORD
  (gebruiker 2026-10-13), in `main`.**
  1. **Omschrijving verplicht** (SL niet), zoals bij Talen. De code wordt opgeschoond zoals SL (spaties en `'` weg,
     `--` wordt `-`); geen maximale lengte.
  2. **Code wijzigen naar een code die al bestaat** wordt geweigerd: geen samenvoegen. Samenvoegen gaat door de
     rekeningen een andere code te geven en de oude daarna te verwijderen.
  3. **Onbekende code bij een rekening** (GF4): opslaan mag (Cookie heeft 5 kopregels met zo'n code), met een
     waarschuwing onder het veld en een link "Code toevoegen" die de code alvast invult. Niet blokkeren.
  4. **Signaal dode verwijzingen** staat alleen bovenaan Beheer > GIFI-indeling (geel blok met rekening, omschrijving,
     code en "Code toevoegen"), niet ook in de lijst Rekeningschema of op het dashboard.
  5. **"Aantal rekeningen"** telt rekeningen én kopregels; 0 toont "—". Een code met 0 heeft de knop Verwijderen; een
     gebruikte code toont "Gebruikt bij:" met links naar de rekeningen.
  6. **Leveranciersveld "GIFI onderaanneming"** (`vendor.gifi_accno`, in SL een los Canadees veld) blijft vrije tekst
     en gaat niet mee bij hernoemen; telt ook niet mee voor "in gebruik" (Cookie: 0 leveranciers met een waarde).
  7. **Import** (GF5): sleutel `accno`; een bestaande code krijgt hoogstens een nieuwe omschrijving (code wijzigen alleen
     op het scherm); een nieuwe code zonder omschrijving is een fout. Waarschuwing als de code in een xlsx als getal
     staat (voorloopnul kan weg zijn); uitleg op het importscherm en in de help. Export op volgorde van code.
  8. **Audittrail** zoals SL (tabel `gifi`, `saved`/`deleted`, referentie = code); logboek `am_gifi_saved` (met de oude
     code bij hernoemen) en `am_gifi_deleted`.
  9. **Rechten**: scherm, import en export alleen voor de beheerder (zoals Rekeningschema).
  10. **Helpteksten** (EN/DE/FR mee): nieuw "GIFI-indeling" (noemt RGS als officiële NL-indeling, verwijst naar
     RGS-koppeling); bijgewerkt: Rekening (veld GIFI), Importeren, Exporteren, en de zin bovenaan het importscherm.
     Toetsen `tests/test_gifi.py`, `tests/web/test_web_gifi.py`, export/import-rondgang in `tests/test_importer.py`.
  11. **Na akkoord meegenomen** (AKKOORD 2026-10-13; controle "import met GIFI"): bij het importeren van het rekeningschema geeft een
     kolom `gifi_accno` met een code die niet in de GIFI-indeling staat een waarschuwing in het voorbeeld (geen fout,
     zoals op het rekeningscherm). Getoetst: GIFI inlezen (CSV en xlsx), daarna rekeningschema met GIFI-kolom,
     signaal op de GIFI-indeling, export → import ongewijzigd (`test_web_import.py`).

- **Bouwstap 2 boekjaar (BEHEER_EXTRA_ANALYSE.md JA1–JA8, F5, F22, alles volgens advies): keuzes binnen A — 1–10 AKKOORD
  (gebruiker 2026-10-13), in `main`.**
  1. **Standaardperiode** bij een gebroken boekjaar: "Vrije datums" van het begin van het lopende boekjaar t/m vandaag
     (zoals nu 1 januari t/m vandaag), niet de keuze "Boekjaar" (dat zou t/m het einde van het boekjaar lopen, in de
     toekomst). Bij een kalenderboekjaar verandert er niets.
  2. **Keuzelijst Boekjaar** apart van "Jaar" (veld `fyear`, waarde = beginjaar, label "2025/2026"); van volgend boekjaar
     tot 15 jaar terug, zoals de jaarlijst.
  3. **Terug naar december**: een opgeslagen voorkeur of link met "Boekjaar" wordt dan "Jaar" met hetzelfde jaartal;
     de eindmaand zelf wordt nooit als rapportvoorkeur bewaard (komt altijd uit de instelling).
  4. **Instelling opslaan**: december schrijft niets als `yearend` leeg was (leeg = december); een oude SL-waarde
     (`1/31`) blijft staan zolang de maand niet gewijzigd wordt.
  5. **Waarschuwing JA5**: alleen een tekst onder de keuzelijst met de datum van de laatste jaarafsluiting (geen extra
     bevestigingsstap); audittrail: tabel `defaults`, kenmerk "oud -> nieuw", formulier `yearend`, actie `saved`.
  6. **Dashboard**: resultaat, kopregel en legenda tonen "2025/2026"; "deze maand" en de maandkolommen houden het
     kalenderjaar van die maand ("jul 2025"); terugval DB9 per boekjaar. Banklinks gebruiken "Boekjaar" (bij december
     zoals voorheen "Jaar").
  7. **Doorklik vanaf een boeking** (Boekingen > boekingspagina) naar rekeningmutaties: het boekjaar van de boekdatum
     (bij december: het kalenderjaar, zoals voorheen).
  8. **Grafiek bij winst en verlies**: één heel boekjaar heet "2025/2026" (voorheen alleen een kalenderjaar "2026").
  9. **Helpteksten** (EN/DE/FR mee): Instellingen (veld + overgangsjaar), alle rapporten met een periode (regel over
     Jaar/Boekjaar in "Let op"), Dashboard (dit boekjaar), Jaarafsluiting (waar de maand ingesteld wordt), Controleblad
     (begin van het boekjaar). De vaste zin bovenaan Instellingen noemt de jaarafsluitmaand niet meer (die staat nu op
     het scherm).
  10. **Meegenomen fix** (bestond al): de waarschuwing "niet opgeslagen wijzigingen" bij Administratie wisselen werkte
     niet op schermen met een veld `method` (Instellingen, rapporten): `switcher.js` las `form.method`, dat dan het
     veld is; nu `getAttribute("method")`.

- **Bouwstap 1 velden (BEHEER_EXTRA_ANALYSE.md VW1–VW7, alles volgens advies): keuzes binnen A — 1–10 AKKOORD (gebruiker 2026-10-13), in `main`.**
  1. **VW7 ook bij inkoop en offertes**: de korting van de leverancier op inkoopfactuur/inkooporder/offerteaanvraag, en
     de klantkorting op offertes, net als SL (`ir.pl`, `oe.pl`).
  2. **VW7 wanneer**: alleen als een artikel op een regel gekozen of vervangen wordt en die regel nog geen korting
     heeft (leeg of 0, SL `||=`). Wie eerst zelf een korting typt en dan het artikel kiest, houdt die korting. Kiest u
     de klant pas ná het invullen van de regels, dan krijgen die regels de korting niet (zoals SL).
  3. **VW7 bestaande facturen**: openen en verversen verandert niets, ook niet als de korting van de klant intussen
     anders is (getoetst).
  4. **VW1 welke rapporten**: elk rapport met het rondje Stelsel (balans, winst en verlies én de btw-overzichten),
     zoals SL; de laatst gebruikte keuze van de gebruiker gaat voor; een link met `run=1` zonder `method` volgt de
     instelling. Jaarafsluiting blijft factuurstelsel.
  5. **VW3 algemeen opgelost**: een instelling die niet op het scherm staat, wordt bij Opslaan niet meer gewist
     (voorheen werd elke ontbrekende sleutel leeg opgeslagen). Zo blijft `referenceurl` staan.
  6. **VW4**: knop "Wijzigen" en het veld Decimalen bij "Valuta toevoegen" weg; een nieuwe valuta krijgt 2.
  7. **VW5/VW6**: bij opslaan van een bestaande branche/betaalmethode blijven korting en kosten in de database staan
     (bibliotheek: `None` = laten staan); een nieuwe krijgt 0. Lijst en formulier tonen ze niet meer.
  8. **VW2 "Vast"**: de hint op Instellingen zegt nu ook "klant of leverancier"; helptekst Instellingen vult aan dat
     "Vast" bij Klanten/Leveranciers het nummer van een bestaande relatie vastzet.
  9. **Helpteksten** bijgewerkt (EN/DE/FR mee): Instellingen (kasbasis, Vast), Valuta (Decimalen + "anders dan SL"),
     Branches (geen korting; verwijst naar Korting % op de relatie), Betaalmethoden (Kosten weg), Klant/Leverancier en
     Factuur (Korting % op nieuwe regels), Winst en verlies en Btw-overzichten (beginstand Stelsel). Ordersscherm
     heeft nog geen helpblok (golf 4).
  10. **README**: afwijking 144 gecorrigeerd (kasafronding werkt), nieuwe afwijkingen 236–239.

- **v1.0.1 naam PyBalans (NB1–NB5) en kleine fixes: keuzes binnen A — 1–11 AKKOORD (gebruiker 2026-10-12)** (branch `v1.0.1`; NB1–NB5 alles A).
  1. **Waar "SQL-Ledger" PyBalans werd** (app zelf): `<title>`, kopregel (`base.html`, ook inlog/2FA/activeren/fouten),
     "Welkom bij PyBalans" (wizard), e-mail met activeringscode ("Je account voor PyBalans"), FastAPI-titel, startbanner
     "PyBalans first start" in het serverlog, `X-Mailer: PyBalans <versie>`, CLI-naam/-hulp, opdracht in Back-up maken
     (`pybalans backup`, cron/launchd-voorbeeld), Dockerfile/compose, TOTP-issuer.
  2. **Bewust laten staan** (gaat over de oude software): back-up uit SQL-Ledger, Medewerkerslogin in SQL-Ledger,
     SQL-Ledger-database koppelen/Bestaande SQL-Ledger-database, Overstap uit SQL-Ledger en de overstaplijst,
     "Niet inlezen in SQL-Ledger", oude SQL-Ledger-versie, "(SQL-Ledger 3.2.4)" als schemaversie op dashboard en bij
     Verbinding testen, meldingen van `slbackup`/`slupgrade`, `-- SQL-Ledger Backup`, codecommentaar en docstrings
     over Perl-gedrag, `compare/`, analysebestanden, kopregels in `sqlledger/charts/*.csv`, launchd-label
     `nl.sqlledger.backup`, databasenamen `sqlledger_app`, `data_dir` `~/.local/share/sqlledger`, omgevingsvariabelen
     `SQLLEDGER_*` (bestaande installaties blijven zo werken).
  3. **Back-upkop** van de app: `-- PyBalans back-up` (was `-- SQL-Ledger (Python) back-up`); de oude kop blijft
     herkend (`dbbackup.OLD_HEADERS`), `-- App-Dataset`/`-- App-Version` ongewijzigd.
  4. **PDF**: `Creator` = "PyBalans 1.0.1" voor rapporten, btw-formulier en documenten (ook eigen sjablonen, want het
     wordt in Python gezet, niet in het sjabloon); `Producer` blijft "WeasyPrint …" (de schrijvende bibliotheek). De
     voetregels krijgen géén app-naam: op facturen hoort de eigen bedrijfsinformatie, op rapporten "Afgedrukt op".
  5. **Over PyBalans**: eigen pagina `/over` (na inloggen), link klein onderaan het menu; met versie, licentie, één
     zin dat een deel is afgeleid van SQL-Ledger 3.2.12 (© DWS Systems Inc., GPL) en de twee merkregels.
  6. **Ondertitel** alleen op inlogscherm en wizard-welkom (2FA, activeren en foutpagina's tonen alleen de naam); EN "accounting
     in Python on PostgreSQL · reads and replaces SQL-Ledger companies", DE "…SQL-Ledger-Mandanten", FR "…dossiers
     SQL-Ledger" (administratie = company/Mandant/dossier zoals in de rest van de vertaling).
  7. **CLI**: `pybalans` en `sqlledger-web` (zelfde entrypoint); de hulptekst toont de naam waarmee hij is aangeroepen.
     Bestaande launchd-plists met `…/bin/sqlledger-web` blijven werken.
  8. **TOTP-issuer** standaard "PyBalans"; bestaande koppelingen in een authenticator-app blijven werken (zelfde
     geheim), alleen het label in de app blijft "SQL-Ledger" tot iemand 2FA opnieuw instelt. Wie `totp_issuer` in de
     config heeft gezet, houdt die waarde.
  9. **Grootte op Back-up controleren**: tot 999 kB in hele kB (naar boven, minimaal 1 kB), daarboven in MB met één
     decimaal (was altijd MB, waardoor een klein bestand "( MB)" gaf).
  10. **Bestandsknop**: Beheer > Importeren én Systeem > RGS-lijst gebruiken nu de vertaalde `filepick`-knop (RGS had
      hetzelfde probleem).
  11. **Installatie**: `pyproject.toml` naam `pybalans`, versie 1.0.1 (ook `sqlledger.__version__`); package-data
      ongewijzigd nodig (nieuwe bestanden vallen onder de bestaande patronen); nieuwe test
      `test_pyproject_name_scripts_and_package_data` bewaakt dat elk bestand in de pakketten meekomt.
- **v1.0.1 opmaak (PB18, PB9, PB12, PB15, PB28): keuzes binnen A** — keuzes 1–14 **AKKOORD (gebruiker 2026-10-12)** (branch `v1.0.1`; voor/na-afdrukken
  van 42 schermen op 1280 en 375 px in `~/sqlledger-test/v101-opmaak/index.html`).
  1. **Eén formulierraster** (`static/style.css`, blok "v1.0.1 opmaak"): `.grid`, `.glhead` en het nieuwe `.fgrid`
     zijn een raster van 4 kolommen; elk veld is een `<label>` met drie regels (CSS **subgrid**): bijschrift – veld –
     hulptekst. Daardoor staan bijschriften van één rij op één lijn, de velden direct daaronder op één lijn, en duwt
     een hulptekst onder een veld (Kenmerk, Code) de buren niet weg. Geen herschrijving van alle labels: de bestaande
     vorm `<label>Tekst <input> <span class="muted small">…</span></label>` past al; `tests/web/test_web_opmaak.py`
     bewaakt dat op alle GET-pagina's (en dat er geen inline `style` met een breedte bijkomt).
  2. **Kolommen volgen het werkvlak**, niet het venster (container queries op het werkvlak en op smalle kolommen
     zoals Inkoopfactuur uploaden en Huisstijl): 4 kolommen, 2 onder 760 px werkvlak, 1 onder 400 px.
  3. **Breedte per veld vanzelf**: keuzelijsten en velden voor naam, omschrijving, adres, plaats, e-mail, website,
     IBAN, login en zoeklijsten (`list=`) 2 kolommen; tekstvakken (`textarea`) en `.wide` de volle breedte; bedragen,
     aantallen, datums en codes (ook Databasenaam) 1 kolom. Afwijken kan met `.w1`/`.w2`/`.w3`/`.wide`.
  4. **Alle velden even hoog** (2,25 rem): tekst, getal, datum, keuzelijst, zoeklijst en knoppen (ook `a.button`).
     Kleine knoppen (dashboard, `.small` in tabelregels) houden hun lagere hoogte. Getallen staan rechts en vullen de
     cel: het gewicht 0,00000092410714285715 van artikel Z is nu helemaal zichtbaar.
  5. **Vinkjes en keuzerondjes in het raster**: 2 kolommen breed, het vakje op de veldlijn (een lege bijschriftregel
     erboven, ook op de telefoon), bijschrift ernaast; in filterrijen schuiven ze mee naar de veldlijn.
  6. **Geen afbreekstreepjes meer** (`hyphens: manual` op de hele pagina): alleen het smalle menu breekt nog af
     volgens de taal (TL7). Een woord dat echt niet past breekt zonder streepje; bijschriften en kolomkoppen nooit
     midden in een woord.
  7. **Alleen-lezen waarde als veldblok** (`.ro`: gestippelde rand, lichte achtergrond, zelfde hoogte):
     Artikel (Op voorraad met de links Voorraadcorrectie/Assembleren eronder, Gemiddelde kostprijs, de sommen van een
     samengesteld artikel) en Inkoopfactuur uploaden (Netto). Het adres/kredietblok van de relatie op facturen blijft
     tekst naast het relatieveld.
  8. **Linkrij bovenaan**: de titel staat op een eigen regel, de links ("Artikelen zoeken · Artikel toevoegen · …")
     in een rij eronder met "·" ertussen, die op een smal scherm doorloopt.
  9. **PB9 knoppen**: `.button.secondary` bestond niet (alleen `button.secondary`), daarom zagen PDF/CSV/Excel en
     andere link-knoppen eruit als hoofdknop; nu secundair. "Terug naar …" is overal een secundaire knop in de
     knoppenrij van het formulier (Assembleren, Voorraadcorrectie, wizard Nieuwe administratie/Database koppelen/
     Back-up inlezen, Valuta, Relaties verwijderen, Aangifteformulier, foutpagina); zonder formulier een knoppenrij
     onderaan (Prijshistorie, Bankregel). Herhaalschema: de link "Terug naar de boeking" en de knop "Annuleren"
     (zelfde adres) zijn samen één knop "Terug naar de boeking". "Sluiten" op memoriaal, boekingen, facturen en
     orders en "PDF openen" na opslaan zijn secundaire knoppen in plaats van een link/hoofdknop.
  10. **PB12 rapportfilters**: losse vinkjes bovenaan komen in een eigen kader (`Field.group`): "Vorig jaar ernaast"
      in **Vergelijken** (W&V, Balans), andere (Proefbalans "Ook rekeningen zonder mutaties", Rekeningmutaties "Per
      rekening") in **Opties**. Kaders in een filterrij zijn even hoog; losse velden ernaast staan op de veldlijn.
  11. **PB15 herhaalschema**: "Elke [1] [maand(en)] – [24] keer in totaal (inclusief de eerste) *" op één regel
      (op de telefoon over twee); Eenheid en Aantal keer houden een `aria-label`.
  12. **Kleinere schermaanpassingen**: Instellingen (Adres en Documenttaal in het raster, Opties als raster),
      Back-up controleren (formulier breed, vinkje en "Jij bent in deze administratie" in het raster), Bankgegevens
      van een rekening (raster, IBAN 2 kolommen), Huisstijl (instellingenkolom niet meer 340 px smal; "Overnemen van …"
      past op de telefoon), Notities onder formulieren (bijschrift boven, even breed als de kaders), tabelregels met
      invoervelden verticaal gecentreerd, Administratie kiezen (tekst links). Vijf tabellen die op de telefoon uit
      beeld liepen staan in een eigen scrollkader (Gebruikers, Administraties, Back-up maken, Logboek,
      Referentiedocumenten). Kolomkoppen in een scrollende tabel op één regel. Klasse `visually-hidden` toegevoegd.
  13. **PB28 brede PDF-tabellen** (`reportpdf.choose_layout`/`split_columns`, `templates/reports/pdf.html`): past een
      tabel liggend op 6,5 pt niet, dan kolomblokken. De **vaste kolommen** zijn de niet-bedragkolommen links tot de
      eerste bedragkolom (rekening, omschrijving); de bedragkolommen gaan in volgorde over zo weinig mogelijk blokken
      van gelijke grootte, bij een even aantal steeds een even aantal per blok (dit jaar/vorig jaar blijven samen); het
      totaal (laatste kolom) staat dus in het laatste blok. De letter is de grootste die voor het breedste blok past
      (hoogstens 8 pt). Elk blok begint op een nieuwe pagina met "Kolommen, deel n van m"; kop met titel, periode en
      administratie op elke pagina (zoals al). Kolomkoppen breken na "t/m" zoals op het scherm. Geldt voor alle
      rapporten. W&V 2024 per maand + vorig jaar van administratie A (26 bedragkolommen): 3 blokken (10/10/6
      kolommen) op 8 pt, 8 pagina's liggend (voorheen 2 pagina's waarop de kolommen vanaf september rechts van het papier vielen).
  14. **Bewijs**: voor/na-afdrukken (headless Chrome via het DevTools-protocol, `~/sqlledger-test/v101-opmaak/tools`),
      een rondgang langs alle 117 menupagina's van beide administraties op 1280 en 375 px (geen horizontaal scrollen
      buiten tabel-/grafiekkaders, geen labels buiten de vaste vorm) en de pytest-controle uit punt 1.

- **v1.0.1 inlezen en overstap: keuzes binnen A** (V101_ANALYSE.md PB1–PB6, PB8, PB10, PB14, PB16, plus
  PB23–PB25; branch `v1.0.1`). **Keuzes 1–16 AKKOORD (2026-10-12); 17 gebouwd (ter keuring).**
  1. **Klaarzetten** (PB1): het geüploade bestand staat tot het inlezen in `<data_dir>/restore/<token>.bin` (+ `.json`
     met wat erin staat, `.zip` met sjablonen), alleen voor dezelfde gebruiker; weg na gelukt inlezen, Annuleren of na
     een dag. Na een **mislukt** inlezen blijft het staan, zodat "Opnieuw proberen" (bv. met een andere naam) kan.
  2. **Controlescherm** (PB1/PB2): `slbackup.preview` leest het bestand zonder iets uit te voeren (ook .gz):
     bedrijfsnaam uit `defaults` 'company' (SL-INSERT, ook helemaal achteraan; pg_dump/back-up van deze app: COPY-blok
     of de kopregel `-- Company:`), **laatste boeking = hoogste datum in `acc_trans`** (dus ook betalingen; datums
     volgens de DateStyle van de SL-back-up, dd-mm-jjjj bij EUROPEAN), medewerkers. Administratie A: 3 s, administratie B
     0,05 s. Naam = bedrijfsnaam (anders dataset, anders bestandsnaam), databasenaam = `suggest_dbname(dataset)`.
  3. **Eén inlezen tegelijk per installatie** (PB5; niet alleen per databasenaam): een back-up van 100 MB vraagt veel
     geheugen; een tweede start krijgt "Er wordt al een back-up ingelezen" (409). Taken staan in het geheugen van het
     serverproces (24 uur zichtbaar op Back-up inlezen, "Bekijken"); na een herstart is de stand weg.
  4. **Voortgang** (PB4): uploaden in % (XMLHttpRequest, de server-doorverwijzing naar het controlescherm wordt
     gevolgd; zonder JS een gewone post). Inlezen: fasen Lezen en controleren (%) → Terugzetten (% verwerkte tekens)
     → Bijwerken (stap x van n; n = scripts voor deze versie, 2.8.37 → 3.2.4) → Afronden; de pagina vraagt elke
     seconde `…/taak/<id>/stand` (JSON). Balk = HTML `<progress>` (impliciet `role=progressbar` met waarde) in plaats
     van een div met `aria-valuenow`: de CSP verbiedt inline-stijl en zo werkt de balk ook zonder JS (`<noscript>`
     ververst de pagina elke 3 s); tekst in `aria-live="polite"`, fase met `aria-current="step"`.
  5. **Alles of niets** blijft: mislukt iets na het terugzetten (medewerker koppelen, administratie in de
     app-database), dan gaat de nieuwe database weer weg. De zip met sjablonen wordt al bij het uploaden gecontroleerd.
  6. **`split()` 5× sneller** (gewone tekens in één keer, geen kopie van de rest van de tekst per `$`): administratie A
     16 s → 3,4 s, uitkomst identiek (vergeleken op beide back-ups van 16-4).
  7. **"Jij bent in deze administratie"** (PB14): vooringevuld op naam (zonder accenten/hoofdletters; bij meer
     dezelfde naam: met login, zonder einddatum, nieuwste — administratie B: "Jan Jansen (jansen)", niet de oude zonder
     login), anders op gelijke login, anders "geen". Een medewerker **zonder SL-login** krijgt de login van de
     app-gebruiker (als die nog vrij is); "nieuwe medewerker aanmaken" maakt er een met naam en login van de
     app-gebruiker (of gebruikt die als hij al bestaat); "geen" = de app-login, zoals tot nu toe. Alleen zichtbaar als
     "mij toevoegen" aan staat.
  8. **Systeem > Gebruikers > bewerken**: kolom "Medewerker" = keuzelijst van de medewerkers **met login** ("naam
     (login)", "– uit dienst" bij een einddatum), de huidige waarde als "<login> (geen medewerker)" als die niet
     bestaat, en "nieuwe medewerker aanmaken" (maakt de medewerker bij Opslaan; logboek `employee_created`). Niet
     bereikbare administratie: het oude tekstveld. Nieuwe administratie (Systeem en wizard) maakte al een medewerker
     voor de beheerder (`newcompany`); nu met test.
  9. **Mijn uren** zonder koppeling: systeembeheerder krijgt "Koppel je login aan een medewerker" (naar de eigen
     gebruiker, anker bij deze administratie), anderen "Vraag dit aan de beheerder."
  10. **Back-up maken** (PB6): de download loopt via `fetch` (blob); tijdens het maken staan alle knoppen uit met
      "Bezig met maken…" (aria-live), daarna "Back-up gedownload: <bestand>" of de foutmelding. Zonder JS: gewone post.
  11. **Controleblad** (PB8): oordeel in `x_setting` `switch_check` (datum, naam van de app-gebruiker, notitie).
      Akkoord vinkt "Cijfers controleren" af (en wist een eerdere notitie); "Er klopt iets niet" vraagt een notitie
      (verplicht), zet het punt weer open als het al was afgevinkt en toont de notitie bij het punt. Logboek
      `switch_check_ok`/`switch_check_issue`. Na akkoord terug naar Ingebruikname; na een notitie blijft u op het blad.
      Uitleg en knoppen staan niet in afdruk of PDF.
  12. **Terug naar de overstaplijst** (PB10): punten en deelstappen linken met `?overstap=<punt>&terug=<lijst>`; de
      middleware onthoudt dat 2 uur in cookie `sl_overstap`, zodat het balkje ook na Opslaan staat; het verschijnt
      alleen op de pagina's van dat punt (`switchbar.PAGES`) en verdwijnt bij terugkomen op de lijst. Terugadres alleen
      `/` of `/beheer/ingebruikname`, punten alleen uit de lijst (geen open redirect). Btw: links Btw-rubrieken en
      Aangiftetijdvak; bank: IBAN bij de bankrekening (rekeningschema) en Bankafschrift inlezen. "Back-up ingelezen"
      heeft geen balkje (linkt naar de lijst zelf).
  13. **Terugkerende boekingen nakijken** (PB16): punt alleen als de administratie herhaalschema's heeft (module
      Terugkerend); open zolang een schema een volgende datum heeft vóór de **laatste boeking bij het inlezen**
      (zonder: vandaag). Per schema "Volgende datum aanpassen" (schemascherm) en **Stoppen** = volgende datum leeg,
      zoals een afgelopen schema (schema en boeking blijven; opnieuw starten = datum kiezen op het schemascherm;
      audittrail "stopped"). **Verwerken**: een schema dat meer dan één termijn achterloopt staat niet aangevinkt en
      wordt alleen met het vinkje "toch verwerken" (per schema) geboekt, anders overgeslagen met reden; het boekt dan
      zoals altijd één termijn (Perl-gelijkheid van de boeking ongewijzigd). **Achterstand in de tijd gerekend**
      (`recurring.terms_due`): de volgende datum plus elke termijn daarna t/m vandaag, ook voorbij einddatum/aantal
      keer — anders zou een herhaalschema van administratie A (volgende = einddatum 1-11-2015, dus nog maar één boeking)
      gewoon aangevinkt staan; nu "loopt 126 termijnen achter".
  14. **PB23 Login en Naam**: inloggen was al hoofdletterongevoelig (login klein opgeslagen en klein gezocht, uniek;
      CHECK in de app-database); nu met test. De startcode-hint noemt `log/web.log` als voorbeeld (voor een installatie
      die het serverlog naar een bestand schrijft).
  15. **PB24 uitleg per schema**: eigen regel per standaardschema (NL eenmanszaak/vof, bv, basis; BE MAR; UK ltd;
      FR PCG; CH KMU), elk leeg schema de bestaande tekst, onbekend schema een algemene tekst; wisselt mee
      (`static/companycreate.js`).
  16. **PB25 Standaardvaluta** als keuzelijst: eerste keuze "<code> (volgens het land)" (wisselt mee), daarna 25
      gangbare ISO-codes (EUR, CHF, GBP, USD, Scandinavische, Midden-Europese, enkele wereldvaluta); een eerder
      ingevulde andere waarde blijft als keuze zichtbaar; de servercontrole blijft.
  17. **Harde stop tijdens inlezen** (gebouwd, AKKOORD 2026-10-12): per lopend inlezen een markering in
      `<data_dir>/restore-runs/<taak>.json` (databasenaam, naam, gebruiker, start; een bestand overleeft een herstart en
      vraagt geen wijziging van de app-database). `created` wordt pas gezet bij de eerste voortgang van "terugzetten",
      dus nadat `slbackup.restore` de database zelf heeft aangemaakt (die weigert een bestaande). Na afloop (gelukt of
      mislukt) gaat de markering weg. Bij de **start van de server** en bij het **openen van Back-up inlezen** geldt een
      markering zonder lopende taak als afgebroken: de database wordt alleen verwijderd als `created` waar is, de naam
      geldig is en er **geen administratie** met die databasenaam is; anders blijft alles staan. Daarna logboek
      `restore_aborted` (met `dropped`) en een dag lang een melding op Back-up inlezen: "Een inlezen van <naam> is
      afgebroken (server gestopt); de halve database is opgeruimd. Probeer het opnieuw." (of "… voordat er iets was
      aangemaakt"; geen melding als de administratie toch geregistreerd was).
- **v1.0.1 schermen: keuzes binnen A (ter keuring)** — keuzes 1–10 **AKKOORD (gebruiker 2026-10-12)**. Branch `v1.0.1-ui` (PB11, PB13, PB17, PB19, PB20, PB22, PB26,
- **v1.1 grafiek Winst en verlies: keuzes binnen A** — keuzes 1–12 **AKKOORD (gebruiker 2026-10-12)**; branch `v1.1-grafiek` (GR1–GR5 = A, 2026-10-12).
  1. **Standaardweergave Tabel**: wie niets kiest ziet het rapport zoals nu (ook de PDF); de keuze Tabel / Grafiek /
     Beide staat zichtbaar boven het resultaat en wordt onthouden.
  2. **Onthouden per gebruiker én administratie**, in de bestaande `report_pref` (eigen regel
     `winst-en-verlies:weergave`, los van de zoekinstellingen); geen migratie (botst niet met andere v1.1-branches).
     Ook een link met `run=1` gebruikt de eigen weergave (de bedragen blijven voor iedereen gelijk).
  3. **"Geheel"**: één groep (opbrengsten, kosten en een punt voor het resultaat), geen melding; met vorig jaar staan
     de twee resultaatpunten iets uit elkaar.
  4. **Klik op een rekening of groep** via een klein grafiekteken achter de omschrijving (alleen bij Beide); de
     omschrijving zelf blijft doorklikken naar de rekeningmutaties. Ook bij de rubrieken Opbrengsten en Kosten, niet bij
     totalen of het resultaat. Daarnaast een keuzelijst "Toon in grafiek" bij de grafiek (ook bij alleen Grafiek).
  5. **Gekozen rekening**: staven van alleen die rekening (en vorig jaar lichter), geen resultaatlijn; "« terug naar
     opbrengsten en kosten" en de keuzelijst gaan terug. Het zoekformulier (Toon) neemt de keuze niet mee: na Toon weer
     opbrengsten en kosten.
  6. **Vorig jaar**: lichtere staaf rechts naast elke staaf (volgorde zoals de tabel: dit jaar, dan vorig jaar) én een
     gestreepte lijn met open punten voor het resultaat vorig jaar.
  7. **Kleuren**: opbrengsten blauw, kosten oranje (onderscheidbaar bij kleurenblindheid), resultaat donkergrijs;
     donker thema via CSS-variabelen. Niet alleen kleur: legenda in tekst, gestreepte lijn/open punten voor vorig jaar,
     tooltip met periode en bedrag, `<title>`/`<desc>` en een uitklapbare tabel "Cijfers bij de grafiek".
  8. **Volgorde altijd chronologisch** (januari → december), ook bij "Omgekeerde weergave" (die geldt voor de tabel).
  9. **Asbedragen** kort (12K, 1,5M) in de getalnotatie van de gebruiker; tooltips en de cijfertabel met het volledige
     bedrag (negatief met minteken). Negatieve bedragen (creditkosten, verlies) onder de nullijn.
  10. **Breedte**: vaste maten in pixels (niet meeschalen); bij weinig ruimte (375 px) scrolt de grafiek horizontaal
      binnen zijn kader, de pagina niet. In PDF en afdrukken wordt hij op paginabreedte verkleind.
  11. **PDF**: bij Beide de grafiek boven de tabel, bij Grafiek alleen de grafiek (met legenda), bij Tabel zoals
      vroeger. CSV en Excel blijven alleen de tabel.
  12. **Kleine eigen JS** (`static/plchart.js`): tooltip direct (ook bij aanraken) en keuzelijst verstuurt bij kiezen;
      alles werkt ook zonder (browser-tooltip via `<title>`, knop Toon). Het dashboard gebruikt dezelfde
      as-hulpjes (`nice_step`, `short_amount`).
- **v1.0.1 schermen: keuzes binnen A (ter keuring)** — branch `v1.0.1-ui` (PB11, PB13, PB17, PB19, PB20, PB22, PB26,
  PB27; gekozen A, 2026-10-12). Niet in deze branch: de algemene formulieropmaak PB18/PB9/PB12/PB15.
  1. **AKKOORD (2026-10-12).** **PB11 Logo**: tekst "Sleep het logo hierheen of kies een bestand" (neutraal; de pagina gebruikt "u", de wens zei
     "je"). Opslaan bij kiezen/slepen via `fetch` naar het bestaande adres met `Accept: application/json` (antwoord JSON:
     melding + nieuw logo als data-URL); zonder JavaScript blijft de knop "Logo opslaan". Te groot wordt al in de
     browser gemeld (zelfde grens als de server, 1 MB). Vangnet: `confirm` bij de gewone Opslaan als er een gekozen
     maar (door een fout) niet opgeslagen logo is. "Logo verwijderen" blijft een gewone knop (pagina laadt opnieuw).
     Sjablonen houdt zijn eigen logoveld (zelfde bestand, gecontroleerd).
  2. **AKKOORD (2026-10-12).** **PB13 RGS**: aanwijzing in 4 stappen met link naar https://www.referentiegrootboekschema.nl/kbase; "bij uitgifte"
     staat als constante `RGS_CURRENT = "3.8"` in `routes_rgs.py`. Alfa/bèta wordt herkend aan de bestandsnaam
     (alfa/alpha/bèta/beta/consultatie/concept/draft), het blad `Totaal-RGS…` (moet op `-def` eindigen) en de eerste zin
     van het blad Recap (zonder "definitie…" en met een van die woorden); andere bladen (bv. `Zorg-RGS3.8-beta` in het
     officiële bestand) tellen niet. **Waarschuwen, niet weigeren**: de lijst wordt ingelezen en gebruikt; de
     waarschuwing blijft op Systeem > RGS-lijst staan zolang die lijst de nieuwste is (opgeslagen in de JSON) en staat
     in het logboek. Een definitieve lijst van dezelfde versie vervangt hem; een bèta van een hogere versie is te verwijderen
     (keuze 10).
  3. **AKKOORD (2026-10-12).** **PB17 Zoeklijst**: één script voor alle `<input list>`; de `<datalist>` blijft in de HTML (zonder JavaScript werkt
     die gewoon). "Begint met" = het label, het nummer of de naam begint met de invoer; daarna "bevat". Weergave
     "nummer – naam" als de optie `data-n` heeft (rekeningen, relaties, artikelen); bankkandidaten tonen de naam met
     `#id` grijs erachter. Serverlijsten (relaties, artikelen) worden niet opnieuw gefilterd (de server zoekt ook op
     streepjescode), alleen gesorteerd; de server geeft nu 50 opties + het aantal meer (artikelen: tot 500 opgehaald).
     Klikken in een **leeg** veld met een vaste lijst (rekeningen) toont alles; met tekst opent de lijst bij typen of
     pijl omlaag. Tab en Enter nemen de gemarkeerde regel over; zonder markering sluit Enter de lijst en doet verder wat het altijd deed. Breedte minstens
     300 px (binnen het scherm), boven het veld als er onder te weinig ruimte is. Aanraken: de "change" die de browser
     bij het tikken in de lijst geeft wordt vastgehouden tot duidelijk is wat er gekozen is (anders verversten
     relatie-/artikelvelden al met de halve tekst). Gevonden bij de controle en meteen opgelost: labels met spaties in
     het nummer (administratie A: 69 leveranciers en 184 artikelen) gaven
     "Geen … gevonden"; de server herkent nu het exacte label (ook met dubbele spaties). Twee relaties met dezelfde naam
     en zonder nummer blijven dubbelzinnig (servermelding blijft als vangnet).
  4. **AKKOORD (2026-10-12).** **PB19 Datumvelden**: melding via `setCustomValidity` (verplicht veld: zonder "of maak het veld leeg"); ×-knopje
     alleen bij niet-verplichte, niet-uitgeschakelde velden, zichtbaar als er iets (ook een halve datum) staat; het is
     met Tab bereikbaar. Leeg = geen grens nagelopen voor alle filterschermen (test `test_web_dates.py`); bewuste
     uitzonderingen zoals in SQL-Ledger: W&V/balans "tot en met"/"per datum" leeg = vandaag, Afletteren met beide leeg =
     standaardperiode, Batch en Betalingsregister alleen bij het eerste openen (uit het menu) een standaardperiode.
     Safari kent dezelfde `badInput`; niet in Safari zelf getest.
  5. **AKKOORD (2026-10-12).** **PB20 Menu productie**: ook "Samengesteld artikel toevoegen" verdwijnt zonder samengestelde artikelen (anders een
     losse ingang naar productie); de schakelaar maakt alle vier zichtbaar. Met samengestelde artikelen staat de
     schakelaar aangevinkt en grijs ("staat aan, want …"). Geen kolom in de app-database (geen migratie, botst niet met
     de andere v1.0.1-branch): het menu vraagt de administratie en onthoudt het antwoord 60 s per administratie;
     opslaan van een artikel of de instellingen wist dat meteen. Directe adressen blijven werken.
  6. **AKKOORD (2026-10-12).** **PB22 Kolomvolgorde**: in de web-laag (`statement_columns`), de bibliotheek blijft gelijk aan rp.pl. Met "Vorig
     jaar ernaast" per periode eerst dit jaar, dan vorig jaar (Q1 2026, Q1 2025, …, geheel 2026, geheel 2025).
     "Omgekeerde weergave" geeft nu de volgorde van SQL-Ledger (geheel vooraan, december → januari, vorig jaar eerst).
     Ook meegenomen: **Balans** per maand/kwartaal (van oud naar nieuw, peildatum als laatste); andere rapporten hebben
     geen periodekolommen. Scherm, CSV, Excel en PDF gebruiken dezelfde tabel.
  7. **AKKOORD (2026-10-12).** **PB26 Tegenrekening bij bankregels**: "Eerder gebruikt" = de 10 rekeningen die de laatste 2 jaar het vaakst (bij
     gelijk aantal het laatst) als tegenrekening in **memoriaalboekingen** op deze bankrekening staan (`acc_trans` via de
     index op rekening; ook boekingen uit SQL-Ledger en uit bankregels; btw-rekeningen en de bank zelf niet), dan
     "Alle rekeningen" (de eerder gebruikte staan daar ook nog). Zonder historie: opbrengsten/kosten (I/E) vóór de
     balansrekeningen. Groepskoppen alleen zolang er niets getypt is; bij typen over alles, elke rekening één keer.
     Alleen op Bankregels verwerken en één bankregel (niet bij Vaste regels, die geldt voor alle bankrekeningen).
  8. **AKKOORD (2026-10-12).** **PB27 "Voortaan zo boeken?"**: na het boeken van een bankregel als memoriaal (in de lijst of op de regelpagina)
     staat bovenaan de lijst een vooringevulde vaste regel: rekeningnummer (standaard als de regel er een heeft),
     rekeningnummer én trefwoord (bv. coupon en dividend van dezelfde bank) of trefwoord; het trefwoord zijn de eerste
     twee aaneengesloten woorden zonder cijfers uit de omschrijving (anders de naam), bv. "Coupon 4% Staatslening" →
     "coupon"; zelfde tegenrekening en btw, richting van de regel, alleen deze bankrekening. De vraag komt niet als de
     regel al via een vaste regel ging of een bestaande regel hem al zou vinden. **Vaste regels boeken niet zelf** (zoals
     nu): na opslaan meldt de lijst hoeveel open regels hem nu als voorstel hebben; boeken met "Voorstel boeken" per
     regel (niet in "Alle zekere voorstellen boeken", dat blijft voor kenmerk/factuurnummer). Na een **betaling aan een
     relatie** zonder rekeningnummer: "Sla rekeningnummer … op bij …" (tabel `bank`, zoals SQL-Ledger), zodat volgende
     regels via het rekeningnummer een betaalvoorstel krijgen; een bestaand rekeningnummer wordt niet overschreven.
  9. **AKKOORD (2026-10-12).** **Gedeelde bestanden**: de nieuwe CSS staat onderaan `static/style.css` (blok "v1.0.1 schermen"); de teksten van
     zoeklijst en datumvelden staan als `data-msg-*` op `<body>` (`base.html`), vertalingen in elk catalogusbestand in
     het blok `# v1.0.1 schermen`.
  10. **RGS-versie verwijderen (gebouwd, AKKOORD 2026-10-12)**: Systeem > RGS-lijst toont "Ingelezen versies" (nieuwste eerst,
     "in gebruik", teken bij een niet-definitieve versie) met per versie **Verwijderen** (alleen systeembeheerder).
     Bevestiging op de pagina zelf (geen browservenster): zegt welke versie daarna geldt (of dat er geen lijst meer is)
     en hoeveel rekeningen per administratie aan die versie gekoppeld zijn (alle actieve administraties worden even
     geopend; een onbereikbare wordt overgeslagen). Na verwijderen geldt de nieuwste overgebleven versie; logboekregel
     "RGS-lijst verwijderd". **Koppelingen blijven staan**: `x_account_rgs` bewaart per rekening versie + code als
     tekst, zonder verwijzing naar het lijstbestand; ze worden weer gebruikt als die versie opnieuw wordt ingelezen, en
     een koppeling aan een oudere versie blijft het voorstel bij een nieuwere (`previous_code`). Een koppeling aan een
     verwijderde nieuwere (bèta)versie wordt bij de oudere lijst niet gebruikt en doet geen kwaad. Hiermee is het open
     punt bij keuze 2 (bèta van een hogere versie niet te verwijderen) opgelost.
- **v1.1 interne uren: keuzes binnen A — 1 en 3–16 AKKOORD (2026-10-12), 2 AKKOORD met helptekst (gebouwd)** —
  IN1–IN7 gekozen: alles A (2026-10-12); analyse en keuzes
  in `UREN_ANALYSE.md` ("Interne uren (v1.1)"), branch `v1.1-uren`. Kort:
  1. Soort project in eigen tabel `x_project` (migratie 18, met `x_employee_rate`, `x_assignment.internalrate`,
     `x_jcitem_cost`); SL's `project` ongewijzigd; geen app-db-migratie. **AKKOORD (2026-10-12).**
  2. Bestaand project zonder keuze: met klant = klantproject, zonder klant = "niet ingedeeld" (werkt zoals voorheen). **AKKOORD met helptekst (gebouwd):** uitleg op het projectscherm, voetnoot in de projectlijst en een regel in het rapport.
  3. Kiezen verplicht bij een nieuw project; een bestaand, niet ingedeeld project kan ongewijzigd opgeslagen worden. **AKKOORD (2026-10-12).**
  4. Intern project met klant en klantproject zonder klant worden geweigerd; wisselen van soort mag. **AKKOORD (2026-10-12).**
  5. Kostenplaats = afdeling soort 'C'; "of nieuwe kostenplaats" maakt hem in dezelfde transactie; zonder JavaScript. **AKKOORD (2026-10-12).**
  6. Kostenplaats niet op de tijdkaart (via het project); een kostenplaats van een project kan niet weg. **AKKOORD (2026-10-12).**
  7. Scherm Uren > Interne tarieven (boekhouder); zelfde datum = overschrijven; verwijderen mag (zonder bevestiging). **AKKOORD (2026-10-12).**
  8. Toewijzing: veld Intern tarief (leeg = tarief van de medewerker). **AKKOORD (2026-10-12).**
  9. Tarief vast bij opslaan (`x_jcitem_cost`); bij wijzigen alleen opnieuw als medewerker/project/dienst/datum anders
     is; niet met de hand te wijzigen, wel te zien op de tijdkaart. **AKKOORD (2026-10-12).**
  10. Tijdkaarten zonder vastgelegd tarief (oud, of uit SL): het rapport rekent ze met het tarief op de datum en meldt
      het aantal. **AKKOORD (2026-10-12).**
  11. Geen tarief te vinden → 0. **AKKOORD (2026-10-12).**
  12. Verkooporders uit uren: interne projecten niet in de lijst en geweigerd op de server (afwijking van SL, dat geen
      interne projecten kent; in de Perl-vergelijking gelijk omdat `x_project` daar leeg is). **AKKOORD (2026-10-12).**
  13. Label "intern" op Mijn uren/Maandstaat en Tijdkaarten; "(intern)" in de projectkeuzes. **AKKOORD (2026-10-12).**
  14. Rapport in het rapportkader, menu Uren > Uren per project; per project of per project en medewerker; doorklik
      naar Tijdkaarten. **AKKOORD (2026-10-12).**
  15. Verkoopwaarde = uren × prijs op de tijdkaart; "op een order" = `allocated`; bij interne projecten leeg; niet
      ingedeeld = zoals klantprojecten. **AKKOORD (2026-10-12).**
  16. Rapport en tarieven zichtbaar vanaf alleen lezen; tarieven wijzigen alleen de boekhouder. **AKKOORD (2026-10-12).**

- **Fase 6d-2 Overstaplijst en controleblad (I4–I6, I8 = A) — gebouwd op `fase6d`, keuzes binnen A 1–10 AKKOORD (gebruiker 2026-10-12; plus deelstappen per punt DS1–DS4 A).**
  (Gebruiker afwezig: gekozen volgens advies.)
  1. **Plek**: op het dashboard op de plek van de startlijst (niet allebei), alleen voor de beheerder; volledige pagina
     **Beheer > Ingebruikname** (menu altijd zichtbaar voor de beheerder) met uitleg per punt en de knop naar het
     controleblad. De wizard zet bij inlezen niet meer de startlijst aan (ES10) maar de overstaplijst. **AKKOORD (2026-10-12).**
  2. **Aan** bij elk inlezen (ook Systeem > Back-up inlezen, niet alleen de wizard); voor een gekoppelde database met
     de knop "Overstaplijst aanzetten" (herkomst "gekoppelde database"). **AKKOORD (2026-10-12).**
  3. **Zelf afvinken** ("gedaan") alleen bij controleblad en nummering; alle andere punten controleert de app en kunnen
     op "niet nodig". "Back-up ingelezen" is altijd klaar en toont dataset, versie en datum uit de kop van de back-up
     (bij SL zoals SL die schrijft, bv. "Thu Apr 16 11:35:05 2026"). **AKKOORD (2026-10-12).**
  4. **Btw** klaar als het tijdvak "geen" is, of als élke open btw-rekening een NL-rubriek (of "geen aangifte") heeft
     én de afdrachtrekening is gekozen. **AKKOORD (2026-10-12).**
  5. **Bank** klaar als minstens één betaalrekening een IBAN heeft, er een afschrift is ingelezen en het **eerste**
     afschrift per rekening geen saldowaarschuwing gaf (de bestaande controle bij inlezen: grootboeksaldo ≠ beginsaldo). **AKKOORD (2026-10-12); wens: in de app per punt tonen welke deelstappen nog open staan; DS1–DS4 alles A akkoord (2026-10-12): deelstappen alleen op Beheer > Ingebruikname, dashboard alleen teller; lange lijsten eerste 5 + "en nog n"; namen zonder 2FA alleen voor de systeembeheerder; bank per voorwaarde.**
  6. **Afsluitdatum** klaar zodra die later ligt dan bij het inlezen (welke datum, volgt de uitleg: einde laatst
     aangegeven btw-tijdvak; I8). **AKKOORD (2026-10-12).**
  7. **Gebruikers** klaar als elke actieve gebruiker met toegang tot de administratie 2FA heeft; **back-up** als de
     laatste back-up niet ouder is dan 7 dagen; **RGS** alleen als er een RGS-lijst is ingelezen (anders geen punt). **AKKOORD (2026-10-12).**
  8. **Controleblad**: peildatum standaard de laatste boekdatum bij het inlezen (administratie B: 14-04-2026), te wijzigen;
     boekjaar = kalenderjaar van de peildatum (**bouwstap 2, F22/JA7: begin van het ingestelde boekjaar**). Onderdelen in deze volgorde: controles, bevindingen (eerste 50 per
     soort), aantal boekingen per jaar, kas en bank, proefbalans, W&V, balans, openstaande verkoop-/inkoopfacturen per
     relatie. Proefbalans, W&V en balans komen uit `reportdefs` (dezelfde cijfers als Rapporten); de openstaande
     posten uit de dashboardquery in standaardvaluta (de ouderdomsanalyse zelf duurde bij administratie A 15 s; per
     factuur gelijk, getoetst). PDF via de rapport-PDF. **AKKOORD (2026-10-12).**
  9. **Controles** lezen alleen. Verschillen ≤ 0,01 op een boeking met koersregels tellen als "ter informatie"
     (SL rondt koersregels per regel af; C 2014–2015), al het andere als "nakijken"; een bevinding is niet per se
     een fout van het inlezen. **AKKOORD (2026-10-12).**
  10. **Kas en bank** op het controleblad: activa met koppeling betaling (zoals het dashboard), ook gesloten rekeningen. **AKKOORD (2026-10-12).**

- **Fase 6d-1 Back-up maken (FASE6_ANALYSE.md I7 = A; I1 = geen boekingen na 16-4-2026, I2–I10 = A, gebruiker
  2026-10-11) — gebouwd op `fase6d`, keuzes binnen A 1–10 AKKOORD (gebruiker 2026-10-12, met 1a).** (Gebruiker afwezig: gekozen volgens advies.)
  1. **Vorm**: `pg_dump` in platte SQL zonder eigenaar en rechten, gzip, met een eigen kop (administratie, versie,
     datum, bedrijfsnaam); bestandsnaam `<database>-JJJJMMDD-UUMM.sql.gz`. Geen `pg_dump -Fc`: het eigen Back-up inlezen
     leest alleen platte SQL (F1/F2) en zo kan iemand de back-up ook zonder de app lezen. **AKKOORD (2026-10-12).**
     **1a** (gebruiker 2026-10-12, akkoord): de kop heeft `-- App-Dataset:`/`-- App-Version:` in plaats van
     `-- Dataset:`/`-- Version:`, zodat SQL-Ledger (am.pl restore zoekt die twee) het bestand weigert: SL's Restore wist
     eerst alle tabellen en kan een pg_dump niet lezen. Getoond op een wegwerpkopie van administratie B: met de oude kop
     92 lege tabellen en een Perl-fout, met de nieuwe "Not a SQL-Ledger backup!". Back-up inlezen leest beide koppen.
     Weg terug naar SL getest: psql in een nieuwe database, alle 92 tabellen gelijk, SL-rapporten (proefbalans, balans,
     W&V, ouderdom) draaien erop (README "Terug naar SQL-Ledger").
  2. **Sjablonen en logo** staan niet in de database: aparte zip, bij Back-up inlezen optioneel als tweede bestand, met
     dezelfde controles als bij uploaden; vorige versies van sjablonen gaan wel in de zip maar niet terug (overgeslagen
     en gemeld). Niet per administratie en dus niet in de back-up: configbestand, `secret.key`, eigen woorden, RGS-lijst
     (README "Back-up maken"). **AKKOORD (2026-10-12).**
  3. **Wie**: alleen de systeembeheerder (de back-up bevat alles, ook vertrouwelijke bijlagen van anderen). De beheerder
     van de administratie ziet wel de herinnering, zonder link. **AKKOORD (2026-10-12).**
  4. **Herinnering** in het blok Te doen (oranje, onderaan): bij nooit of ouder dan **7 dagen**, voor beheerder en
     systeembeheerder; er is geen knop "niet nodig" (wie zelf `pg_dump` draait, gebruikt de opdrachtregel of het scherm). **AKKOORD (2026-10-12).**
  5. **Opdrachtregel** `sqlledger-web backup --dir … [--company …] [--inactive] [--keep N] [--no-app-db]`: standaard
     alle actieve administraties + de app-database; bij een fout doorgaan, aan het eind code 1; logboek met login `cli`. **AKKOORD (2026-10-12).**
  6. **App-database** alleen via de opdrachtregel (bevat wachtwoordhashes en 2FA-geheimen van iedereen), terugzetten
     met psql bij een nieuwe installatie (handleiding in de README); niet via het scherm in te lezen. **AKKOORD (2026-10-12).**
  7. **pg_dump**: instelling `pg_dump` (pad, standaard uit het zoekpad); het Docker-image krijgt `postgresql-client`.
     Een te oude pg_dump (ouder dan de server) geeft de foutmelding van pg_dump zelf.  **AKKOORD (2026-10-12); uitleg hoort in de installatiehandleiding, niet in de helptekst.**
  8. **`\restrict`-regels** van nieuwere pg_dump-versies (15.14/16.10/17.6 en later) slaat Back-up inlezen over, ook bij
     een gewone pg_dump; alle andere psql-opdrachten blijven geweigerd. Lokaal draait 15.13 (nog zonder die regels); de
     Docker-image krijgt een nieuwere, daarom nu al.  **AKKOORD (2026-10-12).**
  9. **Download** via een tijdelijke map die na het verzenden weg is; het scherm bewaart geen back-ups op de server
     (dat doet alleen de opdrachtregel, in de opgegeven map). Een volledige back-up kan bij een grote administratie
     enkele seconden duren (A: 2,7 s). **AKKOORD (2026-10-12).**
  10. **Logboek**: "back-up gemaakt" (soort database/sjablonen, bestand, grootte, versie, via web/cli) en "back-up
      mislukt" (met de melding); het ontbrekende label "back-up ingelezen" voor `company_restored` is erbij gekomen. **AKKOORD (2026-10-12).**

- **RGS-koppeling (RS13, keuze A 2026-10-11: lijst niet meeleveren, koppelscherm bouwen) — gebouwd, keuzes 1–9 AKKOORD (gebruiker 2026-10-11).**
  Keuzes binnen A bij het bouwen:
  1. **Systeem > RGS-lijst** (alleen systeembeheerder): het officiële `RGS … -def.xlsx` uploaden (max. 32 MB); omgezet naar
     `rgs/rgs-<versie>.json` in de datamap, voor de hele installatie. Een nieuwere versie komt ernaast; de nieuwste geldt.
  2. **Beheer > RGS-koppeling** (beheerder): per rekening de huidige code, tot drie voorstellen (keuzerondjes), "andere
     code" (vrij invullen, gecontroleerd tegen de lijst) en "geen code" (alleen als er een code is). Niets aangevinkt =
     niets veranderd; er wordt nooit iets vanzelf gekoppeld.
  3. Standaard alleen de rekeningen **zonder code** (gesloten rekeningen niet); "alle rekeningen" toont ook de gekoppelde.
     Teller "x van y rekeningen hebben een code".
  4. **Zoeken** in de lijst op code (begin) of woorden (alle woorden in de omschrijving), max. 40 resultaten.
  5. **Voorstellen**: eerst een koppeling uit een oudere RGS-versie (via het versus-blad), dan de SL-koppelingen
     (debiteuren, crediteuren, kas/bank, btw, voorraad, omzet), de soort (balans/W&V, activa/passiva/eigen vermogen,
     debet/credit), gelijke en verwante woorden (eigen lijst met synoniemen, bv. btw → omzetbelasting, hoog → algemeen
     tarief), MKB-codes iets hoger. Een kop krijgt een groepscode (niveau 1–3), een rekening een detailcode.
     Afschrijvingsrekeningen die het oude SL-schema als passiva heeft, krijgen de cumulatieve afschrijving bij de activa.
  6. Getoond wordt de **lange** RGS-omschrijving (de korte is vaak dubbel, bv. "Verkrijgings- of vervaardigingsprijs").
  7. Kolom **RGS** op Beheer > Rekeningschema (nieuwste versie per rekening), alleen als er codes zijn.
  8. Logboek: "RGS-lijst ingelezen" en "RGS-koppeling opgeslagen" (aantal).
  9. Rapportage/dashboard op RGS en export (bv. SBR/XBRL) **later**, eigen beslispunt (RS13).
- **Fase 6, F1–F9 (FASE6_ANALYSE.md) en keuzes 1–7 — AKKOORD gebruiker (2026-10-11). F6: overstap per 1-1-2027 (begin
  boekjaar en btw-tijdvak), Q4 2026 parallel met het vergelijkingsrapport, daarna SL alleen-lezen; met welke
  administratie eerst: nog te vragen (6d). F7: nu lokaal (A), later een VPS in de EU (B) zodra anderen meedoen.** Gebouwd: 6a back-up inlezen en 6b bijwerken.
  Keuzes binnen A bij het bouwen:
  1. Een statement dat bij het **bijwerken** faalt wordt zoals SL overgeslagen (savepoint) en getoond, niet "stoppen bij
     de eerste fout" (zoals F3 eerst zei): anders zou een back-up die SL wél bijwerkt hier weigeren. Bij de zeven echte
     back-ups faalde niets. Bij het **terugzetten** stopt elke fout wel alles.
  2. Grens 512 MB per back-up (los van de uploadgrens voor bijlagen); de back-up wordt niet bewaard.
  3. Naam en databasenaam mogen leeg: dan uit de back-up (`-- Dataset:`); code optioneel; alle modules aan; de
     documenttaal is de standaard (nl); "mij als beheerder" staat aan.
  4. Pad `/systeem/back-up` (onder `/systeem/administraties/…` botst het met het id van een administratie).
  5. In de eerste start zet inlezen de startlijst aan (zoals koppelen via de wizard, ES10).
  6. Koppelen van een bestaande database ouder dan 3.2.4 blijft geweigerd (ES12); bijwerken op de plek (zonder
     back-up) komt pas als daar een back-upstap vóór staat (6c/6d).
  7. Niet gedaan: 6c vergelijkingsrapport (F5) en 6d overstaphandleiding (na F6/F7).

- **Fase Uren, UR1–UR11 en keuzes 1–14 — AKKOORD gebruiker (2026-10-11)** (`UREN_ANALYSE.md`). U-1
  tijdkaarten gebouwd; keuzes binnen A bij het bouwen:
  1. Uren onder de module "Afdelingen en projecten" (geen nieuwe module), kopje "Uren" met Tijdkaarten en Tijdkaart
     toevoegen; bekijken voor iedereen met de module, wijzigen voor de boekhouder (U-3 voegt de rol urenschrijver toe).
  2. Medewerkers en projecten in de keuzelijst zoals SL: actief op de datum van de tijdkaart (plus de huidige).
  3. Tijden als uu:mm (seconden zoals SL niet op het scherm), uren met de nauwkeurigheid van de administratie.
  4. Andere dienst: prijs van de dienst; de omschrijving alleen als je die leeg liet of nog die van de vorige dienst was.
  5. Geen prijsmatrix (SL `PM->price_matrix` voor de klant van het project) op de tijdkaart: prijs van de dienst; de
     klantprijs komt bij de verkooporder (U-2).
  6. Niet afdrukken/batch voor tijdkaarten (SL-sjabloon `timecard`): de urenstaat-PDF komt in U-4.
  7. U-2: "Verkooporders uit uren" in het menu bij Uren (boekhouder, alleen met de module Orders); de klant per regel
     alleen kiezen bij een project zonder klant (SL: "Select Customer"-scherm); orderdatum en leverdatum van de order =
     vandaag (zoals SL), leverdatum per regel = datum van de uren; ordernummer uit de gewone teller.
  8. U-2: de order krijgt betalingstermijn, valuta, prijzen-incl.-btw en taal van de klant (zoals `AA->get_name`).
  9. U-3: de rol urenschrijver is per administratie (zoals de andere rollen) en staat onder "Alleen lezen"; wie
     urenschrijver is, ziet alleen Mijn uren, het wisselen van administratie en zijn instellingen.
  10. U-3: Mijn uren is er voor iedereen met de module (ook de boekhouder voor zijn eigen uren); de medewerker = de
      medewerker met dezelfde login als de gebruiker in deze administratie (`membership.employee_login`).
  11. U-3: wijzigen van een bestaande tijdkaart via de maandstaat past alleen het aantal aan (begin-/eindtijd blijven);
      een nieuwe dag krijgt geen tijden (00:00) en de prijs/omschrijving van de toewijzing/dienst.
  12. U-3: geen melding aan de projectleider (2011: mail "staat klaar"); de projectleider staat wel bij de toewijzing,
      voor later.
  13. U-4: urenstaat als nieuw document `timesheet` in de standaardset (eigen sjablonen kunnen hem overschrijven zoals
      de andere); per maand (alle projecten) of per project (dan de klant als geadresseerde); geen bedragen op de staat
      (alleen uren); niet in de batch/mail-wachtrij en zonder status "geprint" (het is geen boeking).
  14. U-4: de urenstaat toont de omschrijving van de tijdkaart (anders die van de dienst) en de notities eronder.


- **Importeren en exporteren (stap 7), keuzes binnen A 1–12 — AKKOORD gebruiker 1–18 (2026-10-11)** (lijst onderaan
  `FASE4_ANALYSE.md`; 13 = fout zoals gebouwd). Daarnaast bij het bouwen:
  13. Een nummer dat in de administratie **meer dan eens** voorkomt (A/C: tientallen tot honderden) geeft een fout
      "komt meer dan eens voor"; SL neemt stilzwijgend één (artikelen: de nieuwste). Alternatief: ook de nieuwste nemen.
  14. Onbekende artikelgroep wordt **aangemaakt** (zoals SL), met de melding "Nieuwe artikelgroep: …" (keuze 10 aangepast).
  15. Nieuw artikel of nieuwe dienst zonder rekeningen krijgt de standaardrekeningen; past de standaard niet bij een dienst
      (IC_income/IC_expense), dan de eerste rekening met de juiste koppeling.
  16. Nieuwe relatie zoals het scherm: startdatum vandaag, soort bedrijf; memoriaal met de medewerker van de gebruiker.
  17. Het voorbeeld verbruikt volgnummers (id's, klant-/artikelnummers in een teruggedraaide transactie houden hun teller
      niet vast, maar PostgreSQL-reeksen schuiven wel op): gaten in de id's, niet in de nummers die je ziet.
  18. "Exporteren" staat alleen bij de beheerder in het menu (anders zag een alleen-lezen-gebruiker de rubriek Beheer); de
      lijstschermen hebben al CSV/xlsx.
- **Belgisch rekeningschema (deelstap 6), BE1–BE4 en keuzes binnen A — AKKOORD gebruiker 1–6 en B1–B6 doorgevoerd
  (2026-10-11)** na controle tegen de MAR-tekst van de CBN: B1 163 → **164** (163 = milieu); B2 637 → **638**; B3 641 →
  **642** (641 = vaste activa); B4 kopje 42 met de officiële naam + 420000, nieuw kopje 43 Financiële schulden, 430000
  officiële naam, nieuw 433000 rekening-courant; B5 officiële namen 110, 491, 77, 771; B6 bron = bijlage KB 21-10-2018:
  1. BE1–BE4 alle A (eigen MKB-selectie uit het MAR; roosters 54/59; geen 69/79; één koersverschilrekening 654000).
  2. Selectie (126, na B1–B6 128): eigen vermogen incl. "inbreng buiten kapitaal" (WVV), voorzieningen, schulden > 1 jaar; vaste
     activa per rubriek 20–28 met afschrijvingen 2X9 (contra); voorraden; vorderingen/schulden ≤ 1 jaar (40–49);
     geldbeleggingen en liquide middelen; kosten 60–67 en opbrengsten 70–77 met 66/76 "niet-recurrent".
  3. Extra kopjes: 42 (schulden ≤ 1 jaar, zodat 430000 niet onder "schulden > 1 jaar" valt), 44 heet nu
     "Handelsschulden"; 492 (overlopende passiva, L).
  4. `leeg-be` noemt 612000 "Algemene kosten" (MAR: leveringen aan de onderneming); zo gelaten, met daarnaast 612100
     energie, 613000 administratie, 613100 erelonen, 614000–618000 volgens het MAR; bankkosten op 657100.
  5. Geen 0 %-rekening, geen verlegde btw (medecontractant) en geen intracommunautaire rekeningen: zelf toevoegen.
  6. Naam in de keuzelijst: MKB (MAR) / PME (PCMN) / SME (Belgian minimum chart) / KMU (Mindestkontenplan).

- **Frans rekeningschema (deelstap 5), FR1–FR5 en keuzes binnen A — AKKOORD gebruiker 1–6 en B1–B7 doorgevoerd
  (2026-10-11):**
  1. FR1–FR5 alle A (eigen PCG-abrégé-selectie, één koersverschilrekening (na B5: 656000), CA3-regels, 445620 zonder koppeling,
     één schema voor vennootschap en eenmanszaak).
  2. **Te controleren tegen de officiële teksten:** de nummers voor de cessions van vaste activa na ANC 2022-06
     (hier 657000 "Valeurs comptables des immobilisations cédées" / 757000 "Produits des cessions d'immobilisations")
     en de CA3-regels (08, 09, 9B, 14 voor 2,1 %, 20) volgens de actuele notice 3310-CA3-SD. **Gecontroleerd** tegen de
     ANC-lijst (Recueil des normes comptables françaises, 1-1-2025) en formulier + notice 3310-CA3-SD 2026; doorgevoerd:
     B1 officiële namen 657/757; B2 2,1 % → regel **T6** (regel 14 bestaat niet meer; ook `leeg-fr`); B3 671/771
     geschrapt (bestaan niet meer); B4 777 → **747**, kop 74 "Subventions", 740 → 741; B5 koersverschil 666 → **656**
     (commercieel; ook `leeg-fr`); B6 514 Chèques postaux geschrapt; B7 158 → **152** Provisions pour charges, 758
     "Indemnités et autres produits" (afronding blijft op 658000).
  3. Selectie (178): klasse 1 kapitaal/reserves/resultaat/provisions/leningen; 2 immateriële, materiële en financiële
     vaste activa met afschrijvingen (28x, contra) en dépréciations (29x); 3 voorraden met dépréciations (39x); 4
     leveranciers, klanten, personeel, sociaal, staat/btw, associés, overlopend; 5 bank, kas, VMP, concours bancaires;
     6 en 7 de gangbare MKB-rekeningen per subklasse; geen klasse 8/9.
  4. Extra kopjes binnen een klasse waar de categorie wisselt: 409 (fournisseurs débiteurs, A), 419 (clients
     créditeurs, L), 4456 (TVA déductible, A), 4457 (TVA collectée, L), 487 (produits constatés d'avance, L), 519
     (concours bancaires, L), zodat de balans ze onder het juiste kopje toont.
  5. Koppelingen: artikelopbrengst 701000/707000 (IC_sale), 704000/706000 (IC_income); kostprijs 601000/607000
     (IC_cogs), dienstenkosten 604000/606000/611000 (IC_expense); overige kosten alleen AP_amount; 512000, 530000
     als betaalrekening.
  6. Namen: Frans naar de officiële PCG-benamingen (soms ingekort), en/nl/de eigen werk.

- **Brits rekeningschema (deelstap 4), UK1–UK5 en keuzes binnen A — AKKOORD gebruiker 1–6 (2026-10-11)** ( analyse onderaan `REKENINGSCHEMA_ANALYSE.md`):
  1. UK1–UK5 alle A (basis UK_General aangepast aan `leeg-uk`; één schema voor een limited company; btw 20/5/0 % per
     richting met VAT100-box 1/4; fouten in UK_General hersteld; lege schema's ook met box/Ziffer).
  2. Hernummerd: kopjes 0000/1000/2000/2300/3000/4000/5000/7000 (0000 en 2300 nieuw); 3000 Ordinary shares → 3001,
     4000 Sales → 4001, 5000/5001 Materials → 5001/5002, 5002/5003 Opening/Closing stock → 5004/5005, 7000 Staff wages →
     7003, 2210 PAYE/NI → 2220, 2220 Net wages → 2230, 2300 Bank loan → 2301, 1240 Company credit card → 2120 (L),
     2205 VAT 5 % → 2201, 8500 Dividends → 3300 (Q).
  3. Nieuw: 2202 VAT liability, 2203/2212 btw 0 %, 2210/2211 voorbelasting 20/5 %, 7001 General expenses (standaard
     kostenrekening), 7906/7907 koers- en afrondingsverschillen (gelijk aan `leeg-uk`).
  4. Koppelingen van UK_General overgenomen (o.a. 5001/5002 met IC_cogs én IC_expense, 4900 als artikelopbrengst);
     9999 Suspense account als kostenrekening (zoals UK_General en Sage).
  5. Namen in het Engels licht gemoderniseerd ("Debtors Control Account" → "Trade debtors", gelijk aan `leeg-uk`;
     "General Rates" → "Business rates"); vertalingen nl/de/fr eigen werk.
  6. Naam in de keuzelijst: Limited company (SME) / Limited (MKB) / Limited (KMU) / Limited (PME).

- **Zwitsers rekeningschema (deelstap 3), keuzes binnen A bij het bouwen — AKKOORD gebruiker 1–9 (2026-10-11)** (CH2 = A,
  CH3 = A):
  1. Hernummerd (KMU/PME/PMI-schema uit 2012 → huidige KMU, gecontroleerd met RMACH): Vorsteuer 1173/1171/1172 → **1170 (8,1) / 1171 (2,6) /
     1172 (3,8)**, Vorsteuer Investitionen 1174 → **1173** (1171 is in `leeg-ch` al 2,6 %); MWST 2013/2011/2012 → **2200 /
     2201 / 2202**, Dividenden 2200 → **2261**; 1220 Fertigfabrikate → 1260, 1280 Angefangene Arbeiten → 1270; 1410
     Beteiligungen → 1480, 1420/1430/1440 → 1440/1441/1460; 2120 → 2140, 2310 → 2330, 2400/2410/2460 → 2450/2451/2430;
     2990/2995 → 2970/2979; 3090 → 3900, 3900 Debitorenverluste → 3805; afschrijvingen 69/6900 → **68/6800**, financieel
     68/6800/6850 → **69/6900/6950**; 82/8200/8210 → 80/8100/8000, 80/8000/8010 → 85/8510/8500. Overige nummers ongewijzigd
     (ook als de huidige KMU ze niet kent, bv. 1010 Post, 1040, 1050, 2020, 2110, 74xx).
  2. Nieuw t.o.v. het schema uit 2012: 2209 Abrechnungskonto MWST, 6940 Kursdifferenzen, 6990 Rundungsdifferenzen (gelijk aan `leeg-ch`);
     3950/3955 uit dat schema vervallen.
  3. Eigen vermogen (28, 280, 290 en rekeningen) als categorie **Q** in plaats van L.
  4. Koppelingen van het bronschema overgenomen, ook de vreemde: vaste activa 1500–1550 met `IC` (keuzelijst voorraadrekening),
     8900 Steuern met `AP_amount`; alleen bij **9000/9100** (Erfolgsrechnung/Bilanz) `AP_amount` weggelaten (een
     inkoopboeking op "Bilanz" is zinloos). 1109 Delkredere zonder contra (zoals het bronschema).
  5. Tikfouten in de bronteksten hersteld: "Lanfristige", "Beständesänderung", dubbele spaties; fr "Durcroire",
     "antinicpés", "investitions", "intérêts financières"; it "die terzi", "Titoli de credito", "Cambialo", "socziale",
     "partezipazione", "estreaneo", "prodotti finite". Ontbrekende fr/it-kopjes (200–260, 68, 69, 79/7900) zelf vertaald;
     7500 "Liegenschaftenerfolg" → "Liegenschaftenertrag" (fr/it hadden al "opbrengst").
  6. Btw-namen in de stijl van `leeg-ch` ("MWST Vorsteuer auf Aufwand 8,1 %", "Geschuldete MWST 8,1 %"), komma als
     decimaalteken behalve in het Engels.
  7. MWST-Ziffern in `x_vat_box` met kolom `country` (migratie 16, sleutel rekening + land) i.p.v. een prefix in de
     rubriekcode; bestaande rijen krijgen NL. Beheer > Btw-rubrieken (NL) blijft ook bij een Zwitserse administratie in het
     menu staan (zoals bij `leeg-ch`), maar de aangifte gaat pas aan na het koppelen van NL-rubrieken.
  8. Italiaans in de keuzelijst "Taal van de rekeningnamen" voor alle schema's (bij een schema zonder Italiaans: "Dit
     rekeningschema is er niet in deze taal"); documenttaal dan die van het scherm (anders Engels).
  9. Namen in de keuzelijst: KMU (de), PME (fr), PMI (it), SME (en), MKB (nl); schema bovenaan bij Zwitserland.

- **Huisstijl, keuzes binnen A bij het bouwen — akkoord gebruiker 1–9 (2026-10-11):**
  1. Lettertypen **onveranderd** meegeleverd (variabele lettertypen; WeasyPrint 70 maakt er zelf vet van): geen gedoe met de Reserved Font Names (Liberation, Source, Lora), maar het pakket wordt ±3,7 MB groter i.p.v. 1,5–2 MB. Licentievermelding (voorwaarde gebruiker): regel onder de lettertypekeuze + pagina **Lettertypen: licenties en makers** (`/lettertypen`, ook bereikbaar voor niet-beheerders) + `fonts/NOTICE.txt`.
  2. Groot logo = **24 × 95 mm** (niet 25): de kop is 24 mm hoog tussen bovenmarge en vensterplek.
  3. Bedrijfsgegevens in de kop met huisstijl iets kleiner (naam 11 pt, adres 9 pt; was 12/10) zodat vier adresregels naast het logo passen; vijf of meer → waarschuwing "kop te hoog" (in de browser gemeten).
  4. Adres **rechts** (Zwitserland): tegen de rechtermarge (109–194 mm van links), 45 mm van boven. SN 010130 is niet vrij in te zien; een C5-envelop met venster rechts heeft een venster van 45 × 100 mm. Nog controleren met een echte envelop.
  5. Live voorbeeld = de browser (één A4-blad, voetregel en paginanummer nagebootst); de knop PDF bekijken toont de echte opmaak. Het kader heeft een eigen strenge CSP (de app staat geen inline opmaak toe).
  6. Logo uploaden of verwijderen op het huisstijlscherm herlaadt de pagina; niet-opgeslagen keuzes gaan dan verloren (de browser waarschuwt). Na "Laden" van een andere administratie is het logoblok verborgen tot Opslaan of Annuleren.
  7. Voorbeeldfactuur zonder verkoopfacturen: regelnummers A-100/A-200, bedragen in de notatie van de documenttaal, btw = eerste geldige verkooptarief (rekening met AR_tax), IBAN van de eerste betaalrekening met IBAN.
  8. Startlijst: het oude punt "logo" bestaat niet meer; wie het eerder op "niet nodig" zette, ziet "Logo en huisstijl" als niet nodig. De uitleg "Kies logo, kleur en lettertype …" staat achter het punt zolang het open is.
  9. Kopjes op het scherm: "Logo" (bestand) en "Grootte en ligging van het logo"; Engels "House style", Duits "Erscheinungsbild", Frans "Charte graphique" (zoals de oude startlijsttekst).

- **Eerste start (wizard), keuzes binnen A bij het bouwen — akkoord gebruiker 1–10 (2026-10-11):**
  1. Startcode-melding in het serverlog in het Engels (met "startcode" erbij, de taal is dan nog niet gekozen);
     het adres in de melding komt uit `SQLLEDGER_PUBLIC_URL` (`serve` zet `http://<host>:<poort>`).
  2. De startcode mag in kleine letters en met spaties; na een fout blijven login, naam en e-mail staan, code en
     wachtwoord niet. Naam en e-mail van de beheerder zijn niet verplicht.
  3. Welkomstscherm: vier taalknoppen; komt iemand terug, dan staat de gekozen taal voorgeselecteerd; op het
     beheerderscherm een link "Andere taal".
  4. Databasenaam: leeg = afgeleid uit de naam (Bakkerij Één & Zn → `bakkerij_een_zn`), in de browser live
     voorgesteld zolang je hem niet zelf wijzigt; geldt ook voor Systeem > Administraties > Nieuwe administratie.
  5. Koppelen in de wizard: ook daar het vinkje "Mij als beheerder van deze administratie toevoegen" (standaard aan),
     anders kan de beheerder de administratie na afloop niet openen.
  6. Uitnodigen: hoogstens 50 regels; een regel met naam of e-mail maar zonder login geeft "Login is verplicht";
     medewerkerslogin = login; na een gedeeltelijke fout staan de al gemaakte codes boven de foute regels en worden
     ze daarna niet opnieuw getoond (zoals elke eenmalige code).
  7. Code mailen (Systeem > Gebruikers): met de e-mailinstellingen van een administratie (die van de gebruiker eerst,
     keuzelijst bij meer dan één), alleen een geldige ongebruikte code van die gebruiker, Reply-To = de beheerder,
     tekst in de taal van de beheerder; logboek `code_mailed`.
  8. Startlijst, wanneer "klaar": bedrijfsgegevens = naam én adres ingevuld; logo = logobestand bij Sjablonen;
     huisstijl = er is een documentinstelling; btw-tijdvak = opgeslagen bij Beheer > Instellingen (reparatie: ook de
     standaard "kwartaal" wordt nu vastgelegd); bank = een betaalrekening met IBAN in `bank`; beginbalans = er is een
     memoriaalboeking; eerste verkoopfactuur = er is een verkoopboeking (link naar de verkoopfactuur met artikelen
     als module Artikelen aanstaat, anders naar de boeking zonder artikelen).
  9. Reparatie (bestond al in `fase4`): bij het eerste openen van een gekoppelde database liepen de dashboardblokken
     tegelijk `ensure_schema` en botsten; het slot staat nu vóór het aanmaken (`tests/test_extensions.py`).
  10. Reparatie: rekeningnummer "0" geweigerd in schemabestanden (Perl laat het weg, afwijking 224); `leeg-de`-kop = 08.
- **Eerste start, nieuwe beslispunten ES10–ES12 — keuze gebruiker A, A, A (2026-10-11), gebouwd:** startlijst alleen bij
  in de app aangemaakte of via de wizard gekoppelde administraties; IBAN/BIC/banknaam bij een betaalrekening in Beheer >
  Rekeningschema (alleen schrijven bij een wijziging; am_keeptest A/C 0 verschillen, `bank` vergeleken); koppelen
  ouder dan 3.2.4 geweigerd. Keuzes binnen A (akkoord gebruiker a–e, 2026-10-11): (a) het startlijstpunt IBAN wijst naar de eerste
  betaalrekening zonder IBAN waarvan de omschrijving op een bank lijkt (bank/banque of een IBAN), anders de eerste;
  (b) IBAN en BIC alleen gecontroleerd als ze gewijzigd zijn (zoals bij relaties), IBAN zonder spaties in hoofdletters
  bewaard; (c) bij een niet-betaalrekening worden de bankvelden getoond maar niet bewaard; (d) is de database bij het
  koppelen niet bereikbaar, dan wordt er zoals vroeger opgeslagen (geen versiecontrole mogelijk); (e) bestaande
  administraties die al vóór ES10 in de app waren aangemaakt krijgen geen startlijst (geen vlag).

- **NL-rekeningschema's (RS3, deelstap 2), keuzes binnen A 1–12 en rekeninglijst 1–5 — akkoord gebruiker (2026-10-11):**
  1. Drie bestanden: `nl-eenmanszaak.csv` (ook vof), `nl-bv.csv` en `nl-basis.csv` (deelverzameling van eenmanszaak,
     zelfde nummers). In de keuzelijst eerst Eenmanszaak/vof (standaard), dan BV, Basis, leeg (kopregel `volgorde`).
  2. Decimaal, 4 cijfers; kernnummers gelijk aan `leeg-nl` (kas 1001, bank 1100, debiteuren 1300, btw 15xx,
     crediteuren 1600, voorraad 3001, omzet 8001, inkoopwaarde 7001, afronding 4900, koersverschil 4910).
  3. Indeling zoals Exact: 0 vaste activa, eigen vermogen (bij eenmanszaak/vof ook privé), voorzieningen,
     langlopende schulden · 1 liquide middelen, vorderingen, kortlopende schulden en btw · 2 tussenrekeningen ·
     3 voorraad · 4 kosten (40 personeel … 46 afschrijvingen, 48/49 overig) · 7 kostprijs · 8 omzet · 9 financieel
     en vpb. Eén kopniveau. Privé staat bij het eigen vermogen (balans), niet in reeks 9.
  4. Btw 21/9 % te betalen (1A/1B) en te vorderen (5B); verlegd/EU/invoer als paar met negatief tarief (afwijking 223).
  5. Paren: 2A 21 %, 4A 21 %, 4B 21 % en 9 %; geen 1C/1D-rekening; geen 0%-rekeningen (omzet zonder btw via eigen
     omzetrekeningen en de rubriek van de klant, BA4).
  6. Per rekening de RGS 3.8-referentiecode (alleen codes, geen RGS-omschrijvingen); koppen de groepscode. Getoetst
     tegen `RGS 3.8-def.xlsx` van referentiegrootboekschema.nl (niet in de repo; brontabel en generator in
     `dev/rsnl/`, niet in git).
  7. Bij aanmaken in `x_account_rgs` (versie 3.8); zichtbaar in de kolom RGS van Beheer > Rekeningschema en te
     wijzigen bij Beheer > RGS-koppeling (RS13).
  8. Omschrijvingen NL/EN/DE/FR. 9. Vaste activa: aanschafwaarde + cumulatieve afschrijving (`contra`).
  10. Standaardrekeningen gelijk aan `leeg-nl`. 11. Getoetst met pytest, run_compare en `newcompany_check` (Perl,
      met een EU-inkoop met het btw-paar). 12. Eerst de lijst ter controle (Excel), daarna de code.
  Rekeninglijst: (1) basis is eenmanszaak-gericht, een bv kiest het bv-schema; (2) omzet hoog/laag met de RGS-code
  voor goederen, diensten hoog apart (8020), verlegd/vrijgesteld, EU-leveringen, EU-diensten en export elk een eigen
  rekening; (3) kostenrekeningen voor inkoopfacturen met inkoopkoppeling, lonen/afschrijvingen/privé/overlopend
  zonder (memoriaal), kruisposten en pinbetalingen onderweg als betaalrekening; (4) extra: tussenrekening
  beginbalans, btw privégebruik auto, niet-aftrekbare btw, betalingsverschillen, vennoot 2 bij de vof; (5) wettelijke
  reserves en boekresultaat vaste activa met de groepscode (RGS heeft daar geen passende rekeningcode).
- **Nieuwe administratie + lege schema's (RS1, deelstap 1 van de rekeningschema's), keuzes binnen A 1–11 — akkoord gebruiker (2026-10-11):**
  1. Systeem > Administraties heeft twee knoppen: **Nieuwe administratie** (aanmaken met schema) en **Bestaande
     database koppelen** (het oude formulier, ongewijzigd).
  2. Een **leeg schema per land** (NL, BE, VK, FR, CH; 17–22 rekeningen): kas, bank, debiteuren, crediteuren, btw,
     voorraad, omzet, inkoopwaarde, algemene kosten, afrondings- en koersverschillen, eigen vermogen en onverdeelde
     winst. Nummers volgens het land: NL decimaal 4 cijfers, BE/FR 6 cijfers (PCMN/PCG), VK Sage-banden, CH KMU.
     Omschrijvingen in NL/EN/DE/FR. Koppen per categorie, zodat W&V en balans per kop optellen.
  3. Btw: per tarief een rekening **te betalen** én **te vorderen** (SL rekent per btw-rekening met één tarief).
     Tarieven: NL 21/9, BE 21/12/6, VK 20/5, FR 20/10/5,5/2,1, CH 8,1/2,6/3,8 %. Geen 0%-rekening (verkoop zonder btw
     loopt via de rubriek van de klant, BA4); verlegd en EU (2A, 4A/4B) komen met het NL-schema (deelstap 2).
  4. Rubrieken van de aangifte alleen voor NL (1A, 1B, 5B); `x_vat_box` krijgt pas een landcode als er een tweede
     aangifte komt (RS9).
  5. Taal van de rekeningnamen = documenttaal van de administratie; standaard de taal van het land. Geen extra
     vertalingen in SL's tabel `translation`.
  6. Alleen de gekozen standaardvaluta in `curr`; andere valuta voegt de gebruiker zelf toe (Beheer > Valuta).
  7. `defaults`: bedrijfsnaam, precisie 2, afronding 0,01, gewicht kg, laatste maand boekjaar, **audittrail aan**;
     de aanmaker wordt medewerker. Adres en andere bedrijfsgegevens daarna in Beheer > Instellingen (melding wijst
     erheen).
  8. Databasenaam streng (kleine letters, cijfers, _, begint met een letter); altijd op de standaardserver uit de
     configuratie (geen server/poort in het formulier). Bestaat de database al: geweigerd, niets aangeraakt.
  9. NL: btw-aangifte meteen in het menu (de rubrieken staan er al); de ICP-opgaaf verschijnt vanzelf zodra een klant
     of btw-rekening op rubriek 3B staat (ICP1).
  10. "Mij als beheerder van deze administratie toevoegen" staat standaard aan (rol beheerder, eigen login als
      medewerker).
  11. Fouten in een meegeleverd schemabestand (`ChartFileError`) zijn voor de ontwikkelaar en niet vertaald; de toets
      controleert elk bestand (koppelingen, standaardrekeningen, dubbele nummers, koppen, tarieven, rubrieken).
- **Administratie wisselen (AW1 A, AW2 B, AW3–AW7 A), keuzes binnen A 1–8 — akkoord gebruiker (2026-10-11):**
  1. Recent gebruikt = de laatste 5 andere administraties (de huidige staat er apart bovenaan, met code · database ·
     rol); bewaard worden de huidige + 5, oudere vallen weg. Ook automatisch kiezen (maar één administratie) telt.
  2. Zoekveld vanaf 8 administraties (waar de gebruiker lid van is), zoekt in naam, code en databasenaam; Enter met
     precies één treffer opent die meteen, anders de keuzepagina met dezelfde zoekterm (ook zonder JavaScript). De
     keuzepagina heeft vanaf 8 ook een zoekveld.
  3. "Wijzigingen verlaten?" alleen bij wisselen via het keuzemenu, als er in een formulier dat opslaat (POST) iets is
     getypt of gekozen; een eigen vraag (`confirm`), niet de browsermelding "Site verlaten?" bij elke link. De andere
     menulinks waarschuwen dus (nog) niet.
  4. Code: hoogstens 12 tekens, geen spaties, uniek zonder op hoofdletters te letten, leeg mag (ook vaker); alleen de
     systeembeheerder stelt hem in (Systeem > Administraties, kolom vooraan in de lijst), niet de beheerder van de
     administratie.
  5. Kopregel: "code naam ▾ · rol"; lijsten blijven op naam gesorteerd (niet op code).
  6. Vergrendeld: grijs met "op slot", niet aan te klikken, de melding als tooltip; ook op de keuzepagina is de knop
     nu uitgeschakeld (was: klikken → melding). Een beheerder van die administratie kan hem gewoon kiezen.
  7. Geen knop "nieuw" zoals Exact: een nieuwe administratie aanmaken komt met de rekeningschema's (RS1).
  8. Sluiten met Escape of klik ernaast; openen zet de cursor in het zoekveld. Logboek: het bestaande
     `company_selected`.

- **ICP-opgaaf (ICP1–ICP11 A), keuzes binnen A 1–12 — akkoord gebruiker (2026-10-11):**
  1. Bron = de boekingen in rubriek 3B van de aangifte (zelfde rekenregels). Een 0%-rekening krijgt in SL geen regel
     (keuze 3 btw-aangifte), dus ICP-verkoop zonder btw telt via de **rubriek 3B bij de klant**.
  2. Btw-nummer verplicht (wens gebruiker): opslaan geweigerd voor een klant in 3B (rubriek bij de klant of een
     standaard-btw-rekening op 3B) en een leverancier met een btw-rekening op 4B zonder geldig nummer uit een ander
     EU-land (leeg, NL, GR i.p.v. EL, niet-EU, verkeerde vorm). Bestaande gegevens zonder nummer: melding op de
     ICP-opgaaf en de aangifte (met link), de bedragen tellen wel mee. Het land in het adres wordt niet vergeleken.
  3. Leveringen/diensten: per factuur het 3B-bedrag naar verhouding van de artikel- en dienstregels (centen naar
     diensten); dienst = artikel zonder voorraadrekening (geen samengesteld artikel). De ICP-soort van de klant geldt
     alleen voor boekingen zonder artikelen; bij een ABC-klant gaan de leveringen naar de ABC-lijst, diensten blijven
     diensten.
  4. Afronden per afnemer en kolom rekenkundig (0,50 omhoog, −2,50 → −3); het totaal is de som van de afgeronde regels
     en kan daardoor een paar euro van 3B op de aangifte (in uw voordeel) verschillen: getoond, geen melding. Melding
     alleen bij boekingen in 3B zonder afnemer (memoriaal, inkoop).
  5. Bedragen in de standaardvaluta (grondslag uit de aangifte); de verhouding uit de factuurregels.
  6. Tijdvak: Beheer > Instellingen "ICP-opgaaf per" (alleen zichtbaar als de ICP-opgaaf aan staat); zonder instelling
     maand bij btw per maand, anders kwartaal. Keuze: laatste 12 tijdvakken + andere periode; standaard het laatst
     afgesloten tijdvak.
  7. Vastleggen: bij de btw-boeking vanzelf als het ICP-tijdvak gelijk is en er ICP-regels zijn; anders knop "ICP-opgaaf
     vastleggen" (boekhouder+), daarna verdwijnt de knop. Verschil per afnemer (afgerond), ook nieuwe of verdwenen
     afnemers; te verwerken bij "Eerdere opgaven corrigeren".
  8. Dashboard: eigen regel direct onder de btw-taak, "ICP-opgaaf … vóór <einde maand na het tijdvak>", alleen met
     bedragen en tot de opgaaf is vastgelegd; geen knop Afgehandeld (vastleggen = afgehandeld).
  9. Menu: Btw > ICP-opgaaf onder Btw-aangifte, alleen met module Verkoop en iets op 3B (kolom `company.icp`,
     bijgewerkt bij opslaan van btw-rubrieken, instellingen en klanten). Link vanaf de aangifte (melding bij 3B).
  10. Klantscherm: "ICP-soort" (leveringen standaard / diensten / ABC) naast de btw-rubriek, zichtbaar zodra er
      btw-rubrieken zijn, zodat 3B en de soort in één keer in te stellen zijn.
  11. Rechten: alleen-lezen ziet de opgaaf en exports, niet de knop vastleggen.
  12. Uitvoer: CSV (puntkomma, hele euro's, kolommen als het portaal), Excel (+ per afnemer de boekingen exact), PDF
      (overzicht, met doorklik de gekozen afnemer). Geen XML/SBR; geen voorraad op afroep (uitleg op het scherm).
  - Herinnering (wens gebruiker 2026-10-11, akkoord 1–3): de dashboardregels btw-aangifte en ICP-opgaaf zijn alleen een
    herinnering; na de termijn (einde van de maand na het tijdvak, jaar 31 maart) vervallen ze en volgt het volgende
    tijdvak zodra dat is afgesloten. De rode stand "te laat" is weg. Afgehandeld (btw) en vastleggen (ICP) blijven.

- **Btw-aangifte (BA1–BA9 A), keuzes binnen A 1–12 — akkoord gebruiker (2026-10-11):**
  1. De btw telt op de datum van de btw-regel (`acc_trans.transdate`), zoals het grootboek en de btw-boeking; het
     bestaande btw-rapport gebruikt de factuurdatum (verschil bij 5 facturen van A).
  2. Meer tarieven op één boeking (akkoord 2026-10-11): factuur met artikelen per regel (btw-rekeningen van het artikel
     die op de factuur staan); anders btw ÷ tarief, verschil ≤ € 1 naar het hoogste tarief, een 0%-rekening op de boeking
     krijgt het restant, groter verschil = zonder btw + melding.
  3. Omzet zonder btw (BA4) = verkoopboekingen zonder btw-regel. Een 0%-rekening krijgt in SL geen regel (bedrag 0),
     dus zo'n verkoop telt via de klant-rubriek of 1E, ook als de 0%-rekening op 3B staat. Memoriaal-omzet zonder
     btw-regel en inkoop zonder btw doen niet mee.
  4. 4A/4B: de btw van die rekening telt in 5A; de aftrek als voorbelasting loopt via een (andere) 5B-rekening,
     niet automatisch.
  5. "Niet in de aangifte": btw en grondslag van die rekening tellen niet; een btw-rekening zonder rubriek met
     mutaties in het tijdvak geeft een melding.
  6. Btw-boeking: memo per regel = rubriek, nummer automatisch, omschrijving "Btw-aangifte maart 2026"; is er al
     een btw-boeking voor het tijdvak, dan alleen "Btw-boeking bekijken" (een nieuwe pas na verwijderen); niets te
     boeken → melding.
  7. Vastleggen na Opslaan: bedragen per rubriek én per boeking (voor de BA7-vergelijking); de dashboardtaak is dan
     afgehandeld.
  8. Menu-item Btw-aangifte alleen met rubrieken en tijdvak ≠ "geen" (kolom `company.vat_return` in de app-database,
     bijgewerkt bij opslaan van rubrieken/instellingen); Btw-rubrieken altijd voor de beheerder. Plek: Btw > Btw-aangifte
     (menubesluit MN1) in plaats van Rapporten.
  9. Tijdvakkeuze: de laatste 12 tijdvakken van de ingestelde soort (bij "geen": kwartaal) + andere periode; standaard
     het laatst afgesloten tijdvak.
  10. Aangifteformulier (wens gebruiker, naar Exact Globe): "Proefafdruk" tot de btw-boeking, daarna de vastgelegde
      bedragen met "Definitief"; 5e/5f altijd 0.
  11. Voorstel afdrachtrekening/afrondingsrekening uit de omschrijving (A: 1840 Rc BTW / 4499 Betalingsverschillen).
  12. Rubriek bij de klant alleen zichtbaar als er btw-rubrieken zijn ingesteld (of de klant er al een heeft).

- Batch-boekingen: `VR.pm` zet bij batches altijd `pending`; deze bibliotheek
  laat dat aan de aanroeper (`pending=True`).
- Fase 2, akkoord zoals gebouwd (2026-10-09), mogelijk later anders:
  - Terugkerend: een start op de 30e blijft de 30e (alleen de 31e = maandeinde); inhalen één termijn per
    schema per verwerkronde (zoals Perl); de boeking zelf is de eerste termijn; btw (boekingen zonder artikelen) wordt gekopieerd, niet
    herberekend bij een tariefwijziging; een open termijn in een afgesloten periode wordt overgeslagen.
  - Afletteren: rekeningen zonder koppeling als betaalrekening alleen via `?rekening=`; leeg kenmerk = alle
    regels van die dag samen (zoals Perl).
  - Bankafschrift: betaalkenmerk = bankreferentie; valuta per bankrekening afgeleid (geen eigen veld); geen
    contante korting in voorstellen; bij een onbekende IBAN wordt de rekening niet voorgeselecteerd.
  - Relaties: geen record-lock (zoals Perl); "Opslaan als nieuw" geeft altijd het volgende nummer.
  - Beheer: scherm bankrekeningen (am.pl `bank_accounts`) niet gebouwd; standaardvaluta wisselen alleen in een lege administratie.
- Samengestelde artikelen (fase 3 stap 1b, gebouwd op advies A): kits later; "op documenten" (SA10) is
  gebouwd in fase 4 stap 2 (README 183).
- Orders en offertes (fase 3 stap 3, gebouwd op advies A + ON1–ON4), keuzes binnen A (akkoord gebruiker 2026-10-10):
  een factuur met meer dan besteld geeft geen nabestelling en `ship` = besteld; een factuurregel zonder
  koppeling aan een orderregel telt mee voor de eerste orderregel met hetzelfde artikel; bij omzetten komen
  termijn en btw inbegrepen van de relatie (zoals Perl), leverdatum = datum + termijn; de nabestelling neemt
  geen herhaalschema, afdruk-/mailstatus en referenties over; verwijderen van een order uit een offerte
  zet de offerte weer open.
- Facturen met artikelen (fase 3 stap 2, gebouwd op advies A), keuzes binnen A (akkoord gebruiker 2026-10-10): kostprijs van een
  verkoop zonder voorraad op de datum van de latere aanvoer (blijft daar ook als de verkoop later
  wijzigt); retour klant tegen de toewijzingen van de laatste verkopen aan die klant; ongewijzigd
  opslaan van een verkoop met een open (niet toegewezen) deel wijst niet alsnog toe (alleen een nieuwe
  aanvoer of een gewijzigde regel doet dat). Gekozen: regelprijzen afgerond op de standaarddecimalen (zoals
  Perl); "hoeveel kan ik maken" met de directe onderdelen (sub-producten tellen met hun eigen voorraad).
- Herhalen van facturen en orders (fase 3 stap 4, gebouwd op advies A + RH1–RH6), keuzes binnen A (akkoord
  gebruiker 2026-10-10): de omschrijving van de kopie is die van het origineel (zoals fase 2, README 92); "betaling
  meenemen" bestaat niet bij orders; een gesloten of gefactureerde order kan een schema krijgen en houden
  (sjabloon); een verkoopkopie zonder "voorraad controleren" mag de voorraad negatief maken, net als het
  factuurscherm.
- Mailen (fase 4 stap 1, keuzes M1–M7 akkoord gebruiker 2026-10-10): afzender per administratie met Reply-To
  administratie of gebruiker; handtekening zichtbaar in het bericht; mapmodus zet status en notitie zoals echt
  verzenden; alleen afwijkende standaardteksten bewaard; sleutel `secret_key` of automatisch `secret.key`;
  inloggen zonder TLS alleen naar localhost; geprint/gemaild alleen tonen.
- Documenten fase 4 stap 2 (DS1–DS12, DT2 op advies A), keuzes binnen A ter beoordeling: picklijst op locatie
  gesorteerd; op pakbon/picklijst uit een order is het aantal van een onderdeelregel besteld × stuklijstaantal
  (niet × nu te leveren, zoals Perl); offerteaanvraag zonder prijzen; "Opslaan en PDF" toont daarna de link
  "PDF openen" (automatisch openen wordt door browsers vaak geblokkeerd); het veld Sjabloonvariant is verborgen
  als er geen talen of varianten zijn; één logo per administratie (niet per variant); uploaden zonder document
  van die soort controleert alleen de opbouw (geen proef-PDF); de knoppen PDF/E-mail hebben de bestaande stijl
  van link-knoppen (donker, zoals Herhalen).
- Schermen Duits en Frans (fase 4 stap 2b, TL1–TL8 op advies A), keuzes binnen A ter beoordeling: storneren = DE "stornieren/Storno", FR "contre-passer/contre-passation"; vervallen artikel = "veraltet"/"obsolète"; Beheer > Afsluiten = "Festschreibung"/"Clôture"; veld Login = "Benutzername"/"Identifiant"; knop Herhalen = "Wiederholen"/"Planifier"; bestelniveau = "Meldebestand"/"Seuil de réapprovisionnement"; Frans met vaste spatie vóór `: ; ? !`; documenten en mails in het Duits zeggen nu "Rechnungskorrektur" (was Gutschrift) en "Belastungsanzeige" (was Lastschrift). Een moedertaalspreker kan `dev/vertalingen.py` gebruiken voor een volledige controle.
- Eigen woorden (TL9): per installatie, niet per administratie (zoals SQL-Ledger); documentteksten pas je per administratie aan via
  Beheer > Sjablonen. Geen scherm om ze in de browser te bewerken (TL9 B, kan later).
- Documentinstellingen (fase 4 stap 3), keuzes binnen A **akkoord gebruiker 2026-10-10**: opslag in `x_doc_setting` (niet `defaults`);
  teksten en kolomnamen per documenttaal zonder terugval op Nederlands (leeg = standaardtekst in die taal);
  wisselen van taal op het scherm via een link (eerst opslaan, zoals Beheer > E-mail); kolomvolgorde vast per groep
  (geen verslepen); "kolom als ingevuld" kijkt naar alle regels van het document; leverdatum als regel onder de
  omschrijving alleen als die afwijkt van de documentdatum (zoals de standaardset), als kolom altijd; meer dan
  8 kolommen = compacte tabel (7,5 pt); gewichtsoverzicht = omschrijving, aantal, gewicht, nettogewicht + totalen
  uit Perl (`totalgrossweight`/`totalnetweight`); btw-nummer alleen in de voet; betaalzin aan/uit voor alle
  facturen tegelijk; geen betalingskortingsregel in de totalen (stond in de oude factuur van A, kan als
  instelling later); een eigen sjabloon (Beheer > Sjablonen) gaat vóór, met `doc.*` beschikbaar.
- Aanmaningen en rekeningoverzichten (fase 4 stap 4a, SB1–SB6 op advies A), keuzes binnen A **akkoord door de gebruiker (2026-10-10)**:
  (1) titels NL "Herinnering", "Tweede herinnering", "Aanmaning" (EN Payment reminder/Second reminder/Final
  reminder, DE Zahlungserinnerung/Zweite Zahlungserinnerung/Mahnung, FR Rappel/Deuxième rappel/Mise en demeure);
  (2) standaardtekst: niveau 1 en 2 vragen betaling binnen 14 resp. 7 dagen, niveau 3 binnen 7 dagen met
  "uit handen geven"; slottekst "Heeft u inmiddels betaald? …" (alles aan te passen per niveau en taal);
  (3) het vast te leggen niveau is het niveau dat op het scherm stond (PDF en daarna mailen hogen niet twee keer op;
  pagina opnieuw laden = nieuwe ronde); (4) alle vervallen facturen standaard aangevinkt, minimaal dagen vervallen
  standaard 0; (5) te verrekenen posten: alle open negatieve posten tot en met de peildatum, ook als ze nog niet
  vervallen zijn; (6) bank op de aanmaning = die van de eerste factuur, op het overzicht die van de nieuwste open
  factuur (vaste rekening gaat voor); (7) rekeningoverzicht: standaardkolommen lopend/30/60/90 (de
  ouderdomsanalyse heeft er 7), alleen audittrail; (8) Voorbeeld en PDF openen in een nieuw tabblad;
  (9) geen notitie `[email]` bij de facturen na het mailen van een aanmaning (wel status gemaild + audittrail).
  AM1 (2026-10-10, A): de betaaltermijn in de aanmaningsbrief is per niveau en taal, niet per klant (de
  betalingstermijn van de relatie bepaalt alleen de vervaldatum van de factuur).
- Betalingsspecificatie en betalingsregister (fase 4 stap 4b, SB7–SB8 op advies A), keuzes binnen A **akkoord door de gebruiker (2026-10-10)**:
  (1) naam NL "Betalingsspecificatie" voor ontvangst én betaling (EN Remittance advice, DE Zahlungsavis, FR Avis de
  paiement); in Beheer "Betalingsspecificatie ontvangst (klant)" / "... betaling (leverancier)"; (2) kolommen
  factuurnummer, factuurdatum, factuurbedrag, korting (alleen als er korting is), betaald; de rest als regel
  "Vooruitbetaling"; totaal "Totaal ontvangen"/"Totaal betaald"; de omschrijving van de betaling; bij een ontvangst
  "Ontvangen op rekening <IBAN eigen bank>", bij een betaling "Overgemaakt naar rekening <IBAN leverancier>"; geen
  bedrag in woorden; (3) standaard inleidende tekst "Wij hebben uw betaling ontvangen en als volgt verwerkt." resp.
  "Wij hebben het onderstaande bedrag aan u overgemaakt voor de volgende facturen.", geen standaard slottekst;
  (4) betalingsregister onder Kas en bank, standaardperiode vorige + lopende maand, gestorneerde betalingen en
  storno's staan er niet in (die blijven in Corrigeren), alle rollen met de module zien de lijst en het voorbeeld;
  (5) na het boeken van meerdere relaties een inklapbare lijst met per relatie een vinkje (standaard alle) en
  dezelfde drie knoppen, in plaats van knoppen per relatie; (6) vastleggen = audittrail per factuur, geen
  statusregel, voorbeeld legt niets vast; (7) mail per betaling aan het adres van de relatie (+cc/bcc), in de taal van
  de relatie; `{nummer}` = de factuurnummers, `{kenmerk}` = het betaalkenmerk; (8) de oude, uitgeschakelde knop
  "Afdrukken" op het betaalscherm is weg; (9) Voorbeeld en PDF openen in een nieuw tabblad (zoals 4a).
- Batch (fase 4 stap 4c, SB9–SB11 en SB14 op advies A), keuzes binnen A 1–7 **akkoord door de gebruiker (2026-10-10)**,
  8–11 zijn reparaties (ter kennisname):
  (1) menu: nieuwe groep **Documenten** met "Batch", zichtbaar als verkoop, inkoop, orders of offertes aan staat; de
  lijst en het voorbeeld voor alle rollen met de module, PDF en Mailen voor boekhouder en hoger (SB15);
  (2) soorten: alle 14 documentsoorten van de schermen, met de lange namen uit Beheer ("Factuur zonder artikelen" …);
  pakbon en picklijst uit verkoopfacturen (geen creditfacturen) én verkooporders, een pakbon uit een order heeft het
  label "order"; geprint/gemaild telt per soort (status met precies die `formname`);
  (3) filters: datum van/tot, relatie (naam of nummer, zoals Form::like), nummer, status alle/open/gesloten
  (verkoop/inkoop: open = niet volledig betaald; orders: niet gesloten), geprint en gemaild elk alle/ja/nee. De
  eerste keer: vorige en lopende maand, **niet geprint en niet gemaild** ("nog niet verstuurd");
  (4) lijst: op datum en nummer, maximaal 1.000 regels (daarboven een melding om te verfijnen), niets standaard
  aangevinkt, een vinkje "alles selecteren", nummer als link naar het scherm van het document;
  (5) maximaal **200 documenten per keer, ook voor Voorbeeld en PDF** (niet alleen mailen): een samengevoegde PDF
  van honderden documenten duurt te lang voor één verzoek;
  (6) PDF: lukt één document niet (niet te lezen of op te maken), dan geen PDF, een melding en niets vastgelegd;
  bestandsnaam "<soort>-<aantal>-<datum>.pdf" (één document: zoals de knop PDF); Voorbeeld en PDF in een nieuw
  tabblad, PDF als download;
  (7) mailen: elk document met zijn eigen sjabloonvariant en taal (zoals opgeslagen); onderwerp en bericht leeg =
  standaardtekst per soort in de taal van de relatie, ingevuld = voor de hele reeks met plaatshouders per document
  (knop "Overzicht bijwerken" toont het resultaat vooraf); handtekening van de gebruiker eronder; daarna zoals het
  mailscherm van één document: gemaild, notitie `[email]` en audittrail;
  (8) gedeelde mailfunctie (`routes_dunning._send_all`): een opmaakfout van één brief of document wordt nu als
  "niet gemaild" gemeld en stopt de rest niet (ook bij aanmaningen, overzichten en betalingsspecificaties; eerder
  brak de hele reeks af, wat al verzonden was bleef vastgelegd);
  (9) e-mailadressen in lijsten en overzichten worden niet meer met een koppelteken afgebroken (ook de schermen van
  4a/4b);
  (10) vergelijkingsdriver `compare/compare_am.py` bijgewerkt: hij gebruikte `routes_am.tax_add_row`, dat met BT1
  verdwenen is (run_compare liep daardoor vast bij scenario 81); de extra tariefregel wordt nu in de driver zelf
  aan het formulier toegevoegd, het opslagpad is ongewijzigd. Daarna bleek scenario 81 de BT1-weigering te raken
  (operatie 4 overschrijft een gebruikt tarief van 9%): vastgelegd als verwacht verschil (afwijking 203).
  (11) `pyproject.toml`: de sjabloonmappen `templates/batch` en `templates/dunning` (die laatste ontbrak al sinds
  4a) staan nu in de package-data, met een toets die elke sjabloonmap controleert.
- Herhalen met mailen en PDF (fase 4 stap 4d, SB12–SB13 op advies A), keuzes binnen A 1–8 **akkoord door de gebruiker (2026-10-10)**,
  9–10 zijn reparaties (ter kennisname):
  (1) keuzes per boeking = de documentsoorten van het scherm van die boeking (factuur: factuur/pakbon/picklijst;
  creditfactuur, inkoopfactuur, debetnota: alleen zichzelf; verkooporder: orderbevestiging/pakbon/picklijst;
  inkooporder: inkooporder; zonder artikelen: factuur/creditnota/inkoopboeking/debetnota; memoriaal: geen, met een
  korte uitleg), dezelfde soorten voor Mailen en PDF;
  (2) mailen ook bij verkoop/inkoop **zonder artikelen** (SL biedt dat op het scherm niet aan, maar voert het wel uit);
  (3) opslaan: formname zoals SL, formaat altijd `pdf`, printernaam bewaard maar genegeerd; het bericht hoort bij
  mailen (zonder mailsoort vervalt het); alles uitvinken wist mailen/PDF; soorten uit een Perl-schema die wij niet
  kennen blijven bewaard en staan op het scherm als "overgenomen, niet uitgevoerd";
  (4) bericht: eigen tekst per schema met plaatshouders per document (zoals Batch), leeg = standaardtekst uit Beheer >
  E-mail in de taal van de relatie; onderwerp altijd de standaard per soort; handtekening van de gebruiker die
  verwerkt;
  (5) mailen direct bij Verwerken (synchroon, zonder overzicht vooraf, zoals SL), per document een regel onder het
  schema in het resultaat ("gemaild aan …" of "niet gemaild: reden"), geen maximum;
  (6) PDF: niet tijdens het verwerken maar met de knop **"PDF van de nieuwe documenten (n)"** op de resultaatpagina
  (download, maximaal 200 zoals Batch); pas daarna geprint + audittrail; wie de pagina verlaat zonder te klikken,
  vindt de documenten terug in Documenten > Batch met "niet geprint";
  (7) rechten zoals herhalen nu (boekhouder en hoger, module Terugkerend); per document moet ook de module van dat
  document aan staan, anders "hoort niet bij de boeking; niet gemaakt";
  (8) een onverwachte fout bij mailen/PDF wordt een melding in het resultaat en het logboek, nooit een foutpagina
  (de boekingen staan er dan al);
  (9) foutregels in resultaatlijsten (`.err`) zijn nu rood; ze waren groen in het groene meldingsblok (ook bij
  aanmaningen, rekeningoverzichten, batch en betalingsspecificaties);
  (10) de teksten "afdrukken/mailen volgt in fase 4" op het schemascherm en de lijst zijn vervangen (NL/EN/DE/FR).
- Inkoopfactuur uploaden en bijlagen (fase 4 stap 4e, IU1–IU8 op advies A), keuzes binnen A 1–11 **akkoord door de gebruiker (2026-10-10)**,
  12–13 zijn reparaties/hulpmiddelen (ter kennisname):
  (1) op het uploadscherm is het bestand verplicht om te boeken ("Sleep eerst de factuur …"); zonder bestand boek je via
  Inkoop > Boeking toevoegen;
  (2) "laatste boeking" = de laatste inkoopboeking **zonder artikelen** van de leverancier op factuurdatum (bij gelijke
  datum de laatst ingevoerde); facturen met artikelen tellen niet mee, creditnota's wel; ook de afdeling komt daarvan.
  Terugrekenen op echte data: kostenrekening raak A 90,3%, C 72,8%;
  (3) btw-voorstel: had de laatste boeking geen btw, dan "geen btw" (ook als de leverancier btw-rekeningen heeft); had
  die meer btw-rekeningen, dan de grootste met een melding (splitsen in Volledig scherm); geen historie, of de rekening
  hoort niet meer bij de leverancier: de gekoppelde (twee of meer: kiezen). Raak: A 93,4%, C 86,5%;
  (4) vaste kostenrekening op het leveranciersscherm bij "Voorwaarden en rekeningen", alleen rekeningen voor
  inkoopregels (koppeling AP_amount); gaat mee met "Opslaan als nieuw" en verdwijnt met de leverancier;
  (5) korte scherm: de valuta is die van de leverancier (wijzigen, project, meer regels of een betaling: Volledig
  scherm); bij vreemde valuta een voorgestelde koers;
  (6) na Boeken een leeg scherm (leverancier niet onthouden) met een melding en link naar de boeking; de PDF-viewer
  opent op paginabreedte zonder miniaturen;
  (7) bijlagen bij een opgeslagen boeking: meteen toevoegen/verwijderen, ook in een afgesloten periode of bij een
  boeking die alleen tegengeboekt kan worden; verwijderen na een bevestigingsvraag van de browser; met de rol
  alleen-lezen alleen bekijken en downloaden;
  (8) bij een nieuwe boeking blijft een gesleept bestand tot Opslaan in `<data_dir>/uploads` (alleen voor dezelfde
  gebruiker en administratie), en wordt na een dag opgeruimd als er niet geboekt is;
  (9) omschrijving van een bijlage standaard de bestandsnaam; "Vertrouwelijk (alleen voor mij)" optioneel, zoals SL;
  (10) bereik: het blok Bijlagen staat op verkoop- en inkoopboekingen zonder artikelen en op het memoriaal (IU6);
  facturen met artikelen, orders, relaties en artikelen volgen in stap 6. Referenties zonder bestand (SL) blijven
  apart en ingeklapt onder "Referentiedocumenten";
  (11) app-logboek: toevoegen, verwijderen én bekijken van een bijlage (`attach_added`, `attach_deleted`,
  `attach_viewed`);
  (12) reparatie: een archiefstuk wordt bij opnieuw opslaan of verwijderen van een boeking alleen nog gewist als geen
  andere verwijzing er naar wijst (Perl-fout 28, ook voor bijlagen die SL via "Koppelen aan" deelde);
  (13) vergelijkingsdriver: stappen `ref_save`/`ref_get` (SL's eigen `Form::save_reference`/`get_reference`) en
  `compare/compare_attach.py`; terugrekenen voorstel: `compare/apupload_hitrate.py`.
  (14) reparatie na de keuring: de bestandsknop van de browser ("Choose file / No file chosen") volgt de taal van
  de browser; vervangen door een eigen knop "Bestand kiezen" in de taal van de gebruiker (`templates/_file.html`,
  `static/filepick.js`), ook bij Bankafschrift inlezen en Beheer > Sjablonen (sjabloon en logo).
- Maximale uploadgrootte (fase 4 stap 4f, MU1–MU5 op advies A, BB1–BB4 A voor later), keuzes binnen A 1–6 **akkoord
  door de gebruiker (2026-10-10)**:
  (1) door SQL-Ledger 4.x geschreven waarden: 0 (leeg veld) = standaard 10 MB, een breuk naar beneden afgerond (2,5 → 2),
  boven 25 → 25; het scherm toont wat geldt, en ongewijzigd opslaan laat de waarde van SQL-Ledger 4.x staan;
  (2) 1 MB = 1.048.576 bytes, zoals de bestaande grenzen in de app;
  (3) de grens geldt ook bij het boeken: verlaagt de beheerder hem terwijl er een nog niet opgeslagen bijlage klaarstaat,
  dan weigert Opslaan die met dezelfde melding;
  (4) elk verzoek met een bestand boven 25 MB (+ 512 kB formulier) wordt meteen geweigerd ("groter dan 25 MB"); de grens
  van de administratie zelf controleert de server na ontvangst (dus zonder de browsercontrole wordt een bestand tussen
  die grens en 25 MB nog wel verstuurd, en daarna geweigerd);
  (5) plek: Beheer > Instellingen, blok Boekhouding, na "Map/url referentiedocumenten", alleen beheerder;
  leeg = 10 MB; app-logboek via `am_settings_saved` (sleutel `max_upload_size`);
  (6) browsercontrole: een te groot bestand wordt niet verstuurd; rode melding in het sleepvak en de keuze leeggemaakt.
- Rapporten als PDF (fase 4 stap 5, RP1–RP10 alles A), keuzes binnen A 1–7 **akkoord door de gebruiker (2026-10-11)**:
  (1) staand of liggend volgt uit de gemeten breedte van de inhoud (per kolom langste celtekst of langste woord van de
  kop; tekstkolommen tellen hoogstens 34 tekens) in plaats van een vast aantal kolommen: proefbalans en balans staand,
  rekeningmutaties (8 kolommen met nummer/omschrijving/kenmerk) en verkoopoverzichten liggend; past het liggend niet,
  dan kleiner tot 6,5 pt en lopen teksten door op een volgende regel; bedragen breken nooit af, kolomkoppen wel
  ("01-01-2012 t/m / 31-12-2012");
  (2) de PDF opent in een nieuw tabblad (niet als download); bij meer dan 5.000 regels toont dat tabblad de melding met
  een link "Terug" naar hetzelfde rapport (HTTP 413), zonder logregel;
  (3) de grens telt alle regels van de tabel(len), ook kop-, subtotaal- en totaalregels en het beginsaldo;
  (4) de voet toont "Afgedrukt op <datum> <tijd>" (datumnotatie van de gebruiker, tijd van de server), geen gebruikersnaam;
  (5) lijsten zonder periode (relaties, artikelen, orders, stuklijsten) krijgen alleen de titel en de administratie in de
  kop, geen filterregels (zoals hun Excel-export); rapporten en afgeletterde posten tonen periode en filters zoals op
  het scherm; het bankafschrift gebruikt de titel van het afschrift, de boekingspagina "Verkoop/Inkoop/Memoriaal
  <nummer>" met datum, relatie en omschrijving;
  (6) cellen zijn platte tekst: doorkliklinks vallen weg; een vinkje (✓) blijft;
  (7) pypdf is toegevoegd als testafhankelijkheid (`[test]` in pyproject), alleen voor de toetsen.
- Dashboard (fase 4 stap 5b, DB1–DB13 alles A), keuzes binnen A **akkoord door de gebruiker (2026-10-11)**:
  (1) boekjaar = kalenderjaar (zoals de rapporten; SL kent geen afwijkend boekjaar); **vervallen in bouwstap 2: het
  dashboard volgt het ingestelde boekjaar (JA6)**;
  (2) "deze maand" = de 1e t/m vandaag, vergeleken met dezelfde dagen vorig jaar (niet de hele maand vorig jaar), net als
  dit jaar t/m vandaag; in de grafiek staat vorig jaar wel per hele maand;
  (3) te ontvangen/te betalen in de standaardvaluta (de ouderdomsanalyse toont per valuta); de tegel klikt naar de
  ouderdomsanalyse "vanaf factuurdatum" (zelfde verzameling), de grafiek naar "vanaf vervaldatum" per factuur (die laat
  niet-vervallen facturen weg, zoals SL);
  (4) ouderdom vanaf de vervaldatum: vervaldatum vandaag of later = niet vervallen; zonder vervaldatum telt de factuurdatum;
  (5) "binnen 7 dagen": vervaldatum vandaag t/m over 7 dagen, alleen bij inkoop;
  (6) bank: saldo per vandaag (boekingen met een latere datum tellen niet); een rekening zonder saldo, zonder open
  bankregels en zonder mutaties in de laatste 365 dagen wordt verborgen ("+ n rekeningen");
  (7) btw: alleen het laatst afgesloten tijdvak en alleen als daarin boekingen staan; jaaraangifte vóór 31 maart; na de
  termijn "te laat" tot het volgende tijdvak afgesloten is (daarna gaat de herinnering over dat nieuwe tijdvak); de link
  gaat naar Btw verkoop (of Btw inkoop als verkoop uit staat);
  (8) alleen-lezen: bankregels en terugkerend zonder link, inkoopfacturen naar het rapport Openstaande inkoopfacturen;
  (9) artikelen onder bestelniveau = voorraad < bestelniveau, zoals de artikellijst "tekort" (ook vervallen artikelen);
  (10) orders: open verkooporders met een leverdatum vóór vandaag (zonder leverdatum tellen ze niet); de link opent de
  open verkooporders gesorteerd op leverdatum (10 A; de orderlijst heeft geen filter "leverdatum vóór");
  (11) terugkerend: schema's die klaarstaan (volgende datum vandaag of eerder), alleen met module Terugkerend en als de
  module van de boeking aan staat;
  (12) een blok dat niet laadt toont "Dit blok kon niet worden geladen." (details in het serverlog), de rest werkt door;
  12 A: ook bij een mislukt verzoek in de browser (`static/dashboard.js`), een verlopen sessie laadt de hele pagina
  (HX-Redirect), een databasefout in een blok toont geen servergegevens;
  (13) "Over deze administratie" is ingeklapt; de waarschuwing "geen medewerker" blijft zichtbaar.
- Aanmaningen en rekeningoverzichten (wens gebruiker 2026-10-11, gebouwd met het dashboard, **akkoord**): de schermen openen met
  niets aangevinkt; vinkje "alles selecteren" in de kolomkop (was: alles vooraf aangevinkt, stap 4a).
- Btw-tarieven (vraag gebruiker 2026-10-10): beslispunten **BT1–BT2** in `FASE4_ANALYSE.md` akkoord, beide A; BT1 gebouwd (afwijking 203).
- Btw-aangifte (vraag gebruiker 2026-10-11): er is geen aangiftemodule (SQL-Ledger ook niet; de rapporten Btw
  verkoop/inkoop geven de bedragen). Geadviseerd: eerst analyse voor een overzicht per rubriek (1a–5g, eventueel ICP)
  met koppeling btw-rekening → rubriek; geen digitaal indienen (SBR/Digipoort). Besluit 2026-10-11: alleen een overzicht
  (uitdraai per maand/kwartaal zoals Exact) plus een btw-boeking na de aangifte; bouwen na stap 6, vóór stap 7.
  Analyse BA1–BA9 in `BTW_ANALYSE.md`: akkoord, alles A (2026-10-11).
- Bijlagen op andere schermen (fase 4 stap 6a, RB1/RB2/RB6/RB7 op advies A), keuzes binnen A **akkoord door de gebruiker (2026-10-11)**:
  (1) nieuwe factuur/order/offerte: slepen kan meteen (zoals 4e bij boekingen); nieuwe relatie/artikel: pas na
  opslaan (die schermen hebben geen live verversen; melding in het blok);
  (2) omzetten: op het nieuwe scherm staan de meegekomen bijlagen als "gaat mee bij Opslaan" met "weghalen"; na het
  opslaan toont elke bijlage "ook bij <soort> <nummer> (<naam>)" met een link, in beide richtingen (order ↔ offerte ↔
  factuur), alleen voor verwijzingen die je mag zien;
  (3) vertrouwelijke bijlagen gaan bij omzetten alleen mee voor wie ze toevoegde (de ander ziet ze niet, zoals SL);
  (4) gesloten order/offerte, gefactureerde order, factuur in een afgesloten periode: bijlage toevoegen/verwijderen
  mag (RB6), het record zelf blijft alleen-lezen;
  (5) blok staat onder het formulier, op alle vier schermen gelijk (bekijken, downloaden, verwijderen, toevoegen met
  omschrijving en vertrouwelijk);
  (6) toevoegen controleert de soort tegen het record (offerte/order, klant/leverancier, artikel/dienst/…): een
  verkeerde soort = "niet gevonden";
  (7) een archiefnummer uit het formulier wordt alleen gekoppeld als je dat stuk mag zien of als het een los stuk
  zonder verwijzing is (anders blijft alleen de omschrijving); beschermt tegen een gemanipuleerd verborgen veld;
  (8) verwijderen haalt ook vertrouwelijke verwijzingen van anderen weg (RB7) — geldt voor álle verwijderingen
  (boekingen, memoriaal, facturen, orders, nabestelling, relaties, artikelen, projecten), niet alleen de vier nieuwe;
  (9) reparatie 4e: bijlage op een creditnota/debetnota bleef na opnieuw opslaan staan maar was niet meer te
  verwijderen (formname `ar_credit_note`); de app kent nu ook de formnames die SL zelf schrijft;
  (10) labels "ook bij": Verkoopboeking, Creditnota, Verkoopfactuur, Creditfactuur, Inkoopboeking, Debetnota,
  Inkoopfactuur, Debetnota met artikelen, Memoriaalboeking, Verkooporder, Inkooporder, Offerte, Offerteaanvraag, Klant,
  Leverancier, Artikel, Dienst, Samengesteld artikel, Loon/overhead, Kit.
- Foto's verkleinen en dubbele bestanden (fase 4 stap 6b, RB4/RB5 op advies A), keuzes binnen A **akkoord door de gebruiker (2026-10-11)**:
  (1) grens 120 miljoen beeldpunten (108-megapixelcamera's gaan nog; Pillow weigert zelf pas boven ±179 miljoen);
  (2) kleine foto's (≤ 2.400 px, rechtop): metadata eruit zonder opnieuw comprimeren; kleurprofiel (ICC), JFIF en
  Adobe-kleurruimte blijven;
  (3) een gedraaide foto (EXIF-oriëntatie) wordt altijd opnieuw opgeslagen, ook als dat groter uitvalt (anders staat
  hij zonder EXIF scheef);
  (4) een tijdelijk bestand (nieuwe boeking, uploadscherm) wordt meteen bij het uploaden verkleind: het voorbeeld toont
  wat bewaard wordt; de maximale uploadgrootte geldt voor het origineel;
  (5) hash van het opgeslagen (verkleinde) bestand: dezelfde foto nog eens geeft dezelfde hash; een foto die
  SQL-Ledger onverkleind bewaarde wordt niet als dubbel herkend;
  (6) opgeslagen record: aparte bevestigingspagina; nieuwe boeking en uploadscherm: waarschuwing bij het bestand met
  vinkje, Opslaan/Boeken geweigerd zonder vinkje;
  (7) hetzelfde bestand bij hetzelfde record: geweigerd; een vertrouwelijke bijlage van een ander bij hetzelfde record:
  alleen de waarschuwing (niet verraden waar);
  (8) een los SL-archiefstuk (zonder koppeling) met dezelfde inhoud: waarschuwing "een los document";
  (9) bedrag in de waarschuwing met de valutacode zoals opgeslagen ("EUR 121,00");
  (10) bestaande archiefstukken zonder hash krijgen die bij de eerste controle (A/C hebben er 0);
  (11) Pillow staat bij de webapp (`pyproject` web); de bibliotheek zonder Pillow haalt alleen de metadata weg;
  (12) logboek: `attach_added` met `duplicate=true` als iemand "toch toevoegen" koos.
- Overzicht bijlagen (fase 4 stap 6c, RB3 op advies A), keuzes binnen A **akkoord door de gebruiker (2026-10-11)**:
  (1) plek: menu Rapporten > Bijlagen, adres `/bijlagen`; zichtbaar met module Rapporten, per regel alleen als de module
  van dat scherm aan staat;
  (2) "toegevoegd" uit een eigen tabel (`x_archive_added`, migratie 12: datum, tijd, gebruiker bij elk nieuw
  archiefstuk), niet uit het logboek (dat kent bijlagen die met een nieuwe boeking meekomen niet); van vóór 6c en uit SQL-
  Ledger: leeg;
  (3) drie kolommen: toegevoegd · bestand (naam, vertrouwelijk, omschrijving, downloaden) · gekoppeld aan (link); geen
  bestandsgrootte;
  (4) filters: één tekstveld voor omschrijving én bestandsnaam, soort, gekoppeld aan, periode; volgorde vast: nieuwste
  eerst, zonder datum achteraan;
  (5) hoogstens 1.000 regels met een melding; geen CSV/Excel/PDF-knop;
  (6) hangt één bestand aan meer records (omzetten), dan één regel per koppeling;
  (7) los document: openen voor iedereen met Rapporten, weggooien alleen de beheerder (logboek `attach_deleted`,
  `loose`);
  (8) soort "Overig (uit SQL-Ledger)": verwijzingen bij projecten, urenstaten e.d., zonder link;
  (9) vertrouwelijke bijlagen van een ander: niet getoond en niet meegeteld.
- Menu (MN1–MN7 op advies A), keuzes binnen A **akkoord door de gebruiker (2026-10-11)**:
  (1) Btw-aangifte en Btw-rubrieken staan er al, grijs ("volgt in fase 4"), tot de btw-aangifte gebouwd is;
  (2) rubriek Btw zichtbaar als verkoop of inkoop aan staat, per regel de eigen module (Btw verkoop/Onbelaste omzet
  bij Verkoop, Btw inkoop/Onbelaste inkoop bij Inkoop); de beheerder houdt Btw-tarieven ook zonder die modules;
  kopjes in de rubriek: Overzichten · Beheer (alleen beheerder);
  (3) Inkoop: kopjes Boeken · Overzichten · Leveranciers (geen tegenhanger van Debiteurenbeheer, Inkoop heeft geen
  aanmaningen of rekeningoverzichten);
  (4) Kas en bank: Koersverschilboeking onder Corrigeren, Afletteren onder Bank, Afgeletterde posten onder Overzichten;
  (5) Beheer: kopje "Valuta" in plaats van "Btw en valuta" (btw is verhuisd); menu-item "Btw" heet nu "Btw-tarieven";
  (6) "Documenten in batch" ook onder Orders en Offertes (anders verdwijnt het bij een administratie zonder verkoop en
  inkoop), elk met de eigen soort voorgekozen (factuur, inkoopfactuur, verkooporder, offerte); `?soort=` uit het menu
  houdt de standaardfilters (niet geprint/gemaild); na Toon licht Verkoop op;
  (7) een kopje verschijnt alleen als er voor die rol iets onder staat (alleen-lezen ziet in Verkoop geen "Boeken");
  kopjes klein, in hoofdletters, grijs; menu-kopje "Boeken" heet in het Engels "Entry" (niet "Post");
  (8) de meldingen bij terugkerende boekingen verwijzen nu naar "Verkoop > Documenten in batch".
- Niet vertaald: `cli.py` (beheerdersopdrachten).
