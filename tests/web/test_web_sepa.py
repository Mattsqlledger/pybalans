"""Kas en bank > Betaalbestand maken (SEPA, IM9 A1 / IM10 A, SP1–SP13) in de webapp."""

import re
from datetime import date, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from bankfiles import make_iban
from sqlledger.sepa import next_workday, validate
from test_web_arap import page_fields
from webhelpers import csrf, login, make_user, nohelp

URL = "/betalingen/betaalbestand"
OWN = make_iban("NL", "INGB0001234567")


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("UPDATE ap SET exchangerate = 1 WHERE exchangerate IS NULL")
        conn.execute("UPDATE ap SET paid = 0 WHERE paid IS NULL")
        yield conn


@pytest.fixture
def ibans(cdb):
    cdb.execute("INSERT INTO bank (id, iban, bic) VALUES (10044, %s, 'INGBNL2A')", (OWN,))
    cdb.execute("INSERT INTO bank (id, iban) VALUES (901, %s), (905, %s)",
                (make_iban("NL", "TRIO0198765432"), make_iban("NL", "INGB0007777777")))
    return cdb


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def post(client, page, **data):
    return client.post(URL, data={**page_fields(page), "csrf": csrf(client, page), **data}, follow_redirects=False)


def checked(page):
    return sorted(int(i) for i in re.findall(r'name="sel_(\d+)" value="1" checked', page))


def test_menu_and_rights(booker, client, db, company, cdb):
    assert f'href="{URL}"' in booker.get("/").text
    assert f'href="{URL}"' in booker.get("/betalingen").text
    db.execute("UPDATE company SET modules = array_remove(modules, 'cash') WHERE id = %s", (company["id"],))
    assert booker.get(URL).status_code == 403
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "jan")])
    login(client, "kijk")
    assert client.get(URL).status_code == 403


def test_no_account_with_iban(booker):
    page = nohelp(booker.get(URL).text)
    assert "Geen betaalrekening met een IBAN" in page
    assert re.search(r'<button name="action" value="create" disabled>', page)


def test_create_and_download(booker, ibans, db):
    page = booker.get(URL).text
    execdate = next_workday(date.today())
    assert f'name="execdate" value="{execdate.isoformat()}"' in page
    assert checked(page) == [951, 4001, 4002, 4003]               # alles is al vervallen
    assert "NL20INGB0001234567" in page
    r = post(booker, page, action="create", **{"bedrag_4001": "1000,00"})
    assert r.status_code == 303, re.findall(r'class="error[^"]*"[^>]*>([^<]*)', r.text)
    page = booker.get(r.headers["location"]).text
    assert "Betaalbestand gemaakt: 4 overboekingen, totaal 1.486,50" in page
    link = re.search(r'href="(/betalingen/betaalbestand/(\d+)\.xml)" download', page)
    xml = booker.get(link.group(1))
    assert xml.headers["content-type"].startswith("application/xml")
    assert f'betaalbestand-{execdate.isoformat()}-{link.group(2)}.xml' in xml.headers["content-disposition"]
    validate("03", xml.content)
    assert b"<InstdAmt Ccy=\"EUR\">1000.00</InstdAmt>" in xml.content
    # na het maken: alles staat uit met de datum erbij, en het bestand staat in de lijst
    assert checked(page) == [] and page.count("In betaalbestand op") == 4
    assert "Eerder gemaakte betaalbestanden" in page
    log = db.execute("SELECT detail FROM audit_log WHERE action = 'sepa_export'").fetchone()["detail"]
    assert log["count"] == 4 and log["total"] == "1486.50" and log["version"] == "pain.001.001.03"
    assert booker.get(f"{URL}/999999.xml").status_code == 404


def test_version_09_and_filters(booker, ibans):
    page = booker.get(f"{URL}?leverancier=molen&versie=09").text
    assert checked(page) == [4001, 4002]
    r = post(booker, page, action="create")
    assert r.status_code == 303
    page = booker.get(r.headers["location"]).text
    xml = booker.get(re.search(r'href="(/betalingen/betaalbestand/\d+\.xml)"', page).group(1)).content
    validate("09", xml)
    assert b"pain.001.001.09" in xml


def test_refusals_leave_nothing(booker, ibans):
    page = booker.get(URL).text
    r = post(booker, page, action="create", bedrag_4003="500,00")
    assert r.status_code == 400 and "hoger dan wat openstaat" in r.text
    r = post(booker, page, action="create", due_4003="100.00")
    assert r.status_code == 409 and "intussen veranderd" in r.text
    later = (next_workday(date.today()) + timedelta(days=7)).isoformat()
    r = post(booker, page, action="create", execdate=later)
    assert r.status_code == 400 and "De uitvoerdatum is gewijzigd" in r.text
    data = {k: v for k, v in page_fields(page).items() if not k.startswith("sel_")}
    r = booker.post(URL, data={**data, "csrf": csrf(booker, page), "action": "create"}, follow_redirects=False)
    assert r.status_code == 400 and "Kies minstens één factuur" in r.text
    assert ibans.execute("SELECT count(*) AS n FROM x_sepa_export").fetchone()["n"] == 0


def test_update_keeps_choices_and_vendor_without_iban(booker, ibans):
    ibans.execute("DELETE FROM bank WHERE id = 905")
    page = booker.get(URL).text
    assert checked(page) == [951, 4003]
    assert re.search(r'name="sel_4002" value="1" disabled', page) and "Leverancier heeft geen IBAN." in page
    data = page_fields(page)
    data.pop("sel_951")
    data["bedrag_4003"] = "100,00"
    r = booker.post(URL, data={**data, "csrf": csrf(booker, page), "action": "update"})
    assert r.status_code == 200
    assert checked(r.text) == [4003] and 'name="bedrag_4003" value="100,00"' in r.text
    assert "1 aangevinkt" in r.text, re.findall(r"<tfoot>.*?</tfoot>", r.text, re.S)
