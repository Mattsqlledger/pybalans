"""Overstap uit SQL-Ledger (FASE6_ANALYSE.md 6d-2, I4–I6, I8): Beheer > Ingebruikname, de lijst op het dashboard en
het controleblad.

* **Lijst** (``Dashboard.switch_list``): gaat aan bij elk inlezen van een back-up (``routes_restore``) of met de knop
  op Beheer > Ingebruikname; staat op het dashboard in plaats van de startlijst, alleen voor de beheerder.
* **Controleblad** (``/beheer/ingebruikname/controleblad``, scherm en PDF): integriteitscontroles
  (``sqlledger.switchover``), aantallen per jaar, saldi van de betaalrekeningen, proefbalans en winst en verlies van
  het boekjaar t/m de peildatum, balans en openstaande posten per relatie op de peildatum. De rapporten komen uit
  dezelfde definities als Rapporten (``reportdefs``): dezelfde cijfers als daar; de openstaande posten uit dezelfde
  query als het dashboard (``switchover.open_per_relation``; per factuur gelijk aan de ouderdomsanalyse, die bij een
  grote administratie 15 s duurt). Peildatum standaard: de laatste
  boekdatum op het moment van inlezen.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger import switchover
from sqlledger._db import cursor, get_defaults, get_precision, get_yearend
from sqlledger.dates import fiscal_year
from sqlledger.dashboard import SWITCH_ITEMS, SWITCH_MANUAL, Dashboard
from sqlledger.errors import LedgerError
from sqlledger.numbering import preview as preview_number

from . import backups
from .companies import connect_company
from .i18n import N_, gettext as _
from .reportdefs import REPORTS, Fmt, Lib, booking_link, default_values
from .reportpdf import pdf_response
from .tables import Col, Row, Table, cell_text
from .web import Ctx, Redirect, csrf_form, ctx_company, render

router = APIRouter()

#: tekst, link en uitleg per punt (I5); {..} in de link wordt ingevuld

#: productnaam, niet vertalen
RMA_LABEL = "Run my Accounts / ledger123"


def backup_label(info: Dict[str, Any], ctx: Optional[Ctx] = None) -> str:
    """Herkomst in één regel (VP1): soort en naam (dataset, anders de bedrijfsnaam), versie en datum van de back-up;
    wat ontbreekt (een pg_dump heeft geen dataset en geen datum) wordt weggelaten in plaats van "—"."""
    name = info.get("dataset") or info.get("company") or ((ctx.company or {}).get("name") if ctx else "") or ""
    family = info.get("family") or ("rma" if ctx and (ctx.company or {}).get("rma") else "")
    if family == "rma":
        name = f"{RMA_LABEL}: {name}" if name else RMA_LABEL
    parts = [p for p in (name,
                         _("versie {version}").format(version=info["version"]) if info.get("version") else "",
                         _("back-up van {date}").format(date=info["date"]) if info.get("date") else "") if p]
    return ", ".join(parts)

ITEMS = {
    "restored": (N_("Back-up ingelezen"), "/beheer/ingebruikname",
                 N_("De administratie komt uit een back-up van SQL-Ledger en is bijgewerkt naar de versie van deze app.")),
    "rma": (N_("Run my Accounts-rapport nalezen"), "/beheer/rma",
            N_("Lees het controlerapport van het inlezen: waarschuwingen (zoals de rekening voor koersverschillen) en "
               "wat zonder plek in PyBalans is bewaard. Vink daarna af.")),
    "check": (N_("Cijfers controleren met het controleblad"), "/beheer/ingebruikname/controleblad",
              N_("Leg het controleblad naast de laatste cijfers uit SQL-Ledger (jaarrekening, btw-aangifte, "
                 "bankafschrift) en kijk de controles na. Vink daarna af.")),
    "company": (N_("Bedrijfsgegevens controleren (naam en adres)"), "/beheer/instellingen",
                N_("Naam en adres komen op facturen en andere documenten.")),
    "vat": (N_("Btw-rubrieken en aangiftetijdvak instellen"), "/btw/rubrieken",
            N_("SQL-Ledger kent geen btw-aangifte: koppel elke btw-rekening aan een rubriek (of 'geen aangifte'), "
               "kies de afdrachtrekening en het aangiftetijdvak (Beheer > Instellingen).")),
    "bank": (N_("IBAN invullen en het eerste bankafschrift inlezen"), "/bank/inlezen",
             N_("Vul bij de bankrekening het IBAN in (Beheer > Rekeningschema) en lees een afschrift in. Het "
                "beginsaldo van het afschrift moet gelijk zijn aan het saldo in de boekhouding.")),
    "style": (N_("Logo en huisstijl"), "/beheer/huisstijl",
              N_("Kies logo, kleur en lettertype voor facturen en andere documenten.")),
    "numbering": (N_("Nummering controleren"), "/beheer/instellingen",
                  N_("De nummers lopen door vanuit SQL-Ledger. Controleer de volgende nummers en vink af.")),
    "recurring": (N_("Terugkerende boekingen nakijken"), "/terugkerend",
                  N_("Herhaalschema's waarvan de volgende datum vóór de overstap ligt, heeft SQL-Ledger niet meer "
                     "verwerkt. Stop ze of zet de volgende datum goed; anders boekt Verwerken ze alsnog.")),
    "users": (N_("Iedereen een eigen login met tweestapsverificatie"), "/systeem/gebruikers",
              N_("Geen gedeelde logins: elke gebruiker van deze administratie heeft tweestapsverificatie aan.")),
    "backup": (N_("Back-up instellen"), "/systeem/back-up-maken",
               N_("Maak een back-up met de app en plan die dagelijks in; de herinnering op het dashboard verdwijnt "
                  "zolang de laatste back-up niet ouder is dan 7 dagen.")),
    "closedto": (N_("Oude perioden afsluiten (afsluitdatum)"), "/beheer/afsluiten",
                 N_("Zet de afsluitdatum op het einde van het laatste aangegeven btw-tijdvak; daarna kan niemand "
                    "daarvoor nog iets wijzigen. Het boekjaar van de overstap blijft open tot de jaarrekening.")),
    "rgs": (N_("RGS-codes koppelen"), "/beheer/rgs",
            N_("Voor rapportage volgens het Referentie Grootboekschema: elke rekening een RGS-code.")),
}
NUMBERS = (("sinumber", N_("verkoopfactuur")), ("vinumber", N_("inkoopfactuur")), ("glnumber", N_("memoriaal")),
           ("sonumber", N_("verkooporder")), ("ponumber", N_("inkooporder")), ("customernumber", N_("klant")),
           ("vendornumber", N_("leverancier")))
CHECK_LABELS = {
    "unbalanced": N_("Boekingen niet in evenwicht (debet ≠ credit)"),
    "fx_rounding": N_("Afrondingsverschillen van 1 cent bij vreemde valuta"),
    "missing_account": N_("Grootboekregels op een rekening die niet bestaat"),
    "orphan": N_("Grootboekregels zonder boeking"),
    "overpaid": N_("Facturen met meer betaald dan het bedrag"),
    "arap_mismatch": N_("Facturen die niet aansluiten op de debiteuren-/crediteurenrekening"),
    "upgrade_skipped": N_("Overgeslagen opdrachten bij het bijwerken"),
}
LEVEL_TEXT = {"ok": N_("in orde"), "info": N_("ter informatie"), "warn": N_("nakijken")}


def _next_number(raw: str, dateformat: str) -> str:
    """Het volgende nummer ingevuld, met het sjabloon uit Instellingen erachter als dat er een heeft (K2)."""
    nxt = preview_number(raw, dateformat=dateformat)
    return _("{number} (Instellingen: {raw})").format(number=nxt, raw=raw) if "<%" in raw else nxt


def _require_admin(ctx: Ctx) -> None:
    if ctx.role != "admin":
        raise HTTPException(403, _("Alleen een beheerder kan dit doen."))


def external(ctx: Ctx, conn: psycopg.Connection) -> Dict[str, Optional[bool]]:
    """Stand van de punten die niet in de administratie staan: gebruikers met 2FA, back-up, RGS (alleen als er een
    RGS-lijst is ingelezen)."""
    row = ctx.conn.execute(
        "SELECT coalesce(bool_and(u.totp_secret IS NOT NULL), true) AS ok FROM membership m "
        "JOIN app_user u ON (u.id = m.user_id) WHERE m.company_id = %s AND u.active", (ctx.company["id"],)).fetchone()
    from sqlledger import rgs
    d = rgs.store_dir(ctx.settings.data_dir)                 # alleen kijken óf er een lijst is (niet inlezen)
    rgs_on = d.is_dir() and any(d.glob("rgs-*.json"))
    return {"users": row["ok"], "backup": not backups.overdue(ctx.company),
            "rgs": switchover.rgs_complete(conn) if rgs_on else None}


#: deelstappen (6d-2): tekst per (punt, deelstap)
STEP_TEXTS = {
    ("company", "name"): N_("Bedrijfsnaam ingevuld"),
    ("company", "address"): N_("Adres ingevuld"),
    ("vat", "period"): N_("Aangiftetijdvak gekozen"),
    ("vat", "boxes"): N_("Elke btw-rekening een rubriek"),
    ("vat", "settlement"): N_("Afdrachtrekening gekozen"),
    ("bank", "iban"): N_("IBAN bij een betaalrekening"),
    ("bank", "statement"): N_("Eerste bankafschrift ingelezen"),
    ("bank", "match"): N_("Eerste afschrift sluit aan op de boekhouding"),
    ("users", "twofa"): N_("Tweestapsverificatie aan voor elke gebruiker"),
    ("backup", "recent"): N_("Back-up van de laatste 7 dagen"),
    ("closedto", "later"): N_("Afsluitdatum later dan bij het inlezen"),
    ("recurring", "stale"): N_("Geen schema met een volgende datum vóór de overstap"),
    ("rgs", "codes"): N_("Elke rekening een RGS-code"),
}
#: waar een open deelstap op te lossen is: (menupad, link)
STEP_WHERE = {
    ("company", "name"): ((N_("Beheer"), N_("Instellingen")), "/beheer/instellingen#bedrijf"),
    ("company", "address"): ((N_("Beheer"), N_("Instellingen")), "/beheer/instellingen#bedrijf"),
    ("vat", "period"): ((N_("Beheer"), N_("Instellingen")), "/beheer/instellingen#btw-tijdvak"),
    ("vat", "boxes"): ((N_("Btw"), N_("Btw-rubrieken")), "/btw/rubrieken"),
    ("vat", "settlement"): ((N_("Btw"), N_("Btw-rubrieken")), "/btw/rubrieken"),
    ("bank", "iban"): ((N_("Beheer"), N_("Rekeningschema")), "/beheer/rekeningschema"),
    ("bank", "statement"): ((N_("Kas en bank"), N_("Bankafschrift inlezen")), "/bank/inlezen"),
    ("bank", "match"): ((N_("Kas en bank"), N_("Bankafschrift inlezen")), "/bank/afschriften"),
    ("users", "twofa"): ((N_("Systeem"), N_("Gebruikers")), "/systeem/gebruikers"),
    ("backup", "recent"): ((N_("Systeem"), N_("Back-up maken")), "/systeem/back-up-maken"),
    ("closedto", "later"): ((N_("Beheer"), N_("Afsluiten")), "/beheer/afsluiten"),
    ("recurring", "stale"): ((N_("Terugkerend"), N_("Terugkerende boekingen")), "/terugkerend"),
    ("rgs", "codes"): ((N_("Beheer"), N_("RGS-koppeling")), "/beheer/rgs"),
}
#: lange lijsten (rekeningen, gebruikers): zoveel tonen, dan "en nog {n}" (beslispunt 2)
STEP_LIST = 5
#: alleen de systeembeheerder kan hier iets mee (Systeem-menu)
SYSADMIN_ONLY = {("users", "twofa"), ("backup", "recent")}


def _links(rows: List[Dict[str, Any]], href) -> List[Dict[str, str]]:
    return [{"label": f"{r['accno']} {r['description'] or ''}".strip(), "href": href(r)} for r in rows]


def _users_without_2fa(ctx: Ctx) -> List[Dict[str, Any]]:
    return ctx.conn.execute(
        "SELECT u.id, u.login, u.name FROM membership m JOIN app_user u ON (u.id = m.user_id) "
        "WHERE m.company_id = %s AND u.active AND u.totp_secret IS NULL ORDER BY u.login",
        (ctx.company["id"],)).fetchall()


def steps(ctx: Ctx, conn: psycopg.Connection, dash: Dashboard, keys: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Deelstappen per punt (``keys``: de punten die open staan) voor het scherm: tekst, klaar, waar op te lossen
    (menupad + link), toelichting en een lijst (rekeningen, gebruikers) met ten hoogste ``STEP_LIST`` regels."""
    from sqlledger.dashboard import SwitchStep
    from .libmsg import lib_message
    fmt = Fmt(ctx.user["numberformat"], ctx.user["dateformat"])
    sysadmin = bool(ctx.user["is_sysadmin"])
    lib = dash.switch_steps() if set(keys) & {"company", "vat", "bank", "closedto", "recurring"} else {}
    raw: Dict[str, List[SwitchStep]] = {k: v for k, v in lib.items() if k in keys}
    if "users" in keys:
        users = _users_without_2fa(ctx)
        raw["users"] = [SwitchStep("twofa", not users, [dict(u) for u in users])]
    if "backup" in keys:
        raw["backup"] = [SwitchStep("recent", not backups.overdue(ctx.company),
                                    detail={"last": ctx.company.get("last_backup")})]
    if "rgs" in keys:
        missing, total = switchover.rgs_missing(conn)
        raw["rgs"] = [SwitchStep("codes", not missing, detail={"missing": missing, "total": total})]

    def closed(n: Optional[int], d: Optional[date]) -> str:
        if not n:
            return _("geen")
        return fmt.date(d) if d else _("{value} (geen geldige datum)").format(value=n)

    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, found in raw.items():
        lst = []
        for st in found:
            (path, href) = STEP_WHERE[(key, st.key)]
            if (key, st.key) in SYSADMIN_ONLY and not sysadmin:
                path, href = (), ""
            step = {"text": _(STEP_TEXTS[(key, st.key)]), "done": st.done, "href": href,
                    "where": " > ".join(_(p) for p in path), "detail": "", "links": [], "more": "",
                    "more_href": ""}
            rows: List[Dict[str, str]] = []
            if not st.done:
                if (key, st.key) == ("vat", "boxes") and st.rows:
                    step["detail"] = _("{n} zonder rubriek:").format(n=len(st.rows))
                    rows = _links(st.rows, lambda r: "/btw/rubrieken")
                elif (key, st.key) == ("bank", "iban"):
                    # klaar bij één betaalrekening met IBAN; de lijst van alle betaalrekeningen (ook kas, kruisposten
                    # en oude SL-koppelingen) zou alleen ruis zijn
                    step["detail"] = _("nog bij geen enkele betaalrekening")
                elif (key, st.key) == ("bank", "match") and st.rows:
                    rows = [{"label": f"{r['accno']} {r['description'] or ''}".strip(),
                             "href": f"/bank/afschriften/{int(r['statement_id'])}",
                             "note": lib_message(r["warning"])} for r in st.rows]
                elif key == "users":
                    if sysadmin:
                        step["detail"] = _("zonder tweestapsverificatie:")
                        rows = [{"label": u["name"] or u["login"], "href": f"/systeem/gebruikers/{int(u['id'])}"}
                                for u in st.rows]
                    else:
                        step["detail"] = (_("1 gebruiker zonder tweestapsverificatie") if len(st.rows) == 1 else
                                          _("{n} gebruikers zonder tweestapsverificatie").format(n=len(st.rows)))
                elif key == "backup":
                    last = st.detail["last"]
                    step["detail"] = (_("laatste back-up: nooit") if last is None else
                                      _("laatste back-up: {date}, ouder dan 7 dagen").format(
                                          date=fmt.date(last.astimezone().date())))
                elif key == "closedto":
                    d = st.detail
                    now = closed(d["now"], d["now_date"])
                    # geen of een ongeldige afsluitdatum bij het inlezen (administratie A: 20173112): dan telt elke
                    # geldige datum; zeg welke (I8)
                    step["detail"] = (_("nu: {now}; moet later zijn dan bij het inlezen: {then}").format(
                        now=now, then=closed(d["then"], d["then_date"])) if d["then_date"] else
                        _("nu: {now}; kies het einde van het laatst aangegeven btw-tijdvak").format(now=now))
                elif key == "recurring" and st.rows:
                    step["detail"] = (_("1 schema; stop het of pas de volgende datum aan:") if len(st.rows) == 1 else
                                      _("{n} schema's; stop ze of pas de volgende datum aan:").format(n=len(st.rows)))
                    rows = [{"label": _("{name} – volgende {date}").format(
                                name=r["description"] or r["reference"] or f"#{int(r['id'])}",
                                date=fmt.date(r["nextdate"])),
                             "href": f"/herhalen/{int(r['id'])}", "edit": _("Volgende datum aanpassen"),
                             "stop": int(r["id"])} for r in st.rows]
                elif key == "rgs":
                    step["detail"] = _("{missing} van {total} rekeningen hebben nog geen code").format(
                        missing=st.detail["missing"], total=st.detail["total"])
            if len(rows) > STEP_LIST:
                step["more"] = _("en nog {n}").format(n=len(rows) - STEP_LIST)
                step["more_href"] = href
                rows = rows[:STEP_LIST]
            step["links"] = rows
            lst.append(step)
        out[key] = lst
    return out


def items(ctx: Ctx, conn: psycopg.Connection, dash: Dashboard, info: Optional[Dict[str, Any]],
          modules: List[str], detail: bool = False) -> List[Dict[str, Any]]:
    """Punten voor het scherm (dashboard en Beheer > Ingebruikname); leeg als de lijst uit staat of klaar is.
    ``detail``: met deelstappen (pagina); anders alleen de teller "(1 van 3)" achter een open punt (dashboard)."""
    found = dash.switch_list(modules, external(ctx, conn)) or []
    open_keys = [i.key for i in found if not i.done and not i.skipped and i.key in {k for k, _s in STEP_TEXTS}]
    if not detail:                       # dashboard: alleen de teller, alleen bij meer dan één deelstap
        open_keys = [k for k in open_keys if k in ("company", "vat", "bank")]
    per_key = steps(ctx, conn, dash, open_keys) if open_keys else {}
    numbers = dash.next_numbers() if any(i.key == "numbering" for i in found) else {}
    verdict = switchover.check_status(conn) if any(i.key == "check" for i in found) else None
    fmt = Fmt(ctx.user["numberformat"], ctx.user["dateformat"])
    out = []
    for i in found:
        text, href, help_text = ITEMS[i.key]
        hint = ""
        if i.key == "restored" and info:
            hint = backup_label(info, ctx)
        elif i.key == "vat" and i.unavailable:
            hint = _("btw-aangifte voor dit land nog niet beschikbaar")
        elif i.key == "numbering":
            hint = _("volgende: {numbers}").format(numbers=", ".join(
                f"{_(label)} {_next_number(numbers[k], ctx.user['dateformat'])}"
                for k, label in NUMBERS if numbers.get(k)) or "—")
        note = ""
        if i.key == "check" and verdict:                     # oordeel over het controleblad (PB8)
            when = fmt.date(date.fromisoformat(verdict["date"])) if verdict.get("date") else ""
            if i.done and verdict.get("verdict") == "ok":
                hint = _("akkoord op {date} door {name}").format(date=when, name=verdict.get("name") or "—")
            elif not i.done and verdict.get("verdict") == "issue":
                note = _("Er klopt iets niet: {note} ({name}, {date})").format(
                    note=verdict.get("note") or "—", name=verdict.get("name") or "—", date=when)
        if i.key in ("users", "backup") and not ctx.user["is_sysadmin"]:   # Systeem-menu (6d-1 keuze 3)
            href = ""
        st = per_key.get(i.key, [])
        count = (_("({done} van {total})").format(done=sum(1 for x in st if x["done"]), total=len(st))
                 if len(st) > 1 else "")
        out.append({"key": i.key, "text": _(text), "href": href, "help": _(help_text), "hint": hint, "note": note,
                    "done": i.done, "skipped": i.skipped, "manual": i.key in SWITCH_MANUAL,
                    "steps": st if detail else [], "count": "" if detail else count})
    return out


# ------------------------------------------------------------------ Beheer > Ingebruikname

@router.get("/beheer/ingebruikname", name="switch_page")
def switch_page(ctx: Ctx = Depends(ctx_company)):
    _require_admin(ctx)
    with connect_company(ctx.settings, ctx.company) as conn:
        info = switchover.restore_info(conn)
        dash = Dashboard(conn, ctx.user_config())
        rows = items(ctx, conn, dash, info, list(ctx.company["modules"] or []), detail=True) if info else []
        hidden = info is not None and not rows
    return render(ctx, "am/switch.html", info=info, items=rows, hidden=hidden,
                  origin=backup_label(info, ctx) if info else "")


@router.post("/beheer/ingebruikname/aanzetten", name="switch_enable")
def switch_enable(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    """Lijst aanzetten (gekoppelde administratie) of weer tonen (verborgen of alles klaar)."""
    _require_admin(ctx)
    with connect_company(ctx.settings, ctx.company) as conn:
        if switchover.restore_info(conn) is None:
            switchover.enable(conn, ctx.employee_login)
        else:
            with conn.transaction():
                conn.execute("DELETE FROM x_task_done WHERE kind = 'switch_hidden'")
    ctx.log("switch_list_enabled")
    raise Redirect("/beheer/ingebruikname")


@router.post("/dashboard/overstaplijst")
def switch_mark(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    _require_admin(ctx)
    key = str(form.get("item") or "")
    done = form.get("done") == "1"
    if key != "hidden" and (key not in SWITCH_ITEMS or (done and key not in SWITCH_MANUAL)):
        raise HTTPException(400, _("Onbekend punt."))
    with connect_company(ctx.settings, ctx.company) as conn:
        Dashboard(conn, ctx.user_config()).switch_mark(key, done, ctx.employee_login)
    back = str(form.get("back") or "/")
    raise Redirect(back if back in ("/", "/beheer/ingebruikname") else "/")


# ------------------------------------------------------------------ controleblad (I6)

def _peildatum(ctx: Ctx, info: Optional[Dict[str, Any]]) -> date:
    q = ctx.request.query_params.get("datum", "")
    try:
        if q:
            return date.fromisoformat(q)
    except ValueError:
        pass
    last = (info or {}).get("last_transdate")
    try:
        return date.fromisoformat(last) if last else date.today()
    except ValueError:
        return date.today()


def _run(key: str, lib: Lib, fmt: Fmt, peil: date, **values) -> List[Table]:
    rep = REPORTS[key]
    v = default_values(rep.fields, peil)
    v.update(values)
    res = rep.run(lib, v, fmt)
    for t in res.tables:
        t.title = t.title or _(rep.title)
    if res.tables and res.info:
        res.tables[0].title = f"{_(rep.title)} – {res.info[0]}"
    return res.tables


def sheet(ctx: Ctx, conn: psycopg.Connection, info: Optional[Dict[str, Any]], peil: date) -> List[Table]:
    fmt = Fmt(ctx.user["numberformat"], ctx.user["dateformat"])
    mods = set(ctx.company["modules"] or [])
    tables: List[Table] = []
    findings = switchover.integrity(conn, info)
    rows = [Row("row", {"check": _(CHECK_LABELS[f.key]), "count": f.count, "level": _(LEVEL_TEXT[f.level])})
            for f in findings]
    tables.append(Table([Col("check", _("Controle")), Col("count", _("Aantal")), Col("level", _("Uitkomst"))],
                        rows, _("Controles")))
    for f in findings:
        if not f.rows:
            continue
        cols = [Col("reference", _("Boeking")), Col("transdate", _("Datum"), "date")]
        if f.key in ("unbalanced", "fx_rounding"):
            cols += [Col("module", _("Soort")), Col("diff", _("Verschil"), "amount", dash="0")]
        elif f.key == "missing_account":
            cols += [Col("chart_id", _("Rekening-id")), Col("amount", _("Bedrag"), "amount", dash="0")]
        elif f.key == "orphan":
            cols += [Col("lines", _("Regels"))]
        else:
            cols += [Col("curr", _("Valuta")), Col("amount", _("Bedrag"), "amount", dash="0"),
                     Col("paid", _("Betaald"), "amount", dash="0")]
            if f.key == "arap_mismatch":
                cols += [Col("account", _("Op de rekening"), "amount", dash="0"),
                         Col("diff", _("Verschil"), "amount", dash="0")]
        title = _(CHECK_LABELS[f.key])
        if f.count > len(f.rows):
            title += " " + _("(eerste {n} van {total})").format(n=len(f.rows), total=f.count)
        tables.append(Table(cols, [Row("row", {**r, "reference": r.get("reference") or r.get("trans_id")},
                                       {"reference": booking_link(r.get("trans_id"))}) for r in f.rows], title))
    years = switchover.counts_per_year(conn)
    tables.append(Table([Col("year", _("Jaar")), Col("gl", _("Memoriaal")), Col("ar", _("Verkoop")),
                         Col("ap", _("Inkoop")), Col("lines", _("Grootboekregels"))],
                        [Row("row", y) for y in years], _("Aantal boekingen per jaar")))
    p = get_precision(cursor(conn))
    bal = switchover.payment_balances(conn, peil)
    tables.append(Table([Col("accno", _("Rekening")), Col("description", _("Omschrijving")), Col("iban", "IBAN"),
                         Col("balance", _("Saldo"), "amount", p, dash="0")],
                        [Row("row", b) for b in bal],
                        _("Kas en bank per {date}").format(date=fmt.date(peil))))
    lib = Lib(conn, ctx.user_config())
    with cursor(conn) as cur:
        start = fiscal_year(peil, get_yearend(cur))[0].isoformat()     # F22/JA7: begin van het boekjaar
    period = dict(period="", fromdate=start, todate=peil.isoformat())
    tables += _run("proefbalans", lib, fmt, peil, **period)
    tables += _run("winst-en-verlies", lib, fmt, peil, **period)
    tables += _run("balans", lib, fmt, peil, asof=peil.isoformat())
    for arap, title, who in (("ar", N_("Openstaande verkoopfacturen per klant per {date}"), N_("Klant")),
                             ("ap", N_("Openstaande inkoopfacturen per leverancier per {date}"), N_("Leverancier"))):
        if arap not in mods:
            continue
        rows = [Row("row", r) for r in switchover.open_per_relation(conn, arap, peil)]
        if rows:
            rows.append(Row("total", {"name": _("Totaal"), "count": sum(r.cells["count"] for r in rows),
                                      "open": sum(r.cells["open"] for r in rows),
                                      "overdue": sum(r.cells["overdue"] for r in rows)}))
        tables.append(Table([Col("name", _(who)), Col("number", _("Nummer")), Col("count", _("Facturen")),
                             Col("open", _("Openstaand"), "amount", p, dash="0"),
                             Col("overdue", _("Waarvan vervallen"), "amount", p, dash="0")],
                            rows, _(title).format(date=fmt.date(peil))))
    return tables


@router.post("/beheer/ingebruikname/controleblad/oordeel", name="switch_sheet_verdict")
def switch_sheet_verdict(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    """Onderaan het controleblad (PB8): "Cijfers kloppen – akkoord" vinkt het punt af; "Er klopt iets niet" legt
    een notitie vast en laat het punt open."""
    _require_admin(ctx)
    ok = form.get("verdict") == "ok"
    note = str(form.get("note") or "").strip()
    datum = str(form.get("datum") or "")
    if not ok and not note:
        raise Redirect(f"/beheer/ingebruikname/controleblad?ok=switch_note_missing&datum={_iso(datum)}#oordeel")
    with connect_company(ctx.settings, ctx.company) as conn:
        switchover.check_verdict(conn, ok, note, ctx.user["name"] or ctx.user["login"], ctx.employee_login)
    ctx.log("switch_check_ok" if ok else "switch_check_issue", note=note[:300] if not ok else "",
            peildatum=_iso(datum))
    if ok:
        raise Redirect("/beheer/ingebruikname?ok=switch_check_ok")
    raise Redirect(f"/beheer/ingebruikname/controleblad?ok=switch_check_issue&datum={_iso(datum)}#oordeel")


def _iso(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return ""


@router.get("/beheer/ingebruikname/controleblad", name="switch_sheet")
def switch_sheet(ctx: Ctx = Depends(ctx_company)):
    _require_admin(ctx)
    fmt = Fmt(ctx.user["numberformat"], ctx.user["dateformat"])
    error = ""
    with connect_company(ctx.settings, ctx.company) as conn:
        info = switchover.restore_info(conn)
        peil = _peildatum(ctx, info)
        try:
            tables = sheet(ctx, conn, info, peil)
        except (LedgerError, ValueError) as exc:
            from .libmsg import lib_message
            tables, error = [], lib_message(exc)
        company = get_defaults(cursor(conn), "company").get("company")
        verdict = switchover.check_status(conn)
    title = _("Controleblad overstap")
    lines = [_("Peildatum {date}").format(date=fmt.date(peil))]
    if info and info.get("kind") != "linked":
        lines.append(backup_label(info, ctx))
    if ctx.request.query_params.get("export") == "pdf" and tables:
        return pdf_response(ctx, tables, title, lines, "controleblad", company,
                            back=f"/beheer/ingebruikname/controleblad?datum={peil.isoformat()}")
    return render(ctx, "am/switch_sheet.html", tables=tables, title=title, lines=lines, peil=peil, error=error,
                  verdict=verdict, verdict_date=fmt.date(date.fromisoformat(verdict["date"]))
                  if verdict and verdict.get("date") else "",
                  cell=lambda c, r: cell_text(c, r, fmt.nf, fmt.df))
