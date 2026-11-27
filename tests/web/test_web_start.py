"""Eerste start (wizard): EERSTE_START_ANALYSE.md, ES1–ES9 en keuzes 1–16."""

import re
import uuid
from pathlib import Path

import psycopg
import pyotp
import pytest
from psycopg.rows import dict_row

from sqlledger import UserConfig
from sqlledger.dashboard import Dashboard
from sqlledger_web import accounts, mailer, routes_start
from sqlledger_web.routes_system import suggest_dbname

from webhelpers import PASSWORD, csrf, login, make_user, post, totp_now


def actions(db):
    return [r["action"] for r in db.execute("SELECT action FROM audit_log ORDER BY id").fetchall()]


@pytest.fixture
def code(db):
    """Een bekende startcode (de server maakt er bij het starten een; deze vervangt hem)."""
    return routes_start.issue_start_code(db)


@pytest.fixture
def newdb(pg_admin):
    name = f"wn_{uuid.uuid4().hex[:10]}"
    yield name
    pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def admin_form(client, code, lang="nl", **kw):
    post(client, "/welkom", {"language": lang})
    data = {"startcode": code, "login": "Beheer", "name": "Bea Heer", "email": "bea@example.com",
            "password": PASSWORD, "repeat": PASSWORD}
    data.update(kw)
    return post(client, "/welkom/beheerder", data)


def through_totp(client, r):
    """Stap 3: het bestaande scherm 2FA instellen; geeft het antwoord na instellen."""
    secret = re.search(r'<code class="secret">([A-Z2-7]+)</code>', r.text).group(1)
    return post(client, "/2fa/instellen", {"code": pyotp.TOTP(secret).now()}, page="/2fa/instellen?welkom=1"), secret


# ------------------------------------------------------------------ keuze 1–3: route, sluiten, startcode

def test_everything_goes_to_welcome_without_users(client):
    for url in ("/", "/login", "/systeem/gebruikers", "/activeren", "/instellingen"):
        r = client.get(url)
        assert r.url.path == "/welkom", url
    assert "Welcome to PyBalans" in r.text and 'lang="en"' in r.text      # Engels (ES3)
    assert client.get("/static/style.css").status_code == 200
    r = client.get("/dashboard/blok/bank", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.headers["HX-Redirect"] == "/welkom" and not r.text


def test_welcome_closed_once_a_user_exists(client, db):
    make_user(db, "anna")
    assert client.get("/welkom").url.path == "/login"
    assert client.get("/welkom/beheerder").url.path == "/login"
    assert client.get("/login").url.path == "/login"


def test_start_code_only_without_users(db, capsys):
    code = routes_start.issue_start_code(db)
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
    stored = db.execute("SELECT value FROM app_meta WHERE key = 'start_code'").fetchone()["value"]
    assert stored != code and len(stored) == 64                             # alleen de hash
    assert routes_start.issue_start_code(db) != code                         # elke start een nieuwe
    routes_start.announce_start_code(code)
    assert code in capsys.readouterr().err
    make_user(db, "anna")
    assert routes_start.issue_start_code(db) is None
    assert not db.execute("SELECT 1 FROM app_meta WHERE key = 'start_code'").fetchone()


def test_server_start_creates_app_database_and_start_code(pg_admin, settings, capsys):
    """Keuze 2: de app-database bestaat nog niet; de server maakt hem en zet de startcode in het log."""
    from fastapi.testclient import TestClient
    from sqlledger_web.main import create_app
    pg_admin.execute(f'DROP DATABASE "{settings.app_dsn.split("dbname=")[1]}" WITH (FORCE)')
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        assert c.get("/").url.path == "/welkom"
    assert "Start code / startcode:" in capsys.readouterr().err


# ------------------------------------------------------------------ keuze 4–5: taal en beheerder

def test_language_choice_and_admin_form(client, code):
    page = client.get("/welkom", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).text
    assert re.search(r'<button name="language" value="fr" lang="fr" class=""', page)   # browsertaal voorgekozen
    assert client.get("/welkom/beheerder").url.path == "/welkom"                     # eerst een taal
    r = post(client, "/welkom", {"language": "de"})
    assert r.url.path == "/welkom/beheerder" and "Administrator anlegen" in r.text and 'lang="de"' in r.text
    assert client.cookies.get("sl_lang") == "de"
    # v1.0.1 PB23: uitleg onder Login en Naam, startcode in het logbestand
    assert "Groß- und Kleinschreibung spielt keine Rolle" in r.text and "zum Beispiel Jane Smith" in r.text
    assert "log/web.log" in r.text
    r = post(client, "/welkom", {"language": "xx"})
    assert r.status_code == 400


def test_wrong_start_code_refused_and_counts_for_ip_block(client, db, code, settings):
    for _ in range(settings.lockout_attempts):
        r = admin_form(client, "AAAA-BBBB-CCCC")
        assert r.status_code == 400 and "Startcode onjuist" in r.text
    assert actions(db).count("code_failed") == settings.lockout_attempts
    r = admin_form(client, code)                                           # nu ook de goede geweigerd
    assert r.status_code == 429 and "Te veel mislukte pogingen" in r.text
    assert not routes_start.has_users(db)


def test_admin_form_checks(client, db, code):
    assert admin_form(client, code, repeat="anders").status_code == 400
    assert admin_form(client, code, login="Met Spatie").status_code == 400
    assert admin_form(client, code, email="geen-adres").status_code == 400
    assert not routes_start.has_users(db)
    r = client.post("/welkom/beheerder", data={"startcode": code, "login": "x", "password": PASSWORD,
                                              "repeat": PASSWORD})
    assert r.status_code == 400                                            # zonder CSRF-token


def test_admin_created_then_forced_totp_then_company_step(client, db, code):
    r = admin_form(client, code, lang="de")
    assert r.url.path == "/2fa/instellen" and r.url.query == b"welkom=1"
    assert "Zwei-Faktor-Authentifizierung" in r.text and 'aria-current="step"' in r.text
    user = accounts.get_user(db, login="beheer")
    assert user["is_sysadmin"] and user["name"] == "Bea Heer" and user["email"] == "bea@example.com"
    assert (user["language"], user["numberformat"], user["dateformat"]) == ("de", "1.000,00", "dd.mm.yy")
    assert "sl_lang" not in client.cookies
    assert not db.execute("SELECT 1 FROM app_meta WHERE key = 'start_code'").fetchone()
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'user_created'").fetchone()
    assert log["detail"] == {"via": "welkom", "sysadmin": True}
    # zonder 2FA verder niets (ook niet de volgende wizardstap)
    assert client.get("/welkom/administratie").url.path == "/2fa/instellen"
    r, _ = through_totp(client, r)
    assert r.url.path == "/welkom/administratie" and "Erster Mandant" in r.text
    assert "Sicherung einlesen" in r.text and 'href="/welkom/administratie/back-up"' in r.text     # fase 6a (was grijs)
    # tweede keer: welkom is dicht, ook met de oude code
    assert client.get("/welkom").url.path != "/welkom"
    r = client.post("/welkom/beheerder", data={"csrf": csrf(client, client.get("/welkom/administratie").text),
                                              "startcode": code, "login": "tweede", "password": PASSWORD,
                                              "repeat": PASSWORD})
    assert accounts.get_user(db, login="tweede") is None


def test_wizard_steps_need_sysadmin(client, db, company):
    assert client.get("/welkom/administratie").url.path == "/welkom"        # nog niemand
    make_user(db, "jan", memberships=[(company["id"], "admin")])
    assert client.get("/welkom/administratie").url.path == "/login"
    secret = make_user(db, "boek", totp=False, memberships=[(company["id"], "bookkeeper")])
    login(client, "boek", secret)
    for url in ("/welkom/administratie", "/welkom/administratie/nieuw", f"/welkom/gebruikers?administratie={company['id']}"):
        assert client.get(url).status_code == 403, url


def test_resume_after_interruption(client, db):
    """Keuze 6: systeembeheerder logt in terwijl er nog geen administratie is → stap 4."""
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    r = login(client, "anna", secret)
    assert r.url.path == "/welkom/administratie"
    assert client.get("/administratie").url.path == "/welkom/administratie"


# ------------------------------------------------------------------ keuze 7–11: administratie en gebruikers

def _sysadmin(client, db, lang="nl"):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    db.execute("UPDATE app_user SET language = %s WHERE login = 'anna'", (lang,))
    login(client, "anna", secret)


@pytest.mark.parametrize("lang,chart,label", [("nl", "nl-eenmanszaak", "Nederland"), ("en", "leeg-uk", "United Kingdom"),
                                              ("fr", "leeg-fr", "France"), ("de", "leeg-de", "Deutschland")])
def test_new_company_defaults_follow_language(client, db, lang, chart, label):
    _sysadmin(client, db, lang)
    page = client.get("/welkom/administratie/nieuw").text
    assert f'value="{chart}" selected' in page and f'<optgroup label="{label}">' in page
    assert f'<option value="{lang}" selected>' in page                     # rekeningnamen in de taal
    assert 'action="/welkom/administratie/nieuw"' in page and "data-dbname-target" in page


def test_new_company_then_invite_users_then_dashboard(client, db, newdb, pg_admin):
    _sysadmin(client, db)
    data = {"name": "Bakkerij Één", "code": "", "dbname": "", "chart": "leeg-de", "language": "de", "currency": "",
            "yearend": "12", "mod_gl": "1", "mod_ar": "1", "mod_ap": "1", "mod_cash": "1", "add_me": "1"}
    assert suggest_dbname("Bakkerij Één") == "bakkerij_een"
    # databasenaam leeg = uit de naam; voor de toets een eigen naam
    r = post(client, "/welkom/administratie/nieuw", dict(data, dbname=newdb))
    assert r.url.path == "/welkom/gebruikers", r.text[:500]
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (newdb,)).fetchone()
    assert comp["name"] == "Bakkerij Één" and comp["doc_language"] == "de" and not comp["vat_return"]
    with psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={newdb}", row_factory=dict_row) as conn:
        assert conn.execute("SELECT description FROM chart WHERE accno = '1776'").fetchone()["description"] == \
            "Umsatzsteuer 19 %"
        assert conn.execute("SELECT curr FROM curr WHERE rn = 1").fetchone()["curr"] == "EUR"

    url = f"/welkom/gebruikers?administratie={comp['id']}"
    page = client.get(url).text
    assert page.count('name="login_') == 3
    r = post(client, "/welkom/gebruikers", {"company_id": comp["id"], "rows": "3", "action": "add"}, page=url)
    assert r.text.count('name="login_') == 4
    rows = {"company_id": comp["id"], "rows": "4", "action": "save",
            "name_0": "Jan", "login_0": "jan", "email_0": "jan@example.com", "role_0": "bookkeeper",
            "name_1": "Kees", "login_1": "kees", "role_1": "readonly",
            "name_2": "Dubbel", "login_2": "anna", "role_2": "admin"}
    r = post(client, "/welkom/gebruikers", rows, page=url)
    assert r.status_code == 400 and "Login &#39;anna&#39; bestaat al." in r.text
    assert len(re.findall(r'<td class="code">[A-Z2-9-]{14}</td>', r.text)) == 2
    assert 'href="/systeem/gebruikers/' in r.text and "Toegang per administratie" in r.text
    m = {x["login"]: x["role"] for x in db.execute(
        "SELECT u.login, m.role FROM membership m JOIN app_user u ON u.id = m.user_id WHERE m.company_id = %s",
        (comp["id"],)).fetchall()}
    assert m == {"anna": "admin", "jan": "bookkeeper", "kees": "readonly"}
    assert db.execute("SELECT count(*) AS n FROM setup_code").fetchone()["n"] == 2

    # niets ingevuld = melding; klaar = administratie gekozen, dashboard met startlijst
    r = post(client, "/welkom/gebruikers", {"company_id": comp["id"], "rows": "3", "action": "save"}, page=url)
    assert r.status_code == 400
    r = post(client, "/welkom/klaar", {"company_id": comp["id"]}, page=url)
    assert r.url.path == "/" and 'hx-get="/dashboard/blok/start"' in r.text
    block = client.get("/dashboard/blok/start").text
    assert "Aan de slag" in block and "Bedrijfsgegevens invullen" in block and "Beginbalans boeken" in block
    assert "Eerste verkoopfactuur maken" in block and "Logo en huisstijl" in block


def test_link_existing_database_in_wizard(client, db, company_db):
    _sysadmin(client, db)
    page = client.get("/welkom/administratie/koppelen").text
    assert 'action="/welkom/administratie/koppelen"' in page and 'name="add_me" value="1" checked' in page
    r = post(client, "/welkom/administratie/koppelen",
             {"name": "Oud bedrijf", "dbname": company_db, "mod_gl": "1", "active": "1", "add_me": "1"})
    assert r.url.path == "/welkom/gebruikers"
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (company_db,)).fetchone()
    role = db.execute("SELECT role FROM membership WHERE company_id = %s", (comp["id"],)).fetchone()["role"]
    assert role == "admin"
    r = post(client, "/welkom/klaar", {"company_id": comp["id"]}, page=r.url.path + "?" + r.url.query.decode())
    assert r.url.path == "/"


# ------------------------------------------------------------------ keuze 12: startlijst

def test_start_list_marks_and_hides(client, db, company):
    with psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={company['dbname']}") as conn:
        Dashboard(conn, UserConfig(login="baas")).enable_start_list()
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper")])
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin")])
    login(client, "baas", secret)
    block = client.get("/dashboard/blok/start").text
    # sl_template: bedrijfsgegevens en boekingen staan er al in → die punten zijn klaar
    assert 'class="done"' in block and "Logo en huisstijl" in block and 'href="/beheer/huisstijl"' in block
    assert "Kies logo, kleur en lettertype" in block
    r = post(client, "/dashboard/startlijst", {"item": "style"}, page="/")
    assert r.url.path == "/"
    block = client.get("/dashboard/blok/start").text
    assert re.search(r'class="skipped">.*?Logo en huisstijl', block, re.S)
    assert client.post("/dashboard/startlijst", data={"csrf": csrf(client, client.get("/").text),
                                                      "item": "bestaatniet"}).status_code == 400
    post(client, "/dashboard/startlijst", {"item": "hidden"}, page="/")
    assert "Aan de slag" not in client.get("/dashboard/blok/start").text
    # boekhouder ziet de startlijst niet
    post(client, "/uitloggen", {}, page="/instellingen")
    login(client, "boek")
    assert 'blok/start' not in client.get("/").text
    assert client.get("/dashboard/blok/start").status_code == 403


# ------------------------------------------------------------------ keuze 11: code mailen bij Systeem > Gebruikers

def test_code_mail_from_users_screen(client, db, company, settings):
    _sysadmin(client, db)
    db.execute("INSERT INTO company_mail (company_id, mode, from_address) VALUES (%s, 'folder', 'adm@example.com')",
               (company["id"],))
    r = post(client, "/systeem/gebruikers/nieuw", {"login": "piet", "name": "Piet", "email": "piet@example.com"},
             page="/systeem/gebruikers/nieuw")
    code = re.search(r'<p class="code">([A-Z2-9-]+)</p>', r.text).group(1)
    assert "Code mailen naar piet@example.com" in r.text
    uid = accounts.get_user(db, login="piet")["id"]
    bad = client.post(f"/systeem/gebruikers/{uid}/code-mailen",
                      data={"csrf": csrf(client, r.text), "code": "AAAA-BBBB-CCCC", "company_id": company["id"]})
    assert bad.status_code == 400
    ok = client.post(f"/systeem/gebruikers/{uid}/code-mailen",
                     data={"csrf": csrf(client, r.text), "code": code, "company_id": company["id"]})
    assert ok.status_code == 200 and "Code gemaild naar piet@example.com" in ok.text
    mails = list((Path(settings.data_dir) / "mail" / str(company["id"])).glob("*.eml"))
    assert len(mails) == 1 and code in mails[0].read_text()
    assert "code_mailed" in actions(db)


def test_code_page_without_mail_explains(client, db):
    _sysadmin(client, db)
    r = post(client, "/systeem/gebruikers/nieuw", {"login": "piet", "email": "piet@example.com"},
             page="/systeem/gebruikers/nieuw")
    assert "Code mailen" not in r.text and "Mailen kan zodra e-mail is ingesteld" in r.text


# ------------------------------------------------------------------ ES10–ES12

def _flag(dbname):
    with psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={dbname}") as conn:
        if not conn.execute("SELECT to_regclass('x_setting')").fetchone()[0]:
            return None
        return conn.execute("SELECT value FROM x_setting WHERE key = 'start_list'").fetchone()


def test_start_list_only_for_new_or_wizard_linked(client, db, company_db, pg_admin):
    """ES10 A: koppelen via Systeem = geen startlijst; via de wizard wel."""
    _sysadmin(client, db)
    r = post(client, "/systeem/administraties/nieuw", {"name": "Via systeem", "dbname": company_db, "mod_gl": "1",
                                                       "active": "1"}, page="/systeem/administraties/nieuw")
    assert r.status_code == 200
    assert _flag(company_db) is None
    other = f"wc_{uuid.uuid4().hex[:10]}"
    pg_admin.execute(f'CREATE DATABASE "{other}" TEMPLATE sl_template')
    try:
        post(client, "/welkom/administratie/koppelen",
             {"name": "Via wizard", "dbname": other, "mod_gl": "1", "active": "1", "add_me": "1"})
        assert _flag(other)[0] == "1"
    finally:
        pg_admin.execute(f'DROP DATABASE "{other}" WITH (FORCE)')


def test_old_sql_ledger_version_refused(client, db, company_db):
    """ES12 A: databaseversie onder 3.2.4 = waarschuwing bij testen, opslaan geweigerd (wizard en Systeem)."""
    with psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={company_db}", autocommit=True) as conn:
        conn.execute("UPDATE defaults SET fldvalue = '2.8.10' WHERE fldname = 'version'")
    _sysadmin(client, db)
    page = client.get("/welkom/administratie/koppelen").text
    data = {"name": "Oud", "dbname": company_db, "mod_gl": "1", "active": "1", "add_me": "1"}
    r = client.post("/systeem/administraties/test", data={"csrf": csrf(client, page), **data})
    assert "SQL-Ledger-versie 2.8.10; nodig is 3.2.4 of hoger" in r.text and 'class="error"' in r.text
    for url, pg in (("/welkom/administratie/koppelen", None), ("/systeem/administraties/nieuw", "/systeem/administraties/nieuw")):
        r = post(client, url, data, page=pg)
        assert r.status_code == 400 and "nodig is 3.2.4 of hoger" in r.text, url
    assert db.execute("SELECT count(*) AS n FROM company").fetchone()["n"] == 0
    # 3.2.4 en 4.x mogen (4.x met de melding "niet ondersteund")
    from sqlledger_web.companies import too_old
    assert too_old("2.8.10") and too_old("3.2.3") and not too_old("3.2.4") and not too_old("4.1.0") and not too_old("")


def test_newer_sql_ledger_version_not_supported(client, db, company_db):
    """Een database nieuwer dan 3.2.4 (SQL-Ledger 4.x): koppelen mag, met de melding "niet ondersteund"."""
    with psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={company_db}", autocommit=True) as conn:
        conn.execute("UPDATE defaults SET fldvalue = '4.1.0' WHERE fldname = 'version'")
    _sysadmin(client, db)
    page = client.get("/welkom/administratie/koppelen").text
    data = {"name": "Nieuw", "dbname": company_db, "mod_gl": "1", "active": "1", "add_me": "1"}
    r = client.post("/systeem/administraties/test", data={"csrf": csrf(client, page), **data})
    assert "nieuwer dan SQL-Ledger 3.2.12" in r.text and "gebruik op eigen risico" in r.text
    assert 'class="notice warning"' in r.text and 'class="error"' not in r.text
    post(client, "/welkom/administratie/koppelen", data)
    assert db.execute("SELECT count(*) AS n FROM company").fetchone()["n"] == 1
    from sqlledger_web.companies import too_new
    assert too_new("4.1.0") and not too_new("3.2.4") and not too_new("2.8.10") and not too_new("")

def test_bank_details_on_account_screen(client, db, company):
    """ES11 A: IBAN/BIC/banknaam bij een betaalrekening in Beheer > Rekeningschema; startlijst wijst ernaar."""
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin")])
    login(client, "baas", secret)
    with psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={company['dbname']}", row_factory=dict_row) as conn:
        Dashboard(conn, UserConfig(login="baas")).enable_start_list()
        acc = conn.execute("SELECT id, accno, description, category, link FROM chart WHERE link LIKE '%%AR_paid%%' "
                           "ORDER BY accno LIMIT 1").fetchone()
    block = client.get("/dashboard/blok/start").text
    assert f'href="/beheer/rekeningschema/{acc["id"]}#bank"' in block
    page = client.get(f"/beheer/rekeningschema/{acc['id']}").text
    assert 'name="iban"' in page and "Bankgegevens" in page
    form = {"id": acc["id"], "accno": acc["accno"], "description": acc["description"], "category": acc["category"],
            "charttype": "A", **{f"link_{k}": "1" for k in acc["link"].split(":")}}
    r = post(client, "/beheer/rekeningschema", dict(form, iban="NL00 BANK 0000 0000 01", action="save"),
             page=f"/beheer/rekeningschema/{acc['id']}")
    assert r.status_code == 400 and "is niet geldig" in r.text
    r = post(client, "/beheer/rekeningschema", dict(form, iban="nl91 abna 0417 1643 00", bic="abnanl2a",
                                                    bankname="ABN AMRO", action="save"),
             page=f"/beheer/rekeningschema/{acc['id']}")
    assert r.status_code == 200
    with psycopg.connect(f"host=127.0.0.1 port=55432 user=postgres dbname={company['dbname']}", row_factory=dict_row) as conn:
        b = conn.execute("SELECT name, iban, bic FROM bank WHERE id = %s", (acc["id"],)).fetchone()
    assert (b["name"], b["iban"], b["bic"]) == ("ABN AMRO", "NL91ABNA0417164300", "ABNANL2A")
    block = client.get("/dashboard/blok/start").text
    assert re.search(r'class="done">.*?IBAN van de bankrekening invullen', block, re.S)
