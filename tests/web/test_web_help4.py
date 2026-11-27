"""Kleine verbeteringen uit golf 4 van de helpteksten (HELP_ANALYSE, dev/help_golf4.md V1: F28–F37) en de pagina
Help "Hoe doe ik …?" (HL10, V5/V6)."""

import re
from datetime import date, timedelta

import pytest

from sqlledger.dates import format_date
from sqlledger_web import production

from test_web_ic import add_part, cdb, ok, step  # noqa: F401
from test_web_jc import cconn, choose, setup  # noqa: F401
from test_web_recurring import save, schedule
from webhelpers import csrf, login, make_user, nohelp


@pytest.fixture
def admin(client, db, company, cdb):  # noqa: F811
    secret = make_user(db, "baas", totp=True, memberships=[(company["id"], "admin", "jan")])
    login(client, "baas", secret)
    production.clear()
    return client


def test_f28_production_links_follow_menu(admin):
    html = nohelp(ok(admin.get("/artikelen")))
    assert 'href="/artikelen-assembleren"' not in html and "soort=samengesteld" not in html
    assert "soort=samengesteld" not in nohelp(ok(admin.get("/artikelen/nieuw")))
    pid = add_part(admin)
    from test_web_asm import add_assembly
    add_assembly(admin, [("T-1", "2")])
    production.clear()
    html = nohelp(ok(admin.get("/artikelen")))
    assert 'href="/artikelen-assembleren"' in html and "soort=samengesteld" in html
    assert "soort=samengesteld" in nohelp(ok(admin.get(f"/artikelen/{pid}")))


def test_f29_columns_after_switching_to_lines(admin):
    html = ok(admin.get("/artikelen?run=1&colmode=artikelen&cols=partnumber&cols=description&transactions=sold"))
    head = html[html.index("<thead"):html.index("</thead>")]
    assert ">Datum<" in head and ">Relatie<" in head
    # zelfde soort lijst: de eigen kolomkeuze blijft
    html = ok(admin.get("/artikelen?run=1&colmode=regels&cols=partnumber&transactions=sold"))
    head = html[html.index("<thead"):html.index("</thead>")]
    assert ">Datum<" not in head


def test_f31_stop_schedule_from_list(client, db, company, cdb):  # noqa: F811
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    save(client, 3006, startdate=(date.today() + timedelta(days=30)).isoformat(), howmany="2")
    page = client.get("/terugkerend").text
    assert 'formaction="/terugkerend/3006/stoppen"' in page
    r = client.post("/terugkerend/3006/stoppen", data={"csrf": csrf(client, page), "back": "/terugkerend"},
                    follow_redirects=False)
    assert r.headers["location"] == "/terugkerend?ok=recurring_stopped"
    assert schedule(cdb, 3006)["nextdate"] is None
    assert 'formaction="/terugkerend/3006/stoppen"' not in client.get("/terugkerend").text


def test_f33_f35_f36_hours_screens(client, db, company, setup):  # noqa: F811
    secret = make_user(db, "boek", totp=True, memberships=[(company["id"], "bookkeeper", "admin")])
    login(client, "boek", secret)
    choose(client, company)
    page = client.get("/uren/toewijzingen?nieuw=1").text
    client.post("/uren/toewijzingen", data={"csrf": csrf(client, page), "employee_id": str(setup["employee"]),
                                            "project_id": "1300", "parts_id": "1200", "validfrom": "2026-03-01",
                                            "validto": "", "sellprice": "", "projectleader_id": "", "notes": ""})
    page = client.get("/uren/tarieven").text
    client.post("/uren/tarieven", data={"csrf": csrf(client, page), "employee_id": str(setup["employee"]),
                                        "validfrom": "2026-01-01", "rate": "45", "notes": "", "action": "save"})
    fmt = db.execute("SELECT dateformat FROM app_user WHERE login = 'boek'").fetchone()["dateformat"]
    shown = format_date(date(2026, 3, 1), fmt)
    lst = nohelp(client.get("/uren/toewijzingen").text)
    assert shown in lst and "2026-03-01" not in lst.split("<tbody>")[1]                      # F35
    rates = client.get("/uren/tarieven").text
    assert "data-confirm=" in rates and format_date(date(2026, 1, 1), fmt) in rates           # F33
    client.post("/uitloggen", data={"csrf": csrf(client, rates)})
    secret = make_user(db, "kijk", totp=True, memberships=[(company["id"], "readonly", "admin")])
    login(client, "kijk", secret)
    choose(client, company)
    assert client.get("/uren/verkooporders").status_code == 403                               # F36


def test_f37_yearend_without_gl_and_cash(admin, db, company):
    db.execute("UPDATE company SET modules = array_remove(array_remove(modules, 'gl'), 'cash') WHERE id = %s",
               (company["id"],))
    page = admin.get("/beheer/jaarafsluiting").text
    r = admin.post("/beheer/jaarafsluiting", data={
        "csrf": csrf(admin, page), "todate": "2026-06-30", "accno": "0550", "method": "accrual", "reference": "",
        "description": "", "action": "post"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/beheer/jaarafsluiting?ok=yearend_posted"
    assert "Jaarafsluiting geboekt." in admin.get(r.headers["location"]).text


# ------------------------------------------------------------------ pagina Help (HL10)

def test_help_link_and_page_for_admin(admin):
    page = ok(admin.get("/"))
    assert '<a href="/help">Help</a>' in page
    html = ok(admin.get("/help"))
    assert "Hoe doe ik …?" in html
    assert "Een verkoopfactuur maken" in html and 'href="/verkoopfactuur/nieuw"' in html
    assert "Overstappen uit SQL-Ledger" in html and 'href="/beheer/ingebruikname"' in html
    assert "(beheerder)" not in html                       # de beheerder mag alles in de administratie


def test_help_page_bookkeeper_and_modules(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    html = ok(client.get("/help"))
    assert "(beheerder)" in html and 'href="/beheer/afsluiten"' not in html                  # V5: wie het doet
    assert "Een gebruiker toegang geven" not in html                                         # systeemtaken: beheerder
    db.execute("UPDATE company SET modules = array_remove(modules, 'recurring') WHERE id = %s", (company["id"],))
    html = ok(client.get("/help"))
    assert 'href="/terugkerend"' not in html and "Een boeking elke maand laten terugkomen" not in html


def test_help_page_hours_role(client, db, company):
    make_user(db, "uren", memberships=[(company["id"], "hours", "jan")])
    login(client, "uren")
    html = ok(client.get("/help"))
    assert "Uren schrijven en factureren" in html and 'href="/uren/mijn"' in html
    assert "Een verkoopfactuur maken" not in html


@pytest.mark.parametrize("lang", ["en", "de", "fr"])
def test_help_page_translated(admin, db, lang):
    from sqlledger_web.i18n_de import TEXTS as DE
    from sqlledger_web.i18n_en import EN
    from sqlledger_web.i18n_fr import TEXTS as FR
    cat = {"en": EN, "de": DE, "fr": FR}[lang]
    db.execute("UPDATE app_user SET language = %s WHERE login = 'baas'", (lang,))
    html = ok(admin.get("/help"))
    assert cat["Hoe doe ik …?"] in html and cat["Een verkoopfactuur maken"] in html
    assert re.search(r'<a href="/help">%s</a>' % re.escape(cat["Help"]), html)
