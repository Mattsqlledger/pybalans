"""Systeem > Administraties > Nieuwe administratie (REKENINGSCHEMA_ANALYSE.md, RS1)."""

import uuid

import pytest

from sqlledger_web import appdb

from webhelpers import csrf, login, make_user, post


@pytest.fixture
def newdb(pg_admin):
    """Naam voor de nieuwe database; na de toets weer weg."""
    name = f"wn_{uuid.uuid4().hex[:10]}"
    yield name
    pg_admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _form(newdb, **kw):
    data = {"name": "Bakkerij Jansen", "code": "BJ1", "dbname": newdb, "chart": "leeg-nl", "language": "",
            "currency": "", "yearend": "12", "mod_gl": "1", "mod_ar": "1", "mod_ap": "1", "add_me": "1"}
    data.update(kw)
    return data


def test_create_company_and_open_it(client, db, newdb, pg_admin):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    page = client.get("/systeem/administraties").text
    assert 'href="/systeem/administraties/aanmaken"' in page and 'href="/systeem/administraties/nieuw"' in page
    form = client.get("/systeem/administraties/aanmaken").text
    assert '<optgroup label="Zwitserland">' in form and 'value="nl-eenmanszaak" selected' in form

    r = post(client, "/systeem/administraties/aanmaken", _form(newdb))
    assert r.status_code == 200 and "De administratie is aangemaakt" in r.text
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (newdb,)).fetchone()
    assert (comp["name"], comp["code"], comp["modules"], comp["doc_language"], comp["vat_return"]) == \
        ("Bakkerij Jansen", "BJ1", ["ar", "ap", "gl"], "nl", True)
    m = db.execute("SELECT m.* FROM membership m JOIN app_user u ON u.id = m.user_id WHERE u.login = 'anna'").fetchone()
    assert (m["company_id"], m["role"], m["employee_login"]) == (comp["id"], "admin", "anna")
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'company_made'").fetchone()
    assert log["detail"]["chart"] == "leeg-nl" and log["detail"]["dbname"] == newdb

    # openen: dashboard, rekeningschema en btw-rubrieken staan klaar
    r = client.post("/administratie", data={"csrf": csrf(client, client.get("/administratie").text),
                                            "company_id": comp["id"]})
    assert r.status_code == 200 and r.url.path == "/"
    r = client.get("/beheer/rekeningschema")
    assert "Te betalen btw hoog" in r.text and "Debiteuren" in r.text

    # dezelfde naam of database nog eens: geweigerd, niets aangeraakt
    r = post(client, "/systeem/administraties/aanmaken", _form(newdb, code="BJ2"), page="/systeem/administraties/aanmaken")
    assert r.status_code == 400 and "al een administratie met deze naam" in r.text
    r = post(client, "/systeem/administraties/aanmaken", _form(newdb, name="Andere", code=""),
             page="/systeem/administraties/aanmaken")
    assert r.status_code == 400 and "bestaat al een database" in r.text
    assert db.execute("SELECT count(*) AS n FROM company").fetchone()["n"] == 1


def test_create_english_chart_without_membership(client, db, newdb):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = post(client, "/systeem/administraties/aanmaken",
             _form(newdb, chart="leeg-uk", add_me="", language="en", currency="gbp", yearend="3"))
    assert r.status_code == 200
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (newdb,)).fetchone()
    assert comp["doc_language"] == "en" and comp["vat_return"] is False
    assert db.execute("SELECT count(*) AS n FROM membership").fetchone()["n"] == 0


@pytest.mark.parametrize("kw, message", [
    ({"dbname": "Hoofd_Letters"}, "kleine letters"),
    ({"dbname": "1begint_met_cijfer"}, "kleine letters"),
    ({"name": ""}, "Naam is verplicht"),
    ({"chart": "bestaat-niet"}, "Onbekend rekeningschema"),
    ({"language": "it"}, "niet in deze taal"),
    ({"currency": "EURO"}, "drie letters"),
])
def test_create_validation(client, db, newdb, pg_admin, kw, message):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    r = post(client, "/systeem/administraties/aanmaken", _form(newdb, **kw))
    assert r.status_code == 400 and message in r.text
    assert db.execute("SELECT count(*) AS n FROM company").fetchone()["n"] == 0
    assert not pg_admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (newdb,)).fetchone()


def test_only_sysadmin(client, db, company):
    make_user(db, "jan", memberships=[(company["id"], "bookkeeper")])
    login(client, "jan")
    assert client.get("/systeem/administraties/aanmaken").status_code == 403


def test_create_page_in_english(client, db):
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    db.execute("UPDATE app_user SET language = 'en' WHERE login = 'anna'")
    login(client, "anna", secret)
    r = client.get("/systeem/administraties/aanmaken")
    assert "Create company" in r.text and "Switzerland" in r.text and "Empty chart" in r.text
    assert appdb.MODULES


def test_create_swiss_chart_in_italian(client, db, newdb):
    """Italiaans alleen voor de rekeningnamen: de documenttaal wordt die van het scherm (keuze binnen A)."""
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    form = client.get("/systeem/administraties/aanmaken").text
    assert 'value="ch-kmu"' in form and '<option value="it">Italiaans</option>' in form
    r = post(client, "/systeem/administraties/aanmaken", _form(newdb, chart="ch-kmu", language="it", code="CH1"))
    assert r.status_code == 200 and "De administratie is aangemaakt" in r.text
    comp = db.execute("SELECT * FROM company WHERE dbname = %s", (newdb,)).fetchone()
    assert comp["doc_language"] == "nl" and comp["vat_return"] is False


def test_chart_help_and_currency_select(client, db, newdb):
    """v1.0.1 PB24/PB25: uitleg per schema (wisselt mee via companycreate.js) en Standaardvaluta als keuzelijst
    met als eerste keuze de valuta van het schema."""
    import html
    secret = make_user(db, "anna", sysadmin=True, totp=True)
    login(client, "anna", secret)
    page = html.unescape(client.get("/systeem/administraties/aanmaken").text)
    assert '<span class="muted small" id="chart-help" aria-live="polite" data-chart-help>Volledig schema voor een ' \
           'eenmanszaak of vof (MKB), met RGS-codes.</span>' in page
    assert 'data-help="Het lege schema heeft alleen de rekeningen' in page and 'data-currency="CHF"' in page
    assert '<select name="currency" autocomplete="off">' in page and '<input name="currency"' not in page
    assert ">EUR (volgens het land)</option>" in page and '<option value="CHF">CHF</option>' in page
    assert '<script src="/static/companycreate.js" defer></script>' in page
    r = post(client, "/systeem/administraties/aanmaken", _form(newdb, chart="ch-kmu", currency="", language="de"))
    assert r.status_code == 200
    r = post(client, "/systeem/administraties/aanmaken", _form(newdb + "x", name="Fout", code="", currency="EURO"),
             page="/systeem/administraties/aanmaken")
    assert r.status_code == 400 and '<option value="EURO" selected>' in r.text      # servercontrole blijft
