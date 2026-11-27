"""App-skelet: inloggen, 2FA, administratie kiezen, menu, rollen, beheer, logboek."""

import re
from datetime import timedelta

import pyotp
from fastapi.testclient import TestClient

from sqlledger_web import accounts, cli
from sqlledger_web.main import create_app
from webhelpers import PASSWORD, csrf, login, make_user, post, totp_now


def actions(db, login_name=None):
    q = "SELECT action FROM audit_log" + (" WHERE login = %s" if login_name else "") + " ORDER BY id"
    return [r["action"] for r in db.execute(q, (login_name,) if login_name else ())]


# --- eerste beheerder, code inwisselen, verplichte 2FA ----------------------------

def test_create_admin_activate_and_forced_totp(client, db, settings, monkeypatch, capsys):
    monkeypatch.setenv("SQLLEDGER_APP_DSN", settings.app_dsn)
    assert cli.main(["create-admin", "Beheer", "--name", "Beheerder"]) == 0
    code = re.search(r"([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", capsys.readouterr().out).group(1)

    # verkeerde code telt als mislukte poging
    r = post(client, "/activeren", {"login": "beheer", "code": "AAAA-BBBB-CCCC",
                                     "password": PASSWORD, "repeat": PASSWORD})
    assert r.status_code == 400 and "verlopen" in r.text
    # te kort wachtwoord
    r = post(client, "/activeren", {"login": "beheer", "code": code, "password": "kort", "repeat": "kort"})
    assert r.status_code == 400
    # code mag met kleine letters en zonder streepjes
    r = post(client, "/activeren", {"login": "beheer", "code": code.lower().replace("-", ""),
                                     "password": PASSWORD, "repeat": PASSWORD})
    assert r.url.path == "/login" and "wachtwoord is ingesteld" in r.text
    # code werkt maar één keer
    r = post(client, "/activeren", {"login": "beheer", "code": code, "password": PASSWORD, "repeat": PASSWORD})
    assert r.status_code == 400

    r = login(client, "beheer")
    assert r.url.path == "/2fa/instellen" and "<svg" in r.text
    # zonder 2FA geen toegang tot de rest
    assert client.get("/systeem/gebruikers").url.path == "/2fa/instellen"
    secret = re.search(r'<code class="secret">([A-Z2-7]+)</code>', r.text).group(1)
    r = post(client, "/2fa/instellen", {"code": "000000"})
    assert r.status_code == 400
    r = post(client, "/2fa/instellen", {"code": pyotp.TOTP(secret).now()})
    # systeembeheerder zonder administraties: verder met de eerste start (keuze 6)
    assert r.url.path == "/welkom/administratie"
    user = accounts.get_user(db, login="beheer")
    assert user["totp_secret"] == secret and user["is_sysadmin"]
    assert {"code_failed", "code_redeemed", "totp_enabled", "login_ok"} <= set(actions(db))


def test_totp_code_not_reusable(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    code = totp_now(secret)
    post(client, "/login", {"login": "anna", "password": PASSWORD})
    assert post(client, "/2fa", {"code": code}).url.path == "/welkom/administratie"
    post(client, "/uitloggen", {}, page="/instellingen")
    post(client, "/login", {"login": "anna", "password": PASSWORD})
    r = post(client, "/2fa", {"code": code})
    assert r.status_code == 401 and "al gebruikt" in r.text


# --- blokkades, CSRF, sessies ---------------------------------------------------

def test_account_lockout(client, db, settings):
    make_user(db, "piet")
    for _ in range(settings.lockout_attempts):
        assert login(client, "piet", password="fout").status_code == 401
    # ook met het goede wachtwoord geblokkeerd, met dezelfde melding
    r = login(client, "piet")
    assert r.status_code in (401, 429)
    assert accounts.get_user(db, login="piet")["locked_until"] is not None
    assert "account_locked" in actions(db, "piet")


def test_ip_lockout(client, db, settings):
    make_user(db, "kees")           # zonder gebruikers gaat alles naar /welkom (eerste start)
    for i in range(settings.lockout_attempts):
        assert login(client, f"bestaatniet{i}").status_code == 401
    r = login(client, "kees")
    assert r.status_code == 429 and "Te veel mislukte pogingen" in r.text


def test_unknown_and_inactive_user_same_message(client, db):
    make_user(db, "oud")
    db.execute("UPDATE app_user SET active = false WHERE login = 'oud'")
    a, b = login(client, "oud"), login(client, "niemand")
    assert a.status_code == b.status_code == 401
    assert "Onjuiste login of wachtwoord" in a.text and "Onjuiste login of wachtwoord" in b.text


def test_csrf_required(client, db, company):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    client.get("/login")
    r = client.post("/login", data={"login": "x", "password": "y"})
    assert r.status_code == 400
    login(client, "jan")
    r = client.post("/instellingen", data={"action": "sessions", "csrf": "verkeerd"})
    assert r.status_code == 400
    # het token van vóór het inloggen werkt niet meer zodra er een sessie is
    r = client.post("/instellingen", data={"action": "sessions", "csrf": client.cookies["sl_csrf"]})
    assert r.status_code == 400


def test_cookies_and_headers(client, db, company):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    r = client.post("/login", data={"login": "jan", "password": PASSWORD, "csrf": csrf(client)},
                    follow_redirects=False)
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-sl_session=")
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    r = client.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_session_timeout_and_logout(client, db, company, settings):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    login(client, "jan")
    assert client.get("/").url.path == "/"
    db.execute("UPDATE app_session SET last_seen = now() - make_interval(secs => %s)",
               (settings.session_timeout + 5,))
    r = client.get("/")
    assert r.url.path == "/login" and "verlopen" in r.text
    assert db.execute("SELECT count(*) AS n FROM app_session").fetchone()["n"] == 0

    login(client, "jan")
    r = post(client, "/uitloggen", {}, page="/")
    assert r.url.path == "/login"
    assert client.get("/").url.path == "/login"


def test_session_token_rotates_after_totp(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    post(client, "/login", {"login": "anna", "password": PASSWORD})
    first = client.cookies["__Host-sl_session"]
    post(client, "/2fa", {"code": totp_now(secret)})
    assert client.cookies["__Host-sl_session"] != first
    assert db.execute("SELECT count(*) AS n FROM app_session").fetchone()["n"] == 1


# --- administratie kiezen, menu, rollen ----------------------------------------

def test_single_company_autoselect_and_home(client, db, company):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    r = login(client, "jan")
    assert r.url.path == "/"
    assert "Testbedrijf BV" in r.text and "Jan Jansen" in r.text  # uit de SQL-Ledger-database
    assert "Boeking toevoegen" in r.text and "Proefbalans" in r.text
    assert "Beheer" not in r.text and "/systeem/" not in r.text
    assert "company_selected" in actions(db, "jan")


def test_employee_missing_warning(client, db, company):
    make_user(db, "nieuw", memberships=[(company["id"], "readonly")])
    r = login(client, "nieuw")
    assert "Geen medewerker met login" in r.text
    # alleen-lezen ziet geen invoerschermen
    assert "Proefbalans" in r.text and "Boeking toevoegen" not in r.text


def test_choose_between_companies(client, db, company, company_db):
    other = db.execute("INSERT INTO company (name, dbname, modules) VALUES ('Tweede', %s, '{gl}') "
                       "RETURNING *", (company_db,)).fetchone()
    closed = db.execute("INSERT INTO company (name, dbname, active) VALUES ('Dicht', 'x', false) "
                        "RETURNING *").fetchone()
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper"), (other["id"], "readonly", "admin"),
                                      (closed["id"], "bookkeeper")])
    r = login(client, "jan")
    assert r.url.path == "/administratie"
    assert "Tweede" in r.text and "Dicht" not in r.text
    assert client.get("/").url.path == "/administratie"
    r = post(client, "/administratie", {"company_id": closed["id"]})
    assert r.status_code == 403
    r = post(client, "/administratie", {"company_id": other["id"]})
    assert r.url.path == "/" and "Tweede" in r.text
    # module-filter: alleen Grootboek (+ niets van verkoop)
    assert "Memoriaal" not in r.text  # alleen-lezen
    assert "Boekingen zoeken" in r.text and "Ouderdomsanalyse" not in r.text and "Proefbalans" not in r.text
    assert "Beheerder" in r.text  # medewerker 'admin' in de SQL-Ledger-database


def test_company_admin_needs_totp(client, db, company):
    make_user(db, "chef", memberships=[(company["id"], "admin")])
    r = login(client, "chef")
    assert r.url.path == "/2fa/instellen"
    secret = re.search(r'<code class="secret">([A-Z2-7]+)</code>', r.text).group(1)
    r = post(client, "/2fa/instellen", {"code": pyotp.TOTP(secret).now()})
    assert r.url.path == "/" and "Jaarafsluiting" in r.text
    # verplicht: uitzetten mag niet
    r = post(client, "/instellingen", {"action": "totp_off"})
    assert r.status_code == 403


def test_non_sysadmin_has_no_system_access(client, db, company):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    login(client, "jan")
    assert client.get("/systeem/gebruikers").status_code == 403
    assert client.get("/systeem/logboek").status_code == 403


# --- systeembeheer --------------------------------------------------------------

def test_sysadmin_manages_company_and_user(client, db, company_db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)

    # verbinding testen (HTMX-fragment)
    html = client.get("/systeem/administraties/nieuw").text
    token = csrf(client, html)
    r = client.post("/systeem/administraties/test", data={"dbname": company_db},
                    headers={"HX-Request": "true", "X-CSRF-Token": token})
    assert "Verbinding gelukt" in r.text and "Testbedrijf BV" in r.text
    r = client.post("/systeem/administraties/test", data={"dbname": "bestaat_niet"},
                    headers={"HX-Request": "true", "X-CSRF-Token": token})
    assert "Geen verbinding" in r.text

    data = {"name": "Administratie C", "dbname": company_db, "mod_ar": "1", "mod_gl": "1", "active": "1"}
    r = post(client, "/systeem/administraties/nieuw", data, page="/systeem/administraties/nieuw")
    assert r.status_code == 200 and "Opgeslagen" in r.text
    comp = db.execute("SELECT * FROM company WHERE name = 'Administratie C'").fetchone()
    assert comp["modules"] == ["ar", "gl"]
    r = post(client, "/systeem/administraties/nieuw", data, page="/systeem/administraties/nieuw")
    assert r.status_code == 400  # naam bestaat al

    r = post(client, "/systeem/gebruikers/nieuw", {"login": "Jan", "name": "Jan Jansen"})
    code = re.search(r'<p class="code">([A-Z0-9-]+)</p>', r.text).group(1)
    jan = accounts.get_user(db, login="jan")
    page = f"/systeem/gebruikers/{jan['id']}"
    r = post(client, page, {"action": "save", "name": "Jan Jansen", "active": "1",
                            f"role_{comp['id']}": "bookkeeper"})
    assert "Opgeslagen" in r.text
    m = db.execute("SELECT * FROM membership WHERE user_id = %s", (jan["id"],)).fetchone()
    assert (m["role"], m["employee_login"]) == ("bookkeeper", "jan")

    # eigen beheerrechten uitzetten kan niet
    me = accounts.get_user(db, login="anna")
    r = post(client, f"/systeem/gebruikers/{me['id']}", {"action": "save", "active": "1"})
    assert r.status_code == 400

    with TestClient(client.app, base_url="https://testserver") as jan_client:
        post(jan_client, "/activeren", {"login": "jan", "code": code, "password": PASSWORD,
                                        "repeat": PASSWORD})
        r = login(jan_client, "jan")
        assert r.url.path == "/" and "Jan Jansen" in r.text and "Administratie C" in r.text
        # beheerder trekt toegang in en beëindigt sessies
        post(client, page, {"action": "sessions"})
        assert jan_client.get("/").url.path == "/login"

    log = actions(db)
    for a in ("company_created", "user_created", "code_created", "user_updated", "sessions_ended"):
        assert a in log


def test_log_page_and_csv(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "=cmd|' /C calc'!A0")  # vrije invoer in het logboek
    login(client, "anna", secret)
    r = client.get("/systeem/logboek?action=login_failed")
    assert r.status_code == 200 and "=cmd" in r.text and "ingelogd" not in r.text.split("<tbody>")[1]
    r = client.get("/systeem/logboek?csv=1")
    assert r.headers["content-type"].startswith("text/csv")
    assert "'=cmd" in r.text and "\n=cmd" not in r.text


def test_password_change_ends_other_sessions(client, db, company):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    with TestClient(client.app, base_url="https://testserver") as other:
        login(other, "jan")
        login(client, "jan")
        new = "een-ander-lang-wachtwoord"
        r = post(client, "/instellingen", {"action": "password", "current": "fout",
                                           "password": new, "repeat": new})
        assert r.status_code == 400
        r = post(client, "/instellingen", {"action": "password", "current": PASSWORD,
                                           "password": new, "repeat": new})
        assert "andere sessies zijn beëindigd" in r.text
        assert other.get("/").url.path == "/login"
        assert client.get("/").url.path == "/"


def test_user_settings_notation(client, db, company):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    login(client, "jan")
    r = post(client, "/instellingen", {"action": "profile", "name": "Jan", "email": "",
                                       "numberformat": "1,000.00", "dateformat": "yyyy-mm-dd",
                                       "language": "nl"})
    assert "Opgeslagen" in r.text
    u = accounts.get_user(db, login="jan")
    assert (u["numberformat"], u["dateformat"]) == ("1,000.00", "yyyy-mm-dd")
    r = post(client, "/instellingen", {"action": "profile", "numberformat": "x",
                                       "dateformat": "yyyy-mm-dd", "language": "nl"})
    assert r.status_code == 400


def test_user_config_for_library(client, db, company):
    """De bibliotheek krijgt de medewerkerslogin en notatie van de gebruiker."""
    from sqlledger_web.web import _load

    make_user(db, "jan2", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "jan2")
    token = client.cookies["__Host-sl_session"]

    class Req:
        cookies = {"__Host-sl_session": token}
        app = client.app

        class state:
            pass

    ctx = _load(Req, db)
    cfg = ctx.user_config()
    assert (cfg.login, cfg.numberformat, cfg.dateformat) == ("jan", "1.000,00", "dd-mm-yy")


def test_settings_load_from_env_and_file(tmp_path):
    from sqlledger_web.settings import Settings

    f = tmp_path / "app.toml"
    f.write_text('db_port = 6000\nsecure_cookies = false\n')
    pw = tmp_path / "pw"
    pw.write_text("geheim\n")
    s = Settings.load(str(f), {"SQLLEDGER_DB_PORT": "7000", "SQLLEDGER_DB_PASSWORD_FILE": str(pw)})
    assert (s.db_port, s.secure_cookies, s.db_password) == (7000, False, "geheim")
    assert timedelta(seconds=s.session_timeout) == timedelta(hours=1)


def test_closedto_display():
    from sqlledger_web.routes_main import _closedto

    assert _closedto("20251231", "dd-mm-yy") == "31-12-2025"
    assert _closedto("2025-12-31", "yyyy-mm-dd") == "2025-12-31"
    assert _closedto("20173112", "dd-mm-yy") == "20173112"  # ongeldig in echte data
    assert _closedto("", "dd-mm-yy") == ""


def test_app_conninfo_gets_password():
    from sqlledger_web.settings import Settings

    assert "password=x" in Settings(app_dsn="host=db dbname=a", db_password="x").app_conninfo()
    assert Settings(app_dsn="host=db dbname=a").app_conninfo() == "host=db dbname=a"
    assert "password=y" in Settings(app_dsn="dbname=a password=y", db_password="x").app_conninfo()


def test_package_data_has_every_template_dir():
    """Elke sjabloonmap staat in pyproject (anders ontbreekt hij in een wheel/Docker-image)."""
    import tomllib
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["sqlledger_web"]
    web = root / "sqlledger_web"
    dirs = {p.parent.relative_to(web).as_posix() for p in (web / "templates").rglob("*.html")}
    missing = [d for d in sorted(dirs) if f"{d}/*.html" not in data]
    assert not missing, missing
