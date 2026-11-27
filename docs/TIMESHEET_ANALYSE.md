# Urenstaat-module (2011) — korte analyse (2026-10-10, niets gebouwd)

Noot 2026-10-10: SQL-Ledger 4.2.0 heeft het urenrapport (timecard) verbeterd (opmaak,
ISO-weekdagen, recent gebruikt; commits 05fad416, e0740725); bekijken als de fase "Uren" doorgaat.

Bron: `Timesheet.doc` + `timesheet.zip` (+ `timesheet.ini`) uit het eigen archief van de gebruiker.

## Wat het was
Uitbreiding op de tijdkaarten (jc.pl) van SQL-Ledger 2.8 (2011), voor medewerkers met rol **timesheet**:
1. **Toewijzen** (HR > Employees > Projects > Assign Projects): per medewerker een projectleider en regels
   project + dienst + startdatum/einddatum (+ omschrijving en verkoopprijs) in een eigen tabel `assignemployee`.
   Overzicht "Assigned Projects".
2. **Maandstaat** ("Timesheet In-1-Go", jc.pl `inonego`): raster dagen × toegewezen project/dienst, uren in
   stappen van 0,1 (6 minuten); Opslaan schrijft per dag per project/dienst één `jcitems`-regel (zonder
   omschrijving); Afdrukken = urenstaat om door de klant te laten aftekenen; Mail = projectleider melden dat de
   staat klaar is. De bestaande tijdkaart per dag bleef bestaan.
3. **Rol timesheet**: eigen menu (`timesheet.ini`), alleen eigen uren.
Daarna liepen de uren via de gewone SL-weg (project → verkooporder, `allocated`).

## Echte data
Niet gebruikt in administratie A of C: geen tabel `assignemployee`; `jcitems` A 0, C 2 regels (2012). In ons plan
staan uren/JC buiten de scope (plan-beslispunt 2); dit zou dus een uitbreiding van de scope zijn.

## Waarom het maar voor 80% werkte (gevonden in de code)
1. **Opslaan van de maandstaat wist en herschrijft de uren** van die maand en zet `allocated` op 0, ook als de
   uren al in een verkooporder/factuur zaten → kans op **dubbel factureren** en verbroken koppeling.
2. **Beperking alleen op het scherm**: de medewerker is een verborgen veld dat de server gelooft → uren voor een
   ander invoeren kan; en ingevoerde id's komen ongecontroleerd in de SQL (**SQL-injectie**).
3. Rol timesheet wisselt alleen het menubestand; alle andere SL-schermen blijven via de URL bereikbaar.
4. "Kern niet wijzigen" klopt niet: `pe.pl` en `SL/PE.pm` zijn volledige kopieën van de 2011-kern (past niet op
   SL 3.x); het script om `assignemployee` aan te maken ontbreekt (kolommen omschrijving/prijs/projectleider
   staan niet in het document).
5. Toewijzing opslaan = alles van de medewerker weg en opnieuw invoegen (geen historie, geen controle op
   overlappende perioden); de prijs komt uit het formulier.

## Hoe het in de nieuwe app zou passen (voorstel, nog niet besloten)
Eerst de JC-basis (tijdkaarten `jcitems`, uren van een project naar een verkooporder zoals SL), dan deze
uitbreiding opnieuw ontworpen: toewijzing in een eigen tabel (`x_assignment`, met historie en
overlapcontrole), maandstaat die alleen gewijzigde, nog niet gefactureerde uren aanraakt, rol **urenschrijver**
met echte rechten (alleen eigen uren, alleen dit scherm), aftekenbare urenstaat als PDF via de documentengine,
melding aan de projectleider via de mailer, schermen NL/EN/DE/FR. Omvang: ongeveer een stap van fase 3.

**Besluit U1 = A (gebruiker, 2026-10-10)**: na fase 4 (vóór fase 6) een eigen fase "Uren", eerst analyse + beslispunten.
Advies was: **A** na fase 4 als eigen fase "Uren"; **B** alleen de maandstaat zonder JC-basis (niet aan te raden);
**C** niet doen.

Vragen: voor welke administratie/klant was dit en is het nog nodig? Moeten uren naar een verkooporder (zoals SL)
of direct naar een factuur? Moet de klant digitaal kunnen aftekenen, of is een PDF genoeg?
