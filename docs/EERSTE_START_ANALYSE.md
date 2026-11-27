# Eerste start — analyse en beslispunten

**Status: akkoord gebruiker 2026-10-11, ES1–ES9 alles A.** Bouw direct na de NL-schema's (deelstap 2), vóór CH/VK/FR/BE.

Wens gebruiker (2026-10-11): wie de applicatie voor het eerst start, krijgt een welkomstscherm in het Engels,
kiest de taal, maakt de beheerder aan, kiest dan **nieuwe administratie** (met rekeningschema) of **back-up
inlezen**, en maakt daarna eventueel extra gebruikers aan.

## Hoe het nu gaat

1. Op de server: `sqlledger-web init-db` en `sqlledger-web create-admin <login>` (opdrachtregel). Dat geeft een
   eenmalige code (geldig `setup_code_hours`).
2. In de browser: `/activeren` met login + code → wachtwoord kiezen → `/login` → tweestapsverificatie instellen
   (verplicht voor systeembeheerders). De taal van de gebruiker volgt bij activeren de browser (`Accept-Language`).
3. Systeem > Administraties: **Nieuwe administratie** (land + schema, sinds deelstap 1) of **Bestaande database
   koppelen**. Back-up inlezen bestaat nog niet (stap 7 / fase 6, zie `sqlledger-backup-import`).
4. Systeem > Gebruikers: per gebruiker een eenmalige code, die de gebruiker op `/activeren` gebruikt.

Alle bouwstenen bestaan dus al. Wat ontbreekt: een begeleide route in de browser zonder opdrachtregel, en een
duidelijke volgorde.

## Voorgestelde route

```
Welcome (Engels, taalkeuze) → beheerder aanmaken → 2FA instellen → administratie: nieuw | back-up | koppelen
      → gebruikers uitnodigen (overslaan mag) → dashboard van de nieuwe administratie (met startlijst)
```

## Beslispunten (advies steeds A)

**ES1 Wanneer verschijnt de wizard.** A: alleen zolang de app-database **nog geen enkele gebruiker** heeft; elke
pagina leidt dan naar `/welkom`. De app-database wordt bij het starten zelf aangemaakt/bijgewerkt (nu
`init-db`). Zodra de beheerder bestaat, is `/welkom` dicht; de rest van de wizard loopt als ingelogde beheerder.
B: blijft opdrachtregel (`create-admin`), alleen de stappen daarna begeleid.

**ES2 Beveiliging van de eerste stap.** Een open welkomstscherm betekent: wie het eerst de server bereikt, wordt
beheerder. A: de server zet bij het starten (zolang er geen gebruiker is) een **eenmalige startcode** in het
log/de console (`docker compose logs` laat hem zien); het welkomstscherm vraagt die code. Zo werkt het ook bij
Grafana/Jupyter-achtige pakketten. B: alleen vanaf dezelfde computer (localhost) zonder code. C: geen controle.

**ES3 Taal.** A: welkomstscherm in het **Engels** met keuze Nederlands/English/Deutsch/Français (voorgeselecteerd
op de browsertaal); vanaf de volgende stap alles in die taal, en het wordt de taal van de beheerder (getal- en
datumnotatie erbij, zoals nu bij activeren). B: welkomstscherm meteen in de browsertaal.

**ES4 Beheerder aanmaken.** A: login, naam, e-mail, wachtwoord (twee keer), daarna direct **2FA instellen**
(verplicht, zoals nu voor systeembeheerders). De beheerder is systeembeheerder. B: 2FA mag later.

**ES5 Administratie.** A: drie keuzes op één scherm:
- **Nieuwe administratie** — het bestaande scherm; land en schema voorgekozen op de taal
  (nl → Nederland – Eenmanszaak/vof, en → Verenigd Koninkrijk, fr → Frankrijk, de → leeg schema tot DE er is);
- **Back-up inlezen** — zichtbaar maar nog niet te kiezen ("komt in een volgende versie") tot stap 7 er is;
- **Bestaande database koppelen** — het bestaande formulier (voor wie al een SQL-Ledger-database heeft).

B: alleen nieuwe administratie; back-up en koppelen pas via Systeem.

**ES6 De beheerder werkt zelf in de administratie.** A: de beheerder wordt standaard **gebruiker van de nieuwe
administratie met rol beheerder** (vinkje staat aan, kan uit). B: alleen systeembeheerder; boekhouders apart.

**ES7 Extra gebruikers.** A: stap **Gebruikers uitnodigen** (overslaan mag): per regel naam, login, e-mail en
rol in deze administratie; elke gebruiker krijgt de eenmalige code zoals nu, **getoond** en, als e-mail is
ingesteld, **gemaild**; de gebruiker kiest zelf wachtwoord en (afhankelijk van de rol) 2FA op `/activeren`.
B: geen stap; gebruikers later via Systeem > Gebruikers.

**ES8 Afronden.** A: naar het **dashboard** van de nieuwe administratie met een **startlijst** (bedrijfsgegevens en
logo, btw-tijdvak, bankrekening/IBAN, beginbalans, eerste factuur) die verdwijnt als alles gedaan is of
weggeklikt. B: alleen het dashboard, met de bestaande melding naar Beheer > Instellingen.
*Aanvulling gebruiker (2026-10-11):* de startlijst krijgt ook een **huisstijlscherm** (logo, kleur, lettertype,
ligging van logo en adresblok, zonder HTML), net als Sjablonen en Documentinstellingen **per administratie**. Eerst
een korte eigen analyse met beslispunten (o.a. huisstijl overnemen van een andere administratie).

**ES9 Planning.** A: direct na de NL-schema's (deelstap 2), vóór CH/VK/FR/BE: het is de ingang voor elke nieuwe
gebruiker en de NL-schema's zijn er dan; back-up inlezen sluit later aan (stap 7). B: na alle landschema's.

## Raakvlakken

- Rekeningschema's (`REKENINGSCHEMA_ANALYSE.md` RS1/RS2): de wizard gebruikt `newcompany` en `chartfiles`.
- Back-up inlezen (stap 7 / fase 6): de knop in ES5 gaat dan open.
- Beveiliging: zelfde regels als nu (eenmalige codes, 2FA, IP-blokkade bij te veel pogingen); de startcode in ES2
  valt onder dezelfde blokkade.
- Vertalingen: alle nieuwe teksten meteen EN/DE/FR.

## Keuzes binnen A (uitwerking — 1–16 akkoord 2026-10-11, 7 met correctie leeg-de)

Stand van de code: `init-db`/`create-admin` alleen via de opdrachtregel (`cli.py`); `main.py` maakt de app-database
niet zelf aan; `/activeren` + `/2fa/instellen` + `finish_login` → `/administratie`; nieuwe administratie =
`routes_system.company_create` (al met vinkje "ik werk zelf hierin" = `add_me`, rol beheerder); gebruikers =
`/systeem/gebruikers/nieuw` + `_code_page`; dashboard heeft een te-doenblok (`Dashboard.todo`), `x_task_done` en `x_setting`.

1. **Route en sluiten (ES1).** Nieuwe module `routes_start.py`, adressen `/welkom` (stap 1–2) en `/welkom/administratie`,
   `/welkom/gebruikers`, `/welkom/klaar`. Zolang `app_user` leeg is, stuurt de middleware elke pagina (behalve
   `/static/` en `/welkom`) naar `/welkom`. Zodra er een gebruiker is, geeft `/welkom` een doorverwijzing naar `/login`.
   **Akkoord gebruiker 2026-10-11.**
2. **App-database bij het starten.** De server maakt de app-database zelf aan en voert de migraties uit (in
   `lifespan`, zoals nu `init-db`). `init-db` en `create-admin` blijven bestaan als noodroute (beheerder kwijt,
   scripts); README en `compose.yaml` noemen `create-admin` niet meer als verplichte stap.
   **Akkoord gebruiker 2026-10-11.**
3. **Startcode (ES2).** Bij het starten zonder gebruikers maakt de server een startcode (zelfde vorm als de
   eenmalige codes), bewaart alleen de hash (`app_meta`) en schrijft hem in het log:
   `Startcode: XXXX-XXXX — open http://<host>/welkom`. Nieuwe code bij elke herstart; vervalt zodra de beheerder
   bestaat. Foute code = `code_failed` → valt onder de IP-blokkade.
   **Akkoord gebruiker 2026-10-11.**
4. **Taal (ES3).** Welkomstscherm in het Engels met vier knoppen (Nederlands, English, Deutsch, Français), de
   browsertaal voorgeselecteerd. De keuze gaat in een cookie zonder login (`sl_lang`, alleen nl/en/de/fr), die
   `_user_language` gebruikt als er geen sessie is; bij het aanmaken wordt het de taal van de beheerder met de
   notatie uit `i18n.FORMATS`, en het cookie verdwijnt.
   **Akkoord gebruiker 2026-10-11.**
5. **Beheerder (ES4).** Eén scherm: startcode + login, naam, e-mail, wachtwoord (2×), dezelfde regels als bij
   activeren. Daarna meteen een sessie in de stap `totp_setup` → het bestaande scherm 2FA instellen → daarna
   verder naar `/welkom/administratie` (niet naar `/administratie`). Logboek: `user_created` met `via=welkom`.
   **Akkoord gebruiker 2026-10-11.**
6. **Doorgaan na onderbreken.** Logt een systeembeheerder in terwijl er nog geen enkele administratie is, dan komt
   die op `/welkom/administratie` in plaats van naar een lege keuzelijst. De wizardstappen 3–4 blijven voor
   systeembeheerders bereikbaar; overslaan kan altijd ("Later via Systeem").
   **Akkoord gebruiker 2026-10-11.**
7. **Administratie (ES5), nieuw.** Hetzelfde aanmaakformulier en dezelfde code als Systeem > Administraties >
   Nieuwe administratie (gedeeld, niet gekopieerd). Voorinvulling op de gekozen taal: nl → `nl-eenmanszaak`, EUR;
   en → `leeg-uk`, GBP; fr → `leeg-fr`, EUR; de → `leeg-de`, EUR (land aan te passen); omschrijvingen in de
   gekozen taal; boekjaar eindigt in december. Databasenaam wordt voorgesteld uit de naam (kleine letters,
   cijfers, `_`), aan te passen.
   **Akkoord gebruiker 2026-10-11, met correctie de → `leeg-de`** (was `leeg-nl`). Dat schema bestaat nog niet en
   wordt in deze bouwstap gemaakt: `sqlledger/charts/leeg-de.csv` zoals `leeg-ch` (alleen wat het programma nodig
   heeft, DE/EN/NL/FR, EUR, btw 19 % en 7 %), `DE` in `chartfiles.COUNTRIES`, "Duitsland" in `COUNTRY_NAMES` (EN/DE/FR).
   **DE1 nummering = A: SKR03-nummers** (1000 Kasse, 1200 Bank, 1400 Forderungen, 1576/1571 Vorsteuer, 1776/1771
   Umsatzsteuer, 3400 Wareneingang, 8400 Erlöse …). Een volledig Duits schema komt later bij de rekeningschema's.
8. **Back-up inlezen.** Derde tegel zichtbaar maar uitgeschakeld, met "komt in een volgende versie" (gaat open met stap 7).
   **Akkoord gebruiker 2026-10-11.**
9. **Bestaande database koppelen.** Het bestaande formulier (met Verbinding testen); na opslaan door naar stap gebruikers.
   **Akkoord gebruiker 2026-10-11.**
10. **Beheerder werkt zelf in de administratie (ES6).** Het bestaande vinkje `add_me`, standaard aan, rol beheerder.
    **Akkoord gebruiker 2026-10-11.**
11. **Gebruikers uitnodigen (ES7).** Tabel met drie lege regels + "Regel toevoegen": naam, login, e-mail, rol
    (Alleen lezen / Boekhouder / Beheerder, standaard Boekhouder) voor de zojuist gemaakte administratie. Daarna één
    pagina met per gebruiker de eenmalige code en de activeringslink (afdrukbaar). **Mailen**: alleen als de
    administratie e-mail heeft ingesteld — bij een eerste start is dat nooit zo, dus in de wizard alleen tonen,
    met de tip dat het later via Systeem > Gebruikers kan; daar komt bij de code een knop "Code mailen" zodra
    e-mail is ingesteld. Lege regels worden genegeerd; een fout per regel blokkeert alleen die regel.
    **Akkoord gebruiker 2026-10-11**, met aanvulling: op de codepagina per gebruiker een link "Toegang per
    administratie" naar het bestaande scherm Systeem > Gebruikers > (gebruiker), waar per administratie een rol
    staat; de wizard zelf geeft alleen toegang tot de zojuist gemaakte administratie.
12. **Startlijst (ES8).** Blok "Aan de slag" bovenaan het dashboard, met per punt een link en een automatische
    controle: bedrijfsgegevens (naam + adres in Beheer > Instellingen), logo (`logo.png` aanwezig), btw-tijdvak
    (`x_setting` ingesteld), bankrekening met IBAN (`bank.iban`), beginbalans (er is een memoriaalboeking),
    eerste verkoopfactuur (er is een verkoopboeking), huisstijl (zie 13). Elk punt kan op "niet nodig"; het hele
    blok kan weg ("Startlijst verbergen"); opslag in `x_task_done` (soort `start_<punt>`), dus per administratie.
    Alleen zichtbaar voor de rol beheerder; punten waarvoor de module uit staat, vallen weg. Het blok verdwijnt
    vanzelf als alle punten klaar of op "niet nodig" staan.
    **Akkoord gebruiker 2026-10-11.**
13. **Huisstijl.** Eigen korte analyse met beslispunten (`sqlledger-huisstijl`) **na** de wizard; tot het
    huisstijlscherm er is, verwijst het punt "Huisstijl en logo" naar Beheer > Sjablonen (logo) en
    Documentinstellingen.
    **Akkoord gebruiker 2026-10-11.**
14. **Na afloop.** `/welkom/klaar` kiest de nieuwe administratie en opent het dashboard met de startlijst
    (zoals administratie wisselen, AW2 B).
    **Akkoord gebruiker 2026-10-11.**
15. **Vertalingen en documentatie.** Alle nieuwe teksten meteen EN/DE/FR (welkomstscherm zelf staat in het Engels
    maar volgt ook de catalogus); README (installatie, webtabel, afwijking 224: wizard i.p.v. `create-admin`),
    `compose.yaml`-commentaar (`docker compose logs app` voor de startcode); README ook `leeg-de`.
    **Akkoord gebruiker 2026-10-11.**
16. **Toetsen en werkwijze.** pytest per stap, met beveiligingstoetsen (zonder of met foute code geweigerd, IP-
    blokkade, `/welkom` dicht zodra er een gebruiker is, geen sprong naar stap 3 zonder ingelogde
    systeembeheerder, CSRF); regressie pytest + run_compare; browsercontrole met een **lege** app-database
    (`sqlledger_app_start`), alle vier talen, 375px. Branch `fase4-start`, eigen worktree,
    server 8769.
    **Akkoord gebruiker 2026-10-11.** Alle keuzes 1–16 akkoord → bouwen.

## Nieuwe beslispunten na de bouw (2026-10-11, advies steeds A)

Gevonden bij de browsercontrole, ook met een teruggezette echte back-up (administratie B, SL 2.8.37, 16-04-2026).

**ES10 Startlijst bij bestaande administraties.** Nu gebouwd: elke administratie toont de beheerder de startlijst
tot alles klaar, op "niet nodig" of verborgen is, dus ook administratie A of C na de overstap (logo, huisstijl, btw-tijdvak,
IBAN staan daar meestal nog open). A: alleen bij administraties die in de app zijn aangemaakt (Nieuwe administratie)
of via de wizard zijn gekoppeld (een vlag in `x_setting`); B: bij elke administratie (zoals nu); C: alleen bij
nieuw aangemaakte, niet bij gekoppelde.
**Keuze gebruiker 2026-10-11: A.**

**ES11 IBAN van de eigen bankrekening invullen.** Er is geen scherm voor; het IBAN komt nu alleen in `bank` bij het
inlezen van het eerste bankafschrift (README fase 2 open punt "scherm bankrekeningen"). Het startlijstpunt wijst
daarom naar Bankafschrift inlezen. A: velden IBAN, BIC en banknaam bij een betaalrekening in Beheer >
Rekeningschema (schrijft `bank` zoals SL am.pl `bank_accounts`, controle IBAN), startlijst wijst daarheen;
B: eigen scherm Beheer > Bankrekeningen; C: zo laten.
**Keuze gebruiker 2026-10-11: A.**

**ES12 Oude SQL-Ledger-versie koppelen.** De teruggezette back-up (databaseversie 2.8.10) liet zich zonder
waarschuwing koppelen ("Verbinding gelukt … SQL-Ledger 2.8.10"); daarna laadden drie dashboardblokken niet
(tabel `archive` bestaat pas vanaf 3.x). Na bijwerken met de Perl-route (`compare/upgrade_dataset.pl`, = SL
`User::dbupdate`) naar 3.2.4 werkte alles. A: Verbinding testen en Opslaan waarschuwen bij een versie lager dan
3.2.4 ("eerst bijwerken; dat gaat straks vanzelf bij Back-up inlezen") en opslaan kan pas na bijwerken; tot stap 7
legt de README uit hoe je bijwerkt; B: alleen waarschuwen, opslaan mag; C: niets doen.
**Keuze gebruiker 2026-10-11: A.**
