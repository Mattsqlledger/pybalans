"""Administratie kiezen, hoofdscherm en eigen instellingen."""

from __future__ import annotations

from datetime import date

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger.config import DATEFORMATS, NUMBERFORMATS
from sqlledger import LedgerError
from sqlledger.admin import fix_fxgainloss
from sqlledger.dates import format_date

from . import accounts, i18n, security
from .i18n import gettext as _
from .libmsg import lib_message
from .companies import connect_company, probe
from .appdb import RECENT_COMPANIES, SEARCH_FROM
from .web import Ctx, Redirect, csrf_form, ctx_company, ctx_user, render

router = APIRouter()


def _memberships(ctx: Ctx):
    return ctx.conn.execute(
        "SELECT c.id, c.name, c.code, c.dbname, m.role, c.locked, c.lock_message FROM membership m "
        "JOIN company c ON c.id = m.company_id "
        "WHERE m.user_id = %s AND c.active ORDER BY c.name", (ctx.user["id"],)).fetchall()


def _matches(row: dict, q: str) -> bool:
    """Zoeken in het keuzemenu (AW1): op naam, code of databasenaam; ook static/switcher.js."""
    return any(q in (row[k] or "").casefold() for k in ("name", "code", "dbname"))


def _select(ctx: Ctx, company_id: int) -> None:
    ctx.conn.execute("UPDATE app_session SET company_id = %s WHERE token_hash = %s",
                     (company_id, ctx.session["token_hash"]))
    # recent gebruikt (AW4): per gebruiker, de huidige + de laatste RECENT_COMPANIES daarvoor
    ctx.conn.execute(
        "INSERT INTO recent_company (user_id, company_id) VALUES (%s, %s) "
        "ON CONFLICT (user_id, company_id) DO UPDATE SET used = now()", (ctx.user["id"], company_id))
    ctx.conn.execute(
        "DELETE FROM recent_company WHERE user_id = %s AND company_id NOT IN ("
        "SELECT company_id FROM recent_company WHERE user_id = %s ORDER BY used DESC LIMIT %s)",
        (ctx.user["id"], ctx.user["id"], RECENT_COMPANIES + 1))
    accounts.log_event(ctx.conn, "company_selected", user=ctx.user, company_id=company_id, ip=ctx.ip)
    _repair(ctx, company_id)


def _repair(ctx: Ctx, company_id: int) -> None:
    """Bij het kiezen van een administratie: fxgainloss_accno_id rechtzetten (beheer A8)."""
    company = ctx.conn.execute("SELECT * FROM company WHERE id = %s", (company_id,)).fetchone()
    try:
        with connect_company(ctx.settings, company) as conn:
            fix_fxgainloss(conn)
    except (psycopg.Error, LedgerError, ValueError):
        pass        # niet bereikbaar of geen SQL-Ledger: het hoofdscherm meldt dat al


@router.get("/over")
def about(ctx: Ctx = Depends(ctx_user)):
    """Over PyBalans (v1.0.1 NB2): versie, licentie en de merkregels."""
    from sqlledger import __version__
    return render(ctx, "about.html", version=__version__)


@router.get("/administratie")
def choose_form(ctx: Ctx = Depends(ctx_user)):
    rows = _memberships(ctx)
    # precies één administratie en nog niets gekozen: meteen door
    if len(rows) == 1 and not ctx.company and not (rows[0]["locked"] and rows[0]["role"] != "admin"):
        _select(ctx, rows[0]["id"])
        raise Redirect("/")
    if not rows and ctx.user["is_sysadmin"]:
        no_company = not ctx.conn.execute("SELECT EXISTS (SELECT 1 FROM company) AS x").fetchone()["x"]
        raise Redirect("/welkom/administratie" if no_company else "/systeem/gebruikers")
    # zoeken zonder JavaScript: het zoekveld in het keuzemenu komt hier uit
    q = str(ctx.request.query_params.get("q", "")).strip()
    shown = [r for r in rows if _matches(r, q.casefold())] if q else rows
    return render(ctx, "choose_company.html", companies=shown, q=q, search=len(rows) >= SEARCH_FROM or bool(q))


@router.post("/administratie")
def choose(ctx: Ctx = Depends(ctx_user), form: FormData = Depends(csrf_form)):
    try:
        company_id = int(form.get("company_id", ""))
    except ValueError:
        raise HTTPException(400, _("Geen administratie gekozen."))
    rows = {r["id"]: r for r in _memberships(ctx)}
    if company_id not in rows:
        raise HTTPException(403, _("Geen toegang tot deze administratie."))
    if rows[company_id]["locked"] and rows[company_id]["role"] != "admin":
        raise Redirect("/administratie?ok=company_locked")
    _select(ctx, company_id)
    raise Redirect("/")


@router.get("/")
def home(ctx: Ctx = Depends(ctx_company)):
    from .routes_dashboard import page
    return page(ctx)                                    # dashboard (fase 4 stap 5b, DB1)


def _closedto(value: str, dateformat: str) -> str:
    """``defaults.closedto`` is jjjjmmdd als getal (oude data soms ongeldig: dan ruw tonen)."""
    value = (value or "").replace("-", "")
    try:
        return format_date(date(int(value[:4]), int(value[4:6]), int(value[6:8])), dateformat) \
            if len(value) == 8 else value
    except ValueError:
        return value


def _settings_page(ctx: Ctx, status: int = 200, **kw):
    return render(ctx, "settings.html", status, numberformats=NUMBERFORMATS,
                  dateformats=DATEFORMATS, languages=i18n.LANGUAGES,
                  totp_required=accounts.needs_totp(ctx.conn, ctx.user),
                  other_sessions=ctx.conn.execute(
                      "SELECT count(*) AS n FROM app_session WHERE user_id = %s AND token_hash <> %s",
                      (ctx.user["id"], ctx.session["token_hash"])).fetchone()["n"], **kw)


@router.get("/instellingen")
def settings_form(ctx: Ctx = Depends(ctx_user)):
    return _settings_page(ctx)


@router.post("/instellingen")
def settings_save(ctx: Ctx = Depends(ctx_user), form: FormData = Depends(csrf_form)):
    action = form.get("action")
    uid = ctx.user["id"]
    if action == "profile":
        nf, df, lang = form.get("numberformat"), form.get("dateformat"), form.get("language")
        if nf not in NUMBERFORMATS or df not in DATEFORMATS or lang not in i18n.LANGUAGES:
            raise HTTPException(400, _("Ongeldige keuze."))
        signature = str(form.get("mail_signature", "")).replace("\r\n", "\n").strip()[:2000]
        ctx.conn.execute(
            "UPDATE app_user SET name = %s, email = %s, numberformat = %s, dateformat = %s, "
            "language = %s, mail_signature = %s WHERE id = %s",
            (str(form.get("name", "")).strip(), str(form.get("email", "")).strip(), nf, df, lang, signature, uid))
        ctx.log("profile_saved")
        raise Redirect("/instellingen?ok=saved")
    if action == "password":
        ok, __ = security.verify_password(ctx.user["password_hash"], str(form.get("current", "")))
        if not ok:
            ctx.log("password_change_failed")
            return _settings_page(ctx, 400, error=_("Huidig wachtwoord onjuist."))
        try:
            accounts.check_password(ctx.settings, str(form.get("password", "")),
                                    str(form.get("repeat", "")))
        except accounts.AccountError as exc:
            return _settings_page(ctx, 400, error=lib_message(exc))
        with ctx.conn.transaction():
            ctx.conn.execute("UPDATE app_user SET password_hash = %s WHERE id = %s",
                             (security.hash_password(str(form["password"])), uid))
            ctx.conn.execute("DELETE FROM app_session WHERE user_id = %s AND token_hash <> %s",
                             (uid, ctx.session["token_hash"]))
        ctx.log("password_changed")
        raise Redirect("/instellingen?ok=password")
    if action == "totp_off":
        if accounts.needs_totp(ctx.conn, ctx.user):
            raise HTTPException(403, _("Tweestapsverificatie is verplicht voor beheerders."))
        ctx.conn.execute("UPDATE app_user SET totp_secret = NULL, totp_last_step = NULL WHERE id = %s",
                         (uid,))
        ctx.log("totp_disabled")
        raise Redirect("/instellingen?ok=totp_off")
    if action == "sessions":
        ctx.conn.execute("DELETE FROM app_session WHERE user_id = %s AND token_hash <> %s",
                         (uid, ctx.session["token_hash"]))
        ctx.log("sessions_ended")
        raise Redirect("/instellingen?ok=sessions")
    raise HTTPException(400, _("Onbekende actie."))
