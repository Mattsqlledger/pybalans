"""Uren > Tijdkaarten (UREN_ANALYSE.md U-1)."""

import re

import psycopg
import pytest

from conftest import DSN
from webhelpers import csrf, login, make_user, nohelp


def choose(client, company):
    page = client.get("/administratie").text
    client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company["id"]})


def cconn(company):
    return psycopg.connect(f"{DSN} dbname={company['dbname']}", autocommit=True)


@pytest.fixture
def setup(company):
    with cconn(company) as c:
        c.execute("UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'")
        c.execute("UPDATE employee SET startdate = '2026-01-01'")
        c.execute("""INSERT INTO parts (id, partnumber, description, unit, sellprice, income_accno_id, expense_accno_id)
                     VALUES (1200, 'UUR', 'Advies per uur', 'uur', 85, (SELECT id FROM chart WHERE accno = '8520'),
                             (SELECT id FROM chart WHERE accno = '8100'))""")
        c.execute("INSERT INTO project (id, projectnumber, description) VALUES (1300, 'P-UREN', 'Verbouwing')")
        emp = c.execute("SELECT id FROM employee WHERE name = 'Jan Jansen'").fetchone()[0]
    return {"employee": emp}


@pytest.fixture
def bookkeeper(client, db, company):
    secret = make_user(db, "boek", totp=True, memberships=[(company["id"], "bookkeeper", "admin")])
    login(client, "boek", secret)
    choose(client, company)
    return client


def post(client, url_page, data):
    page = client.get(url_page).text
    return client.post("/uren/tijdkaart", data={"csrf": csrf(client, page), **data}, follow_redirects=True)


def test_new_card_with_times_then_edit_and_delete(bookkeeper, company, setup):
    page = bookkeeper.get("/uren/tijdkaart/nieuw")
    assert page.status_code == 200 and "P-UREN" in page.text and "Advies per uur" in page.text
    base = {"transdate": "2026-03-02", "project_id": "1300", "employee_id": str(setup["employee"]), "parts_id": "1200",
            "checkedin": "08:30", "checkedout": "12:00", "noncharge": "0,5", "description": "", "notes": "dag 1"}
    # bijwerken: uren uit de tijden, prijs en omschrijving van de dienst
    r = post(bookkeeper, "/uren/tijdkaart/nieuw", {**base, "action": "update"})
    assert 'name="qty" value="3,00"' in r.text and 'value="Advies per uur"' in r.text and 'value="85,00"' in r.text
    r = post(bookkeeper, "/uren/tijdkaart/nieuw", {**base, "action": "save"})
    assert "Tijdkaart opgeslagen." in r.text
    card = int(re.search(r"/uren/tijdkaart/(\d+)", str(r.url)).group(1))
    with cconn(company) as c:
        qty, price, desc = c.execute("SELECT qty, sellprice, description FROM jcitems WHERE id = %s", (card,)).fetchone()
    assert (qty, price, desc) == (3, 85, "Advies per uur")
    # wijzigen: eigen aantal
    edit = bookkeeper.get(f"/uren/tijdkaart/{card}").text
    old = dict(re.findall(r'name="(old_\w+)" value="([^"]*)"', edit))
    r = post(bookkeeper, f"/uren/tijdkaart/{card}", {**base, **old, "id": str(card), "qty": "2,75", "action": "save"})
    with cconn(company) as c:
        assert c.execute("SELECT qty FROM jcitems WHERE id = %s", (card,)).fetchone()[0] == 2.75
    # lijst en totaal
    lst = bookkeeper.get("/uren/tijdkaarten?project=1300").text
    assert f"/uren/tijdkaart/{card}" in lst and "2,75" in lst
    # verwijderen met bevestiging
    r = post(bookkeeper, f"/uren/tijdkaart/{card}", {"id": str(card), "action": "delete"})
    assert "Deze tijdkaart verwijderen?" in r.text
    r = post(bookkeeper, f"/uren/tijdkaart/{card}", {"id": str(card), "action": "delete_confirmed"})
    assert "Tijdkaart verwijderd." in r.text


def test_readonly_sees_but_cannot_edit(client, db, company, setup):
    secret = make_user(db, "kijk", totp=True, memberships=[(company["id"], "readonly", "admin")])
    login(client, "kijk", secret)
    choose(client, company)
    assert client.get("/uren/tijdkaarten").status_code == 200
    assert client.get("/uren/tijdkaart/nieuw").status_code == 403


def test_menu(bookkeeper):
    page = bookkeeper.get("/").text
    assert 'href="/uren/tijdkaarten"' in page and 'href="/uren/tijdkaart/nieuw"' in page


def test_sales_orders_from_hours(bookkeeper, company, setup):
    with cconn(company) as c:
        cust = c.execute("SELECT min(id) FROM customer").fetchone()[0]
        c.execute("UPDATE project SET customer_id = %s WHERE id = 1300", (cust,))
        c.execute("INSERT INTO project (id, projectnumber, description) VALUES (1301, 'P-ZONDER', 'Zonder klant')")
        for pid, qty, day in ((1300, 3, "2026-03-02"), (1300, 2, "2026-03-03"), (1301, 1, "2026-03-04")):
            c.execute("""INSERT INTO jcitems (project_id, parts_id, description, qty, allocated, sellprice, fxsellprice,
                                              checkedin, checkedout, employee_id)
                         VALUES (%s, 1200, 'Advies', %s, 0, 85, 85, %s, %s, %s)""",
                      (pid, qty, day, day, setup["employee"]))
    page = bookkeeper.get("/uren/verkooporders?samenvatten=1")
    assert page.status_code == 200 and page.text.count('name="row"') == 2 and 'name="customer_2"' in page.text
    # project zonder klant en geen klant gekozen: niets gemaakt
    r = bookkeeper.post("/uren/verkooporders", data={"csrf": csrf(bookkeeper, page.text), "samenvatten": "1",
                                                     "row": ["1", "2"], "action": "generate"})
    assert r.status_code == 400 and "Kies een klant" in r.text
    r = bookkeeper.post("/uren/verkooporders", data={"csrf": csrf(bookkeeper, page.text), "samenvatten": "1",
                                                     "row": ["1", "2"], "customer_2": str(cust), "action": "generate"})
    assert r.status_code == 200 and "Verkooporders gemaakt:" in r.text and "/verkooporder/" in r.text
    with cconn(company) as c:
        assert c.execute("SELECT count(*) FROM oe").fetchone()[0] == 1          # één klant = één order
        assert c.execute("SELECT sum(qty) FROM orderitems").fetchone()[0] == 6
        assert c.execute("SELECT count(*) FROM jcitems WHERE allocated = qty").fetchone()[0] == 3
    assert "Geen open uren gevonden." in bookkeeper.get("/uren/verkooporders").text


def test_assignments_and_month_sheet_for_hours_role(client, db, company, setup):
    # boekhouder wijst toe
    secret = make_user(db, "boek2", totp=True, memberships=[(company["id"], "bookkeeper", "admin")])
    login(client, "boek2", secret)
    choose(client, company)
    page = client.get("/uren/toewijzingen?nieuw=1").text
    r = client.post("/uren/toewijzingen", data={"csrf": csrf(client, page), "employee_id": str(setup["employee"]),
                                                "project_id": "1300", "parts_id": "1200", "validfrom": "2026-03-01",
                                                "validto": "", "sellprice": "", "projectleader_id": "", "notes": ""},
                    follow_redirects=True)
    assert "Toewijzing opgeslagen." in r.text and "P-UREN" in r.text
    client.post("/uitloggen", data={"csrf": csrf(client, client.get("/").text)})
    # urenschrijver jan (medewerker Jan Jansen) ziet alleen zijn maandstaat
    with cconn(company) as c:
        c.execute("UPDATE employee SET login = 'jan' WHERE id = %s", (setup["employee"],))
    secret = make_user(db, "jan", totp=True, memberships=[(company["id"], "hours", "jan")])
    login(client, "jan", secret)
    choose(client, company)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303) and r.headers["location"].endswith("/uren/mijn")
    assert client.get("/uren/tijdkaarten", follow_redirects=False).status_code in (302, 303)
    assert client.post("/uren/tijdkaart", data={"csrf": "x"}).status_code in (400, 403)
    sheet = client.get("/uren/mijn?maand=2026-03").text
    assert "P-UREN" in sheet and 'name="h_1300_1200_2"' in sheet
    menu = client.get("/uren/mijn?maand=2026-03").text
    assert 'href="/uren/tijdkaarten"' not in menu and 'href="/verkoop/nieuw"' not in menu
    r = client.post("/uren/mijn", data={"csrf": csrf(client, sheet), "maand": "2026-03", "h_1300_1200_2": "8",
                                        "h_1300_1200_3": "7,5", "medewerker": "999"})
    assert r.status_code == 200 and "Maandstaat opgeslagen (2 wijzigingen)." in r.text
    with cconn(company) as c:
        rows = c.execute("SELECT employee_id, qty FROM jcitems ORDER BY checkedin").fetchall()
    assert rows == [(setup["employee"], 8), (setup["employee"], 7.5)]          # medewerker uit de login, niet het formulier


def test_timesheet_pdf(bookkeeper, company, setup):
    with cconn(company) as c:
        c.execute("UPDATE project SET customer_id = (SELECT min(id) FROM customer) WHERE id = 1300")
        c.execute("""INSERT INTO jcitems (project_id, parts_id, description, qty, allocated, sellprice, fxsellprice,
                                          checkedin, checkedout, employee_id, notes)
                     VALUES (1300, 1200, 'Ontwerp', 7.5, 0, 85, 85, '2026-03-02', '2026-03-02', %s, 'schetsen')""",
                  (setup["employee"],))
    r = bookkeeper.get(f"/uren/urenstaat.pdf?maand=2026-03&medewerker={setup['employee']}&project=1300")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    assert "urenstaat-Jan_Jansen-2026-03.pdf" in r.headers["content-disposition"]
    # de inhoud via dezelfde renderer als HTML
    from sqlledger_web.documents import DocumentRenderer
    html = DocumentRenderer("/tmp", None, "nl").render_html("timesheet", {
        "employee": "Jan Jansen", "period": "03-2026", "projectnumber": "P-UREN", "total": "7,50", "name": "Klant",
        "rows": [{"transdate": "02-03-2026", "partnumber": "UUR", "description": "Ontwerp", "notes": "", "qty": "7,50"}]})
    assert "Urenstaat" in html and "Voor akkoord" in html and "Handtekening" in html and "7,50" in html
    html_en = DocumentRenderer("/tmp", None, "en").render_html("timesheet", {"employee": "Jan", "period": "03-2026",
                                                                            "rows": [], "total": "0"})
    assert "Timesheet" in html_en and "Signature" in html_en


# ------------------------------------------------------------------ v1.1 interne uren (UREN_ANALYSE.md IN1–IN6)

def test_internal_project_rates_sheet_orders_and_report(bookkeeper, company, setup):
    page = bookkeeper.get("/beheer/projecten/nieuw").text
    assert "Klantproject: uren factureren" in page and "Intern project: uren tegen een intern tarief" in page
    # nieuw project zonder soort: geweigerd; intern zonder kostenplaats: geweigerd
    base = {"csrf": csrf(bookkeeper, page), "key": "", "projectnumber": "INT-9", "description": "Kantoortijd",
            "startdate": "", "enddate": "", "customer_id": "", "department_id": "", "new_department": "",
            "action": "save"}
    r = bookkeeper.post("/beheer/projecten", data=base)
    assert r.status_code == 400 and "Kies het soort project." in r.text
    r = bookkeeper.post("/beheer/projecten", data={**base, "kind": "internal"})
    assert r.status_code == 400 and "Kies een kostenplaats bij een intern project." in r.text
    # intern met een nieuwe kostenplaats
    r = bookkeeper.post("/beheer/projecten", data={**base, "kind": "internal", "new_department": "Overhead"},
                        follow_redirects=True)
    assert r.status_code == 200 and "Intern project" in r.text and "Overhead" in r.text
    with cconn(company) as c:
        pid, kind, role = c.execute("""SELECT p.id, x.kind, d.role FROM project p JOIN x_project x ON x.project_id = p.id
                                       JOIN department d ON d.id = x.department_id
                                       WHERE p.projectnumber = 'INT-9'""").fetchone()
        assert (kind, role.strip()) == ("internal", "C")
        c.execute("UPDATE project SET customer_id = (SELECT min(id) FROM customer) WHERE id = 1300")
        c.execute("UPDATE parts SET lastcost = 30 WHERE id = 1200")
    edit = bookkeeper.get(f"/beheer/projecten/{pid}").text
    assert 'value="internal" checked' in edit and "selected>Overhead" in edit
    # intern tarief van de medewerker
    page = bookkeeper.get("/uren/tarieven").text
    r = bookkeeper.post("/uren/tarieven", data={"csrf": csrf(bookkeeper, page), "employee_id": str(setup["employee"]),
                                                "validfrom": "2026-01-01", "rate": "45,50", "notes": "",
                                                "action": "save"}, follow_redirects=True)
    assert "Intern tarief opgeslagen." in r.text and "45,50" in r.text and "huidig" in r.text
    # toewijzingen + Mijn uren-achtige maandstaat: label intern
    for project in ("1300", str(pid)):
        page = bookkeeper.get("/uren/toewijzingen?nieuw=1").text
        assert "(intern)" in page
        bookkeeper.post("/uren/toewijzingen", data={
            "csrf": csrf(bookkeeper, page), "employee_id": str(setup["employee"]), "project_id": project,
            "parts_id": "1200", "validfrom": "2026-03-01", "validto": "", "sellprice": "", "projectleader_id": "",
            "notes": "", "internalrate": "60" if project == "1300" else ""})
    assert "60,00" in bookkeeper.get("/uren/toewijzingen").text
    sheet = bookkeeper.get(f"/uren/maandstaat?maand=2026-03&medewerker={setup['employee']}").text
    assert sheet.count('class="tag"') == 1 and "INT-9" in sheet
    r = bookkeeper.post("/uren/maandstaat", data={"csrf": csrf(bookkeeper, sheet), "maand": "2026-03",
                                                  "medewerker": str(setup["employee"]), "h_1300_1200_2": "3",
                                                  f"h_{pid}_1200_2": "2"})
    assert "2 wijzigingen" in r.text
    with cconn(company) as c:
        costs = dict(c.execute("""SELECT j.project_id, x.costprice FROM jcitems j
                                  JOIN x_jcitem_cost x ON x.jcitem_id = j.id""").fetchall())
        card = c.execute("SELECT id FROM jcitems WHERE project_id = %s", (pid,)).fetchone()[0]
    assert costs == {1300: 60, pid: 45.5}                     # toewijzing > medewerker
    form = bookkeeper.get(f"/uren/tijdkaart/{card}").text
    assert "Intern tarief" in form and "45,50" in form
    assert '<span class="tag">intern</span>' in bookkeeper.get("/uren/tijdkaarten").text
    # verkooporders uit uren: intern project staat er niet in, ook niet bij posten
    orders = bookkeeper.get("/uren/verkooporders")
    assert orders.text.count('name="row"') == 1 and "INT-9" not in orders.text
    r = bookkeeper.post("/uren/verkooporders", data={"csrf": csrf(bookkeeper, orders.text), "row": ["1", "2"],
                                                     "action": "generate"})
    assert r.status_code == 200 and "Verkooporders gemaakt:" in r.text
    with cconn(company) as c:
        assert c.execute("SELECT count(*) FROM jcitems WHERE allocated > 0").fetchone()[0] == 1
    # rapport Uren per project
    menu = bookkeeper.get("/").text
    assert 'href="/rapporten/uren-per-project"' in menu and 'href="/uren/tarieven"' in menu
    rep = bookkeeper.get("/rapporten/uren-per-project?run=1&fromdate=2026-03-01&todate=2026-03-31")
    assert rep.status_code == 200
    text = rep.text
    assert "INT-9" in text and "Overhead" in text and "Kostwaarde" in text
    assert "180,00" in text and "255,00" in text and "75,00" in text      # klant: kost 3×60, verkoop 3×85, marge
    assert "91,00" in text                                                # intern: 2×45,50
    assert "/uren/tijdkaarten?project=1300" in text
    only = bookkeeper.get("/rapporten/uren-per-project?run=1&kind=internal&grouping=employee").text
    assert ">INT-9</a>" in only and ">P-UREN</a>" not in only and "Jan Jansen" in only and "Totaal project" in only
    for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
        r = bookkeeper.get(f"/rapporten/uren-per-project?run=1&export={fmt}")
        assert r.status_code == 200 and ctype in r.headers["content-type"], fmt
    # tarief verwijderen raakt de vastgelegde tijdkaarten niet
    page = bookkeeper.get("/uren/tarieven").text
    rid = re.search(r'name="id" value="(\d+)"', page).group(1)
    r = bookkeeper.post("/uren/tarieven", data={"csrf": csrf(bookkeeper, page), "id": rid, "action": "delete"},
                        follow_redirects=True)
    assert "Intern tarief verwijderd." in r.text and "Nog geen interne tarieven." in r.text
    with cconn(company) as c:
        assert c.execute("SELECT costprice FROM x_jcitem_cost WHERE jcitem_id = %s", (card,)).fetchone()[0] == 45.5


def test_rates_readonly_and_hours_role(client, db, company, setup):
    secret = make_user(db, "kijk2", totp=True, memberships=[(company["id"], "readonly", "admin")])
    login(client, "kijk2", secret)
    choose(client, company)
    page = client.get("/uren/tarieven")
    assert page.status_code == 200 and 'name="rate"' not in page.text
    assert client.post("/uren/tarieven", data={"csrf": csrf(client, page.text), "action": "save"}).status_code == 403
    assert client.get("/rapporten/uren-per-project").status_code == 200


def test_unclassified_project_help(bookkeeper, company, setup):
    """Keuze 2 (AKKOORD met helptekst): bij een niet-ingedeeld project uitleg op scherm, lijst en rapport."""
    edit = bookkeeper.get("/beheer/projecten/1300").text
    assert "Dit project is nog niet ingedeeld" in edit
    lst = bookkeeper.get("/beheer/projecten").text
    assert "niet ingedeeld" in lst and "open het project en kies Klantproject of Intern project" in lst
    with cconn(company) as c:
        c.execute("""INSERT INTO jcitems (project_id, parts_id, qty, allocated, sellprice, fxsellprice, checkedin,
                                          checkedout, employee_id)
                     VALUES (1300, 1200, 2, 0, 85, 85, '2026-03-02', '2026-03-02', %s)""", (setup["employee"],))
    rep = bookkeeper.get("/rapporten/uren-per-project?run=1&fromdate=2026-03-01&todate=2026-03-31").text
    assert "Niet ingedeelde projecten (uit SQL-Ledger" in rep
    # na indelen: geen uitleg meer
    with cconn(company) as c:
        c.execute("UPDATE project SET customer_id = (SELECT min(id) FROM customer) WHERE id = 1300")
    assert "Dit project is nog niet ingedeeld" not in bookkeeper.get("/beheer/projecten/1300").text
    assert "Niet ingedeelde projecten" not in nohelp(bookkeeper.get(
        "/rapporten/uren-per-project?run=1&fromdate=2026-03-01&todate=2026-03-31").text)
