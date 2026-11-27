"""Gebruikers, eenmalige codes, inlogblokkade en logboek (zonder HTTP)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psycopg
from psycopg.types.json import Jsonb

from . import security
from .i18n import N_, gettext as _
from .settings import Settings

LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,62}$")

#: acties die als mislukte poging tellen voor de blokkade per IP
FAILED_ACTIONS = ("login_failed", "totp_failed", "code_failed")

#: omschrijving per logboekactie
ACTION_LABELS = {
    "login_ok": N_("ingelogd"),
    "login_password_ok": N_("wachtwoord juist (2FA volgt)"),
    "login_failed": N_("inloggen mislukt"),
    "login_blocked": N_("inloggen geweigerd (blokkade)"),
    "account_locked": N_("account geblokkeerd"),
    "logout": N_("uitgelogd"),
    "totp_failed": N_("2FA-code onjuist"),
    "totp_setup_failed": N_("2FA instellen mislukt"),
    "totp_enabled": N_("2FA ingesteld"),
    "totp_disabled": N_("2FA uitgezet"),
    "totp_reset": N_("2FA gereset door beheerder"),
    "code_created": N_("eenmalige code gemaakt"),
    "code_failed": N_("eenmalige code onjuist"),
    "code_redeemed": N_("wachtwoord ingesteld met code"),
    "password_changed": N_("wachtwoord gewijzigd"),
    "password_change_failed": N_("wachtwoord wijzigen mislukt"),
    "profile_saved": N_("eigen instellingen opgeslagen"),
    "sessions_ended": N_("sessies beëindigd"),
    "company_selected": N_("administratie gekozen"),
    "company_created": N_("administratie toegevoegd"),
    "company_made": N_("administratie aangemaakt (nieuwe database)"),
    "company_updated": N_("administratie gewijzigd"),
    "company_unlinked": N_("administratie ontkoppeld"),
    "company_restored": N_("back-up ingelezen"),
    "restore_aborted": N_("inlezen afgebroken (server gestopt), opgeruimd"),
    "backup_made": N_("back-up gemaakt"),
    "backup_failed": N_("back-up mislukt"),
    "switch_list_enabled": N_("overstaplijst aangezet"),
    "switch_check_ok": N_("controleblad: cijfers kloppen"),
    "switch_check_issue": N_("controleblad: er klopt iets niet"),
    "employee_created": N_("medewerker aangemaakt"),
    "recurring_stopped": N_("herhaalschema gestopt"),
    "user_created": N_("gebruiker toegevoegd"),
    "code_mailed": N_("eenmalige code gemaild"),
    "code_mail_failed": N_("eenmalige code mailen mislukt"),
    "user_updated": N_("gebruiker gewijzigd"),
    "user_deleted": N_("gebruiker verwijderd"),
    "attachments_moved": N_("vertrouwelijke bijlagen verhuisd"),
    "user_unlocked": N_("blokkade opgeheven"),
    "log_exported": N_("logboek geëxporteerd"),
    "report_exported": N_("rapport geëxporteerd"),
    "rgs_list": N_("RGS-lijst ingelezen"),
    "rgs_list_deleted": N_("RGS-lijst verwijderd"),
    "rgs_mapping": N_("RGS-koppeling opgeslagen"),
    "vat_return_done": N_("btw-aangifte afgevinkt"),
    "icp_return_registered": N_("ICP-opgaaf vastgelegd"),
    "gl_posted": N_("memoriaalboeking opgeslagen"),
    "gl_deleted": N_("memoriaalboeking verwijderd"),
    "arap_posted": N_("verkoop-/inkoopboeking opgeslagen"),
    "arap_deleted": N_("verkoop-/inkoopboeking verwijderd"),
    "payment_posted": N_("ontvangst/betaling geboekt"),
    "payment_voided": N_("ontvangst/betaling gestorneerd"),
    "payment_reissued": N_("ontvangst/betaling opnieuw uitgegeven"),
    "sepa_export": N_("SEPA-betaalbestand gemaakt"),
    "yearend_posted": N_("jaarafsluiting geboekt"),
    "ct_saved": N_("klant/leverancier opgeslagen"),
    "ct_deleted": N_("klant/leverancier verwijderd"),
    "ct_inactive": N_("klant/leverancier inactief gemaakt"),
    "ic_saved": N_("artikel opgeslagen"),
    "ic_deleted": N_("artikel verwijderd"),
    "ic_stock_adjusted": N_("voorraad gecorrigeerd"),
    "ic_group_saved": N_("artikelgroep opgeslagen"),
    "ic_group_deleted": N_("artikelgroep verwijderd"),
    "asm_built": N_("samengesteld artikel geassembleerd/gedemonteerd"),
    "invoice_posted": N_("factuur met artikelen opgeslagen"),
    "invoice_deleted": N_("factuur met artikelen verwijderd"),
    "order_saved": N_("order/offerte opgeslagen"),
    "order_deleted": N_("order/offerte verwijderd"),
    "order_reopened": N_("order/offerte heropend"),
}


class AccountError(ValueError):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def log_event(conn: psycopg.Connection, action: str, *, user: Optional[dict] = None,
              login: Optional[str] = None, company_id: Optional[int] = None,
              ip: Optional[str] = None, **detail: Any) -> None:
    conn.execute(
        "INSERT INTO audit_log (user_id, login, company_id, action, ip, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user["id"] if user else None, login or (user["login"] if user else None),
         company_id, action, ip, Jsonb(detail) if detail else None))


def check_login(login: str) -> str:
    login = login.strip().lower()
    if not LOGIN_RE.match(login):
        raise AccountError(_("Login: alleen kleine letters, cijfers en . _ @ - (max. 63 tekens)."))
    return login


def check_password(settings: Settings, password: str, repeat: str) -> None:
    if password != repeat:
        raise AccountError(_("De wachtwoorden zijn niet gelijk."))
    if len(password) < settings.password_min_length:
        raise AccountError(_("Het wachtwoord moet minstens {n} tekens hebben.").format(
            n=settings.password_min_length))


def get_user(conn: psycopg.Connection, *, user_id: Optional[int] = None,
             login: Optional[str] = None) -> Optional[dict]:
    if user_id is not None:
        return conn.execute("SELECT * FROM app_user WHERE id = %s", (user_id,)).fetchone()
    return conn.execute("SELECT * FROM app_user WHERE login = %s",
                        ((login or "").strip().lower(),)).fetchone()


def create_user(conn: psycopg.Connection, login: str, name: str = "", email: str = "",
                is_sysadmin: bool = False) -> int:
    login = check_login(login)
    if get_user(conn, login=login):
        raise AccountError(_("Login '{login}' bestaat al.").format(login=login))
    row = conn.execute(
        "INSERT INTO app_user (login, name, email, is_sysadmin) VALUES (%s, %s, %s, %s) RETURNING id",
        (login, name.strip(), email.strip(), is_sysadmin)).fetchone()
    return row["id"]


def issue_setup_code(conn: psycopg.Connection, settings: Settings, user_id: int,
                     created_by: Optional[int]) -> str:
    """Nieuwe eenmalige code; eerdere ongebruikte codes vervallen."""
    code = security.new_setup_code()
    with conn.transaction():
        conn.execute("DELETE FROM setup_code WHERE user_id = %s AND used IS NULL", (user_id,))
        conn.execute(
            "INSERT INTO setup_code (user_id, code_hash, expires, created_by) VALUES (%s, %s, %s, %s)",
            (user_id, security.token_hash(code), now() + timedelta(hours=settings.setup_code_hours),
             created_by))
    return code


def redeem_setup_code(conn: psycopg.Connection, settings: Settings, login: str, code: str,
                      password: str, repeat: str) -> dict:
    """Wachtwoord instellen met een eenmalige code. Wist ook bestaande sessies
    en heft een blokkade op."""
    check_password(settings, password, repeat)
    user = get_user(conn, login=login)
    if not user or not user["active"]:
        raise AccountError(_("Onbekende combinatie van login en code, of de code is verlopen."))
    with conn.transaction():
        row = conn.execute(
            "SELECT id FROM setup_code WHERE user_id = %s AND code_hash = %s AND used IS NULL "
            "AND expires > now() FOR UPDATE",
            (user["id"], security.token_hash(security.normalize_code(code)))).fetchone()
        if not row:
            raise AccountError(_("Onbekende combinatie van login en code, of de code is verlopen."))
        conn.execute("UPDATE setup_code SET used = now() WHERE id = %s", (row["id"],))
        conn.execute(
            "UPDATE app_user SET password_hash = %s, failed_count = 0, locked_until = NULL "
            "WHERE id = %s", (security.hash_password(password), user["id"]))
        conn.execute("DELETE FROM app_session WHERE user_id = %s", (user["id"],))
    return user


def ip_blocked(conn: psycopg.Connection, settings: Settings, ip: Optional[str]) -> bool:
    if not ip:
        return False
    row = conn.execute(
        "SELECT count(*) AS n FROM audit_log WHERE ip = %s AND action = ANY(%s) "
        "AND at > now() - make_interval(mins => %s)",
        (ip, list(FAILED_ACTIONS), settings.lockout_minutes)).fetchone()
    return row["n"] >= settings.lockout_attempts


def account_locked(user: dict) -> bool:
    return bool(user["locked_until"] and user["locked_until"] > now())


def register_failure(conn: psycopg.Connection, settings: Settings, user: dict) -> bool:
    """Telt een mislukte poging; True als het account nu geblokkeerd is."""
    row = conn.execute(
        "UPDATE app_user SET failed_count = failed_count + 1 WHERE id = %s RETURNING failed_count",
        (user["id"],)).fetchone()
    if row["failed_count"] >= settings.lockout_attempts:
        conn.execute(
            "UPDATE app_user SET failed_count = 0, locked_until = now() + make_interval(mins => %s) "
            "WHERE id = %s", (settings.lockout_minutes, user["id"]))
        return True
    return False


def needs_totp(conn: psycopg.Connection, user: dict) -> bool:
    """Tweestapsverificatie is verplicht voor systeembeheerders en beheerders van
    een administratie (H2)."""
    if user["is_sysadmin"]:
        return True
    row = conn.execute("SELECT 1 FROM membership WHERE user_id = %s AND role = 'admin' LIMIT 1",
                       (user["id"],)).fetchone()
    return row is not None
