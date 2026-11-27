# ICP-opgaaf — analyse (2026-10-11)

**Status: akkoord, alles A (gebruiker 2026-10-11); GEBOUWD op `fase4-btw` (2026-10-11), keuzes binnen A 1–12 in README "Open punten" akkoord (2026-10-11); samengevoegd in `fase4`.** Beslispunten ICP1–ICP11.
Wens gebruiker tijdens de bouw: EU-klanten en -leveranciers moeten een btw-nummer hebben, anders kan de ICP niet (keuze 2).
Achtergrond: BA9 sloot de ICP-opgaaf uit; de gebruiker wil hem nu toch (2026-10-11), synchroon met de btw-aangifte.

## Wat de ICP-opgaaf is
- Opgaaf intracommunautaire prestaties: wie goederen levert of diensten verricht aan btw-ondernemers in een ander
  EU-land (rubriek 3b van de btw-aangifte), geeft per afnemer op: **landcode + btw-identificatienummer** en het
  **totaalbedrag in dit tijdvak**, gesplitst in **leveringen** en **diensten** (3a van het portaal), plus apart de
  **ABC-leveringen (vereenvoudigde regeling)** (3b van het portaal). Bedragen in hele euro's.
- Indienen gaat digitaal (Mijn Belastingdienst Zakelijk of via SBR-software); net als de btw-aangifte maken we alleen
  het overzicht om over te nemen (screenshot van de gebruiker: portaal "Gegevens intracommunautaire prestaties
  (rubriek 3)" met per regel landcode, nummer, leveringen, diensten).
- Tijdvak: in de regel per kwartaal, maandelijks als de leveringen van goederen boven de grens van de Belastingdienst
  komen; de termijn loopt gelijk met de btw-aangifte. Eerdere opgaven corrigeer je in het portaal ("Eerdere opgaven
  corrigeren"). Onderdeel "Overbrengen van voorraad op afroep" bestaat ook. (Grenzen en termijnen bij de bouw nakijken
  op belastingdienst.nl; ze staan niet in de code.)
- Exact Online (gelezen 2026-10-11): btw-code met "ICP: levering / dienst / ABC", klant met landcode en btw-nummer;
  opgaaf per jaar + periode, transacties op basis van btw-aangiften, datum of periode; "definitief maken" en verzenden.

## Echte data
- **Administratie A**: 399 klanten, 3 met een btw-nummer (geen EU-buitenland), land bijna nooit ingevuld → in de praktijk geen
  ICP. **Administratie C** (VK): 29 btw-nummers, 14 met een EU-landcode; maar Brits (na Brexit geen ICP, behalve XI). Toetsen
  dus vooral met eigen testgegevens.
- SL: `customer.taxnumber` (vrije tekst) bestaat; land staat als vrije tekst in `address.country`; artikel vs dienst
  via `parts` (dienst = geen voorraad, `inventory_accno_id` leeg); geen ICP-kenmerk op btw-rekeningen.

## Beslispunten (advies steeds A)

**ICP1 Plek.** A: menu **Btw > ICP-opgaaf**, zichtbaar zodra een btw-rekening of een klant op rubriek 3B staat;
zelfde tijdvakkeuze als de aangifte; link vanaf rubriek 3B op het aangifteoverzicht en een eigen regel in de
dashboardtaak btw. B: tabblad binnen de btw-aangifte.

**ICP2 Tijdvak.** A: eigen instelling **ICP-opgaaf per** maand/kwartaal (Beheer > Instellingen), standaard gelijk aan
het btw-tijdvak (btw per jaar → kwartaal). B: altijd het btw-tijdvak.

**ICP3 Welke boekingen (synchroon).** A: precies de verkoopboekingen die in de aangifte in **rubriek 3B** tellen (via
een btw-rekening op 3B of de klant-rubriek 3B), dezelfde rekenregels; het scherm toont totaal ICP naast rubriek 3B en
meldt een verschil groter dan de afronding. B: alle verkoop aan klanten met een EU-landcode in het btw-nummer.

**ICP4 Btw-nummer en landcode.** A: het btw-nummer van de klant; landcode = eerste twee letters (EL voor Griekenland,
XI voor Noord-Ierland); spaties/punten weg; controle op vorm per land; melding bij ontbrekend, ongeldig of NL-nummer
(met link naar de klant). Geen online VIES-controle. B: ook een knop "controleren in VIES" (internet).

**ICP5 Leveringen of diensten.** A: factuur met artikelen per regel (artikel = levering, dienst = dienst); boeking
zonder artikelen volgens een instelling bij de klant **ICP-soort**: leveringen (standaard) of diensten. B: per
opbrengstrekening instellen.

**ICP6 ABC-leveringen.** A: ICP-soort bij de klant kan ook **ABC-levering (vereenvoudigde regeling)** zijn → aparte
lijst 3b. B: niet ondersteunen.

**ICP7 Bedragen.** A: per afnemer en soort de grondslag in euro's (creditnota's verrekend, kan negatief),
afgerond op hele euro's per regel (rekenkundig; ICP kent geen "in uw voordeel"); afnemers op btw-nummer samengevoegd
(ook als hetzelfde nummer bij twee klanten staat). B: afronden naar beneden.

**ICP8 Vastleggen en correcties.** A: vastleggen gebeurt **samen met de btw-boeking** als de tijdvakken gelijk zijn
(anders knop "ICP-opgaaf vastleggen"); later gewijzigde boekingen geven per afnemer het verschil (zoals BA7), te
verwerken bij "Eerdere opgaven corrigeren" in het portaal. B: niet vastleggen.

**ICP9 Uitvoer.** A: scherm in de volgorde van het portaal (landcode, nummer, leveringen, diensten; daaronder ABC),
doorklik per afnemer naar de boekingen, PDF en Excel/CSV. Geen indienen (geen SBR/XML). B: ook een XML-bestand.

**ICP10 Voorraad op afroep.** A: niet ondersteunen (uitleg op het scherm: zelf invullen in het portaal).
B: handmatig regels toevoegen en bewaren.

**ICP11 Planning.** A: bouwen direct na akkoord, op dezelfde branch `fase4-btw` (één geheel met de aangifte), vóór de
rekeningschema's. B: na de rekeningschema's.

## Toetsen (bij A)
- pytest: afnemers samenvoegen, levering/dienst/ABC per regel en per klant, creditnota, btw-nummercontrole (per land,
  EL/XI, NL weigeren), totaal = rubriek 3B, vastleggen met de btw-boeking en verschilmelding, rechten, vier talen.
- Testadministratie met EU-klanten (BE/DE/FR, factuur met artikel + dienst, boeking zonder artikelen, ABC).
- Browsercontrole incl. 375 px; PDF naast het portaalvoorbeeld.
