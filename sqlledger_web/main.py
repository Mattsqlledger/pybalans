"""FastAPI-app. Starten: ``uvicorn --factory sqlledger_web.main:create_app``."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import appdb, i18n, overrides, routes_start, security, switchbar
from .settings import Settings
from .i18n import gettext as _
from .web import CSRF_COOKIE, TEMPLATES, Redirect, session_cookie, set_cookie

log = logging.getLogger(__name__)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")


def _user_language(request: Request) -> str:
    """Taal van de ingelogde gebruiker; zonder sessie de browsertaal (TL4)."""
    token = request.cookies.get(session_cookie(request.app.state.settings))
    row = None
    if token:
        with request.app.state.pool.connection() as conn:
            row = conn.execute(
                "SELECT u.language FROM app_session s JOIN app_user u ON u.id = s.user_id "
                "WHERE s.token_hash = %s", (security.token_hash(token),)).fetchone()
    if not row:
        chosen = request.cookies.get("sl_lang")             # eerste start: gekozen taal (keuze 4)
        if chosen in i18n.LANGUAGES:
            return chosen
        return i18n.browser_language(request.headers.get("accept-language"))
    lang = row["language"]
    return lang if lang in i18n.LANGUAGES else "nl"


def _has_users(request: Request) -> bool:
    with request.app.state.pool.connection() as conn:
        return routes_start.has_users(conn)


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # app-database zelf aanmaken (eerste start keuze 2; init-db blijft als noodroute)
        try:
            appdb.create_database(settings.app_conninfo())
        except psycopg.Error as exc:            # bv. geen recht CREATEDB: dan moet hij er al zijn
            log.warning("app-database niet aangemaakt: %s", str(exc).strip().splitlines()[0])
        app.state.pool = appdb.open_pool(settings.app_conninfo())
        with app.state.pool.connection() as conn:
            code = routes_start.issue_start_code(conn)
        app.state.has_users = code is None
        if code:
            routes_start.announce_start_code(code)
        from . import routes_restore
        routes_restore.recover(settings, app.state.pool)        # inlezen afgebroken door een harde stop (keuze 17)
        from .uploads import PendingFiles
        PendingFiles(settings.data_dir).cleanup()                # tijdelijke bijlagen ouder dan een dag (F44)
        try:
            yield
        finally:
            app.state.pool.close()

    app = FastAPI(title="PyBalans", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings = settings
    app.state.has_users = False             # lifespan zet dit; tot er een gebruiker is: naar /welkom
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        # browsers die het pictogram zonder <link> zoeken (bv. bij een PDF in een eigen tabblad)
        return FileResponse(Path(__file__).parent / "static" / "favicon.ico", media_type="image/x-icon",
                            headers={"Cache-Control": "max-age=86400"})

    from . import routes_am, routes_arap, routes_auth, routes_cp, routes_gl, routes_main, routes_rc, routes_reports, routes_system
    from . import routes_recurring
    from . import routes_bank
    from . import routes_ct
    from . import routes_asm, routes_ic, routes_is, routes_oe
    from . import routes_docs, routes_docsettings, routes_dunning, routes_mail, routes_payspec, routes_style, routes_tpl
    from . import routes_batch
    from . import routes_dashboard
    from . import routes_apupload, routes_attach
    from . import routes_vat
    from . import routes_icp
    from . import routes_import
    from . import routes_jc
    from . import routes_backup, routes_restore, routes_switch
    from . import routes_rgs
    from . import routes_sepa
    from . import routes_help
    app.include_router(routes_auth.router)
    app.include_router(routes_start.router)
    app.include_router(routes_main.router)
    app.include_router(routes_system.router)
    app.include_router(routes_reports.router)
    app.include_router(routes_gl.router)
    app.include_router(routes_apupload.router)      # /inkoop/uploaden vóór /inkoop/{trans_id}
    app.include_router(routes_attach.router)
    app.include_router(routes_arap.router)
    app.include_router(routes_is.router)
    app.include_router(routes_oe.router)
    app.include_router(routes_sepa.router)
    app.include_router(routes_cp.router)
    app.include_router(routes_recurring.router)
    app.include_router(routes_rc.router)
    app.include_router(routes_bank.router)
    app.include_router(routes_ct.router)
    app.include_router(routes_ic.router)
    app.include_router(routes_asm.router)
    app.include_router(routes_am.router)
    app.include_router(routes_docs.router)
    app.include_router(routes_mail.router)
    app.include_router(routes_tpl.router)
    app.include_router(routes_docsettings.router)
    app.include_router(routes_style.router)
    app.include_router(routes_dunning.router)
    app.include_router(routes_payspec.router)
    app.include_router(routes_batch.router)
    app.include_router(routes_dashboard.router)
    app.include_router(routes_vat.router)
    app.include_router(routes_icp.router)
    app.include_router(routes_import.router)
    app.include_router(routes_jc.router)
    app.include_router(routes_restore.router)
    app.include_router(routes_backup.router)
    app.include_router(routes_switch.router)
    app.include_router(routes_rgs.router)
    app.include_router(routes_help.router)

    @app.middleware("http")
    async def basics(request: Request, call_next):
        # token voor formulieren zonder sessie (double-submit)
        cookie = request.cookies.get(CSRF_COOKIE)
        request.state.csrf_cookie = cookie or security.new_token()
        lang = "nl"
        path = request.url.path
        if not path.startswith("/static/"):
            lang = await run_in_threadpool(_user_language, request)
        overrides.refresh(settings.data_dir, lang)  # eigen woorden (TL9)
        # eerste start (keuze 1): zolang er geen gebruiker is, gaat alles naar /welkom
        welcome = False
        if not request.app.state.has_users and not path.startswith(("/static/", "/welkom")):
            if await run_in_threadpool(_has_users, request):
                request.app.state.has_users = True
            else:
                welcome = True
        token = i18n.current_language.set(lang)
        try:
            if welcome:
                response: Response = (Response(status_code=200, headers={"HX-Redirect": "/welkom"})
                                      if request.headers.get("hx-request")
                                      else RedirectResponse("/welkom", status_code=303))
            else:
                response = await call_next(request)
        finally:
            i18n.current_language.reset(token)
        if not cookie:
            set_cookie(response, settings, CSRF_COOKIE, request.state.csrf_cookie)
        switchbar.update_cookie(request, response, settings.secure_cookies)     # PB10
        h = response.headers
        h.setdefault("Content-Security-Policy", CSP)
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith("/static/"):
            h.setdefault("Cache-Control", "no-store")
        if settings.secure_cookies:
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(Redirect)
    async def redirect_handler(request: Request, exc: Redirect):
        if request.headers.get("hx-request"):
            return Response(status_code=200, headers={"HX-Redirect": exc.url})
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: HTTPException):
        messages = {404: _("Pagina niet gevonden."), 405: _("Niet toegestaan.")}
        detail = _(exc.detail) if isinstance(exc.detail, str) and exc.status_code not in messages \
            else messages.get(exc.status_code, str(exc.detail))
        return TEMPLATES.TemplateResponse(
            request, "error.html", {"ctx": None, "status": exc.status_code, "message": detail},
            status_code=exc.status_code)

    return app
