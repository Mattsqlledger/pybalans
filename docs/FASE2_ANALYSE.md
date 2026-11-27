# Fase 2 — analyse en beslispunten (2026-10-08)

Onderdelen: relaties (ct.pl/CT.pm), beheer (am.pl/AM.pm, PE.pm), terugkerende boekingen
(arap.pl schedule, am.pl recurring), afletteren (rc.pl/RC.pm) en bankafschrift inlezen (nieuw).

**Bevestigd door gebruiker (2026-10-08, vóór de nacht):** T4 A, T5 A, K5 A, L4/I2 A (x_-tabellen in de
administratie). Overige punten: advies A — **op 2026-10-09 door de gebruiker akkoord gegeven (alle punten A, plus de
open punten zoals gebouwd).**

**Werkwijze deze nacht (op verzoek gebruiker):** er wordt gebouwd op het advies (A) van elk
beslispunt, op git-branch `fase2`. `main` blijft ongemoeid. Morgen per punt bijsturen; wat
anders moet, wordt aangepast of weggegooid.

## Gebruik in de echte data

| | administratie A | administratie C |
|---|---|---|
| klanten / leveranciers | 399 / 404 (398 met einddatum = inactief) | 835 / 400 |
| contactpersonen | 870, steeds 1 per relatie | 1.329, steeds 1 per relatie |
| relaties zonder adres of contact | 0 | 0 |
| dubbele relatienummers | 13 | 11 |
| verzendadressen (`shipto`) | 6.534 per boeking, 51 per klant | 0 |
| bankgegevens | 3 leveranciers + 1 eigen rekening | 1 klant, 4 leveranciers, 6 eigen rekeningen |
| afdelingen / projecten / talen | 0 / 0 / 0 | 2 / 3 / 2 |
| valuta / koersen | EUR / 0 | GBP, USD, EUR, ZAR / 144 |
| afgeletterde regels | 0 | 701, laatste in 2011 |
| terugkerende schema's | 3, alle afgelopen | 0 |
| audittrail | uit | 17.952 regels |

Conclusie: relaties en beheer zijn kern; afletteren en terugkerend werden nauwelijks gebruikt
(wat past bij hoe omslachtig ze in SQL-Ledger zijn). Het bankafschrift inlezen wordt de
nieuwe manier van werken en neemt het afletteren grotendeels over.

## Gevonden Perl-fouten (gaan naar README "Bewuste afwijkingen")

- **SQL-injectie** in meerdere modules (relaties, beheer, afletteren, terugkerende boekingen,
  import): beveiligingsfout in SQL-Ledger; wordt gemeld aan de onderhouders, details volgen
  daarna.
- **Relaties:** verwijderen zonder servercontrole op boekingen (alleen de knop ontbreekt);
  elke boekingsknop op het relatiescherm wist de einddatum (inactieve relatie wordt actief);
  `bank.dcn`/`rvc` gaan bij elke save verloren; `SELECT enddate FROM {db}` zonder WHERE;
  persoon-naam niet bijgewerkt bij nieuwe achternaam; "Nieuw nummer" hoogt teller op zonder
  opslaan; inner join op address/contact maakt relaties zonder die rij onzichtbaar.
- **Beheer:** btw-vlag weghalen wist de hele tariefhistorie; `save_defaults` gooit alle
  sleutels weg (`yearend` gaat altijd verloren; oud scherm overschrijft nieuwere `closedto`);
  valuta "move" verandert ongemerkt de basisvaluta; basisvaluta en valuta in gebruik bij gl
  zijn te verwijderen; `delete_account` controleert de verkeerde rekening; GIFI met voorloopnul
  niet te wijzigen; dubbele spaties in taalomschrijving worden "-"; nieuwe administratie mist
  `fxgainloss_accno_id` (sjabloon maakt `fxgain_accno_id`/`fxloss_accno_id`).
- **Terugkerend:** maandeinde schuift (31-1 → 29-2 → 29-3 …); geen lock: twee gebruikers of
  een dubbele POST boeken dubbel; mislukt printen = geboekt maar nextdate niet opgehoogd →
  volgende keer dubbel; AP-kopie krijgt vervaldatum = boekdatum (leest `ar.*`); memoriaal-kopie
  ×100 bij notatie 1.000,00; ingevuld kenmerk = elke kopie hetzelfde factuurnummer; geen
  validatie aantal (0/negatief).
- **Afletteren:** "Klaar" zonder "Bijwerken" slaat vinkjes niet op; verschilcontrole op een
  verborgen geformatteerd veld (0,50 → 0, omzeilbaar); "alles selecteren" overschrijft oude
  afletterdatums; samenvoegen per kenmerk zoekt id als deelstring (12 in 123 → niet
  afgeletterd); UNION i.p.v. UNION ALL voegt identieke regels samen; zonder koersverschil-
  rekening een SQL-fout.

## Beslispunten

### Relaties (K)
- **K1** Eén scherm voor klant en leverancier: zoeken/lijst + toevoegen/wijzigen/verwijderen,
  velden zoals ct.pl. Niet: prijslijst/prijsgroep beheren (wel bewaren), POS, artikelhistorie
  (fase 3). **Advies A.** B: alleen wijzigen, geen toevoegen.
- **K2** Contactpersoon: één per relatie zoals Perl (data heeft er altijd precies één).
  **A**. B: meerdere contactpersonen.
- **K3** Verzendadres: één afwijkend adres per relatie zoals Perl; verzendadressen per
  boeking blijven ongemoeid (fase 3). **A**.
- **K4** Bank: naam, IBAN, BIC, lidnummer, clearingnummer; IBAN-controle (mod 97) en BIC-
  vorm, bank-naam niet meer geforceerd in hoofdletters, `dcn`/`rvc` blijven behouden. **A**.
- **K5** Nummer: leeg = volgend nummer bij opslaan (sjabloon zoals Perl), `lock_*number`
  gevolgd; dubbel nummer bij nieuw/wijzigen geweigerd (bestaande dubbelen blijven staan).
  **A**. B: alleen waarschuwen.
- **K6** Verwijderen alleen zonder ar/ap/oe/betalingen, ook op de server gecontroleerd;
  anders "inactief maken" (einddatum). Ook meervoudig verwijderen van ongebruikte relaties.
  **A**.
- **K7** Lijst: filters zoals Perl (naam, nummer, contact, adres, plaats, land, telefoon,
  e-mail, notities, medewerker, branche, status alle/actief/inactief/ongebruikt), kolommen
  kiesbaar, openstaand en beschikbaar krediet, CSV/xlsx, 500 per pagina; doorklik naar de
  boekingen van de relatie. **A**.
- **K8** Knoppen op het relatiescherm: Nieuwe verkoop/inkoop, Ontvangst/Betaling (bestaande
  schermen met de relatie voorgevuld), zonder de einddatum te wissen. **A**.
- **K9** Rechten: boekhouder+ bewerkt, alleen-lezen ziet; module ar resp. ap. **A**.

### Beheer (A)
- **A1** Rekeningschema: lijst, toevoegen/wijzigen (koppelingen als vinkjes, controles zoals
  Perl), verwijderen alleen zonder boekingen en niet als standaardrekening (op de server). GIFI:
  alleen veld bij de rekening, geen GIFI-beheerscherm. **A**. B: ook GIFI-scherm.
- **A2** Btw: tarieven per btw-rekening met geldig-tot, datums strikt oplopend, tarief
  verplicht; tariefhistorie blijft bewaard als de btw-vlag weggaat. **A**.
- **A3** Instellingen: bedrijfsgegevens, nummering (memoriaal/verkoop/inkoop/relaties) met
  lock-vlaggen, standaardrekeningen (koersverschil; voorraad/omzet/kosten tonen voor fase 3),
  precisie, methode, cdt, referentie-url. Opslaan per sleutel (nooit alles weggooien).
  **A**.
- **A4** Boekperiode afsluiten (`closedto`), `revtrans`, audittrail aan/uit: aparte pagina
  "Afsluiten", alleen beheerder; audittrail leegmaken niet aanbieden. **A**.
- **A5** Valuta: toevoegen, precisie wijzigen, volgorde, **standaardvaluta expliciet kiezen**
  (alleen als er nog geen boekingen in vreemde valuta zijn, met bevestiging), verwijderen
  alleen als nergens gebruikt en nooit de standaardvaluta. Koersen: lijst/invoeren/wijzigen
  per datum. **A**.
- **A6** Afdelingen (kosten-/winstplaats), projecten (zonder JC-velden), talen, branches,
  betaalmethoden: eenvoudige lijsten; verwijderen alleen als niet in gebruik. SIC niet. **A**.
- **A7** Audittrail bekijken (filters medewerker, datum, actie, referentie) en "locks
  vrijgeven" (alleen beheerder, per lock of alles). Administratie op slot zetten: vlag in de
  app-database. **A**.
- **A8** Nieuwe/ingelezen administratie: `fxgain_accno_id`/`fxloss_accno_id` →
  `fxgainloss_accno_id` rechtzetten bij openen. **A**.

### Terugkerende boekingen (T)
- **T1** Knop "Herhalen" op memoriaal/verkoop/inkoop opent het schema (start, elke n
  dagen/weken/maanden/jaren, aantal, betaling meenemen, omschrijving) met verwijderen;
  validatie start verplicht, n ≥ 1, aantal ≥ 1. "Opslaan als nieuw" neemt het schema niet mee.
  **A**.
- **T2** Lijst "Terugkerende boekingen" (te verwerken aangevinkt, volgende datum
  bewerkbaar) + "Verwerken": per schema een lock en één databasetransactie (boeking +
  ophogen), controle dat de volgende datum niet intussen veranderd is. **A**.
- **T3** Datums: volgende = start + k·interval (maandeinde blijft maandeinde: 31-1, 28/29-2,
  31-3). **A** (afwijking).
- **T4** Nummer: kopie krijgt altijd het volgende nummer; het kenmerk van het schema wordt
  alleen gebruikt als dat geen bestaand nummer is. **A**. B: Perl (elke kopie hetzelfde
  nummer).
- **T5** Omschrijving van de kopie = omschrijving van het origineel; de schema-omschrijving
  is alleen de naam in de lijst. **A**. B: Perl (schema-omschrijving, standaard de naam van de relatie).
- **T6** Vervaldatum = boekdatum + (vervaldatum − boekdatum) van het origineel, ook bij inkoop
  (Perl-fout); betaling meenemen zoals Perl (datum + zelfde verschuiving, niet afgeletterd).
  **A**.
- **T7** Koers: koers op de nieuwe datum verplicht; ontbreekt die, dan wordt het schema
  overgeslagen met melding (Perl houdt stil de oude koers). **A**.
- **T8** Print/e-mail van kopieën en automatisch verwerken op een tijdstip: fase 4. **A**.

### Afletteren (L)
- **L1** Scherm zoals rc.pl: rekening (standaard betaalrekeningen, vinkje "alle
  balansrekeningen"), periode, afschriftsaldo; regels met vinkje; begin, aangevinkt, verschil
  live; samenvoegen per kenmerk (standaard aan); koersverschilregels meenemen (standaard aan).
  **A**.
- **L2** De server rekent het verschil uit de vinkjes en het ingevoerde saldo; opslaan alleen
  bij verschil 0 (zoals Perl, nu echt gecontroleerd). Vinkjes worden altijd opgeslagen, ook
  zonder "Bijwerken". **A**. B: ook tussentijds opslaan bij verschil.
- **L3** Afletterdatum = afschriftdatum (tot-datum) zoals Perl. **A**.
- **L4** Elke afletterronde vastleggen (rekening, datum, afschriftsaldo, gebruiker) in een
  nieuwe tabel in de administratie (`x_reconciliation`), zodat het beginsaldo van de volgende
  ronde vaststaat. **A**. B: niets bewaren zoals Perl.
- **L5** Overzicht afgeletterde posten (rc.pl report). **A**.

### Bankafschrift inlezen (I)
- **I1** Formaten CAMT.053 (.001.02 t/m .13) en MT940 (ING, Rabo, ABN, Triodos, bunq,
  Knab), zelf ontleden zonder externe pakketten; XML zonder DOCTYPE. **A**.
- **I2** Nieuwe tabellen in de administratie: `x_bank_statement` (afschrift), `x_bank_line`
  (regel, met status en koppeling naar de boeking), `x_bank_rule` (vaste regels).
  Voorvoegsel `x_` zodat ze buiten het SQL-Ledger-schema vallen. **A**.
- **I3** Dubbel inlezen: per regel een vaste sleutel (bankreferentie of hash); al bekende
  regels worden overgeslagen en gemeld, ook bij overlappende exports. **A**.
- **I4** Bankrekening via IBAN (`bank` bij een betaalrekening); onbekend → gebruiker kiest de
  rekening en de IBAN wordt opgeslagen. Afschrift in een andere valuta dan de rekening:
  geweigerd. **A**.
- **I5** Saldocontrole: begin + regels = eind verplicht; aansluiting op het vorige afschrift
  en op het grootboeksaldo als waarschuwing. **A**.
- **I6** Voorstel per regel, in volgorde: (a) betalingskenmerk = `dcn` (NL 16 cijfers met
  controle, RF-kenmerk); (b) factuurnummer in omschrijving/end-to-end-id; (c) IBAN tegenpartij
  → relatie, dan één openstaande post met precies dat bedrag, of alle open posten samen;
  (d) bestaande ongeafletterde boeking op de bankrekening met zelfde bedrag en datum ±7
  dagen → alleen afletteren; (e) vaste regel; anders onbekend. Richting uit het teken (bij =
  klant, af = leverancier, creditnota's omgekeerd). Niets wordt geboekt zonder bevestiging.
  **A**.
- **I7** Boeken: betaling via `Payments.post_payment` (kenmerk = bankreferentie), anders een
  memoriaalboeking tegen de gekozen rekening; vaste regels (IBAN of trefwoord → rekening, btw)
  kan de gebruiker vanuit een regel opslaan. **A**.
- **I8** Alles wat via het afschrift geboekt of gekoppeld wordt, is direct afgeletterd
  (afletterdatum = boekdatum bank). **A**.
- **I9** Batches: één regel per transactie als de bank details levert, anders één regel
  (handmatig verdelen). Storno's: alleen signaleren, niet automatisch terugdraaien. **A**.
- **I10** CSV-betalingsimport (im.pl) blijft fase 4. **A**.

### Algemeen (G)
- **G1** Volgorde: relaties → beheer → terugkerend → afletteren → bankafschrift; daarna
  Engelse vertaling (eis taal) en de herruns uit fase 1a. **A**.
- **G2** Test per onderdeel: pytest; Perl-driver (ct.pl, am.pl, rc.pl, am.pl recurring als
  browser) tegen de webcode met database-diff; echte data administraties A en C: elke relatie/rekening
  openen en ongewijzigd opslaan → database identiek; terugkerend/afletteren op kopieën;
  browsercontrole incl. mobiel. **A**.
