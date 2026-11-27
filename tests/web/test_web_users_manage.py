"""Pakket 3: gebruikers uitzetten of verwijderen (F27a) en "vertrouwelijk" aan de medewerker (F42)."""

import html

import psycopg
import pytest

from conftest import DSN
from webhelpers import login, make_user, nohelp, post


@pytest.fixture
def anna(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    return db.execute("SELECT * FROM app_user WHERE login = 'anna'").fetchone()


def user(db, name):
    return db.execute("SELECT * FROM app_user WHERE login = %s", (name,)).fetchone()


def text(r):
    return html.unescape(r.text)


def cconn(dbname):
    return psycopg.connect(f"{DSN} dbname={dbname}", autocommit=True)


# --- F27a ------------------------------------------------------------------------

def test_deactivate_removes_access_and_list_filter(client, db, company, anna):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    jan = user(db, "jan")
    db.execute("INSERT INTO setup_code (user_id, code_hash, expires) VALUES (%s, 'x', now() + interval '1 day')",
               (jan["id"],))
    url = f"/systeem/gebruikers/{jan['id']}"
    r = post(client, url, {"action": "save", "name": "Jan", f"role_{company['id']}": "bookkeeper"})
    assert "Opgeslagen" in r.text
    assert not db.execute("SELECT 1 FROM membership WHERE user_id = %s", (jan["id"],)).fetchone()
    assert not db.execute("SELECT 1 FROM setup_code WHERE user_id = %s", (jan["id"],)).fetchone()
    d = db.execute("SELECT detail FROM audit_log WHERE action = 'user_updated' ORDER BY id DESC LIMIT 1"
                   ).fetchone()["detail"]
    assert d["active"] is False and d["removed"] == ["Testbedrijf"]

    lst = nohelp(client.get("/systeem/gebruikers").text)
    assert f'/systeem/gebruikers/{jan["id"]}"' not in lst and "Ook uitgezette gebruikers tonen (1)" in lst
    lst = nohelp(client.get("/systeem/gebruikers?uit=1").text)
    assert f'/systeem/gebruikers/{jan["id"]}"' in lst and "Uitgezette gebruikers verbergen" in lst


def test_last_sysadmin_and_self(client, db, anna):
    r = post(client, f"/systeem/gebruikers/{anna['id']}", {"action": "save", "active": "1"})
    assert r.status_code == 400
    page = nohelp(client.get(f"/systeem/gebruikers/{anna['id']}").text)
    assert "Gebruiker verwijderen" not in page                           # eigen account: geen knop
    r = client.get(f"/systeem/gebruikers/{anna['id']}/verwijderen")
    assert r.status_code == 400 and "eigen account" in text(r)


def test_delete_only_never_logged_in(client, db, company, anna):
    make_user(db, "fout", memberships=[(company["id"], "readonly")])
    fout = user(db, "fout")
    page = nohelp(client.get(f"/systeem/gebruikers/{fout['id']}").text)
    assert "Gebruiker verwijderen" in page
    url = f"/systeem/gebruikers/{fout['id']}/verwijderen"
    assert "Ja, gebruiker verwijderen" in client.get(url).text
    r = post(client, url, {})
    assert r.url.path == "/systeem/gebruikers" and "De gebruiker is verwijderd" in r.text
    assert not user(db, "fout")
    d = db.execute("SELECT detail FROM audit_log WHERE action = 'user_deleted'").fetchone()["detail"]
    assert d["target"] == "fout" and d["companies"] == ["Testbedrijf"]

    # wie al eens inlogde: geen knop, verwijderen geweigerd
    make_user(db, "piet")
    db.execute("UPDATE app_user SET last_login = now() WHERE login = 'piet'")
    piet = user(db, "piet")
    assert "Verwijderen kan alleen" in nohelp(client.get(f"/systeem/gebruikers/{piet['id']}").text)
    r = post(client, f"/systeem/gebruikers/{piet['id']}/verwijderen", {}, page=f"/systeem/gebruikers/{piet['id']}")
    assert r.status_code == 400 and user(db, "piet")


def test_reuse_warning_and_inactive_login(client, db, anna):
    make_user(db, "fout")
    fout = user(db, "fout")
    post(client, f"/systeem/gebruikers/{fout['id']}/verwijderen", {})
    url = "/systeem/gebruikers/nieuw"
    r = post(client, url, {"login": "Fout", "name": "Nieuwe Fout", "is_sysadmin": "1"})
    assert r.status_code == 400 and "eerder gebruikt" in text(r) and 'name="reuse"' in r.text
    assert 'name="is_sysadmin" value="1" checked' in r.text and not user(db, "fout")
    r = post(client, url, {"login": "fout", "name": "Nieuwe Fout", "reuse": "1"})
    assert r.status_code == 200 and user(db, "fout")["name"] == "Nieuwe Fout"
    assert db.execute("SELECT detail FROM audit_log WHERE action = 'user_created' ORDER BY id DESC LIMIT 1"
                      ).fetchone()["detail"]["reused"] is True

    make_user(db, "oud")
    db.execute("UPDATE app_user SET active = false WHERE login = 'oud'")
    r = post(client, url, {"login": "oud"})
    assert r.status_code == 400 and "staat uit" in text(r)


# --- F42 -------------------------------------------------------------------------

def test_two_active_users_same_employee_refused(client, db, company, anna):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper", "smith")])
    make_user(db, "piet")
    piet = user(db, "piet")
    url = f"/systeem/gebruikers/{piet['id']}"
    data = {"action": "save", "active": "1", f"role_{company['id']}": "readonly",
            f"employee_{company['id']}": "Smith"}
    r = post(client, url, data)
    assert r.status_code == 400 and "al gekoppeld aan gebruiker jan" in text(r)
    assert not db.execute("SELECT 1 FROM membership WHERE user_id = %s", (piet["id"],)).fetchone()
    # jan uitgezet: dan mag het wel
    db.execute("UPDATE app_user SET active = false WHERE login = 'jan'")
    assert "Opgeslagen" in post(client, url, data).text


def test_change_employee_offers_to_move_confidential(client, db, company, company_db, anna):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper", "jan")])
    jan = user(db, "jan")
    with cconn(company_db) as c:
        for login_ in ("jan", "jan", ""):
            c.execute("INSERT INTO reference (code, trans_id, description, login, formname) "
                      "VALUES ('', 1, 'x', %s, 'gl')", (login_,))
    url = f"/systeem/gebruikers/{jan['id']}"
    data = {"action": "save", "name": "Jan", "active": "1", f"role_{company['id']}": "bookkeeper",
            f"employee_{company['id']}": "jjansen"}
    r = post(client, url, data)
    t = text(r)
    assert "2 vertrouwelijke bijlagen onder login jan meeverhuizen naar jjansen" in t
    assert db.execute("SELECT employee_login FROM membership WHERE user_id = %s",
                      (jan["id"],)).fetchone()["employee_login"] == "jan"       # nog niets opgeslagen
    assert f'name="employee_{company["id"]}" value="jjansen"' in r.text and 'name="moves" value="1"' in r.text

    # bevestigen met vinkje: medewerker gewijzigd en bijlagen verhuisd
    r = post(client, url, dict(data, moves="1", **{f"move_{company['id']}": "1"}), page=url)
    assert "Opgeslagen" in r.text
    assert db.execute("SELECT employee_login FROM membership WHERE user_id = %s",
                      (jan["id"],)).fetchone()["employee_login"] == "jjansen"
    with cconn(company_db) as c:
        assert c.execute("SELECT count(*) FROM reference WHERE login = 'jjansen'").fetchone()[0] == 2
    d = db.execute("SELECT detail FROM audit_log WHERE action = 'attachments_moved'").fetchone()["detail"]
    assert (d["old"], d["new"], d["count"]) == ("jan", "jjansen", 2)

    # terug zonder vinkje: medewerker gewijzigd, bijlagen blijven staan
    r = post(client, url, dict(data, moves="1", **{f"employee_{company['id']}": "jan"}), page=url)
    assert "Opgeslagen" in r.text
    with cconn(company_db) as c:
        assert c.execute("SELECT count(*) FROM reference WHERE login = 'jjansen'").fetchone()[0] == 2
