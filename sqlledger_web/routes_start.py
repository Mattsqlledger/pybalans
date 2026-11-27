"""Eerste start (wizard): welkom → taal → beheerder → 2FA → administratie → gebruikers → dashboard.

``EERSTE_START_ANALYSE.md`` (ES1–ES9, keuzes 1–16). Zolang de app-database geen enkele gebruiker heeft, leidt de
middleware elke pagina naar ``/welkom`` (keuze 1). De eerste stap vraagt de **startcode** die de server bij het
starten in het log zet (keuze 3); alleen de hash staat in ``app_meta``. Zodra de beheerder bestaat, is ``/welkom``
dicht en lopen de volgende stappen als ingelogde systeembeheerder; ze blijven bereikbaar zolang er nog geen
administratie is (keuze 6) en zijn altijd over te slaan.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from typing import Optional

import psycopg
from sqlledger import LedgerError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData

from . import accounts, i18n, security
from .appdb import MODULES, ROLES
from .i18n import N_, gettext as _
from .routes_auth import _ip_block_message, start_session
from .web import TEMPLATES, Ctx, Redirect, csrf_form, ctx_any, ctx_sysadmin, render, set_cookie

router = APIRouter(prefix="/welkom")
log = logging.getLogger(__name__)

#: cookie met de gekozen taal zolang er nog niemand is ingelogd (keuze 4)
LANG_COOKIE = "sl_lang"
#: sleutel in ``app_meta`` met de hash van de startcode
START_KEY = "start_code"
#: regels in de tabel Gebruikers uitnodigen (keuze 11)
INVITE_ROWS = 3
INVITE_MAX = 50
#: rekeningschema en valuta per taal van de wizard (keuze 7; de valuta volgt het schema)
CHART_FOR_LANGUAGE = {"nl": "nl-eenmanszaak", "en": "leeg-uk", "fr": "leeg-fr", "de": "leeg-de"}
#: de stappen in de kop van elke wizardpagina
STEPS = (N_("Taal"), N_("Beheerder"), N_("Tweestapsverificatie"), N_("Administratie"), N_("Gebruikers"))
TEMPLATES.env.globals["WIZARD_STEPS"] = STEPS     # ook op 2FA instellen en de gedeelde administratieformulieren


# ------------------------------------------------------------------ startcode en status

def has_users(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM app_user) AS x").fetchone()["x"]


def has_companies(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM company) AS x").fetchone()["x"]


def issue_start_code(conn: psycopg.Connection) -> Optional[str]:
    """Nieuwe startcode als er nog geen gebruiker is (bij elke start een nieuwe); anders de oude weg."""
    if has_users(conn):
        conn.execute("DELETE FROM app_meta WHERE key = %s", (START_KEY,))
        return None
    code = security.new_setup_code()
    conn.execute("INSERT INTO app_meta (key, value) VALUES (%s, %s) "
                 "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (START_KEY, security.token_hash(code)))
    return code


def announce_start_code(code: str) -> None:
    """In het serverlog/de console (``docker compose logs app``); Engels, want de taal is nog niet gekozen."""
    url = (os.environ.get("SQLLEDGER_PUBLIC_URL") or "http://<server>").rstrip("/") + "/welkom"
    line = "=" * 66
    print(f"\n{line}\n  PyBalans first start / eerste start\n  Start code / startcode: {code}\n"
          f"  Open {url} to set up the administrator.\n{line}\n", file=sys.stderr, flush=True)
    log.info("startcode gemaakt (eerste start)")


def _code_ok(conn: psycopg.Connection, code: str) -> bool:
    row = conn.execute("SELECT value FROM app_meta WHERE key = %s", (START_KEY,)).fetchone()
    given = security.token_hash(security.normalize_code(code))
    return bool(row and code.strip()) and secrets.compare_digest(row["value"], given)


def _closed(ctx: Ctx) -> None:
    """Stap 1–2 bestaan alleen zolang er geen gebruiker is (keuze 1)."""
    if has_users(ctx.conn):
        raise Redirect("/login")


def _language(ctx: Ctx) -> Optional[str]:
    lang = ctx.request.cookies.get(LANG_COOKIE)
    return lang if lang in i18n.LANGUAGES else None


def page(ctx: Ctx, name: str, step: int, status: int = 200, **kw):
    return render(ctx, f"start/{name}.html", status, step=step, **kw)


# ------------------------------------------------------------------ stap 1: welkom en taal (keuze 4)

@router.get("")
def welcome(ctx: Ctx = Depends(ctx_any)):
    _closed(ctx)
    i18n.current_language.set("en")                 # welkomstscherm altijd in het Engels (ES3)
    chosen = _language(ctx) or i18n.browser_language(ctx.request.headers.get("accept-language"))
    return page(ctx, "welcome", 1, languages=i18n.LANGUAGES, chosen=chosen)


@router.post("")
def choose_language(ctx: Ctx = Depends(ctx_any), form: FormData = Depends(csrf_form)):
    _closed(ctx)
    lang = str(form.get("language", ""))
    if lang not in i18n.LANGUAGES:
        raise HTTPException(400, _("Onbekende taal."))
    response = RedirectResponse("/welkom/beheerder", status_code=303)
    set_cookie(response, ctx.settings, LANG_COOKIE, lang)
    return response


# ------------------------------------------------------------------ stap 2: beheerder (keuze 5)

def _admin_page(ctx: Ctx, values: dict, status: int = 200, error: Optional[str] = None):
    return page(ctx, "admin", 2, status, values=values, error=error)


@router.get("/beheerder")
def admin_form(ctx: Ctx = Depends(ctx_any)):
    _closed(ctx)
    if not _language(ctx):
        raise Redirect("/welkom")
    return _admin_page(ctx, {})


@router.post("/beheerder")
def admin_create(ctx: Ctx = Depends(ctx_any), form: FormData = Depends(csrf_form)):
    _closed(ctx)
    lang = _language(ctx)
    if not lang:
        raise Redirect("/welkom")
    values = {k: str(form.get(k, "")).strip() for k in ("login", "name", "email")}
    password, repeat = str(form.get("password", "")), str(form.get("repeat", ""))
    if accounts.ip_blocked(ctx.conn, ctx.settings, ctx.ip):
        return _admin_page(ctx, values, 429, _ip_block_message(ctx))
    if not _code_ok(ctx.conn, str(form.get("startcode", ""))):
        accounts.log_event(ctx.conn, "code_failed", login=values["login"].lower(), ip=ctx.ip, via="welkom")
        return _admin_page(ctx, values, 400, _("Startcode onjuist. De code staat in het log van de server; "
                                               "na een herstart is er een nieuwe."))
    try:
        login = accounts.check_login(values["login"])
        accounts.check_password(ctx.settings, password, repeat)
        if values["email"] and "@" not in values["email"]:
            raise accounts.AccountError(_("Ongeldig e-mailadres."))
        nf, df = i18n.FORMATS[lang]
        with ctx.conn.transaction():
            ctx.conn.execute("LOCK TABLE app_user IN EXCLUSIVE MODE")
            if has_users(ctx.conn):                 # een ander was net eerder
                raise Redirect("/login")
            user_id = accounts.create_user(ctx.conn, login, values["name"], values["email"], is_sysadmin=True)
            ctx.conn.execute(
                "UPDATE app_user SET password_hash = %s, language = %s, numberformat = %s, dateformat = %s "
                "WHERE id = %s", (security.hash_password(password), lang, nf, df, user_id))
            ctx.conn.execute("DELETE FROM app_meta WHERE key = %s", (START_KEY,))
    except accounts.AccountError as exc:
        return _admin_page(ctx, values, 400, str(exc))
    user = accounts.get_user(ctx.conn, user_id=user_id)
    accounts.log_event(ctx.conn, "user_created", user=user, ip=ctx.ip, via="welkom", sysadmin=True)
    accounts.log_event(ctx.conn, "login_password_ok", user=user, ip=ctx.ip)
    # 2FA is verplicht voor systeembeheerders: meteen het bestaande scherm (stap 3)
    response = start_session(ctx, user, "totp_setup", "/2fa/instellen?welkom=1")
    response.delete_cookie(LANG_COOKIE, path="/", secure=ctx.settings.secure_cookies, httponly=True)
    return response


# ------------------------------------------------------------------ stap 4: administratie (keuzes 6–10)

@router.get("/administratie")
def company_choice(ctx: Ctx = Depends(ctx_sysadmin)):
    return page(ctx, "company", 4)


def _defaults(ctx: Ctx) -> dict:
    lang = ctx.user["language"]
    return {"modules": list(MODULES), "chart": CHART_FOR_LANGUAGE.get(lang, "nl-eenmanszaak"),
            "language": lang, "yearend": 12, "add_me": True, "active": True}


@router.get("/administratie/nieuw")
def company_new_form(ctx: Ctx = Depends(ctx_sysadmin)):
    from .routes_system import create_page
    return create_page(ctx, _defaults(ctx), wizard=True)


@router.post("/administratie/nieuw")
def company_new(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    from .routes_system import create_page, make_company
    cid, values, error = make_company(ctx, form)
    if error:
        return create_page(ctx, values, 400, error=error, wizard=True)
    raise Redirect(f"/welkom/gebruikers?administratie={cid}")


@router.get("/administratie/koppelen")
def company_link_form(ctx: Ctx = Depends(ctx_sysadmin)):
    return render(ctx, "system/company_edit.html", company=None, values=_defaults(ctx), wizard=True,
                  action="/welkom/administratie/koppelen")


@router.post("/administratie/koppelen")
def company_link(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    from .routes_system import save_company
    cid, values, error = save_company(ctx, form, None, add_me=form.get("add_me") == "1")
    if error:
        values["add_me"] = form.get("add_me") == "1"
        return render(ctx, "system/company_edit.html", 400, company=None, values=values, wizard=True,
                      action="/welkom/administratie/koppelen", error=error)
    _enable_start_list(ctx, cid)
    raise Redirect(f"/welkom/gebruikers?administratie={cid}")


def _enable_start_list(ctx: Ctx, company_id: int) -> None:
    """Gekoppeld via de wizard: startlijst aan (ES10 A). Niet bereikbaar = geen startlijst, geen fout."""
    from sqlledger import UserConfig
    from sqlledger.dashboard import Dashboard
    from .companies import connect_company
    company = ctx.conn.execute("SELECT * FROM company WHERE id = %s", (company_id,)).fetchone()
    try:
        with connect_company(ctx.settings, company) as conn:
            Dashboard(conn, UserConfig(login=ctx.user["login"])).enable_start_list(ctx.user["login"])
    except (psycopg.Error, LedgerError):
        log.warning("startlijst niet aangezet voor %s", company["dbname"], exc_info=True)


# ------------------------------------------------------------------ stap 5: gebruikers uitnodigen (keuze 11)

def _company(ctx: Ctx, value) -> dict:
    cid = int(value) if str(value or "").isdigit() else 0
    row = ctx.conn.execute("SELECT * FROM company WHERE id = %s", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, _("Administratie niet gevonden."))
    return row


def _rows(form: Optional[FormData], n: int) -> list:
    out = []
    for i in range(n):
        g = (lambda k: str(form.get(f"{k}_{i}", "")).strip()) if form else (lambda k: "")
        role = g("role")
        out.append({"name": g("name"), "login": g("login"), "email": g("email"),
                    "role": role if role in ROLES else "bookkeeper", "error": None})
    return out


def _users_page(ctx: Ctx, company: dict, rows: list, status: int = 200, created=(), error=None):
    return page(ctx, "users", 5, status, company=company, rows=rows, created=list(created), error=error,
                hours=ctx.settings.setup_code_hours)


@router.get("/gebruikers")
def users_form(ctx: Ctx = Depends(ctx_sysadmin)):
    company = _company(ctx, ctx.request.query_params.get("administratie"))
    return _users_page(ctx, company, _rows(None, INVITE_ROWS))


@router.post("/gebruikers")
def users_invite(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    company = _company(ctx, form.get("company_id"))
    n = str(form.get("rows", ""))
    n = min(int(n), INVITE_MAX) if n.isdigit() else INVITE_ROWS
    rows = _rows(form, n)
    if form.get("action") == "add":
        return _users_page(ctx, company, rows + _rows(None, 1) if n < INVITE_MAX else rows)
    created, failed = [], []
    for r in rows:
        if not (r["login"] or r["name"] or r["email"]):
            continue                                # lege regels tellen niet mee
        try:
            if not r["login"]:
                raise accounts.AccountError(_("Login is verplicht."))
            if r["email"] and "@" not in r["email"]:
                raise accounts.AccountError(_("Ongeldig e-mailadres."))
            with ctx.conn.transaction():
                user_id = accounts.create_user(ctx.conn, r["login"], r["name"], r["email"])
                ctx.conn.execute("INSERT INTO membership (user_id, company_id, role, employee_login) "
                                 "VALUES (%s, %s, %s, %s)",
                                 (user_id, company["id"], r["role"], accounts.check_login(r["login"])))
        except accounts.AccountError as exc:
            failed.append(dict(r, error=str(exc)))
            continue
        user = accounts.get_user(ctx.conn, user_id=user_id)
        ctx.log("user_created", target=user["login"], via="welkom", roles={str(company["id"]): r["role"]})
        code = accounts.issue_setup_code(ctx.conn, ctx.settings, user_id, ctx.user["id"])
        ctx.log("code_created", target=user["login"], via="welkom")
        created.append({"user": user, "role": r["role"], "code": code,
                        "url": str(ctx.request.url_for("activate_form").include_query_params(login=user["login"]))})
    if failed:
        return _users_page(ctx, company, failed, 400, created,
                           error=_("Niet alle gebruikers zijn aangemaakt; zie de regels hieronder."))
    if not created:
        return _users_page(ctx, company, rows, 400, error=_("Vul minstens één regel in, of kies Overslaan."))
    return page(ctx, "codes", 5, company=company, created=created, hours=ctx.settings.setup_code_hours)


# ------------------------------------------------------------------ klaar (keuze 14)

@router.post("/klaar")
def finish(ctx: Ctx = Depends(ctx_sysadmin), form: FormData = Depends(csrf_form)):
    """Nieuwe administratie kiezen en naar het dashboard met de startlijst (zoals wisselen, AW2 B)."""
    from .routes_main import _select
    company = _company(ctx, form.get("company_id"))
    member = ctx.conn.execute("SELECT 1 FROM membership WHERE user_id = %s AND company_id = %s",
                              (ctx.user["id"], company["id"])).fetchone()
    if not member or not company["active"]:
        raise Redirect("/systeem/administraties")
    _select(ctx, company["id"])
    raise Redirect("/")
