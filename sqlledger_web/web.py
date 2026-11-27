"""Gedeelde webonderdelen: sjablonen, sessie/context, CSRF en toegangscontrole."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlencode

import psycopg
from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from sqlledger import UserConfig
from sqlledger.attachments import MAX_MB, MB

from . import accounts, i18n, production, security
from .helptext import HELPERS
from .i18n import N_, gettext as _
from .appdb import MODULES, RECENT_COMPANIES, ROLE_LEVEL, ROLES, SEARCH_FROM
from .libmsg import lib_message
from .menu import PRODUCTION, build_menu
from .settings import Settings

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals.update(_=i18n.gettext, N_=i18n.N_, pgettext=i18n.pgettext, lang=i18n.lang, libmsg=lib_message, MODULES=MODULES, ROLES=ROLES,
                             ACTIONS=accounts.ACTION_LABELS)
TEMPLATES.env.globals.update(HELPERS)


def _module_labels(keys) -> str:
    return ", ".join(i18n.gettext(MODULES[k]) for k in MODULES if k in (keys or []))


def _pageparams(params, page: int) -> str:
    """Huidige zoekvelden met een ander paginanummer."""
    items = [(k, v) for k, v in params.multi_items() if k not in ("page", "csv")]
    return urlencode(items + [("page", str(page))])


def _amount(value, numberformat: str, places: int = 2, dash: str = "") -> str:
    from sqlledger.amounts import format_amount
    return "" if value is None else format_amount(value, numberformat, places, dash).replace("&nbsp;", "").strip()


def _userdate(value, dateformat: str) -> str:
    """Datum in de notatie van de gebruiker (F35); leeg bij geen datum."""
    from sqlledger.dates import format_date
    return format_date(value, dateformat) if value else ""


def _switch_bar(ctx):
    from . import switchbar
    return switchbar.bar(ctx)


def _switch_href(url: str, key: str, back: str) -> str:
    from . import switchbar
    return switchbar.href(url, key, back)


TEMPLATES.env.filters.update(module_labels=_module_labels, pageparams=_pageparams, amount=_amount,
                             overstap=_switch_href, userdate=_userdate)
TEMPLATES.env.globals.update(switch_bar=_switch_bar)

CSRF_COOKIE = "sl_csrf"

#: meldingen die via ?ok=<sleutel> getoond worden (nooit vrije tekst uit de URL)
NOTICES = {
    "vat_done": N_("De btw-aangifte is afgevinkt."),
    "vat_booked": N_("De btw-boeking is opgeslagen en aan dit tijdvak gekoppeld."),
    "icp_registered": N_("De ICP-opgaaf is vastgelegd."),
    "vat_boxes_saved": N_("Btw-rubrieken opgeslagen."),
    "import_expired": N_("Het bestand is niet meer beschikbaar; kies het opnieuw."),
    "activated": N_("Uw wachtwoord is ingesteld. U kunt nu inloggen."),
    "logged_out": N_("U bent uitgelogd."),
    "expired": N_("Uw sessie is verlopen. Log opnieuw in."),
    "saved": N_("Opgeslagen."),
    "user_deleted": N_("De gebruiker is verwijderd."),
    "company_unlinked": N_("De administratie is ontkoppeld. De database staat nog op de databaseserver."),
    "company_made": N_("De administratie is aangemaakt. Vul bij Beheer > Instellingen het adres en de andere bedrijfsgegevens in."),
    "password": N_("Uw wachtwoord is gewijzigd; andere sessies zijn beëindigd."),
    "totp_on": N_("Tweestapsverificatie staat aan."),
    "totp_off": N_("Tweestapsverificatie staat uit."),
    "sessions": N_("Uw andere sessies zijn beëindigd."),
    "gl_posted": N_("Boeking opgeslagen."),
    "gl_deleted": N_("Boeking verwijderd."),
    "arap_posted": N_("Boeking opgeslagen."),
    "attach_added": N_("Bijlage toegevoegd."),
    "attach_deleted": N_("Bijlage verwijderd."),
    "arap_deleted": N_("Boeking verwijderd."),
    "invoice_posted": N_("Factuur opgeslagen."),
    "invoice_deleted": N_("Factuur verwijderd."),
    "document_emailed": N_("E-mail verzonden."),
    "order_saved": N_("Opgeslagen."),
    "order_deleted": N_("Verwijderd."),
    "order_reopened": N_("Heropend."),
    "invoice_backorder_kept": N_("Factuur verwijderd. De nabestelling die bij deze factuur ontstond is gewijzigd en blijft staan."),
    "payment_posted": N_("Betaling geboekt."),
    "payments_posted": N_("Betalingen geboekt."),
    "payment_voided": N_("Gestorneerd."),
    "payment_reissued": N_("Opnieuw uitgegeven."),
    "recurring_saved": N_("Herhaalschema opgeslagen."),
    "recurring_deleted": N_("Herhaalschema verwijderd."),
    "ct_saved": N_("Opgeslagen."),
    "ct_deleted": N_("Verwijderd."),
    "ct_deleted_many": N_("De gekozen relaties zijn verwijderd."),
    "ct_inactive": N_("Inactief gemaakt (einddatum vandaag)."),
    "ic_saved": N_("Opgeslagen."),
    "ic_deleted": N_("Verwijderd."),
    "ic_stock": N_("Voorraad gecorrigeerd."),
    "asm_built": N_("Geassembleerd."),
    "asm_unbuilt": N_("Gedemonteerd."),
    "company_locked": N_("Deze administratie is op slot gezet door de beheerder."),
    "switch_check_ok": N_("Akkoord vastgelegd: de cijfers kloppen."),
    "switch_check_issue": N_("Vastgelegd: er klopt iets niet. Het punt blijft open op de overstaplijst."),
    "switch_note_missing": N_("Schrijf op wat er niet klopt."),
    "recurring_stopped": N_("Herhaalschema gestopt."),
    "yearend_posted": N_("Jaarafsluiting geboekt. Zet daarna eventueel de afsluitdatum (Beheer > Afsluiten)."),
}


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def settings(request: Request) -> Settings:
    return request.app.state.settings


def client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def session_cookie(s: Settings) -> str:
    # __Host-: alleen over https, niet voor een subdomein te zetten
    return "__Host-sl_session" if s.secure_cookies else "sl_session"


def set_cookie(response, s: Settings, name: str, value: str) -> None:
    response.set_cookie(name, value, httponly=True, secure=s.secure_cookies,
                        samesite="strict" if name == CSRF_COOKIE else "lax", path="/")


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    with request.app.state.pool.connection() as conn:
        yield conn


@dataclass
class Ctx:
    """Alles over het huidige verzoek: gebruiker, sessie, gekozen administratie."""
    request: Request
    conn: psycopg.Connection
    settings: Settings
    user: Optional[dict] = None
    session: Optional[dict] = None
    company: Optional[dict] = None
    role: Optional[str] = None
    employee_login: Optional[str] = None

    @property
    def ip(self) -> Optional[str]:
        return client_ip(self.request)

    @property
    def csrf(self) -> str:
        if self.session:
            return self.session["csrf"]
        return self.request.state.csrf_cookie

    def has_role(self, role: str) -> bool:
        return self.role is not None and ROLE_LEVEL[self.role] >= ROLE_LEVEL[role]

    def user_config(self) -> UserConfig:
        """Instellingen voor de sqlledger-bibliotheek."""
        assert self.user and self.employee_login
        return UserConfig(login=self.employee_login, numberformat=self.user["numberformat"],
                          dateformat=self.user["dateformat"], countrycode=self.user["language"],
                          timeout=self.settings.session_timeout)

    def menu(self):
        if not self.user:
            return []
        modules = list(self.company["modules"]) if self.company else None
        flags = {k for k in ("vat_return", "icp", "rma") if self.company and self.company.get(k)}
        if self.role and self.production():
            flags.add(PRODUCTION)
        if self.company:
            # land en GIFI van de administratie: RGS alleen NL, btw-rubrieken/betaalbestand alleen waar ze bestaan
            flags |= production.menu_flags(self.settings, self.company)
        elif self.user.get("language") == "nl":
            flags.add("nl")                  # zonder administratie: RGS-lijst alleen voor Nederlandstaligen
        return build_menu(modules, self.role, self.user["is_sysadmin"], frozenset(flags))

    def production(self) -> bool:
        """Productie (samengestelde artikelen) zichtbaar, zoals in het menu (PB20); ook voor de links op de
        artikelschermen (F28)."""
        return bool(self.company and "ic" in (self.company["modules"] or [])
                    and production.visible(self.settings, self.company))

    def switcher(self) -> dict:
        """Keuzemenu administratie wisselen in de kopregel (AW1): recent gebruikt en alle administraties
        waar de gebruiker lid van is (vergrendeld = grijs, behalve voor een beheerder ervan)."""
        if "_switcher" not in self.__dict__:
            rows = self.conn.execute(
                "SELECT c.id, c.name, c.code, c.dbname, m.role, c.locked, c.lock_message, r.used "
                "FROM membership m JOIN company c ON c.id = m.company_id "
                "LEFT JOIN recent_company r ON r.user_id = m.user_id AND r.company_id = c.id "
                "WHERE m.user_id = %s AND c.active ORDER BY c.name", (self.user["id"],)).fetchall()
            for r in rows:
                r["closed"] = r["locked"] and r["role"] != "admin"
            current = self.company["id"] if self.company else None
            recent = sorted((r for r in rows if r["used"] and r["id"] != current),
                            key=lambda r: r["used"], reverse=True)[:RECENT_COMPANIES]
            self.__dict__["_switcher"] = {"all": rows, "recent": recent, "search": len(rows) >= SEARCH_FROM}
        return self.__dict__["_switcher"]

    def log(self, action: str, **detail) -> None:
        accounts.log_event(self.conn, action, user=self.user,
                           company_id=self.company["id"] if self.company else None,
                           ip=self.ip, **detail)


def _load(request: Request, conn: psycopg.Connection) -> Ctx:
    s = settings(request)
    ctx = Ctx(request, conn, s)
    token = request.cookies.get(session_cookie(s))
    if not token:
        return ctx
    row = conn.execute(
        "SELECT * FROM app_session WHERE token_hash = %s", (security.token_hash(token),)).fetchone()
    if not row:
        return ctx
    user = accounts.get_user(conn, user_id=row["user_id"])
    if (not user or not user["active"]
            or row["last_seen"] + timedelta(seconds=s.session_timeout) < accounts.now()):
        conn.execute("DELETE FROM app_session WHERE token_hash = %s", (row["token_hash"],))
        request.state.session_expired = True
        return ctx
    if accounts.now() - row["last_seen"] > timedelta(seconds=30):
        conn.execute("UPDATE app_session SET last_seen = now() WHERE token_hash = %s",
                     (row["token_hash"],))
    ctx.user, ctx.session = user, row
    if row["company_id"] and row["stage"] == "full":
        m = conn.execute(
            "SELECT c.*, m.role, m.employee_login FROM membership m JOIN company c ON c.id = m.company_id "
            "WHERE m.user_id = %s AND m.company_id = %s AND c.active",
            (user["id"], row["company_id"])).fetchone()
        if m:
            ctx.company = {k: m.get(k) for k in ("id", "name", "dbname", "dbhost", "dbport", "modules",
                                                 "locked", "lock_message", "doc_language", "vat_return", "icp", "code",
                                                 "last_backup", "rma")}
            ctx.role, ctx.employee_login = m["role"], m["employee_login"]
    return ctx


def ctx_any(request: Request, conn: psycopg.Connection = Depends(get_conn)) -> Ctx:
    return _load(request, conn)


def ctx_partial(ctx: Ctx = Depends(ctx_any)) -> Ctx:
    """Ingelogd met wachtwoord, tweede stap mag nog openstaan."""
    if not ctx.session:
        raise Redirect("/login?ok=expired" if getattr(ctx.request.state, "session_expired", False)
                       else "/login")
    return ctx


def ctx_user(ctx: Ctx = Depends(ctx_partial)) -> Ctx:
    stage = ctx.session["stage"]
    if stage == "totp":
        raise Redirect("/2fa")
    if stage == "totp_setup":
        raise Redirect("/2fa/instellen")
    return ctx


#: wat een urenschrijver (UR4) mag: de eigen maandstaat en wat nodig is om te wisselen/uit te loggen
HOURS_PATHS = ("/uren/mijn", "/uren/urenstaat.pdf", "/administratie", "/static/", "/uitloggen", "/instellingen")


def ctx_company(ctx: Ctx = Depends(ctx_user)) -> Ctx:
    if not ctx.company:
        raise Redirect("/administratie")
    if ctx.role == "hours" and not ctx.request.url.path.startswith(HOURS_PATHS):
        # de server dwingt het af, niet het menu (UR4; in de 2011-versie alleen het menu)
        if ctx.request.method == "GET":
            raise Redirect("/uren/mijn")
        raise HTTPException(403, _("Uw rol in deze administratie geeft hier geen toegang toe."))
    if ctx.company.get("locked") and ctx.role != "admin":
        # administratie op slot (Beheer > Afsluiten): alleen beheerders kunnen erin (zoals Perl .LCK)
        raise Redirect("/administratie?ok=company_locked")
    return ctx


def ctx_sysadmin(ctx: Ctx = Depends(ctx_user)) -> Ctx:
    if not ctx.user["is_sysadmin"]:
        raise HTTPException(403, _("Alleen voor systeembeheerders."))
    return ctx


def require_role(ctx: Ctx, role: str) -> None:
    if not ctx.has_role(role):
        raise HTTPException(403, _("Uw rol in deze administratie geeft hier geen toegang toe."))


async def csrf_form(request: Request) -> FormData:
    """Formulierdata van een POST, na controle van het CSRF-token.

    Met sessie: token uit de sessie; zonder (inloggen, code inwisselen):
    double-submit tegen het ``sl_csrf``-cookie. HTMX stuurt het mee als header.
    """
    form = await request.form()
    sent = form.get("csrf") or request.headers.get("x-csrf-token") or ""
    s = settings(request)
    token = request.cookies.get(session_cookie(s))
    expected = None
    if token:
        with request.app.state.pool.connection() as conn:
            row = conn.execute("SELECT csrf FROM app_session WHERE token_hash = %s",
                               (security.token_hash(token),)).fetchone()
        expected = row["csrf"] if row else None
    if expected is None:
        expected = request.cookies.get(CSRF_COOKIE)
    if not expected or not secrets.compare_digest(str(sent), expected):
        raise HTTPException(400, _("Formulier verlopen of ongeldig. Laad de pagina opnieuw."))
    return form


#: grootste verzoek met een bestand: de hoogst instelbare uploadgrootte (stap 4f, MU2) plus de rest van
#: het formulier; de grens van de administratie zelf controleert routes_attach.upload_limit
UPLOAD_LIMIT = MAX_MB * MB + 512 * 1024


async def upload_form(request: Request) -> FormData:
    """Als csrf_form, voor formulieren met een bestand: te grote verzoeken meteen weigeren."""
    n = request.headers.get("content-length", "")
    if n.isdigit() and int(n) > UPLOAD_LIMIT:
        raise HTTPException(413, f"Het bestand is groter dan {MAX_MB} MB.")  # bibliotheekmelding, vertaald bij tonen
    return await csrf_form(request)


def render(ctx: Ctx, name: str, status_code: int = 200, **context) -> HTMLResponse:
    ok = ctx.request.query_params.get("ok")
    context.setdefault("notice", _(NOTICES[ok]) if ok in NOTICES else None)
    for key in ("notice", "error"):
        # meldingen die letterlijk in de catalogus staan (ook uit de bibliotheek) vertalen
        if isinstance(context.get(key), str):
            context[key] = _(context[key])
    return TEMPLATES.TemplateResponse(ctx.request, name, {"ctx": ctx, **context},
                                      status_code=status_code)


def clear_session_cookie(response, s: Settings) -> None:
    response.delete_cookie(session_cookie(s), path="/", secure=s.secure_cookies, httponly=True)
