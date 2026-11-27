# Huisstijl — analyse en beslispunten

**Status: akkoord gebruiker 2026-10-11, HS1–HS11 alles A. Bouwen op branch `fase4-huisstijl`.**

Wens gebruiker (2026-10-11): een eenvoudig scherm om de opmaak van facturen en andere documenten aan te passen —
logo, kleur, lettertype, ligging van logo en adresblok — **zonder HTML**, per administratie, en als punt in de
startlijst van de eerste start (ES8). Beslispunt van de gebruiker zelf: de huisstijl overnemen van een andere
administratie.

## Wat er nu is

| Scherm | Wat | Voor wie |
|---|---|---|
| Beheer > **Documentinstellingen** (`routes_docsettings.py`, `x_doc_setting`) | btw-nummer, welke bankrekening op het document, teksten per taal (retouradres, voet, slottekst, opmerking, inleiding), kolommen per groep documenten, gewichtsoverzicht | beheerder |
| Beheer > **Sjablonen** (`routes_tpl.py`, `<data_dir>/templates/<id>/`) | eigen HTML-sjablonen downloaden/uploaden, voorbeeld-PDF, **logo** (png/jpg/svg, ≤ 1 MB), varianten | beheerder |
| Standaardopmaak (`doctemplates/standard/_base.html`) | A4, marges 18/16/24/20 mm, Helvetica 9,5 pt, zwart met grijs, **logo linksboven** boven de bedrijfsnaam (max. 22 × 70 mm), telefoon/e-mail rechtsboven, **adres links** op 45 mm (vensterenvelop links), titel in hoofdletters, lijnen zwart | alle documenten (facturen, orders, pakbonnen, aanmaningen, betalingsspecificaties …) |

Wat ontbreekt: kleur, lettertype en ligging zijn alleen via een eigen HTML-sjabloon te wijzigen, en het logo staat
verstopt onder Sjablonen. De rapporten-PDF's (stap 5) hebben hun eigen, neutrale opmaak.

Achtergrond ligging: in Nederland en Duitsland (DIN 5008) zit het venster van de envelop links; in Zwitserland
meestal rechts (SN 010130). België en Frankrijk kennen beide.

## Beslispunten

**HS1 Plek en naam.** A: nieuw menu-item Beheer > **Huisstijl**, bovenaan de groep "Documenten en e-mail", met links
ernaartoe vanaf Documentinstellingen en Sjablonen; het startlijstpunt wijst hierheen. B: een tab binnen
Documentinstellingen.
**Keuze gebruiker 2026-10-11: A.**

**HS2 Wat is in te stellen.** A, zes dingen, alles met keuzelijsten of een kleurkiezer:
1. **logo** uploaden/verwijderen (hetzelfde bestand als nu bij Sjablonen), met **grootte** klein/middel/groot;
2. **ligging logo**: links, midden of rechts boven;
3. **accentkleur** (één kleur voor titel, lijnen en kolomkoppen; tekst blijft zwart; controle op voldoende contrast
   met wit);
4. **lettertype** (zie HS3);
5. **ligging adresblok**: links (venster links, standaard) of rechts (venster rechts);
6. **bedrijfsgegevens in de kop** tonen of niet (handig bij voorbedrukt briefpapier), en **voetregel** tonen of niet.
B: daarnaast vrije marges, lettergrootte en meer kleuren. C: alleen logo en kleur.
**Keuze gebruiker 2026-10-11: A.**

**HS3 Lettertypen.** WeasyPrint gebruikt de lettertypen van de server; die verschillen per installatie (Docker
heeft er weinig). A: een vaste keuze van **vijf vrije lettertypen** (SIL Open Font License, mag bij GPL-software)
die met het programma worden meegeleverd en in de PDF worden ingesloten: één klassiek schreefloos (zoals nu),
twee modern schreefloos, twee met schreef; zo ziet de PDF er op elke server hetzelfde uit. B: alleen de
systeemlettertypen (kan per server anders uitvallen). C: daarnaast een eigen lettertype uploaden (TTF/OTF).
**Keuze gebruiker 2026-10-11: A.**

**HS4 Opslag.** A: instellingen in `x_doc_setting` (sleutels `style.*`, zoals Documentinstellingen: alleen wat
afwijkt; SL merkt er niets van), het logo blijft een bestand in `<data_dir>/templates/<id>/`. Zonder
huisstijlinstellingen is elk document precies gelijk aan nu (toets met `dev/doc_html_dump.py`, zoals stap 3). B:
ook het logo in de database (dan gaat het mee in de back-up van de administratie; zie BB1–BB4 voor bijlagen).
**Keuze gebruiker 2026-10-11: A.**

**HS5 Eigen sjablonen (Sjablonen).** Een administratie kan voor sommige documenten een eigen HTML-sjabloon hebben.
A: de huisstijl werkt op de standaardopmaak; eigen sjablonen krijgen de instellingen als variabelen (`style.color`,
`style.font` …) maar gebruiken ze alleen als ze dat zelf doen. Het huisstijlscherm meldt welke documenten een eigen
sjabloon hebben ("hier geldt de huisstijl niet"). B: eigen sjablonen negeren zonder melding.
**Keuze gebruiker 2026-10-11: A.**

**HS6 Voorbeeld.** A: **live voorbeeld** naast de instellingen (op mobiel eronder): een factuur met de eigen
bedrijfsgegevens en de laatste verkoopfactuur (of voorbeeldregels als die er nog niet is), ververst bij elke
wijziging (HTMX, zonder op te slaan); knop "PDF bekijken". Opslaan pas als het bevalt. B: alleen een knop
"Voorbeeld-PDF".
**Keuze gebruiker 2026-10-11: A.**

**HS7 Overnemen van een andere administratie.** A: knop **"Overnemen van …"** met een keuzelijst van de
administraties waar de gebruiker ook beheerder is; neemt logo en huisstijl over (niet de teksten, bank of kolommen
van Documentinstellingen — die zijn per bedrijf), toont eerst het voorbeeld en vraagt bevestiging; logboek.
B: met een vinkje ook de Documentinstellingen meenemen. C: niet.
**Keuze gebruiker 2026-10-11: A.**

**HS8 Rechten.** A: beheerder van de administratie (zoals Documentinstellingen en Sjablonen). B: ook boekhouder.
**Keuze gebruiker 2026-10-11: A.**

**HS9 Welke documenten.** A: alle documenten uit de standaardset (facturen, creditfacturen, orders, offertes,
pakbonnen, picklijsten, aanmaningen, rekeningoverzichten, betalingsspecificaties); de rapporten-PDF's blijven
neutraal. B: rapporten ook (logo en lettertype).
**Keuze gebruiker 2026-10-11: A.**

**HS10 Startlijst.** Nu twee punten: "Logo toevoegen" en "Huisstijl van facturen en andere documenten". A:
samenvoegen tot één punt **"Logo en huisstijl"** dat naar Beheer > Huisstijl wijst en klaar is zodra de huisstijl
één keer is opgeslagen (ook ongewijzigd: "zo is het goed"). B: twee punten houden.
**Keuze gebruiker 2026-10-11: A.**

**HS11 Planning.** A: nu bouwen, vóór de rekeningschema's CH → VK → FR → BE (het is het laatste open punt van de
eerste start). B: na de rekeningschema's.
**Keuze gebruiker 2026-10-11: A.**

## Keuzes binnen A

1. **Lettertypen (HS3)** — akkoord 2026-10-11: keuzelijst "Standaard (zoals nu)" (systeem-Helvetica, niet
   ingesloten; zonder instellingen dus gelijk aan nu) + vijf OFL-lettertypen, normaal + vet, met OFL.txt, ingesloten:
   Liberation Sans (klassiek schreefloos, maten als Helvetica/Arial), Inter, Source Sans 3 (modern schreefloos),
   Source Serif 4, Lora (met schreef).
2. **Accentkleur (HS2.3)** — akkoord 2026-10-11: kleur op titel, lijn onder de kop, kolomkoppen (tekst + lijn),
   lijnen boven totalen/eindtotaal, rand betaalblok; tekst, bedragen, grijze bijzaken en dunne regellijnen blijven.
   Standaard `#111` (zoals nu). Kleurkiezer + hexvakje + zes snelkeuzes (zwart, donkerblauw, petrol, donkergroen,
   bordeaux, antraciet). Contrast ≥ 4,5 : 1 tegen wit (WCAG), anders geweigerd met melding; voorbeeld toont het wel.
3. **Logo (HS2.1–2)** — akkoord 2026-10-11: maten (max. hoogte × breedte, nooit vergroten) klein 14 × 45 mm,
   middel 22 × 70 mm (standaard, zoals nu), groot 30 × 95 mm. Ligging links (standaard, boven bedrijfsnaam), midden
   (gecentreerd boven de kop), rechts (boven telefoon/e-mail). Met opgeslagen huisstijl staat het adresblok vast op
   45 mm van boven (vensterplek); kop te hoog → waarschuwing in het voorbeeld; venster als stippellijn alleen in het
   live voorbeeld. Logo = zelfde bestand als Sjablonen (png/jpg/svg ≤ 1 MB), op beide schermen te beheren.
4. **Adresblok, kop, voet (HS2.5–6)** — akkoord 2026-10-11: adres links (standaard, venster NL/DIN 5008,
   afleveradres rechts) of rechts (venster CH volgens SN 010130 — maat bij het bouwen nazoeken en met PDF
   controleren; afleveradres links; retourregel gaat mee). Kop uit: logo/naam/adres/contact/lijn weg, ruimte blijft
   leeg, adres op vensterplek; melding als er een logo is. Voet uit: regel linksonder weg (ook eigen voettekst uit
   Documentinstellingen, met melding), paginanummer en betaalblok blijven. Kop én voet uit → hint KvK/btw-nummer op
   briefpapier; opslaan mag.
5. **Live voorbeeld (HS6)** — akkoord 2026-10-11: laatste verkoopfactuur in haar documenttaal (status/logboek
   ongemoeid); zonder factuur een voorbeeld "VOORBEELD" met eigen bedrijfsgegevens, Voorbeeldklant B.V. (vertaald),
   regels Advieswerk 4 uur à 85,00 / Artikel A 2 × 12,50 / Verzendkosten 1 × 7,95, eerste verkoop-btw-tarief (of
   geen), betaalblok met eigen IBAN. Alleen factuur (uitleg: andere documenten volgen dezelfde stijl). HTML van
   blz. 1 verkleind op A4-verhouding naast de instellingen (mobiel eronder), ververst na ±0,3 s zonder opslaan,
   voetregel nagebootst, vensterstippellijn + waarschuwingen; knop "PDF bekijken" = echte PDF met niet-opgeslagen
   instellingen; te lichte kleur wel tonen, niet opslaan.
6. **Overnemen van … (HS7)** — akkoord 2026-10-11: knop naast de titel, alleen zichtbaar bij ≥ 1 andere
   administratie met rol beheerder (ook voor sysadmin alleen met die rol). Keuzelijst + "Laden" → instellingen en
   logo in formulier en voorbeeld, balk "Huisstijl van X geladen — nog niet opgeslagen" met Opslaan/Annuleren.
   Exacte kopie van `style.*` + logo; andere zonder logo → eigen logo weg (met waarschuwing); zonder huisstijl →
   standaard; niet: Documentinstellingen, eigen sjablonen. Audittrail "Huisstijl overgenomen van X" / "Huisstijl
   gewijzigd". Andere administratie alleen gelezen; onbereikbaar → melding, eigen huisstijl blijft.
7. **Eigen sjablonen (HS5) en links (HS1)** — akkoord 2026-10-11: kader boven het voorbeeld alleen als er eigen
   sjablonen zijn: eigen `_base` → "huisstijl geldt voor geen enkel document, tenzij …"; eigen sjabloon per
   document/variant → lijst; "(gebruikt de huisstijl)" als `style.` in de bron staat; link naar Sjablonen; voorbeeld
   gebruikt het eigen factuursjabloon. Variabelen `style.color`, `style.font`, `style.font_face`,
   `style.logo_height`/`logo_width` (mm), `style.logo_pos` (left/center/right), `style.address_pos` (left/right),
   `style.show_head`/`show_footer`, `style.set`; uitleg op Sjablonen + README. Menu-item bovenaan "Documenten en
   e-mail"; links op Documentinstellingen (bovenaan) en Sjablonen (bij het logo).
8. **Startlijst (HS10)** — akkoord 2026-10-11: één punt `style` "Logo en huisstijl" ("Kies logo, kleur en
   lettertype voor facturen en andere documenten.") → Beheer > Huisstijl; klaar zodra `style.saved` in
   `x_doc_setting` (ook ongewijzigd opslaan of Overnemen); Documentinstellingen opslaan of alleen een logo uploaden
   telt niet meer; eerder "niet nodig" bij `logo` of `style` telt als niet nodig. Eén keer opslaan zet het
   adresblok vast op 45 mm (bedoeld); zonder opslaan byte-gelijk.
9. **Scherm en knoppen** — akkoord 2026-10-11: desktop links instellingen (±40 %), rechts voorbeeld (meeschuivend);
   mobiel onder elkaar. Blokken Logo (miniatuur, Bestand kiezen/Verwijderen, grootte, ligging), Kleur en letter
   (kiezer + hex + 6 snelkeuzes; lettertypenamen in eigen letter), Adres (links/rechts met uitleg venster), Kop en
   voet (twee vinkjes). Logo-upload direct opgeslagen (gedeelde code Sjablonen); rest bij Opslaan. Knoppen Opslaan,
   PDF bekijken, Standaard herstellen (formulier terug, pas bewaard na Opslaan, logo blijft); waarschuwing bij
   verlaten met niet-opgeslagen wijzigingen. Lettertypen (normaal + vet, ±1,5–2 MB) in het pakket; PDF bevat
   alleen het gekozen lettertype als subset.

**Alle keuzes binnen A akkoord (2026-10-11) → bouwen.**

**Lettertypen downloaden (2026-10-11):** akkoord gebruiker, op voorwaarde van een licentievermelding in de app
(onder de lettertypekeuze + pagina met maker, copyright en licentietekst; NOTICE in het pakket). De bestanden gaan
**onveranderd** mee (variabele lettertypen werken in WeasyPrint 70, ook vet): geen probleem met de Reserved Font
Names (Liberation, Source, Lora); pakket ±3,7 MB in plaats van 1,5–2 MB.

### HS12 Kop en vensterenvelop (nieuw, tijdens het bouwen)

Gemeten in de huidige standaardopmaak (A4, bovenmarge 18 mm), bovenkant adresblok (incl. retourregel):

| | 2 adresregels bedrijf | 4 adresregels bedrijf |
|---|---|---|
| zonder logo | 43,5 mm | 53 mm |
| met logo (middel, boven de bedrijfsnaam) | 67,5 mm | 77 mm |

Het venster (NL/DIN 5008 vorm B) begint op 45 mm. Met een logo valt het adres dus nu al ruim onder het venster,
omdat logo en bedrijfsgegevens boven elkaar staan (kop ±40–50 mm hoog). Keuze 3c (adres vast op 45 mm) kan dan niet
zonder dat de kop over het adres heen loopt.

- **A** — met een opgeslagen huisstijl wordt de kop **één rij** van hoogstens ±25 mm: logo **naast** de
  bedrijfsgegevens in plaats van erboven. Links: logo links, bedrijfsnaam en adres ernaast, telefoon/e-mail rechts.
  Midden: bedrijfsnaam en adres links, logo in het midden, telefoon/e-mail rechts. Rechts: bedrijfsnaam en adres
  links, telefoon/e-mail in het midden, logo rechts. Adres vast op 45 mm. Groot (30 mm) past dan net niet → groot
  wordt 25 × 95 mm. Zonder huisstijl blijft alles zoals nu (byte-gelijk).
- **B** — logo blijft boven de bedrijfsgegevens; adres niet vast, alleen een waarschuwing in het voorbeeld als het
  adres onder het venster valt (met logo dus bijna altijd).
- **C** — adres vast op 45 mm en de kop zoals nu; waarschuwing "kop te hoog" (met logo bijna altijd).
**Keuze gebruiker 2026-10-11: A.** (Vergelijking: de LaTeX-kop van SQL-Ledger, `letterhead.tex`, is ook één rij:
bedrijf + adres links (42 %), logo in het midden (20 %), telefoon/fax/e-mail/www rechts, lijn eronder — A "midden"
is dus de SL-indeling. Keuze 3a wordt: groot 25 × 95 mm.)

## Raakvlakken

- Documentinstellingen (`sqlledger/docsettings.py`): zelfde tabel en zelfde manier van opslaan.
- Sjablonen (`routes_tpl.py`): het logo-uploaden wordt gedeeld, niet gekopieerd.
- Eerste start: startlijst (keuze 12/13, HS10).
- Vertalingen: alle nieuwe teksten meteen EN/DE/FR; de documenten zelf hebben al teksten in vier talen.
- Toetsen: pytest; `doc_html_dump` zonder instellingen ongewijzigd (alle documenten A/C/web_demo, vier talen);
  voorbeeld-PDF's per lettertype en ligging; browsercontrole incl. 375 px.

## Bouwplan (voor de volgende chat)

Werkwijze als bij de eerste start: branch `fase4-huisstijl` vanaf `fase4`, eigen worktree,
pytest vanuit de worktree met `PYTHONPATH=. <venv>/bin/python -m pytest`, testserver 8769 vanuit de
worktree. Eerst de **keuzes binnen A** uitwerken en
één voor één laten keuren (o.a. welke vijf lettertypen + licentiebestanden, standaardkleur, maten klein/middel/groot,
contrastgrens, wat "voorbeeldregels" zijn, waar de knop Overnemen staat), dan bouwen:

1. Bibliotheek: `sqlledger/docsettings.py` uitbreiden met `style` (logo_size, logo_pos, color, font, address_pos,
   show_head, show_footer; `style.*` in `x_doc_setting`, standaard = huidige opmaak, validatie + contrast).
2. Lettertypen (OFL) in `sqlledger_web/fonts/` (+ OFL.txt per lettertype, pyproject package-data), `@font-face`
   met `file://` of data-URL in `_base.html` (WeasyPrint; renderer staat alleen data:-URL's toe → data-URL).
3. `doctemplates/standard/_base.html`: CSS-variabelen uit `style` (kleur, lettertype, logo-maat/ligging,
   adresblok links/rechts in `.parties`, kop/voet aan/uit); zonder instellingen byte-gelijk (`dev/doc_html_dump.py`
   oud vs nieuw, A/C/web_demo, vier talen).
4. Web: `routes_style.py` Beheer > Huisstijl (`/beheer/huisstijl`), live voorbeeld via HTMX (HTML-voorbeeld van een
   factuur, knop PDF), logo-upload gedeeld met `routes_tpl` (TemplateStore.save_logo/delete_logo), melding eigen
   sjablonen (HS5), "Overnemen van …" (HS7: administraties waar de gebruiker beheerder is; logo-bestand kopiëren +
   `style.*`; bevestigen; logboek), menu-item bovenaan "Documenten en e-mail" + links vanaf Documentinstellingen/
   Sjablonen (HS1), alleen beheerder (HS8).
5. Startlijst (HS10): punten `logo` + `style` samen tot `style` "Logo en huisstijl" → `/beheer/huisstijl`, klaar
   zodra `style.saved` (ook ongewijzigd opslaan) in `x_doc_setting`; `Dashboard.START_ITEMS`, `START_TEXTS`,
   toetsen `test_dashboard`/`test_web_start` bijwerken.
6. Vertalingen EN/DE/FR, README (webtabel, afwijking 225, "Gelijkheid", open punten), toetsen, regressie (pytest,
   run_compare, doc_html_dump), voorbeeld-PDF's per lettertype/ligging (`dev/voorbeeld-pdf/huisstijl/`, niet in git),
   browsercontrole incl. 375 px en DE/FR.
