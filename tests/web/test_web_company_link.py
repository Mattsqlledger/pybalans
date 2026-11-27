"""Systeem > Administraties: koppelen met "Mij als beheerder" en dubbele database (F23), andere database met
bevestigstap (F24)."""

import uuid

import psycopg
import pytest

from conftest import DSN, TEMPLATE
from webhelpers import login, make_user, nohelp, post


@pytest.fixture
def other_db(pg_admin):
    """Tweede SQL-Ledger-database met een eigen bedrijfsnaam."""
    name = f"wc_{uuid.uuid4().hex[:10]}"
    pg_admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{TEMPLATE}"')
    with psycopg.connect(f"{DSN} dbname={name}", autocommit=True) as conn:
        conn.execute("UPDATE defaults SET fldvalue = 'Ander BV' WHERE fldname = 'company'")
    yield name
    pg_admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def anna(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    return db.execute("SELECT * FROM app_user WHERE login = 'anna'").fetchone()


def detail(db, action):
    return db.execute("SELECT detail FROM audit_log WHERE action = %s ORDER BY id DESC LIMIT 1",
                      (action,)).fetchone()["detail"]


def test_link_adds_me_and_refuses_same_database(client, db, company_db, other_db, anna):
    page = client.get("/systeem/administraties/nieuw").text
    assert 'name="add_me" value="1" checked' in page                     # F23: standaard aan
    url = "/systeem/administraties/nieuw"
    r = post(client, url, {"name": "Adm C", "dbname": company_db, "active": "1", "add_me": "1"})
    assert r.url.path.startswith("/systeem/administraties/") and "Opgeslagen" in r.text
    admc = db.execute("SELECT * FROM company WHERE name = 'Adm C'").fetchone()
    m = db.execute("SELECT * FROM membership WHERE company_id = %s", (admc["id"],)).fetchone()
    assert (m["user_id"], m["role"], m["employee_login"]) == (anna["id"], "admin", "anna")
    assert detail(db, "company_created")["admin"] == "anna"

    # dezelfde database nog eens koppelen: geweigerd (ook met de standaardserver uitgeschreven)
    r = post(client, url, {"name": "Adm C 2", "dbname": company_db, "dbhost": "127.0.0.1", "dbport": "55432"})
    assert r.status_code == 400 and "hoort al bij de administratie Adm C" in r.text
    assert not db.execute("SELECT 1 FROM company WHERE name = 'Adm C 2'").fetchone()

    # zonder vinkje: niemand heeft toegang → melding op lijst en scherm, vinkje daar alsnog
    r = post(client, url, {"name": "Holding", "dbname": other_db, "active": "1"})
    hold = db.execute("SELECT * FROM company WHERE name = 'Holding'").fetchone()
    assert "Niemand heeft toegang" in r.text
    lst = nohelp(client.get("/systeem/administraties").text)
    assert lst.count("niemand heeft toegang") == 1
    page = client.get(f"/systeem/administraties/{hold['id']}").text
    assert 'name="add_me" value="1">' in page                            # hier standaard uit
    assert 'name="add_me"' not in client.get(f"/systeem/administraties/{admc['id']}").text
    r = post(client, f"/systeem/administraties/{hold['id']}",
             {"name": "Holding", "dbname": other_db, "active": "1", "add_me": "1"})
    assert "Opgeslagen" in r.text and "Niemand heeft toegang" not in r.text
    assert db.execute("SELECT role FROM membership WHERE company_id = %s AND user_id = %s",
                      (hold["id"], anna["id"])).fetchone()["role"] == "admin"
    assert "niemand heeft toegang" not in nohelp(client.get("/systeem/administraties").text)


def test_inactive_user_is_no_access(client, db, company, anna):
    make_user(db, "oud", memberships=[(company["id"], "admin")])
    db.execute("UPDATE app_user SET active = false WHERE login = 'oud'")
    assert "niemand heeft toegang" in nohelp(client.get("/systeem/administraties").text)


def test_change_database_needs_confirmation(client, db, company, company_db, other_db, anna):
    url = f"/systeem/administraties/{company['id']}"
    base = {"name": "Testbedrijf", "active": "1", "mod_gl": "1"}
    # alleen de naam: geen bevestigstap
    r = post(client, url, dict(base, dbname=company_db, code="T1"))
    assert "Opgeslagen" in r.text
    # niet bereikbaar: geweigerd (vroeger stilletjes opgeslagen)
    r = post(client, url, dict(base, dbname="bestaat_niet", code="T1"))
    assert r.status_code == 400 and "Geen verbinding met de nieuwe database" in r.text
    # bereikbaar: eerst bevestigen, met de bedrijfsnaam uit beide databases
    r = post(client, url, dict(base, dbname=other_db, code="T1"))
    assert r.status_code == 200 and "andere database" in r.text
    assert "Testbedrijf BV" in r.text and "Ander BV" in r.text and 'name="confirm" value="1"' in r.text
    assert db.execute("SELECT dbname FROM company WHERE id = %s", (company["id"],)).fetchone()["dbname"] == company_db
    r = post(client, url, dict(base, dbname=other_db, code="T1", confirm="1"))
    assert "Opgeslagen" in r.text
    row = db.execute("SELECT * FROM company WHERE id = %s", (company["id"],)).fetchone()
    assert row["dbname"] == other_db and row["modules"] == ["gl"]
    d = detail(db, "company_updated")
    assert d["old_dbname"] == company_db and d["dbname"] == other_db and "old_name" not in d

    # een tweede administratie mag niet naar dezelfde database wijzen
    r = post(client, "/systeem/administraties/nieuw", {"name": "Kopie", "dbname": company_db, "active": "1"})
    kopie = db.execute("SELECT id FROM company WHERE name = 'Kopie'").fetchone()
    r = post(client, f"/systeem/administraties/{kopie['id']}",
             {"name": "Kopie", "dbname": other_db, "active": "1", "confirm": "1"})
    assert r.status_code == 400 and "hoort al bij de administratie Testbedrijf" in r.text
