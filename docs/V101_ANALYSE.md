# v1.0.1 en v1.1 — wensen uit de proefronde (2026-10-12)

Bron: proefronde van de gebruiker op de eigen installatie (v1.0.0) met administratie A uit een SQL-Ledger-
back-up van 16-4-2026. Advies steeds **A**. **Gekozen 2026-10-12: alles A** (PB1–PB22, IN1–IN7, GR1–GR5); bouwen: eerst v1.0.1, dan v1.1 (interne uren + grafiek). Keuzes binnen A per onderdeel in README "Open punten" ter keuring.

Indeling: **v1.0.1** = verbeteringen van wat er is (geen nieuwe functies, geen databasewijziging behalve waar
vermeld); **v1.1** = nieuwe functionaliteit. Daarna: proefdata + app-db weg (na akkoord), v1.0.1 installeren,
opnieuw beginnen (wizard, administraties B en A inlezen).

## v1.0.1 — inlezen en overstap

| Nr | Wat | A (advies) | B |
|---|---|---|---|
| PB1 | Werkwijze back-up inlezen | twee stappen: uploaden → controlescherm (soort, SL-versie, datum, bedrijfsnaam, laatste boeking; velden vooringevuld) → Inlezen | browser leest het bestand vooraf |
| PB2 | Vooringevulde waarden | naam = bedrijfsnaam uit `defaults.company` (anders dataset); databasenaam = dataset; code leeg | databasenaam uit bedrijfsnaam |
| PB3 | Uitleg Databasenaam | "Naam van de database op de server: kleine letters, cijfers en _. Wordt niet getoond in de app." (NL/EN/DE/FR) | — |
| PB4 | Voortgang inlezen | echte balk: upload %, terugzetten % (bytes), bijwerken stap x van 19; achtergrondtaak + opvragen elke seconde | alleen draaiend teken |
| PB5 | Venster dicht / dubbel klikken | inlezen loopt door, terugkomen toont stand/resultaat; tweede start met dezelfde naam geweigerd | — |
| PB6 | Back-up maken | knop grijs + "Bezig met maken…" | — |
| PB7 | Werkwijze | branch `v1.0.1`, tests, keuring; installeren na back-up | — |
| PB8 | Controleblad akkoord | knoppen "Cijfers kloppen – akkoord" / "Er klopt iets niet" (+ notitie), uitleg in 3 stappen bovenaan | alleen "gedaan" op de lijst (nu) |
| PB10 | Terug naar overstaplijst | balkje "Overstap: … – Terug naar de overstaplijst" op elke pagina die je vanuit de lijst opent, ook na Opslaan; punten met twee pagina's tonen beide links (btw: rubrieken + tijdvak; bank: IBAN + afschrift) | automatisch terug na Opslaan |
| PB14 | Medewerker bij login | bij inlezen keuzelijst "Jij bent in deze administratie:" (op naam vooringevuld, of nieuw); nieuwe administratie maakt medewerker voor de beheerder; link op Mijn uren; kolom "Medewerker" als keuzelijst | alleen betere melding |
| PB16 | Oude herhaalschema's | overstappunt "Terugkerende boekingen nakijken" (volgende datum vóór de overstap: Stoppen / datum aanpassen); Verwerken weigert > 1 termijn achterstand zonder bevestiging | — |

## v1.0.1 — schermen en opmaak

| Nr | Wat | A (advies) | B |
|---|---|---|---|
| PB9 | Losse links rechtsboven | "Terug naar …" als knop bij de andere knoppen; overal zo | alleen controleblad |
| PB11 | Logo | sleepvak, meteen opslaan bij kiezen/slepen, miniatuur + voorbeeld verversen; waarschuwing bij gewone Opslaan met niet-opgeslagen logo | pas opslaan met gewone Opslaan |
| PB13 | RGS-downloadlink | link naar de Kennisbank van referentiegrootboekschema.nl + "kies de nieuwste definitieve versie (bij uitgifte: 3.8)"; waarschuwing bij alfa/bèta | directe link naar 3.8 |
| PB17 | Zoeklijsten | eigen zoeklijst op alle 9 plekken: 10 regels + schuifbalk, "begint met" eerst, vet, pijltjes/Enter, max 50 + "typ verder", naam één keer | datalist houden, dubbele naam weg |
| PB18 | Formulieropmaak overal | vast raster van 4 kolommen, velden even hoog, vinkjes op veldhoogte zonder afbreken, alleen-lezen waarden als blok, getallen niet afkappen, linkrij onder de titel, vóór/na-schermafdrukken 1280 + 375 px ter keuring (omvat PB12 W&V-vinkje, PB15 herhaalschema, "Reke-ning") | alleen aangewezen schermen |
| PB19 | Datumvelden | eigen foutmelding in de taal van de gebruiker, ×-knop om leeg te maken, leeg = geen grens | — |
| PB20 | Menu productie | Assembleren/Stuklijsten/Productiehistorie alleen als er samengestelde artikelen zijn (A: 0) + schakelaar in Instellingen | altijd tonen |
| PB22 | Kolomvolgorde W&V | januari → december, totaal als laatste kolom | totaal, december → januari (nu, zoals SL) |

## v1.1 — nieuwe functies

| Nr | Wat | A (advies) | B |
|---|---|---|---|
| IN1 | Soort project | keuze Klantproject (factureren) / Intern project (intern tarief), met uitleg | zonder klant = intern |
| IN2 | Kostenplaats | intern project → afdeling soort kostenplaats (meteen aan te maken) | per urenregel |
| IN3 | Intern tarief | toewijzing > intern uurtarief medewerker > kostprijs dienst; vast bij opslaan | één tarief per dienst |
| IN4 | Verwerking | alleen registratie + rapport, geen boeking | ook maandelijkse doorbelastingsboeking |
| IN5 | Rapport Uren per project | filters klant/intern, kostenplaats, medewerker, periode; uren, tarief, waarde, gefactureerd/open, marge; PDF/xlsx | — |
| IN6 | Mijn uren / verkooporder | intern zichtbaar met label, nooit in verkooporder uit project | — |
| IN7 | Wanneer | v1.1, analyse uitschrijven in `UREN_ANALYSE.md` | in v1.0.1 |
| GR1 | Grafiek W&V: wat | staafjes opbrengsten/kosten per periode + lijn resultaat; klik rekening/groep | alleen opbrengsten/kosten |
| GR2 | Grafiek: waar | keuze Tabel / Grafiek / Beide, onthouden per gebruiker | altijd boven de tabel |
| GR3 | Grafiek: techniek | SVG van de server, ook in PDF en afdrukken | Chart.js |
| GR4 | Vorig jaar | lichtere staaf ernaast | — |
| GR5 | Balans | geen grafiek | — |

GR1–GR5 **gebouwd** op branch `v1.1-grafiek` (2026-10-12); keuzes binnen A in README "Open punten".

## Gegevens (geen keuze, ter informatie)

- A: artikel Z met een sterk negatieve voorraad en een verkoopprijs die ver van de lijstprijs afwijkt; na de nieuwe
  start eventueel een opruimlijst (negatieve voorraad, afwijkende prijzen).
- A: één medewerker staat dubbel, enkele zonder login.
- A: een herhaalschema staat al sinds 2015 stil (zie PB16).
- Een extra archiefbestand bij de back-ups van 16-4 bevat alleen dezelfde twee back-ups.
