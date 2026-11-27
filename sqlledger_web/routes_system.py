"""Systeembeheer: gebruikers, administraties, rechten en logboek (alleen systeembeheerders)."""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

import sqlledger
from sqlledger import chartfiles, newcompany
from sqlledger.errors import ValidationError

from . import accounts, security
from .appdb import MODULES, ROLES
from .companies import DBNAME_RE, MIN_VERSION, connect_company, probe, too_new, too_old
from .i18n import N_, gettext as _
from .web import Ctx, Redirect, csrf_form, ctx_sysadmin, render

router = APIRouter(prefix="/systeem")

LOG_PAGE = 100
MIN_TEXT = ".".join(map(str, MIN_VERSION))
#: melding bij een te oude SQL-Ledger-database (ES12 A); ook in _probe.html
OLD_VERSION = N_("Deze database heeft SQL-Ledger-versie {version}; nodig is {minimum} of hoger. Werk hem eerst "
                 "bij (zie de handleiding, \"Oude SQL-Ledger-versie\"); straks gaat dat vanzelf bij Back-up inlezen.")
#: melding bij een database nieuwer dan 3.2.4 (SQL-Ledger 4.x): koppelen mag, maar zonder ondersteuning
NEW_VERSION = N_("Deze database heeft versie {version}, nieuwer dan SQL-Ledger 3.2.12 (databaseversie 3.2.4). "
                 "Koppelen kan, maar PyBalans ondersteunt deze versie niet: gebruik op eigen risico.")


def _user_or_404(ctx: Ctx, user_id: int) -> dict:
    user = accounts.get_user(ctx.conn, user_id=user_id)
    if not user:
        raise HTTPException(404, _("Gebruiker niet gevonden."))
    return user


# --- gebruikers --------------------------------------------------------------

@router.get("/gebruikers")
def users(ctx: Ctx = Depends(ctx_sysadmin)):
    show_all = ctx.request.query_params.get("uit") == "1"   # F27a: uitgezette gebruikers alleen op verzoek
    rows = ctx.conn.execute(
        "SELECT u.*, (SELECT array_agg(ARRAY[c.name, m.role] ORDER BY c.name) "
        "  FROM membership m JOIN company c ON c.id = m.company_id WHERE m.user_id = u.id) AS memberships "
        "FROM app_user u" + ("" if show_all else " WHERE u.active") + " ORDER BY u.login").fetchall()
    # rol in de taal van het scherm (F14), niet de interne sleutel
    rows = [{**r, "companies": ", ".join(f"{name} ({_(ROLES.get(role, role))})" for name, role in r["memberships"] or [])}
            for r in rows]
    inactive = ctx.conn.execute("SELECT count(*) AS n FROM app_user WHERE NOT active").fetchone()["n"]
    return render(ctx, "system/users.html", users=rows, now=accounts.now(), show_all=show_all, inactive=inactive)


@router.get("/gebruikers/nieuw")
def user_new_form(ctx: Ctx = Depends(ctx_sysadmin)):
    return render(ctx, "system/user_new.html", values={})


def _deleted_login(ctx: Ctx, login: str) -> dict | None:
    """Laatste keer dat deze login verwijderd is (F27a: waarschuwen bij hergebruik)."""
    return ctx.conn.execute("SELECT at, detail FROM audit_log WHERE action = 'user_deleted' AND detail->>'target' = %s "
                            "ORDER BY id DESC LIMIT 1", (login,)).fetchone()


@router.post("/gebruikers/nieuw")
def user_new(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    values = {k: str(form.get(k, "")) for k in ("login", "name", "email")}
    values["is_sysadmin"] = form.get("is_sysadmin") == "1"
    old = None
    try:
        login = accounts.check_login(values["login"])
        same = accounts.get_user(ctx.conn, login=login)
        if same and not same["active"]:
            raise accounts.AccountError(_("Login '{login}' bestaat al, maar staat uit. Open die gebruiker en zet hem "
                                          "weer aan.").format(login=login))
        old = None if same else _deleted_login(ctx, login)
        if old and form.get("reuse") != "1":
            return render(ctx, "system/user_new.html", 400, values=values, reuse=old,
                          error=_("De login '{login}' is eerder gebruikt en op {date} verwijderd. In het logboek "
                                  "staan beide gebruikers dan onder dezelfde login.").format(
                                      login=login, date=old["at"].strftime("%Y-%m-%d")))
        user_id = accounts.create_user(ctx.conn, values["login"], values["name"], values["email"],
                                       is_sysadmin=form.get("is_sysadmin") == "1")
    except accounts.AccountError as exc:
        return render(ctx, "system/user_new.html", 400, values=values, error=str(exc))
    user = accounts.get_user(ctx.conn, user_id=user_id)
    ctx.log("user_created", target=user["login"], sysadmin=user["is_sysadmin"], **({"reused": True} if old else {}))
    return _code_page(ctx, user)


def _mail_companies(ctx: Ctx, user: dict) -> list:
    """Administraties waarvan de e-mailinstellingen een code kunnen versturen (eerste start, keuze 11): die
    van de gebruiker eerst."""
    from . import mailer
    rows = ctx.conn.execute(
        "SELECT c.id, c.name, EXISTS (SELECT 1 FROM membership m WHERE m.company_id = c.id AND m.user_id = %s) AS own "
        "FROM company c JOIN company_mail cm ON cm.company_id = c.id WHERE c.active AND cm.mode <> 'off' "
        "ORDER BY 3 DESC, c.name", (user["id"],)).fetchall()
    return [r for r in rows if mailer.load(ctx.conn, r["id"]).enabled]


def _code_page(ctx: Ctx, user: dict, code: str | None = None, status: int = 200, **kw) -> HTMLResponse:
    if code is None:
        code = accounts.issue_setup_code(ctx.conn, ctx.settings, user["id"], ctx.user["id"])
        ctx.log("code_created", target=user["login"])
    return render(ctx, "system/code.html", status, target=user, code=code,
                  hours=ctx.settings.setup_code_hours, mail_companies=_mail_companies(ctx, user) if user["email"] else [],
                  url=_activate_url(ctx, user), **kw)


def _activate_url(ctx: Ctx, user: dict) -> str:
    return str(ctx.request.url_for("activate_form").include_query_params(login=user["login"]))


@router.post("/gebruikers/{user_id}/code-mailen")
def user_code_mail(user_id: int, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    """De zojuist getoonde code mailen met de e-mailinstellingen van een administratie (keuze 11). Alleen een
    geldige, ongebruikte code van deze gebruiker; de code zelf wordt nergens bewaard."""
    from . import mailer
    user = _user_or_404(ctx, user_id)
    code = security.normalize_code(str(form.get("code", "")))
    valid = ctx.conn.execute(
        "SELECT 1 FROM setup_code WHERE user_id = %s AND code_hash = %s AND used IS NULL AND expires > now()",
        (user["id"], security.token_hash(code))).fetchone()
    if not valid:
        raise HTTPException(400, _("Deze code is niet (meer) geldig. Maak een nieuwe code."))
    cid = str(form.get("company_id", ""))
    company = next((c for c in _mail_companies(ctx, user) if str(c["id"]) == cid), None)
    if not company or not user["email"]:
        raise HTTPException(400, _("E-mail is niet ingesteld voor deze administratie (Beheer > E-mail)."))
    ms = mailer.load(ctx.conn, company["id"])
    body = "\n".join([
        _("Uw account voor PyBalans staat klaar."), "",
        _("Login: {login}").format(login=user["login"]), _("Eenmalige code: {code}").format(code=code), "",
        _("Ga naar {url} en kies daar een wachtwoord. De code is {hours} uur geldig en werkt één keer.").format(
            url=_activate_url(ctx, user), hours=ctx.settings.setup_code_hours)])
    try:
        out = mailer.compose(user["email"], "", "", _("Uw account voor PyBalans"), body)
        how = mailer.send(ctx.settings, ms, mailer.build_message(ms, out, reply_to=ctx.user.get("email") or ""),
                          out.recipients)
    except mailer.MailError as exc:
        ctx.log("code_mail_failed", target=user["login"], error=str(exc)[:300])
        return _code_page(ctx, user, code, 400, error=exc.text())
    ctx.log("code_mailed", target=user["login"], to=user["email"], company=company["name"], via=how)
    return _code_page(ctx, user, code, notice=_("Code gemaild naar {adres}.").format(adres=user["email"]))


def _user_page(ctx: Ctx, user: dict, status: int = 200, **kw):
    memberships = {r["company_id"]: r for r in ctx.conn.execute(
        "SELECT * FROM membership WHERE user_id = %s", (user["id"],)).fetchall()}
    companies = ctx.conn.execute("SELECT * FROM company ORDER BY name").fetchall()
    sessions = ctx.conn.execute(
        "SELECT count(*) AS n FROM app_session WHERE user_id = %s", (user["id"],)).fetchone()["n"]
    return render(ctx, "system/user_edit.html", status, target=user, companies=companies,
                  memberships=memberships, sessions=sessions, now=accounts.now(),
                  employees={c["id"]: employee_choices(ctx, c, user, memberships.get(c["id"])) for c in companies},
                  totp_required=accounts.needs_totp(ctx.conn, user), deletable=_delete_refusal(ctx, user) is None,
                  **kw)


def _new_employee(ctx: Ctx, company_id: int, user: dict) -> str:
    """Medewerker met naam en login van de gebruiker in de administratie (PB14); geeft de login."""
    from .routes_restore import EMP_NEW, link_employee
    company = ctx.conn.execute("SELECT * FROM company WHERE id = %s", (company_id,)).fetchone()
    try:
        with connect_company(ctx.settings, company) as conn:
            login = link_employee(conn, EMP_NEW, user)
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    ctx.log("employee_created", target=user["login"], company=company["name"])
    return login


def employee_choices(ctx: Ctx, company: dict, user: dict, membership: dict | None) -> list | None:
    """Keuzelijst "Medewerker" per administratie (v1.0.1 PB14): de medewerkers met een login (label "naam
    (login)", waarde de login), plus de huidige koppeling als die geen medewerker is. Gekozen: de huidige
    koppeling, anders de medewerker met dezelfde naam, anders de eigen login. None als de administratie niet
    bereikbaar is (dan een gewoon tekstveld)."""
    from .routes_restore import _norm
    try:
        with connect_company(ctx.settings, company) as conn:
            rows = conn.execute("SELECT login, name, enddate FROM employee WHERE coalesce(login, '') <> '' "
                                "ORDER BY lower(name), login").fetchall()
    except psycopg.Error:
        return None
    current = (membership or {}).get("employee_login") or ""
    if not current:
        same = [r for r in rows if user.get("name") and _norm(r[1] or "") == _norm(user["name"])]
        current = same[0][0] if same else user["login"]
    out = [{"value": r[0], "label": f"{r[1] or '—'} ({r[0]})", "left": r[2] is not None, "selected": r[0] == current}
           for r in rows]
    if not any(o["selected"] for o in out):
        out.append({"value": current, "label": _("{login} (geen medewerker)").format(login=current), "left": False,
                    "selected": True})
    if not any(r[0] == user["login"] for r in rows):
        out.append({"value": NEW_EMPLOYEE, "left": False, "selected": False,
                    "label": _("nieuwe medewerker aanmaken: {name} ({login})").format(
                        name=user.get("name") or user["login"], login=user["login"])})
    return out


#: keuze "nieuwe medewerker aanmaken" bij Medewerker (geen geldige login, dus geen botsing)
NEW_EMPLOYEE = "+new"


@router.get("/gebruikers/{user_id}")
def user_edit_form(user_id: int, ctx: Ctx = Depends(ctx_sysadmin)):
    return _user_page(ctx, _user_or_404(ctx, user_id))


def _last_sysadmin(ctx: Ctx, user: dict) -> bool:
    """F27a: ``user`` is de enige actieve systeembeheerder."""
    return bool(user["is_sysadmin"] and user["active"] and not ctx.conn.execute(
        "SELECT 1 FROM app_user WHERE is_sysadmin AND active AND id <> %s", (user["id"],)).fetchone())


def _delete_refusal(ctx: Ctx, user: dict) -> str | None:
    """F27a A: echt verwijderen alleen wie nooit heeft ingelogd (verkeerd aangemaakt); anders de reden."""
    if user["id"] == ctx.user["id"]:
        return _("Uw eigen account kunt u niet verwijderen.")
    if _last_sysadmin(ctx, user):
        return _("Er moet minstens één actieve systeembeheerder blijven.")
    if user["last_login"] is not None:
        return _("Deze gebruiker heeft al eens ingelogd. Zet hem uit in plaats van te verwijderen; het logboek "
                 "blijft dan compleet.")
    return None


def short_login(login: str | None) -> str:
    """Login zoals SQL-Ledger hem in ``reference.login`` zet (zonder ``@dataset``)."""
    return (login or "").split("@", 1)[0]


def _employee_taken(ctx: Ctx, user: dict, company_id: int, emp: str) -> str | None:
    """F42 A: login van een andere actieve gebruiker die in deze administratie al medewerker ``emp`` is."""
    row = ctx.conn.execute(
        "SELECT u.login FROM membership m JOIN app_user u ON u.id = m.user_id WHERE m.company_id = %s AND u.active "
        "AND u.id <> %s AND lower(split_part(m.employee_login, '@', 1)) = lower(%s) ORDER BY u.login LIMIT 1",
        (company_id, user["id"], short_login(emp))).fetchone()
    return row["login"] if row else None


def _confidential_count(ctx: Ctx, company: dict, login: str) -> int:
    """Aantal vertrouwelijke bijlagen onder deze login (F42); 0 als de administratie niet bereikbaar is."""
    try:
        with connect_company(ctx.settings, company) as conn:
            return conn.execute("SELECT count(*) FROM reference WHERE login = %s", (short_login(login),)).fetchone()[0]
    except psycopg.Error:
        return 0


def _move_confidential(ctx: Ctx, user: dict, company: dict, old: str, new: str) -> None:
    """Vertrouwelijke bijlagen van medewerker ``old`` naar ``new`` (F42 A, na bevestiging)."""
    try:
        with connect_company(ctx.settings, company) as conn:
            n = conn.execute("UPDATE reference SET login = %s WHERE login = %s",
                             (short_login(new), short_login(old))).rowcount
    except psycopg.Error:
        raise HTTPException(503, _("De administratie is niet bereikbaar."))
    ctx.log("attachments_moved", target=user["login"], company=company["name"], old=short_login(old),
            new=short_login(new), count=n)


@router.post("/gebruikers/{user_id}")
def user_edit(user_id: int, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    user = _user_or_404(ctx, user_id)
    action = form.get("action")
    self_edit = user["id"] == ctx.user["id"]
    if action == "save":
        is_sysadmin = form.get("is_sysadmin") == "1"
        active = form.get("active") == "1"
        if self_edit and (not is_sysadmin or not active):
            return _user_page(ctx, user, 400,
                              error=_("U kunt uw eigen beheerrechten of account niet uitzetten."))
        if (not is_sysadmin or not active) and _last_sysadmin(ctx, user):
            return _user_page(ctx, user, 400, error=_("Er moet minstens één actieve systeembeheerder blijven."))
        companies = {c["id"]: c for c in ctx.conn.execute("SELECT * FROM company").fetchall()}
        old = {r["company_id"]: r for r in ctx.conn.execute(
            "SELECT m.*, c.name FROM membership m JOIN company c ON c.id = m.company_id WHERE m.user_id = %s",
            (user["id"],)).fetchall()}
        roles = {}
        for cid in companies if active else ():            # F27a A: uitzetten haalt ook alle toegang weg
            role = form.get(f"role_{cid}", "")
            if role:
                if role not in ROLES:
                    raise HTTPException(400, _("Ongeldige rol."))
                roles[cid] = (role, str(form.get(f"employee_{cid}", "")).strip() or user["login"])
        changes = []
        for cid, (role, emp) in roles.items():
            login = user["login"] if emp == NEW_EMPLOYEE else emp
            other = _employee_taken(ctx, user, cid, login)
            if other:
                return _user_page(ctx, user, 400, error=_(
                    "In {company} is medewerker {login} al gekoppeld aan gebruiker {other}. Twee actieve gebruikers "
                    "kunnen niet dezelfde medewerker zijn: ze zouden elkaars vertrouwelijke bijlagen en uren zien."
                    ).format(company=companies[cid]["name"], login=login, other=other))
            before = old[cid]["employee_login"] if cid in old else None
            if before and short_login(before) != short_login(login):
                n = _confidential_count(ctx, companies[cid], before)
                if n:
                    changes.append({"company_id": cid, "company": companies[cid]["name"], "old": short_login(before),
                                    "new": short_login(login), "count": n})
        if changes and form.get("moves") != "1":            # F42 A: eerst vragen wat er met de bijlagen gebeurt
            return render(ctx, "system/user_empchange.html", target=user, changes=changes,
                          fields=[(k, v) for k, v in form.multi_items() if k not in ("csrf", "moves")
                                  and not k.startswith("move_")])
        for cid, (role, emp) in roles.items():
            if emp == NEW_EMPLOYEE:
                roles[cid] = (role, _new_employee(ctx, cid, user))
        with ctx.conn.transaction():
            ctx.conn.execute(
                "UPDATE app_user SET name = %s, email = %s, is_sysadmin = %s, active = %s WHERE id = %s",
                (str(form.get("name", "")).strip(), str(form.get("email", "")).strip(),
                 is_sysadmin, active, user["id"]))
            ctx.conn.execute("DELETE FROM membership WHERE user_id = %s", (user["id"],))
            for cid, (role, emp) in roles.items():
                ctx.conn.execute(
                    "INSERT INTO membership (user_id, company_id, role, employee_login) "
                    "VALUES (%s, %s, %s, %s)", (user["id"], cid, role, emp))
            if not active:
                ctx.conn.execute("DELETE FROM app_session WHERE user_id = %s", (user["id"],))
                ctx.conn.execute("DELETE FROM setup_code WHERE user_id = %s AND used IS NULL", (user["id"],))
        detail = {}
        if not active and old:
            detail["removed"] = sorted(m["name"] for m in old.values())
        ctx.log("user_updated", target=user["login"], sysadmin=is_sysadmin, active=active,
                roles={str(k): v[0] for k, v in roles.items()}, **detail)
        for ch in changes:
            if form.get(f"move_{ch['company_id']}") == "1":
                _move_confidential(ctx, user, companies[ch["company_id"]], ch["old"], ch["new"])
        raise Redirect(f"/systeem/gebruikers/{user['id']}?ok=saved")
    if action == "code":
        return _code_page(ctx, user)
    if action == "totp_reset":
        ctx.conn.execute("UPDATE app_user SET totp_secret = NULL, totp_last_step = NULL WHERE id = %s",
                         (user["id"],))
        ctx.conn.execute("DELETE FROM app_session WHERE user_id = %s", (user["id"],))
        ctx.log("totp_reset", target=user["login"])
        raise Redirect(f"/systeem/gebruikers/{user['id']}?ok=saved")
    if action == "unlock":
        ctx.conn.execute("UPDATE app_user SET failed_count = 0, locked_until = NULL WHERE id = %s",
                         (user["id"],))
        ctx.log("user_unlocked", target=user["login"])
        raise Redirect(f"/systeem/gebruikers/{user['id']}?ok=saved")
    if action == "sessions":
        if self_edit:
            ctx.conn.execute("DELETE FROM app_session WHERE user_id = %s AND token_hash <> %s",
                             (user["id"], ctx.session["token_hash"]))
        else:
            ctx.conn.execute("DELETE FROM app_session WHERE user_id = %s", (user["id"],))
        ctx.log("sessions_ended", target=user["login"])
        raise Redirect(f"/systeem/gebruikers/{user['id']}?ok=saved")
    raise HTTPException(400, _("Onbekende actie."))


@router.get("/gebruikers/{user_id}/verwijderen")
def user_delete_form(user_id: int, ctx: Ctx = Depends(ctx_sysadmin)):
    user = _user_or_404(ctx, user_id)
    refusal = _delete_refusal(ctx, user)
    if refusal:
        return _user_page(ctx, user, 400, error=refusal)
    return render(ctx, "system/user_delete.html", target=user)


@router.post("/gebruikers/{user_id}/verwijderen")
def user_delete(user_id: int, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    """F27a A: een verkeerd aangemaakte gebruiker (nooit ingelogd) echt verwijderen. Lidmaatschappen, codes en
    sessies gaan mee (ON DELETE CASCADE); het logboek houdt de login als tekst."""
    user = _user_or_404(ctx, user_id)
    refusal = _delete_refusal(ctx, user)
    if refusal:
        return _user_page(ctx, user, 400, error=refusal)
    companies = [r["name"] for r in ctx.conn.execute(
        "SELECT c.name FROM membership m JOIN company c ON c.id = m.company_id WHERE m.user_id = %s ORDER BY c.name",
        (user["id"],)).fetchall()]
    ctx.conn.execute("DELETE FROM app_user WHERE id = %s", (user["id"],))
    ctx.log("user_deleted", target=user["login"], name=user["name"], email=user["email"], companies=companies)
    raise Redirect("/systeem/gebruikers?ok=user_deleted")


# --- administraties ----------------------------------------------------------

#: korte code per administratie (AW5), zoals Exact 108220
CODE_MAX = 12

def _company_values(form: FormData) -> dict:
    port = str(form.get("dbport", "")).strip()
    return {
        "name": str(form.get("name", "")).strip(),
        "code": str(form.get("code", "")).strip(),
        "dbname": str(form.get("dbname", "")).strip(),
        "dbhost": str(form.get("dbhost", "")).strip() or None,
        "dbport": int(port) if port.isdigit() else None,
        "modules": [m for m in MODULES if form.get(f"mod_{m}") == "1"],
        "active": form.get("active") == "1",
    }


def _check_company(values: dict) -> None:
    if not values["name"]:
        raise accounts.AccountError(_("Naam is verplicht."))
    if not DBNAME_RE.match(values["dbname"]):
        raise accounts.AccountError(_("Ongeldige databasenaam."))
    if len(values["code"]) > CODE_MAX or any(ch.isspace() for ch in values["code"]):
        raise accounts.AccountError(_("Code: hoogstens {n} tekens, zonder spaties.").format(n=CODE_MAX))


#: aantal actieve gebruikers met toegang tot administratie ``c`` (F23: 0 = niemand kan haar openen)
ACCESS_SQL = ("SELECT count(*) FROM membership m JOIN app_user u ON u.id = m.user_id "
              "WHERE m.company_id = c.id AND u.active")


@router.get("/administraties")
def companies(ctx: Ctx = Depends(ctx_sysadmin)):
    rows = ctx.conn.execute(
        "SELECT c.*, (SELECT count(*) FROM membership m WHERE m.company_id = c.id) AS users, "
        f"({ACCESS_SQL}) AS access FROM company c ORDER BY c.name").fetchall()
    return render(ctx, "system/companies.html", companies=rows)


@router.get("/administraties/nieuw")
def company_new_form(ctx: Ctx = Depends(ctx_sysadmin)):
    return render(ctx, "system/company_edit.html", company=None,
                  values={"modules": list(MODULES), "active": True, "add_me": True},
                  action="/systeem/administraties/nieuw")


# --- nieuwe administratie aanmaken (RS1) ----------------------------------------

#: databasenaam voor een nieuwe administratie: strenger dan bij koppelen (geen hoofdletters, punt of streepje)
NEW_DBNAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
#: talen van de omschrijvingen (de webapp kent deze vier)
CHART_LANGUAGES = {"nl": N_("Nederlands"), "en": N_("Engels"), "de": N_("Duits"), "fr": N_("Frans"),
                   "it": N_("Italiaans")}
#: documenttalen (``company.doc_language``); bij rekeningnamen in een andere taal (Italiaans) die van het scherm
DOC_LANGS = ("nl", "en", "de", "fr")
#: landen van de meegeleverde schema's (codes en volgorde in ``chartfiles.COUNTRIES``)
COUNTRY_NAMES = {"NL": N_("Nederland"), "BE": N_("België"), "GB": N_("Verenigd Koninkrijk"), "FR": N_("Frankrijk"),
                 "DE": N_("Duitsland"), "CH": N_("Zwitserland")}
MONTHS = (N_("januari"), N_("februari"), N_("maart"), N_("april"), N_("mei"), N_("juni"), N_("juli"),
          N_("augustus"), N_("september"), N_("oktober"), N_("november"), N_("december"))


#: korte uitleg per standaardschema onder "Land en schema" (v1.0.1 PB24); wisselt mee met de keuze
#: (static/companycreate.js). Een leeg schema (leeg-…) krijgt EMPTY_CHART_HELP, een onbekend schema CHART_HELP_OTHER.
CHART_HELP = {
    "nl-eenmanszaak": N_("Volledig schema voor een eenmanszaak of vof (MKB), met RGS-codes."),
    "nl-bv": N_("Volledig schema voor een bv (MKB), met RGS-codes."),
    "nl-basis": N_("Kort schema met ongeveer 60 rekeningen, zelf uit te breiden, met RGS-codes."),
    "be-mar": N_("Schema voor een Belgische kmo volgens het minimumrekeningenstelsel (MAR)."),
    "uk-ltd": N_("Schema voor een Britse limited company (MKB)."),
    "fr-pcg": N_("Verkort Plan comptable général voor een Franse kmo."),
    "ch-kmu": N_("Schweizer Kontenrahmen KMU voor een Zwitserse kmo."),
}
EMPTY_CHART_HELP = N_("Het lege schema heeft alleen de rekeningen die het programma nodig heeft: kas, bank, debiteuren, "
                      "crediteuren, btw, voorraad, omzet, kosten en eigen vermogen.")
CHART_HELP_OTHER = N_("Eigen of extra schema; zie de rekeningen na het aanmaken bij Beheer > Rekeningschema.")
#: keuzelijst Standaardvaluta (PB25): gangbare ISO-valuta; de eerste keuze (leeg) = valuta van het schema
CURRENCIES = ("EUR", "CHF", "GBP", "USD", "DKK", "SEK", "NOK", "PLN", "CZK", "HUF", "RON", "BGN", "ISK", "CAD",
              "AUD", "NZD", "JPY", "CNY", "HKD", "SGD", "ZAR", "TRY", "INR", "BRL", "MXN")


def chart_help(chart_id: str) -> str:
    if chart_id.startswith("leeg-"):
        return _(EMPTY_CHART_HELP)
    return _(CHART_HELP.get(chart_id, CHART_HELP_OTHER))


def create_page(ctx: Ctx, values: dict, status: int = 200, error: str | None = None, wizard: bool = False):
    """Formulier nieuwe administratie; ook stap 4 van de eerste start (``routes_start``, keuze 7)."""
    charts = chartfiles.available()
    groups = [(COUNTRY_NAMES.get(code, code), [c for c in charts if c.country == code])
              for code in dict.fromkeys(c.country for c in charts)]
    chosen = next((c for c in charts if c.id == values.get("chart")), charts[0] if charts else None)
    currencies = list(CURRENCIES)
    cur = (values.get("currency") or "").upper()
    if cur and cur not in currencies:                        # wat iemand eerder koos/stuurde blijft zichtbaar
        currencies.insert(0, cur)
    return render(ctx, "system/company_create.html", status, values=values, groups=groups,
                  chart_help=chart_help, chosen=chosen, currencies=currencies,
                  languages=CHART_LANGUAGES, months=MONTHS, error=error, wizard=wizard,
                  action="/welkom/administratie/nieuw" if wizard else "/systeem/administraties/aanmaken")


def suggest_dbname(name: str) -> str:
    """Databasenaam uit de naam van de administratie: kleine letters, cijfers en _ (keuze 7; ook static/dbname.js)."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not text or not text[0].isalpha():
        text = "adm_" + text if text else "administratie"
    return text[:63].rstrip("_")


@router.get("/administraties/aanmaken")
def company_create_form(ctx: Ctx = Depends(ctx_sysadmin)):
    return create_page(ctx, {"modules": list(MODULES), "chart": "nl-eenmanszaak", "yearend": 12, "add_me": True})


def make_company(ctx: Ctx, form: FormData) -> tuple[int | None, dict, str | None]:
    """Nieuwe database + rij in ``company`` (RS1); geeft (id, ingevulde waarden, foutmelding)."""
    values = _company_values(form)
    values.update(chart=str(form.get("chart", "")), language=str(form.get("language", "")),
                  currency=str(form.get("currency", "")).strip().upper(), add_me=form.get("add_me") == "1")
    ym = str(form.get("yearend", ""))
    values["yearend"] = int(ym) if ym.isdigit() else 0
    if not values["dbname"] and values["name"]:
        values["dbname"] = suggest_dbname(values["name"])
    try:
        _check_company(values)
        if not NEW_DBNAME_RE.match(values["dbname"]):
            raise accounts.AccountError(_("Databasenaam: kleine letters, cijfers en _, beginnend met een letter."))
        if ctx.conn.execute("SELECT 1 FROM company WHERE lower(name) = lower(%s) OR "
                            "(code <> '' AND lower(code) = lower(%s))", (values["name"], values["code"])).fetchone():
            raise accounts.AccountError(_("Er is al een administratie met deze naam of code."))
        chart = chartfiles.get(values["chart"])
        new = newcompany.NewCompany(
            dbname=values["dbname"], company=values["name"], chart=chart, language=values["language"],
            currency=values["currency"], yearend=values["yearend"] or 0,
            employee_login=ctx.user["login"] if values["add_me"] else "",
            employee_name=ctx.user["name"] if values["add_me"] else "", login=ctx.user["login"])
        with sqlledger.connect(**ctx.settings.company_conninfo("postgres"),
                               connect_timeout=5) as admin:
            info = newcompany.create(
                admin, lambda d: sqlledger.connect(**ctx.settings.company_conninfo(d), connect_timeout=5), new)
    except (accounts.AccountError, ValidationError) as exc:
        return None, values, str(exc)
    except psycopg.errors.InsufficientPrivilege:
        return None, values, _("De databasegebruiker mag geen databases aanmaken (recht CREATEDB ontbreekt).")
    except psycopg.Error as exc:
        return None, values, _("Aanmaken mislukt: {error}").format(error=str(exc).strip().splitlines()[0])
    doc_language = info["language"] if info["language"] in DOC_LANGS else (
        ctx.user.get("language") if ctx.user.get("language") in DOC_LANGS else "en")
    try:                                                     # ook in de administratie zelf (F25 A)
        with sqlledger.connect(**ctx.settings.company_conninfo(values["dbname"]), connect_timeout=5) as conn:
            newcompany.save_setting(conn, newcompany.DOC_LANGUAGE_SETTING, doc_language, ctx.user["login"])
    except psycopg.Error:
        pass                                                 # niet erg: Beheer > Instellingen zet hem opnieuw
    with ctx.conn.transaction():
        cid = ctx.conn.execute(
            "INSERT INTO company (name, code, dbname, modules, active, doc_language, vat_return) "
            "VALUES (%s, %s, %s, %s, true, %s, %s) RETURNING id",
            (values["name"], values["code"], values["dbname"], values["modules"], doc_language,
             info["country"] == "NL")).fetchone()["id"]
        if values["add_me"]:
            ctx.conn.execute("INSERT INTO membership (user_id, company_id, role, employee_login) "
                             "VALUES (%s, %s, 'admin', %s)", (ctx.user["id"], cid, ctx.user["login"]))
    ctx.log("company_made", target=values["name"], code=values["code"], dbname=values["dbname"],
            chart=info["chart"], language=info["language"], currency=info["currency"], modules=values["modules"])
    return cid, values, None


@router.post("/administraties/aanmaken")
def company_create(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    cid, values, error = make_company(ctx, form)
    if error:
        return create_page(ctx, values, 400, error=error)
    raise Redirect(f"/systeem/administraties/{cid}?ok=company_made")


@router.get("/administraties/{company_id}")
def company_edit_form(company_id: int, ctx: Ctx = Depends(ctx_sysadmin)):
    row = _company_or_404(ctx, company_id)
    return _edit_page(ctx, row, row)


def _company_or_404(ctx: Ctx, company_id: int) -> dict:
    row = ctx.conn.execute(f"SELECT c.*, ({ACCESS_SQL}) AS access FROM company c WHERE c.id = %s",
                           (company_id,)).fetchone()
    if not row:
        raise HTTPException(404, _("Administratie niet gevonden."))
    return row


def _edit_page(ctx: Ctx, company: dict | None, values: dict, status: int = 200, **kw):
    return render(ctx, "system/company_edit.html", status, company=company, values=values,
                  action=f"/systeem/administraties/{company['id'] if company else 'nieuw'}", **kw)


@router.post("/administraties/test")
def company_test(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    """HTMX: verbinding testen met de ingevulde gegevens."""
    values = _company_values(form)
    if not DBNAME_RE.match(values["dbname"]):
        return render(ctx, "system/_probe.html", error=_("Ongeldige databasenaam."))
    try:
        info = probe(ctx.settings, values)
    except psycopg.Error as exc:
        return render(ctx, "system/_probe.html", error=str(exc).strip().splitlines()[0])
    version = info.get("version") or ""
    old = too_old(version)
    return render(ctx, "system/_probe.html", info=info,
                  warning=_(OLD_VERSION).format(version=version, minimum=MIN_TEXT) if old else None,
                  unsupported=_(NEW_VERSION).format(version=version) if too_new(version) else None)


def _check_version(ctx: Ctx, values: dict, required: bool = False) -> dict | None:
    """ES12 A: geen database van een oudere SQL-Ledger-versie koppelen (de eigen tabellen en schermen gaan uit
    van 3.2.4). Niet bereikbaar = zoals vroeger opslaan (dat meldt het dashboard), behalve bij ``required``:
    een andere database voor een bestaande administratie moet bereikbaar zijn (F24 A). Geeft ``probe``."""
    try:
        info = probe(ctx.settings, values)
    except psycopg.Error as exc:
        if required:
            raise accounts.AccountError(_("Geen verbinding met de nieuwe database: {error}").format(
                error=str(exc).strip().splitlines()[0])) from None
        return None
    if too_old(info.get("version") or ""):
        raise accounts.AccountError(_(OLD_VERSION).format(version=info.get("version"), minimum=MIN_TEXT))
    return info


def _where(ctx: Ctx, row: dict) -> tuple:
    """Database van een administratie als (naam, server, poort); leeg = de standaard uit de instellingen."""
    return (row["dbname"], row.get("dbhost") or ctx.settings.db_host, row.get("dbport") or ctx.settings.db_port)


def _check_unique_db(ctx: Ctx, values: dict, existing: dict | None) -> None:
    """F23/F24 A: één database hoort bij hoogstens één administratie (zoals bij Back-up inlezen)."""
    for row in ctx.conn.execute("SELECT id, name, dbname, dbhost, dbport FROM company WHERE id <> %s",
                                (existing["id"] if existing else 0,)):
        if _where(ctx, row) == _where(ctx, values):
            raise accounts.AccountError(_("De database {dbname} hoort al bij de administratie {name}.").format(
                dbname=values["dbname"], name=row["name"]))


def _db_name(ctx: Ctx, row: dict) -> str | None:
    """Bedrijfsnaam uit de database (bevestigstap F24); ``None`` = niet bereikbaar."""
    try:
        return probe(ctx.settings, row).get("company") or ""
    except psycopg.Error:
        return None


def save_company(ctx: Ctx, form: FormData, existing: dict | None, add_me: bool = False
                 ) -> tuple[int | None, dict, str | None]:
    """Bestaande database koppelen of een koppeling wijzigen; ook stap 4 van de eerste start (keuze 9).
    ``add_me``: de systeembeheerder wordt beheerder van de administratie (keuze 10; F23 ook vanuit Systeem).
    Een andere database bij een bestaande administratie vraagt eerst ``confirm`` (F24); zonder geeft dit
    (None, waarden, None) met ``values["confirm"]`` = gegevens voor de bevestigstap."""
    values = _company_values(form)
    moved = existing is not None and _where(ctx, existing) != _where(ctx, values)
    try:
        _check_company(values)
        _check_unique_db(ctx, values, existing)
        info = _check_version(ctx, values, required=moved)
        if moved and form.get("confirm") != "1":
            values["confirm"] = {"old": _where(ctx, existing), "new": _where(ctx, values),
                                 "old_company": _db_name(ctx, existing), "new_company": info.get("company") or ""}
            return None, values, None
        cols = ("name", "code", "dbname", "dbhost", "dbport", "modules", "active")
        params = [values[c] for c in cols]
        with ctx.conn.transaction():
            if existing:
                ctx.conn.execute(
                    "UPDATE company SET name = %s, code = %s, dbname = %s, dbhost = %s, dbport = %s, modules = %s, "
                    "active = %s WHERE id = %s", (*params, existing["id"]))
                cid = existing["id"]
            else:
                cid = ctx.conn.execute(
                    "INSERT INTO company (name, code, dbname, dbhost, dbport, modules, active) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id", params).fetchone()["id"]
            if add_me:
                ctx.conn.execute("INSERT INTO membership (user_id, company_id, role, employee_login) "
                                 "VALUES (%s, %s, 'admin', %s) ON CONFLICT DO NOTHING",
                                 (ctx.user["id"], cid, ctx.user["login"]))
    except accounts.AccountError as exc:
        return None, values, str(exc)
    except psycopg.errors.UniqueViolation as exc:
        same_code = exc.diag.constraint_name == "company_code"
        return None, values, (_("Er is al een administratie met deze code.") if same_code
                              else _("Er is al een administratie met deze naam."))
    detail = {}
    if existing:                                             # oude waarden in het logboek (F24 A)
        detail = {f"old_{k}": existing[k] for k in ("name", "code", "dbname", "dbhost", "dbport")
                  if existing[k] != values[k]}
    if add_me:
        detail["admin"] = ctx.user["login"]
    ctx.log("company_updated" if existing else "company_created", target=values["name"],
            code=values["code"], dbname=values["dbname"], modules=values["modules"], active=values["active"],
            **detail)
    return cid, values, None


@router.post("/administraties/{company_id}")
def company_save(company_id: str, ctx: Ctx = Depends(ctx_sysadmin),
                 form: FormData = Depends(csrf_form)):
    """``company_id`` = 'nieuw' of een nummer."""
    existing = None
    if company_id != "nieuw":
        existing = _company_or_404(ctx, int(company_id) if company_id.isdigit() else 0)
    # F23 A: bij koppelen standaard aan; bij een bestaande administratie alleen als niemand toegang heeft
    add_me = form.get("add_me") == "1" and (existing is None or not existing["access"])
    cid, values, error = save_company(ctx, form, existing, add_me=add_me)
    values["add_me"] = add_me
    if error:
        return _edit_page(ctx, existing, values, 400, error=error)
    if cid is None:                                          # andere database: eerst bevestigen (F24)
        return render(ctx, "system/company_dbchange.html", company=existing, values=values,
                      change=values.pop("confirm"),
                      action=f"/systeem/administraties/{existing['id']}")
    raise Redirect(f"/systeem/administraties/{cid}?ok=saved")


def _unlink_refusal(ctx: Ctx, company: dict) -> str | None:
    """F27b A: ontkoppelen alleen als de administratie uit staat en er geen inleestaak loopt."""
    from . import restorejobs
    if company["active"]:
        return _("Zet de administratie eerst uit (vinkje Actief) en sla op; daarna kunt u haar ontkoppelen.")
    if restorejobs.running() or company["dbname"] in restorejobs.pending_dbnames(ctx.settings.data_dir):
        return _("Er loopt een inleestaak (Back-up inlezen). Wacht tot die klaar is.")
    return None


def _unlink_page(ctx: Ctx, company: dict, status: int = 200, **kw):
    users = ctx.conn.execute(
        "SELECT u.login FROM membership m JOIN app_user u ON u.id = m.user_id WHERE m.company_id = %s "
        "ORDER BY u.login", (company["id"],)).fetchall()
    return render(ctx, "system/company_unlink.html", status, company=company, users=[u["login"] for u in users],
                  where=_where(ctx, company), **kw)


@router.get("/administraties/{company_id}/ontkoppelen")
def company_unlink_form(company_id: int, ctx: Ctx = Depends(ctx_sysadmin)):
    company = _company_or_404(ctx, company_id)
    refusal = _unlink_refusal(ctx, company)
    if refusal:
        return _edit_page(ctx, company, company, 400, error=refusal)
    return _unlink_page(ctx, company)


@router.post("/administraties/{company_id}/ontkoppelen")
def company_unlink(company_id: int, ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    """F27b A: de administratie uit deze app halen. De rij gaat weg (met lidmaatschappen, mailinstellingen,
    rapportvoorkeuren en recent gebruikt), net als de sjablonen- en mailmap in de datamap. De database blijft op
    de server; het logboek houdt de naam (``detail.unlinked_company``)."""
    import shutil
    from .backups import templates_dir
    company = _company_or_404(ctx, company_id)
    refusal = _unlink_refusal(ctx, company)
    if refusal:
        return _edit_page(ctx, company, company, 400, error=refusal)
    if str(form.get("confirm_name", "")).strip() != company["name"]:
        return _unlink_page(ctx, company, 400, error=_("Typ de naam van de administratie precies over om te "
                                                       "bevestigen."))
    users = [r["login"] for r in ctx.conn.execute(
        "SELECT u.login FROM membership m JOIN app_user u ON u.id = m.user_id WHERE m.company_id = %s "
        "ORDER BY u.login", (company["id"],)).fetchall()]
    with ctx.conn.transaction():
        ctx.conn.execute("UPDATE audit_log SET detail = coalesce(detail, '{}'::jsonb) || "
                         "jsonb_build_object('unlinked_company', %s::text) WHERE company_id = %s",
                         (company["name"], company["id"]))
        ctx.conn.execute("DELETE FROM company WHERE id = %s", (company["id"],))
    data = Path(ctx.settings.data_dir).expanduser()
    for folder in (templates_dir(ctx.settings, company["id"]), data / "mail" / str(int(company["id"]))):
        shutil.rmtree(folder, ignore_errors=True)
    ctx.log("company_unlinked", target=company["name"], code=company["code"], dbname=company["dbname"],
            dbhost=company["dbhost"], dbport=company["dbport"], users=users)
    raise Redirect("/systeem/administraties?ok=company_unlinked")


# --- logboek -------------------------------------------------------------------

def _parse_date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _cell(value) -> str:
    """Geen formules in Excel: logins in het logboek zijn vrije invoer (mislukte pogingen)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("/logboek")
def log(ctx: Ctx = Depends(ctx_sysadmin)):
    q = ctx.request.query_params
    f = {"login": q.get("login", "").strip().lower(), "action": q.get("action", "").strip(),
         "from": _parse_date(q.get("from", "")), "to": _parse_date(q.get("to", ""))}
    where, params = [], []
    if f["login"]:
        where.append("l.login ILIKE %s")                 # F16: een deel van de login is genoeg
        params.append("%" + f["login"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if f["action"]:
        where.append("l.action = %s")
        params.append(f["action"])
    if f["from"]:
        where.append("l.at >= %s")
        params.append(f["from"])
    if f["to"]:
        where.append("l.at < %s")
        params.append(f["to"] + timedelta(days=1))
    query = ("SELECT l.*, coalesce(c.name, l.detail->>'unlinked_company') AS company "
             "FROM audit_log l LEFT JOIN company c ON c.id = l.company_id"
             + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY l.id DESC")
    if q.get("csv"):
        rows = ctx.conn.execute(query, params).fetchall()
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        w.writerow([_("tijd"), "login", _("administratie"), _("actie"), _("omschrijving"), "ip", _("details")])
        for r in rows:
            w.writerow([_cell(v) for v in (
                r["at"].isoformat(timespec="seconds"), r["login"], r["company"], r["action"],
                _(accounts.ACTION_LABELS.get(r["action"], "")), r["ip"],
                json.dumps(r["detail"], ensure_ascii=False) if r["detail"] else "")])
        ctx.log("log_exported", rows=len(rows))
        return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="logboek.csv"'})
    page = max(int(q.get("page", "1")) if q.get("page", "1").isdigit() else 1, 1)
    rows = ctx.conn.execute(query + " LIMIT %s OFFSET %s",
                            [*params, LOG_PAGE + 1, (page - 1) * LOG_PAGE]).fetchall()
    actions = [r["action"] for r in ctx.conn.execute(
        "SELECT DISTINCT action FROM audit_log ORDER BY action").fetchall()]
    return render(ctx, "system/log.html", rows=rows[:LOG_PAGE], more=len(rows) > LOG_PAGE,
                  page=page, f=f, actions=actions)
