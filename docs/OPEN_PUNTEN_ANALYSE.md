# Open punten na v1.2.0 — analyse (2026-10-13)

Werkvoorraad uit OVERDRACHT: wankele toets, ADM-15, vondsten van de installatie (a–c), golf 3 V4 F23–F27,
golf 4 V2 F39–F44. Per punt: wat er is, beslispunten, **advies**.

**AKKOORD 2026-10-13: alles A** (AD2: administratie C (UK) zo laten; F27b: alleen ontkoppelen). Pakket 1 bouwen in een nieuwe
chat (prompt `NIEUWE_CHAT_PROMPT.md`), daarna pakket 2 en 3.

## 1. Wankele toets `test_web_start.py::test_language_choice_and_admin_form`

**Oorzaak gevonden: geen volgorde- of gedeelde-staatfout.** De volledige run van 13-10 (~18:54–19:05, uitvoer
`~/sqlledger-test/pytest_v120.txt`) liep in de hoofdmap. Om 19:02:23 zette een andere chat `teaser-naam` met een
fast-forward in `main`; daarin zat 7e334f2 ("bijvoorbeeld <voornaam>" → "bijvoorbeeld jsmith" in `start/admin.html`,
`i18n_*.py` en de toets). Python had `i18n_de.py` al geladen (oude sleutel), Jinja laadt een sjabloon pas bij het
eerste gebruik en las dus de nieuwe `admin.html` (nieuwe sleutel) → geen vertaling → Nederlandse uitleg → assert
faalt. Pytest toont de broncode bij een fout opnieuw van schijf, daarom stond de nieuwe toetsregel in de melding.
De tweede volledige run (na de merge) was groen; los en per map ook.

- **W1** A: geen codewijziging; werkwijze: volledige run altijd in een eigen worktree (of in de hoofdmap alleen als
  geen andere chat daar samenvoegt), en niet in `main` samenvoegen terwijl daar een run loopt. B: daarnaast een
  bewaking in `conftest.py` (bij afloop waarschuwen als `git rev-parse HEAD` veranderd is). **Advies A** (B is
  hooguit een kleine extra). De BE-chat is al gewaarschuwd.

## 2. ADM-15 in PyBalans (koersen met alleen een verkoopkoers)

`Pg-upgrade-2.9.1-2.9.2.sql` r3 neemt alleen `buy` over en gooit rijen met `buy = 0` weg (`slupgrade.py` draait de
SL-scripts ongewijzigd). Zelfde in Perl 3.2.12 (ADM-15, gepusht 4d9e602).

- **AD1 Hoe repareren.** A: correctie in `slupgrade.py` (tabel `PATCHES`: script + exact oorspronkelijk statement →
  `CASE WHEN coalesce(buy,0) <> 0 THEN buy ELSE sell END`); het .sql-bestand blijft ongewijzigd, een toets
  controleert dat het oorspronkelijke statement er nog staat (anders faalt de correctie stil) en dat een koers met
  alleen `sell` overleeft; README-afwijking. B: het .sql-bestand zelf aanpassen. **Advies A.**
- **AD2 administratie C (UK) (104 koersen al weg).** A: zo laten — slapende bv sinds 2016, boekingen hebben hun eigen koers;
  de koersentabel is alleen een voorstel bij nieuwe stukken. B: na de fix opnieuw inlezen naast de bestaande
  (adm_c_2), vergelijken, dan wisselen (vraagt F27 "ontkoppelen"). C: eenmalig de 104 rijen uit de SL-back-up
  bijvoegen in de eigen installatie (back-up eerst, bewust). **Advies A**; C als je de tabel compleet wilt.

## 3. Kleine vondsten van de installatie

- **K1 (a) Back-up inlezen in het menu Systeem.** A: item "Back-up inlezen" (`/systeem/back-up`) onder
  "Back-up maken" in `SYSTEM_GROUP` (`menu.py:211`); tekst bestaat al in EN/DE/FR. B: ook onder Beheer (daar zocht
  je) — maar inlezen is systeembeheer, geen administratiebeheer. **Advies A.**
- **K2 (b) Ruwe sjablonen bij "Nummering controleren"** (`routes_switch.py:263–265` toont `defaults` letterlijk,
  bv. `20<%yy%>-14337`). A: het **volgende** nummer ingevuld tonen ("volgende verkoopfactuur 2026-14338"), via
  `numbering` zonder op te slaan, sjabloon klein erachter. B: het laatst gebruikte nummer ingevuld met de datum
  van vandaag (kan misleiden: het jaar is dat van vandaag). C: laten en uitleggen. **Advies A.** Beheer >
  Instellingen blijft het sjabloon tonen (dat is het invulveld, zoals SL).
- **K3 (c) Controlescherm: back-up bestaat al.** Elke ingelezen administratie bewaart herkomst (dataset, versie,
  back-updatum; `switchover.record_restore`). A: bij het controlescherm de bestaande administraties nalopen en een
  **waarschuwing** tonen (niet blokkeren): "Deze back-up is al ingelezen als *X*" (zelfde dataset en datum) of
  "Er is al een administratie uit dataset *Y*: *X*" (andere datum); ook bij gelijke databasenaam. B: blokkeren.
  **Advies A** (naast elkaar inlezen moet kunnen, bv. ter vergelijking). Waarom naam/databasenaam bij Cookie leeg
  bleven, zoek ik bij het bouwen uit.
- **K4 Meegenomen:** `sqlledger/vatreturn.py:28` zegt nog "VK box 1–5 in pence"; sinds het besluit van 13-10
  (elke aangifte in hele bedragen) klopt dat niet meer → tekst rechtzetten. **Advies: doen.**

## 4a. Golf 3 V4 (F22 is al af met het boekjaar)

**Stand 2026-10-13 nacht: F23–F26 (pakket 2) GEBOUWD op branch `pakket2`**, keuzes binnen A 1–10 in README Open punten,
ter keuring. Perl: alleen F24 → ADM-16 (findings lokaal, niet gepusht).

- **F23 Koppelen vanuit Systeem zonder beheerder.** Het vinkje "Mij als beheerder" staat alleen in de wizard
  (`company_edit.html:30`); `company_save` geeft het niet door (`routes_system.py:501`) → niemand heeft toegang, geen
  melding. Ook ontbreekt de controle "database al gekoppeld" (restore heeft die wel). A: vinkje ook in Systeem,
  standaard aan; melding "niemand heeft toegang" op lijst en scherm; dubbele databasenaam weigeren. B: altijd
  jezelf beheerder maken. C: systeembeheerder overal toegang. **Advies A.** Perl: kan niet zonder gebruiker.
- **F24 Databasenaam wijzigen zonder waarschuwing.** Fout wordt ingeslikt (`routes_system.py:446–454`); kan naar
  een niet-bestaande of andermans database wijzen (twee administraties op één database, sjablonen/mail/rechten van
  de een op de boeken van de ander); oude naam niet gelogd. A: bij wijzigen moet de database bereikbaar zijn,
  geen andere administratie op dezelfde database, bevestigstap oud → nieuw met bedrijfsnaam uit beide, oude
  waarden in het logboek. B: zoals Perl alleen-lezen + aparte knop "Andere database". **Advies A.**
- **F25 Ingelezen back-up krijgt documenttaal nl** (kolomstandaard, `routes_restore.py:314–317`); ook `vat_return`/
  `icp` staan uit tot iemand de btw-instellingen opslaat (Btw-menu ontbreekt eerst). A: keuze "Documenttaal" op
  het controlescherm (voorinvullen: taal uit de back-up als die er is, anders gebruikerstaal, anders nl); de taal
  voortaan ook in de administratie zelf bewaren (`x_setting`, geen nieuwe migratie) zodat een PyBalans-back-up
  hem meeneemt; na inlezen btw/ICP-vlaggen meteen bepalen. B: alleen gebruikerstaal. **Advies A.**
- **F26 Sjabloonvariant niet te verwijderen na "Terug naar standaard".** Reset bewaart vorige versies in de map;
  `remove_variant` weigert zodra de map niet leeg is (`documents.py:641–649`) — de toets omzeilt dat zelf
  (`test_web_doctemplates.py:136–137`). A: alleen weigeren als er nog een eigen `*.html` is; anders map met vorige
  versies weg, na bevestiging. B: versies naar een archiefmap. **Advies A.**
- **F27 Gebruikers en administraties niet te verwijderen** (help zegt dat bewust; uitzetten kan wel).
  - **F27a Gebruikers.** A: echt verwijderen alleen voor wie nooit is ingelogd (verkeerd aangemaakt); verder
    uitzetten (lidmaatschappen en sessies weg) met filter "ook uitgezette tonen"; niet jezelf, niet de laatste
    systeembeheerder; waarschuwen bij hergebruik van een oude login. B: altijd echt verwijderen. **Advies A.**
  - **F27b Administraties.** Alleen als uitgezet en geen inleestaak loopt, met bevestiging door de naam te typen.
    A: **ontkoppelen** (rij weg, sjablonen- en mailmap weg, naam blijft leesbaar in het logboek; database blijft
    op de server). B: ontkoppelen + optioneel "ook de database verwijderen" (DROP, databasenaam typen,
    waarschuwing als er geen recente back-up is). **Advies A** — weggooien van een database met echte boeken
    hoort niet achter één knop; B kan later.

## 4b. Golf 4 V2

- **F39 Prijshistorie mengt prijsbases.** Factuurregels: netto prijs na korting (en zonder btw bij "inclusief"),
  in standaardvaluta; orderregels: ingevoerde prijs vóór korting, in standaardvaluta. Kolom "Valuta" toont de
  documentvaluta terwijl de bedragen al omgerekend zijn. "Regels uit" (`parts.py:740`) rekent orders zelfs niet om.
  A: één basis — ingevoerde prijs (`fxsellprice` × koers, terugval `sellprice` voor oude SL-regels) + "Korting %",
  kolomkop "Prijs (EUR)"; ook in "Regels uit". B: twee kolommen (ingevoerd / netto). C: alleen helptekst.
  **Advies A.**
- **F40 Geen link naar orders/offertes** in prijshistorie en "Regels uit" (Perl heeft die wel, `ic.pl:4567`,
  `2022`). A: link naar `/verkooporder/{id}` e.d. als de module aanstaat (opent zoals vanuit de orderlijst, met
  lock voor boekhouders). B: alleen-lezenvariant van het orderscherm. **Advies A**; helpteksten bijwerken.
- **F41 Voorraadcorrectie en assembleren negeren de afsluitdatum** (vrije datum is een PyBalans-toevoeging; Perl
  gebruikt altijd vandaag). A: weigeren vóór/op de afsluitdatum zoals bij urenregistratie
  (`timecards.py:200`). B: datum weer vast op vandaag. **Advies A.**
- **F42 "Vertrouwelijk" hangt aan de medewerkerlogin** (zoals SL: `reference.login`). Mis bij: medewerker van een
  gebruiker wijzigen (eigen vertrouwelijke bijlagen weg, of naar wie die login nu heeft; zonder eigenaar voor
  niemand meer zichtbaar) en bij twee gebruikers op dezelfde medewerker (zien elkaars "alleen voor mij").
  A: in Systeem > Gebruikers weigeren dat twee actieve gebruikers in één administratie dezelfde medewerker
  hebben; bij wijzigen tonen "N vertrouwelijke bijlagen onder login X" met aanbod mee te verhuizen; label
  "alleen voor medewerker {login}". B: daarnaast beheerder kan wezen vrijgeven. C: eigenaar per app-gebruiker
  (eigen tabel). **Advies A**, B later.
- **F43 Datumfilter Bijlagen laat SL-bestanden stil weg** (geen datum; help zegt het wel). A: melding "N bijlagen
  zonder datum (uit SQL-Ledger) niet getoond — tonen". B: boekdatum als terugval. C: vinkje "ook zonder datum".
  **Advies A.**
- **F44 Tijdelijk bestand blijft na "Annuleren" op "hangt al elders"** (Annuleren is een gewone link; opruimen
  gebeurt alleen bij een volgende upload na 24 uur; ook een lek als koppelen faalt). A: Annuleren als POST die het
  bestand weggooit, `try/finally` bij koppelen, plus opruimen bij het starten van de server. B: alleen opruimen
  bij start. **Advies A.**

## 5. Perl 3.2.12 (sql-ledger-findings)

- **PF1 INV-22 corrigeren.** Daar staat dat de factuurprijs "in de factuurvaluta" is; in 3.2.12 (`IS.pm:1117`,
  `IR.pm:939`) wordt hij vóór opslaan omgerekend naar de
  standaardvaluta. Het deel over korting/btw klopt. A: tekst rechtzetten (lokaal commit, push na je "ja").
  **Advies A.**
- **PF2 Nieuw item:** in Perl blijft een geüpload bestand in `userspath` staan als je het formulier verlaat
  zonder op te slaan (`Form.pm:81–96`; alleen `save_reference` ruimt op) — nooit opgeruimd. Bij het bouwen van
  F44 in 3.2.12 nagaan; zo ja item in hoofdstuk 09 (push na "ja"). **Advies: doen.**
- F41, F42 (gedeelde login), F23–F27: alleen PyBalans (Perl kent het probleem niet of anders).

## 6. Volgorde en omvang

| Pakket | Inhoud | Omvang |
|---|---|---|
| 1 | W1, AD1, K1–K4, F41, F43, F44, F40, F39, PF1/PF2 | klein, ~1 sessie |
| 2 | F23, F24, F25, F26 | middel, ~1 sessie |
| 3 | F27a/b, F42 | middel–groot, eigen sessie |

Elk pakket op een eigen branch/worktree, volledige toetsen in die worktree, helpteksten en EN/DE/FR mee,
browsercontrole op de testcluster (55432), daarna in `main`; pas installeren in de eigen installatie als v1.2.1 na
akkoord (back-up → label → herstart).
