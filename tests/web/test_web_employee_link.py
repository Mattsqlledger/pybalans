"""v1.0.1 PB14: medewerker bij de login. Nieuwe administratie = meteen een medewerker voor de beheerder; Mijn uren
zonder koppeling wijst de weg; Systeem > Gebruikers > bewerken heeft per administratie de keuzelijst "Medewerker"."""

import html
import re
import uuid

import psycopg
import pytest

from conftest import DSN
from webhelpers import csrf, login, make_user, post


def choose(client, company_id):
    page = client.get("/administratie").text
    client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company_id})


def cconn(dbname):
    return psycopg.connect(f"{DSN} dbname={dbname}", autocommit=True)


@pytest.fixture
def newdb(pg_admin):
    name = f"we_{uuid.uuid4().hex[:10]}"
    yield name
    pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_new_company_gives_admin_an_employee(client, db, newdb):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    db.execute("UPDATE app_user SET name = 'Anna de Vries' WHERE login = 'anna'")
    login(client, "anna", secret)
    data = {"name": "Bakkerij Jansen", "code": "", "dbname": newdb, "chart": "leeg-nl", "language": "",
            "currency": "", "yearend": "12", "mod_gl": "1", "mod_ar": "1", "mod_ap": "1", "mod_jc": "1",
            "add_me": "1"}
    r = post(client, "/systeem/administraties/aanmaken", data)
    assert r.status_code == 200
    with cconn(newdb) as c:
        assert c.execute("SELECT name FROM employee WHERE login = 'anna'").fetchone() == ("Anna de Vries",)
    cid = db.execute("SELECT id FROM company WHERE dbname = %s", (newdb,)).fetchone()["id"]
    choose(client, cid)
    page = html.unescape(client.get("/uren/mijn").text)
    assert "Uw login is niet aan een medewerker gekoppeld." not in page


def test_my_hours_without_employee_points_the_way(client, db, company):
    # systeembeheerder: link naar de eigen gebruiker, bij deze administratie
    secret = make_user(db, "anna", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "niemand")])
    login(client, "anna", secret)
    choose(client, company["id"])
    page = html.unescape(client.get("/uren/mijn").text)
    me = db.execute("SELECT id FROM app_user WHERE login = 'anna'").fetchone()["id"]
    assert "Uw login is niet aan een medewerker gekoppeld." in page
    assert f'<a href="/systeem/gebruikers/{me}#adm-{company["id"]}">Koppel uw login aan een medewerker</a>' in page
    client.post("/uitloggen", data={"csrf": csrf(client, client.get("/").text)})
    # gewone gebruiker: vraag het de beheerder
    secret = make_user(db, "uren", totp=True, memberships=[(company["id"], "hours", "niemand")])
    login(client, "uren", secret)
    choose(client, company["id"])
    page = html.unescape(client.get("/uren/mijn").text)
    assert "Vraag dit aan de beheerder." in page and "Koppel je login" not in page


def test_user_edit_employee_select(client, db, company):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    make_user(db, "kees", memberships=[(company["id"], "bookkeeper", "oud")])
    kees = db.execute("SELECT id FROM app_user WHERE login = 'kees'").fetchone()["id"]
    page = html.unescape(client.get(f"/systeem/gebruikers/{kees}").text)
    assert "<th>Medewerker</th>" in page and "Medewerkerslogin in SQL-Ledger" not in page
    sel = re.search(rf'<select name="employee_{company["id"]}"[^>]*>(.*?)</select>', page, re.S).group(1)
    assert '<option value="jan">Jan Jansen (jan)</option>' in sel
    assert '<option value="oud" selected>oud (geen medewerker)</option>' in sel       # huidige waarde blijft
    assert '<option value="+new">nieuwe medewerker aanmaken: Kees (kees)</option>' in sel
    # kiezen: membership.employee_login = login van de medewerker
    r = post(client, f"/systeem/gebruikers/{kees}", {"action": "save", "name": "Kees", "active": "1",
                                                    f"role_{company['id']}": "bookkeeper",
                                                    f"employee_{company['id']}": "jan"})
    assert "Opgeslagen" in r.text
    assert db.execute("SELECT employee_login FROM membership WHERE user_id = %s",
                      (kees,)).fetchone()["employee_login"] == "jan"
    # nieuwe medewerker aanmaken
    r = post(client, f"/systeem/gebruikers/{kees}", {"action": "save", "name": "Kees", "active": "1",
                                                    f"role_{company['id']}": "bookkeeper",
                                                    f"employee_{company['id']}": "+new"})
    assert "Opgeslagen" in r.text
    assert db.execute("SELECT employee_login FROM membership WHERE user_id = %s",
                      (kees,)).fetchone()["employee_login"] == "kees"
    with cconn(company["dbname"]) as c:
        assert c.execute("SELECT name FROM employee WHERE login = 'kees'").fetchone() == ("Kees",)
    page = html.unescape(client.get(f"/systeem/gebruikers/{kees}").text)
    assert '<option value="kees" selected>Kees (kees)</option>' in page and 'value="+new"' not in page


def test_user_edit_unreachable_company_keeps_text_field(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    cid = db.execute("INSERT INTO company (name, dbname, modules) VALUES ('Weg', 'bestaat_niet_x', '{gl}') "
                     "RETURNING id").fetchone()["id"]
    me = db.execute("SELECT id FROM app_user WHERE login = 'anna'").fetchone()["id"]
    page = client.get(f"/systeem/gebruikers/{me}").text
    assert f'<input name="employee_{cid}"' in page


def test_login_and_name_help_and_case_insensitive_login(client, db):
    """v1.0.1 PB23: uitleg onder Login en Naam (wizard en Systeem > Gebruikers); inloggen negeert hoofdletters
    (login wordt klein opgeslagen en klein gezocht; uniek ongeacht hoofdletters)."""
    from sqlledger_web import accounts
    from webhelpers import PASSWORD
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    page = html.unescape(client.get("/systeem/gebruikers/nieuw").text)
    # golf 3 F15: hier vult de systeembeheerder het in voor een ander
    assert "Waarmee de gebruiker inlogt. Kort, zonder spaties, bijvoorbeeld jsmith. Hoofdletters maken niet uit." in page
    assert "De volledige naam, bijvoorbeeld Jane Smith." in page
    uid = accounts.create_user(db, "JSmith", "Jane Smith")
    assert accounts.get_user(db, user_id=uid)["login"] == "jsmith"
    with pytest.raises(accounts.AccountError):
        accounts.create_user(db, "JSMITH")
    assert accounts.get_user(db, login=" JSmiTh ")["id"] == uid
    client.post("/uitloggen", data={"csrf": csrf(client, client.get("/").text)})
    db.execute("UPDATE app_user SET password_hash = (SELECT password_hash FROM app_user WHERE login = 'anna') "
               "WHERE id = %s", (uid,))
    r = post(client, "/login", {"login": "JSMITH", "password": PASSWORD})
    assert r.url.path != "/login"
