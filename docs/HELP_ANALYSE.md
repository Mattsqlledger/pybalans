# Helpteksten per scherm: analyse (2026-10-13)

**Akkoord gebruiker (2026-10-13): HL1–HL12 alles A.**

**Golf 1 (2026-10-13):** NL-tekst `dev/help_golf1.md` (niet in git) akkoord, lengte goed; **V1 = A**: help in "u" en
de 58 schermzinnen met "je"/"jij"/"jouw" ook naar "u" (code, catalogi EN/DE/FR als sleutel, toetsen). Gebouwd op
branch `help` (eigen worktree): `helptext.py` (`ht`/`hl`/`hm`), `templates/help/*.html` (16
bestanden, 18 schermen + één bankregel), vertalingen EN/DE/FR (225 zinnen), toetsen. Kleine afwijking van de
goedgekeurde tekst: bij de verkoopfactuur verwijst "voorraad controleren" nu naar het echte veld
"Voorraad controleren bij verkoop" (dan klopt de naam in elke taal).

**Golf 2 (2026-10-13):** NL-tekst `dev/help_golf2.md` (niet in git, 20 teksten voor 29 schermen) akkoord; **V1–V4
alles A**: V1 vaste laatste regel onder *Let op* bij elk rapport (`help/_report_common.html`; Rekeningschema zonder
formulier alleen de knoppen); V2 koersverschilboeking eigen help (`gl_fx.html`, FX-vinkje alleen op de
valutarekening); V3 meteen gebouwd: F2 *Openstaand* "Van" standaard leeg, F4 jaarafsluiting stelt de
resultaatrekening van de vorige afsluiting voor, F7 melding "(Beheer, fase 2)" → "(Beheer > Instellingen)", F8 titel
*Documenten in batch*; V4 later apart analyseren (OVERDRACHT): F1 zoeken op "Betaalkenmerk" in Overzicht/Openstaand
altijd leeg (behalve per journaalregel), F3 btw-overzicht "Per factuur" netto dubbel bij twee tarieven, F5 periode
"Jaar" = kalenderjaar ook bij gebroken boekjaar, F6 koersverschilboeking met alleen FX-regels opent leeg.
Rapporthelp via `REPORT_HELP` in `reportdefs.py` (veld `Report.help`), include in `reports/page.html`.

**Golf 3 (2026-10-13):** NL-tekst `dev/help_golf3.md` (niet in git, 40 teksten voor 51 schermen, delen A/B/C)
akkoord; **V1–V5 alles A**: V1 meteen gebouwd: F9 zin "(fase 3)" weg op Instellingen, F10 hint "Vast" preciezer,
F11 hint "Btw-aangifte per" noemt ook het menu-item, F12 "afgesloten" → "Gesloten" op Btw-rubrieken, F13 menupad
*Systeem > Administraties > Back-up inlezen*, F14 rol vertaald op Gebruikers, F15 hints Login/Naam voor de
beheerder, F16 Logboek zoekt op deel van de login, F17 klantnaam weg bij Sjablonen, F18 keuzelijst Branche op
ingestelde volgorde, F19 nieuwe branche/betaalmethode in audittrail, F20 RGS-teller telt als de overstaplijst, F21
"je" → "u" in importer; V2 veld heet "KvK-nummer", btw-aangifte en ICP tonen het btw-nummer uit
Documentinstellingen; V3 velden die niets doen (kasbasis, referentie-url, Decimalen valuta, Korting branche, Kosten
betaalmethode; "Vast" en "Afronding" bleken te werken) → afgehandeld in bouwstap velden (BEHEER_EXTRA_ANALYSE.md
VW1–VW7); V4 F22–F27 later (F22 controleblad kalenderjaar mee met maand jaarafsluiting; F23 koppelen zonder
beheerder, F24 databasenaam wijzigen zonder waarschuwing, F25 ingelezen back-up altijd documenttaal nl, F26
sjabloonvariant na "Terug naar standaard" niet te verwijderen, F27 gebruikers/administraties niet te verwijderen);
V5 a eigen kopje *Automatisch elke nacht* bij Back-up maken, b geen help op het voortgangsscherm van inlezen, c 2FA-help
alleen op het gewone scherm, d help ook in de wizard, e Lettertypen geen help, f koersregel met aanvulling
rekeningoverzicht/aanmaning. Oude `system/_help_backup.html`/`_help_restore.html` vervangen door `help/sys_*.html`.

**Golf 4 (2026-10-13):** NL-tekst `dev/help_golf4.md` (niet in git; 21 teksten voor 44 schermen + pagina Help met
21 taken) akkoord; **V1–V7 alles A**. Vooraf met een script alle 106 menu-items nagelopen: 25 zonder helpblok, nu 0.
Nieuw: `help/oe_*`, `ic_list/groups/history/stock/build/asmlist/assembly` (eigen blok voor een samengesteld artikel,
V4), `attach_overview`, `recurring_*`, `am_departments`, `am_projects`, `jc_*`, `rep_hours`; bijlagenzin in `gl`,
`ct_form`, `ic`, `is`, `arap` (V3). Pagina *Help* "Hoe doe ik …?" (`/help`, link rechtsboven; `routes_help.py` +
`help/tasks.html`): een taak verschijnt als de module van een kernstap aan staat; een stap die de gebruiker niet mag
staat er zonder link met "(beheerder)" e.d. (V5); systeemtaken alleen voor (systeem)beheerder; urenschrijver alleen
taken die hij kan openen. V1 meteen gebouwd: F28 links samengesteld/assembleren alleen als productie zichtbaar is,
F29 standaardkolommen na wisselen naar "Regels uit", F30 omgezette offerte niet heropenen, F31 "Stoppen" per schema
op *Terugkerende boekingen*, F32 zin maandstaat zonder "stappen van 0,1 uur", F33 bevestiging bij intern tarief
verwijderen, F34 tip grijze dag voor de urenschrijver, F35 datums in de urenlijsten in de notatie van de gebruiker,
F36 *Verkooporders uit uren* alleen boekhouder, F37 jaarafsluiting zonder Grootboek/Kas en bank terug naar het
scherm, F38 bon-regel memoriaal naar Let op. V2 later (OVERDRACHT): F39 prijsbasis prijshistorie, F40 links naar
orders in prijshistorie, F41 voorraadcorrectie/assembleren en afgesloten periode, F42 "vertrouwelijk" per
medewerkerlogin, F43 datumfilter bijlagen en SQL-Ledger-bestanden, F44 tijdelijk bestand na "Annuleren".

Wens gebruiker (2026-10-11): op elk scherm een helplink met per onderdeel een korte tekst wat je moet doen; een
kleine "hoe doe je iets"-kennisbank in de app zelf, **geen** helpwebsite zoals bij Exact.

## Wat er nu is

- **Eén voorbeeld:** uitklapblok "Hoe werkt het?" op Systeem > Back-up maken en Back-up inlezen
  (`templates/system/_help_backup.html`, `_help_restore.html`, CSS `details.help`). De teksten lopen via `_()` en
  staan dus in `i18n_en/de/fr.py`. Knop- en menunamen worden met bestaande labels ingevoegd (`_("Database (.sql.gz)")`),
  zodat ze niet uit de pas lopen als een label verandert.
- **Losse hints bij velden:** ±230 keer `<span class="muted small">` (bv. "inclusief btw", "beschikbaar …"). Die
  blijven zoals ze zijn.
- **Omvang:** 102 menu-items in 13 rubrieken, ±130 schermen (GET-routes), 137 sjablonen. Daarnaast schermen zonder
  eigen menu-item (een bestaande boeking openen, klant bewerken, wizard).
- **Eigen teksten** (`<data_dir>/vertalingen/<taal>.txt`) werken al voor alles wat via `_()` loopt; een gebruiker
  kan dus ook helpzinnen aanpassen zonder code.
- `base.html` bepaalt al welk menu-item "hier" is (`cur.label`); dat is bruikbaar maar dekt niet elk scherm.

## Beslispunten

**HL1: Waar staat de help op het scherm?**
- **A** Een uitklapblok "Hoe werkt het?" direct onder de schermtitel, dicht bij openen (zoals bij Back-up). Geen
  nieuwe pagina, werkt zonder JavaScript en op 375 px.
- B Een "?"-link bij de titel die een aparte helppagina opent (nieuw tabblad).
- C Een zijpaneel dat inschuift (JavaScript).

Advies **A**: bestaat al, gebruiker kent het, je blijft op het scherm met je invoer.

**HL2: Uitleg per veld**
- **A** In het helpblok een korte lijst "Velden" met alleen de velden die niet vanzelf spreken (vaak 3–8 per
  scherm), niet elk veld.
- B Bij elk lastig veld een eigen ⓘ-knopje dat een regel uitklapt.
- C Zweeftekst (`title`) per veld.

Advies **A**: C werkt niet op telefoon/tablet; B maakt formulieren drukker en vraagt per veld opmaakwerk. Kan later
per scherm alsnog als blijkt dat één veld steeds vragen oproept.

**HL3: Waar staan de teksten?**
- **A** Eén sjabloondeel per scherm in `templates/help/<scherm>.html` met `_()`-zinnen (zoals het Back-upvoorbeeld).
  Vertalingen in de bestaande `i18n_*.py`; de bestaande toetsen (niets onvertaald, plaatshouders) gelden meteen.
- B Markdownbestanden per taal (`help/nl/<scherm>.md`, `help/en/…`). Prettiger lezen, maar knop-/menunamen lopen
  uit de pas en er zijn nieuwe toetsen nodig.
- C In de database, te bewerken in een beheerscherm.

Advies **A**: zelfde werkwijze als de rest, labels blijven gelijk, eigen teksten werken al.

**HL4: Hoe koppel je help aan een scherm?**
- **A** Elk sjabloon geeft zelf aan welk helpdeel erbij hoort (`{% block help %}` / `{% include "help/…" %}`).
  Werkt ook voor schermen zonder menu-item (bestaande boeking openen, klant bewerken).
- B Automatisch via het menu-item dat "hier" is.

Advies **A**: B mist de bewerkschermen, en daar is de help juist nodig.

**HL5: Vaste opbouw per helpblok**
- **A** Steeds dezelfde kopjes, alleen waar ze iets toevoegen: *Waarvoor* (1–2 zinnen), *Zo doet u het*
  (genummerde stappen), *Velden* (HL2), *Let op* (valkuilen). Kort: past op ongeveer één scherm.
- B Vrije vorm per scherm.

Advies **A**: herkenbaar en makkelijk te keuren.

**HL6: Verschillen met SQL-Ledger noemen?**
- **A** Alleen waar het een overstapper echt verrast, één regel onder *Let op* ("Anders dan in SQL-Ledger: …").
- B Nooit; de help is voor iedereen.
- C Een aparte overstappagina.

Advies **A**. De handleiding "Overstappen uit SQL-Ledger" in de README blijft voor de installatie.

**HL7: Aanspreekvorm en toon**
- **A** "u" (zoals Back-up en de rest van de app); Duits *Sie*, Frans *vous*. Korte zinnen, gewone woorden,
  menupaden als *Verkoop > Verkoopfactuur*.
- B "je".

Advies **A**.

**HL8: Open of dicht?**
- **A** Altijd dicht bij openen van het scherm.
- B De browser onthoudt per scherm of je het open had.

Advies **A**: eenvoudig; B kan later.

**HL9: Zelfde help voor elke rol?**
- **A** Ja. Een stap die de rol niet mag (bv. boeken als alleen-lezer) staat er gewoon; de knop ontbreekt dan al.
- B Per rol andere tekst.

Advies **A**.

**HL10: Kleine kennisbank "Hoe doe ik …?"**
- **A** Eén pagina *Help* (link rechtsboven naast Mijn instellingen) met 15–25 taken ("Een verkoopfactuur maken",
  "Een bankafschrift inlezen en afletteren", "Btw-aangifte doen", "Het jaar afsluiten" …). Elke taak: 2–4 zinnen +
  links naar de schermen in de goede volgorde; de details staan in de helpblokken. Alleen taken van modules die aan
  staan.
- B Geen aparte pagina, alleen help per scherm.

Advies **A**, als laatste golf (HL11), dan verwijst hij naar schermen die al help hebben.

**HL11: Volgorde en keuring in golven**
- **A** Vier golven, elk apart keuren:
  1. *Dagelijks werk* (±20 schermen): Verkoopfactuur, Boeking toevoegen (verkoop/inkoop), Creditnota,
     Inkoopfactuur uploaden, Inkoopfactuur, Ontvangst, Betaling, Bankafschrift inlezen, Bankregels verwerken,
     Vaste bankregels, Afletteren, Memoriaal, Klant/Leverancier (zoeken + bewerken), Artikel/Dienst, Btw-aangifte,
     ICP-opgaaf, Aanmaningen, Dashboard.
  2. *Overzichten en afsluiten* (±25): rapporten (Openstaand, Ouderdom, Proefbalans, W&V, Balans,
     Rekeningmutaties, Boekingen zoeken, btw-overzichten), Afsluiten, Jaarafsluiting, Ontvangsten/Betalingen
     (meerdere, corrigeren), Koersverschilboeking, Betalingsregister, Documenten in batch, Rekeningoverzichten.
  3. *Beheer en systeem* (±30): Instellingen, Ingebruikname, Rekeningschema, RGS, Btw-tarieven/-rubrieken,
     Valuta/Koersen, Huisstijl, E-mail, Documentinstellingen, Sjablonen, Importeren/Exporteren, Gebruikers,
     Administraties, Back-up (bestaat), Logboek, Audittrail, Locks, Talen, Branches, Betaalmethoden, Mijn instellingen.
  4. *Rest + kennisbank* (±35): orders/offertes, artikelen (samengesteld, assembleren, stuklijsten, groepen),
     terugkerende boekingen, projecten/afdelingen, uren, bijlagen + de *Help*-pagina (HL10).
- B Alles in één keer.
- C Alleen golf 1.

Advies **A**: golf 1 helpt meteen bij jouw eigen gebruik van PyBalans; per golf blijft keuren te overzien.

**HL12: Hoe keuren?**
- **A** Per golf een leesbestand `dev/help_golf<n>.md` (niet in git) met de Nederlandse tekst per scherm, plus een
  steekproef in de browser (NL, EN, 375 px). Na akkoord op de NL-tekst vertaal ik EN/DE/FR en draai ik de
  vertaaltoetsen.
- B Alleen in de browser keuren.

Advies **A**: lezen in één bestand gaat sneller dan 20 schermen langs klikken.

## Wat er technisch bij komt (bij A overal)

- `templates/help/` met één deel per scherm; in elk schermsjabloon één regel `{% include %}` onder de titel.
- Een klein macro-/stijlstuk zodat elk blok dezelfde kopjes en opmaak heeft (`details.help` bestaat).
- Toetsen: elk helpdeel rendert in NL/EN/DE/FR zonder onvertaalde zinnen; elk scherm uit het menu van de golf heeft
  een helpblok; bestaande renderproef blijft groen.
- Branch `help` vanaf `main`, eigen worktree, testserver op een vrije poort; niets samenvoegen
  zonder akkoord. Na alle golven: nieuwe versie (1.2.0) installeren op de eigen installatie volgens de bekende stappen.

## Gevonden tijdens deze analyse (los van help)

Kopieën met " 2" in de naam door bestandssynchronisatie (226 bestanden + 6 lege mappen, niet in git, alle gelijk aan
of ouder dan het origineel): met akkoord van de gebruiker (2026-10-13) opgeruimd.
