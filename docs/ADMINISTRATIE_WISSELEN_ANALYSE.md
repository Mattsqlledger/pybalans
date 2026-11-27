# Administratie wisselen — analyse (2026-10-11)

**Status: akkoord gebruiker 2026-10-11: AW1 A, AW2 B, AW3–AW7 A. GEBOUWD 2026-10-11 op branch `fase4-adm`
(eigen worktree); keuzes binnen A 1–8 (README "Open punten") akkoord; samengevoegd in `fase4` en opgeruimd.**

Aanleiding: in Exact Online klik je op de naam van de administratie (bv. "100001 Voorbeeld BV") en
krijg je een keuzemenu met recent gebruikte administraties, "nieuw | Alles tonen" en een zoekveld "Een andere
administratie openen". Bij ons: link "wisselen" in de kopregel → aparte pagina `/administratie`
(`routes_main.choose_form`, `templates/choose_company.html`), na kiezen het dashboard.

## Besluiten

- **AW1 Plek = A.** De naam van de administratie in de kopregel (`base.html`) wordt een knop met ▾ die een keuzemenu
  opent: huidige administratie (naam + database), **Recent gebruikt** (laatste 5 van deze gebruiker), **Alle tonen**
  (de bestaande keuzepagina) en vanaf 8 administraties een zoekveld "Andere administratie openen".
- **AW2 Waar je uitkomt = B.** Na wisselen altijd naar het dashboard (zoals Exact).
- **AW3 Niet-opgeslagen werk = A.** Wisselen vanaf een half ingevuld formulier: de browser vraagt eerst
  "Wijzigingen verlaten?".
- **AW4 Recent gebruikt = A.** Per gebruiker in de app-database (laatste 5, met tijdstip; werkt op elke computer).
- **AW5 Herkenbaarheid = A.** Elke administratie krijgt een korte code/nummer (zoals Exact 100001, 2, 3), in te stellen
  door de systeembeheerder (Systeem > Administraties), getoond in het keuzemenu en de kopregel.
- **AW6 Rechten = A.** Alleen administraties waar de gebruiker lid van is; een vergrendelde administratie staat er
  grijs in met de melding.
- **AW7 Planning = A.** Direct na het samenvoegen van btw + ICP, vóór de rekeningschema's.

## Bouw (bij A)
- App-db migratie 9: `company.code` (tekst, uniek als ingevuld) en tabel `recent_company (user_id, company_id, used)`.
- Bijwerken bij elke keuze (`POST /administratie`); keuzemenu met de bestaande POST (CSRF), zonder JavaScript bruikbaar
  (`<details>`), zoekveld filtert de lijst (JS) en valt terug op de keuzepagina.
- Toetsen: menu toont recent/lid/vergrendeld, volgorde recent, code in kopregel, wisselen → dashboard, rechten,
  vier talen; browsercontrole incl. 375 px.
