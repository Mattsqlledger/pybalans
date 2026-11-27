"""Inloggen, tweestapsverificatie, code inwisselen en uitloggen."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData

from . import accounts, i18n, security
from .i18n import N_, gettext as _
from .web import (Ctx, Redirect, clear_session_cookie, csrf_form, ctx_any, ctx_partial, render,
                  session_cookie, set_cookie)

router = APIRouter()

BAD_LOGIN = N_("Onjuiste login of wachtwoord, of het account is tijdelijk geblokkeerd.")


def _ip_block_message(ctx: Ctx) -> str:
    return _("Te veel mislukte pogingen vanaf dit adres. Probeer het over {n} minuten opnieuw.").format(
        n=ctx.settings.lockout_minutes)


def start_session(ctx: Ctx, user: dict, stage: str, url: str) -> RedirectResponse:
    """Nieuwe sessie (en dus nieuw token) bij elke stap omhoog; de oude vervalt."""
    token = security.new_token()
    company_id = ctx.session["company_id"] if ctx.session else None
    with ctx.conn.transaction():
        if ctx.session:
            ctx.conn.execute("DELETE FROM app_session WHERE token_hash = %s",
                             (ctx.session["token_hash"],))
        ctx.conn.execute(
            "INSERT INTO app_session (token_hash, user_id, stage, company_id, csrf, ip, user_agent) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (security.token_hash(token), user["id"], stage, company_id, security.new_token(),
             ctx.ip, (ctx.request.headers.get("user-agent") or "")[:300]))
    response = RedirectResponse(url, status_code=303)
    set_cookie(response, ctx.settings, session_cookie(ctx.settings), token)
    return response


def finish_login(ctx: Ctx, user: dict) -> RedirectResponse:
    ctx.conn.execute(
        "UPDATE app_user SET failed_count = 0, last_login = now() WHERE id = %s", (user["id"],))
    ctx.conn.execute("DELETE FROM app_session WHERE last_seen < now() - make_interval(secs => %s)",
                     (ctx.settings.session_timeout,))
    # systeembeheerder zonder administraties: verder met de eerste start (keuze 6)
    if user["is_sysadmin"] and not ctx.conn.execute("SELECT EXISTS (SELECT 1 FROM company) AS x").fetchone()["x"]:
        return start_session(ctx, user, "full", "/welkom/administratie")
    return start_session(ctx, user, "full", "/administratie")


@router.get("/login")
def login_form(ctx: Ctx = Depends(ctx_any)):
    if ctx.session and ctx.session["stage"] == "full":
        raise Redirect("/")
    return render(ctx, "login.html", login="")


@router.post("/login")
def login(ctx: Ctx = Depends(ctx_any), form: FormData = Depends(csrf_form)):
    login = str(form.get("login", "")).strip().lower()
    password = str(form.get("password", ""))
    if accounts.ip_blocked(ctx.conn, ctx.settings, ctx.ip):
        accounts.log_event(ctx.conn, "login_blocked", login=login, ip=ctx.ip, reason="ip")
        return render(ctx, "login.html", 429, login=login, error=_ip_block_message(ctx))
    user = accounts.get_user(ctx.conn, login=login)
    ok, rehash = security.verify_password(user["password_hash"] if user else None, password)
    if user and accounts.account_locked(user):
        accounts.log_event(ctx.conn, "login_blocked", user=user, ip=ctx.ip, reason="account")
        return render(ctx, "login.html", 401, login=login, error=_(BAD_LOGIN))
    if not (user and user["active"] and ok):
        accounts.log_event(ctx.conn, "login_failed", user=user, login=login, ip=ctx.ip)
        if user and accounts.register_failure(ctx.conn, ctx.settings, user):
            accounts.log_event(ctx.conn, "account_locked", user=user, ip=ctx.ip)
        return render(ctx, "login.html", 401, login=login, error=_(BAD_LOGIN))
    if rehash:
        ctx.conn.execute("UPDATE app_user SET password_hash = %s WHERE id = %s", (rehash, user["id"]))
    accounts.log_event(ctx.conn, "login_password_ok", user=user, ip=ctx.ip)
    if user["totp_secret"]:
        return start_session(ctx, user, "totp", "/2fa")
    if accounts.needs_totp(ctx.conn, user):
        return start_session(ctx, user, "totp_setup", "/2fa/instellen")
    accounts.log_event(ctx.conn, "login_ok", user=user, ip=ctx.ip)
    return finish_login(ctx, user)


@router.get("/2fa")
def totp_form(ctx: Ctx = Depends(ctx_partial)):
    if ctx.session["stage"] != "totp":
        raise Redirect("/")
    return render(ctx, "totp.html")


@router.post("/2fa")
def totp(ctx: Ctx = Depends(ctx_partial), form: FormData = Depends(csrf_form)):
    if ctx.session["stage"] != "totp":
        raise Redirect("/")
    user = ctx.user
    if accounts.ip_blocked(ctx.conn, ctx.settings, ctx.ip) or accounts.account_locked(user):
        return render(ctx, "totp.html", 429, error=_ip_block_message(ctx))
    step = security.verify_totp(user["totp_secret"], str(form.get("code", "")),
                                user["totp_last_step"])
    if step is None:
        accounts.log_event(ctx.conn, "totp_failed", user=user, ip=ctx.ip)
        if accounts.register_failure(ctx.conn, ctx.settings, user):
            accounts.log_event(ctx.conn, "account_locked", user=user, ip=ctx.ip)
            ctx.conn.execute("DELETE FROM app_session WHERE user_id = %s", (user["id"],))
            raise Redirect("/login")
        return render(ctx, "totp.html", 401, error=_("Code onjuist of al gebruikt."))
    ctx.conn.execute("UPDATE app_user SET totp_last_step = %s WHERE id = %s", (step, user["id"]))
    accounts.log_event(ctx.conn, "login_ok", user=user, ip=ctx.ip, totp=True)
    return finish_login(ctx, user)


def _pending_secret(ctx: Ctx) -> str:
    secret = ctx.session["pending_totp"]
    if not secret:
        secret = security.new_totp_secret()
        ctx.conn.execute("UPDATE app_session SET pending_totp = %s WHERE token_hash = %s",
                         (secret, ctx.session["token_hash"]))
    return secret


def _setup_page(ctx: Ctx, status: int = 200, error: str | None = None):
    secret = _pending_secret(ctx)
    uri = security.totp_uri(secret, ctx.user["login"], ctx.settings.totp_issuer)
    return render(ctx, "totp_setup.html", status, secret=secret, qr=security.totp_qr_svg(uri),
                  forced=ctx.session["stage"] == "totp_setup", error=error)


@router.get("/2fa/instellen")
def totp_setup_form(ctx: Ctx = Depends(ctx_partial)):
    if ctx.session["stage"] == "totp":
        raise Redirect("/2fa")
    return _setup_page(ctx)


@router.post("/2fa/instellen")
def totp_setup(ctx: Ctx = Depends(ctx_partial), form: FormData = Depends(csrf_form)):
    if ctx.session["stage"] == "totp":
        raise Redirect("/2fa")
    secret = _pending_secret(ctx)
    step = security.verify_totp(secret, str(form.get("code", "")), None)
    if step is None:
        accounts.log_event(ctx.conn, "totp_setup_failed", user=ctx.user, ip=ctx.ip)
        return _setup_page(ctx, 400, _("Code onjuist. Controleer de tijd op uw telefoon en probeer opnieuw."))
    ctx.conn.execute("UPDATE app_user SET totp_secret = %s, totp_last_step = %s WHERE id = %s",
                     (secret, step, ctx.user["id"]))
    ctx.conn.execute("UPDATE app_session SET pending_totp = NULL WHERE token_hash = %s",
                     (ctx.session["token_hash"],))
    accounts.log_event(ctx.conn, "totp_enabled", user=ctx.user, ip=ctx.ip)
    if ctx.session["stage"] == "totp_setup":
        accounts.log_event(ctx.conn, "login_ok", user=ctx.user, ip=ctx.ip, totp=True)
        return finish_login(ctx, ctx.user)
    raise Redirect("/instellingen?ok=totp_on")


@router.get("/activeren")
def activate_form(ctx: Ctx = Depends(ctx_any)):
    return render(ctx, "activate.html", login=ctx.request.query_params.get("login", ""))


@router.post("/activeren")
def activate(ctx: Ctx = Depends(ctx_any), form: FormData = Depends(csrf_form)):
    login = str(form.get("login", "")).strip().lower()
    code = str(form.get("code", ""))
    password, repeat = str(form.get("password", "")), str(form.get("repeat", ""))
    if accounts.ip_blocked(ctx.conn, ctx.settings, ctx.ip):
        return render(ctx, "activate.html", 429, login=login, error=_ip_block_message(ctx))
    try:
        accounts.check_password(ctx.settings, password, repeat)
    except accounts.AccountError as exc:
        return render(ctx, "activate.html", 400, login=login, code=code, error=str(exc))
    try:
        user = accounts.redeem_setup_code(ctx.conn, ctx.settings, login, code, password, repeat)
    except accounts.AccountError as exc:
        accounts.log_event(ctx.conn, "code_failed", login=login, ip=ctx.ip)
        return render(ctx, "activate.html", 400, login=login, error=str(exc))
    accounts.log_event(ctx.conn, "code_redeemed", user=user, ip=ctx.ip)
    if user["password_hash"] is None and user["last_login"] is None:
        # nieuwe gebruiker: taal van de browser met de notatie die daarbij past (TL4, TL6)
        lang = i18n.browser_language(ctx.request.headers.get("accept-language"))
        nf, df = i18n.FORMATS[lang]
        ctx.conn.execute("UPDATE app_user SET language = %s, numberformat = %s, dateformat = %s WHERE id = %s",
                         (lang, nf, df, user["id"]))
    response = RedirectResponse("/login?ok=activated", status_code=303)
    clear_session_cookie(response, ctx.settings)
    return response


@router.post("/uitloggen")
def logout(ctx: Ctx = Depends(ctx_partial), form: FormData = Depends(csrf_form)):
    ctx.conn.execute("DELETE FROM app_session WHERE token_hash = %s", (ctx.session["token_hash"],))
    accounts.log_event(ctx.conn, "logout", user=ctx.user, ip=ctx.ip)
    response = RedirectResponse("/login?ok=logged_out", status_code=303)
    clear_session_cookie(response, ctx.settings)
    return response
