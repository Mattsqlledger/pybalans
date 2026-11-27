"""Klanten en leveranciers in de webapp (fase 2, K1–K9)."""

import re
from datetime import date, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_arap import page_fields
from webhelpers import csrf, login, make_user

TABLES = ("customer", "vendor", "address", "contact", "bank", "shipto", "customertax", "vendortax",
          "reference", "defaults", "audittrail")


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SELECT setval('addressid', (SELECT max(id) FROM address))")
        conn.execute("SELECT setval('contactid', (SELECT max(id) FROM contact))")
        yield conn


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def dump(cdb):
    return {t: sorted(map(repr, cdb.execute(f"SELECT * FROM {t}").fetchall())) for t in TABLES}


def form_fields(html):
    """Zoals page_fields, maar met keuzerondjes en meerdere vinkjes met dezelfde naam."""
    data = page_fields(html)
    for name in ("typeofcontact", "gender", "status"):
        data.pop(name, None)
    data.pop("tax", None)
    data.pop("ids", None)
    for m in re.finditer(r"<input\b[^>]*>", html):
        tag = m.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if not name or " disabled" in tag or " checked" not in tag:
            continue
        if 'type="radio"' in tag:
            data[name.group(1)] = value.group(1)
        elif name.group(1) in ("tax", "ids"):
            data.setdefault(name.group(1), []).append(value.group(1))
    return data


def step(client, url, data, page):
    return client.post(url, data={**form_fields(page), "csrf": csrf(client, page), **data}, follow_redirects=False)


def ok(r):
    assert r.status_code == 200, re.sub(r"\s+", " ", r.text)[:3000]
    return r.text


def saved_id(r, prefix="klanten"):
    assert r.status_code == 303, re.sub(r"\s+", " ", r.text)[:3000]
    m = re.match(rf"/{prefix}/(\d+)\?ok=ct_saved", r.headers["location"])
    assert m, r.headers["location"]
    return int(m.group(1))


def test_menu_list_and_rights(booker, client, db, company):
    page = ok(booker.get("/klanten"))
    assert 'href="/klanten/nieuw"' in page and 'href="/leveranciers"' in page
    for name in ("Bakkerij Cees", "Kassaklant", "Klant A", "Overseas Inc"):
        assert name in page
    assert page.count('href="/klanten/902"') == 2         # nummer en naam; één rij ondanks twee contacten
    assert "Graanhandel De Molen" in ok(booker.get("/leveranciers"))
    db.execute("UPDATE company SET modules = array_remove(modules, 'ar') WHERE id = %s", (company["id"],))
    assert booker.get("/klanten").status_code == 403
    assert booker.get("/leveranciers").status_code == 200


def test_readonly(client, db, company, cdb):
    make_user(db, "kijk", memberships=[(company["id"], "readonly", "jan")])
    login(client, "kijk")
    page = ok(client.get("/klanten/902"))
    assert " disabled" in page and 'value="save"' not in page and "alleen lezen" in page
    assert 'href="/klanten/nieuw"' not in ok(client.get("/klanten"))
    assert client.get("/klanten/nieuw").status_code == 403
    r = client.post("/klanten", data={"csrf": csrf(client, page), "id": "902", "name": "X", "action": "save"})
    assert r.status_code == 403


def test_add_customer(booker, cdb):
    page = ok(booker.get("/klanten/nieuw"))
    assert f'name="startdate" value="{date.today().isoformat()}"' in page and 'value="M" checked' in page
    r = step(booker, "/klanten", {"name": "Nieuwe Klant", "address1": "Laan 1", "city": "Delft", "firstname": "Ans",
                                  "lastname": "Smit", "email": "ans@example.test", "terms": "14", "discount": "2,5",
                                  "creditlimit": "1.000,00", "tax": ["1810", "1811"], "arap_accno": "1200",
                                  "iban": "NL91 ABNA 0417 1643 00", "action": "save"}, page)
    vid = saved_id(r)
    row = cdb.execute("SELECT * FROM customer WHERE id = %s", (vid,)).fetchone()
    assert (row["name"], row["customernumber"], row["terms"], row["contact"]) == ("Nieuwe Klant", "1", 14, "Ans Smit")
    assert abs(row["discount"] - 0.025) < 1e-6 and row["creditlimit"] == 1000
    assert cdb.execute("SELECT iban FROM bank WHERE id = %s", (vid,)).fetchone()["iban"] == "NL91ABNA0417164300"
    assert {x["chart_id"] for x in cdb.execute("SELECT chart_id FROM customertax WHERE customer_id = %s",
                                               (vid,))} == {10078, 10079}
    page = ok(booker.get(f"/klanten/{vid}?ok=ct_saved"))
    assert "Opgeslagen." in page and 'value="2,5"' in page and 'value="1.000,00"' in page
    assert 'value="delete"' in page                       # ongebruikt: verwijderen mag


def test_unchanged_save_and_change(booker, cdb):
    before = dump(cdb)
    page = ok(booker.get("/klanten/902"))
    assert 'value="Cees"' in page and 'value="Bakker"' in page and 'name="tax" value="1810" checked' in page
    assert 'value="delete"' not in page and 'value="inactive"' in page       # in gebruik
    vid = saved_id(step(booker, "/klanten", {"action": "save"}, page))
    assert vid == 902 and dump(cdb) == before
    page = ok(booker.get("/klanten/902"))
    saved_id(step(booker, "/klanten", {"city": "Leusden", "phone": "033-1", "action": "save"}, page))
    assert cdb.execute("SELECT city FROM address WHERE trans_id = 902").fetchone()["city"] == "Leusden"
    assert cdb.execute("SELECT phone FROM customer WHERE id = 902").fetchone()["phone"] == "033-1"
    assert cdb.execute("SELECT phone FROM contact WHERE id = 10").fetchone()["phone"] == "033-1"
    # fouten: dubbel nummer, ongeldige IBAN, te lange naam
    page = ok(booker.get("/klanten/902"))
    r = step(booker, "/klanten", {"number": "K001", "action": "save"}, page)
    assert r.status_code == 400 and "K001 bestaat al" in r.text
    r = step(booker, "/klanten", {"iban": "NL00TEST", "action": "save"}, page)
    assert r.status_code == 400 and "IBAN" in r.text
    r = step(booker, "/klanten", {"terms": "abc", "action": "save"}, page)
    assert r.status_code == 400 and "Ongeldig aantal dagen" in r.text


def test_new_number_does_not_count_until_saved(booker, cdb):
    cdb.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('vendornumber', 'L041')")
    page = ok(booker.get("/leveranciers/nieuw"))
    page = ok(step(booker, "/leveranciers", {"name": "Groothandel", "action": "new_number"}, page))
    assert 'name="number" value="L042"' in page and "bij opslaan" in page
    assert cdb.execute("SELECT fldvalue FROM defaults WHERE fldname = 'vendornumber'").fetchone()["fldvalue"] == "L041"
    vid = saved_id(step(booker, "/leveranciers", {"action": "save"}, page), "leveranciers")
    assert cdb.execute("SELECT vendornumber FROM vendor WHERE id = %s", (vid,)).fetchone()["vendornumber"] == "L042"
    assert cdb.execute("SELECT fldvalue FROM defaults WHERE fldname = 'vendornumber'").fetchone()["fldvalue"] == "L042"


def test_save_as_new(booker, cdb):
    page = ok(booker.get("/klanten/902"))
    vid = saved_id(step(booker, "/klanten", {"name": "Bakkerij Cees Filiaal", "action": "save_as_new"}, page))
    assert vid != 902
    new = cdb.execute("SELECT * FROM customer WHERE id = %s", (vid,)).fetchone()
    assert new["customernumber"] not in ("K002", None) and new["terms"] == 30
    assert cdb.execute("SELECT name FROM customer WHERE id = 902").fetchone()["name"] == "Bakkerij Cees"
    assert cdb.execute("SELECT count(*) AS n FROM contact WHERE trans_id = %s", (vid,)).fetchone()["n"] == 1


def test_person_switch(booker, cdb):
    page = ok(booker.get("/klanten/nieuw"))
    page = ok(step(booker, "/klanten", {"typeofcontact": "person", "action": "update"}, page))
    assert 'name="name" value=""' in page and "achternaam + voornaam" in page
    vid = saved_id(step(booker, "/klanten", {"firstname": "Jan", "lastname": "Vries", "action": "save"}, page))
    assert cdb.execute("SELECT name FROM customer WHERE id = %s", (vid,)).fetchone()["name"] == "Vries Jan"


def test_inactive_and_delete(booker, cdb):
    page = ok(booker.get("/klanten/904"))
    r = step(booker, "/klanten", {"action": "inactive"}, page)
    assert r.headers["location"] == "/klanten/904?ok=ct_inactive"
    assert cdb.execute("SELECT enddate FROM customer WHERE id = 904").fetchone()["enddate"] == date.today()
    # verwijderen van een relatie in gebruik: ook via een zelfgemaakte POST geweigerd
    r = step(booker, "/klanten", {"action": "delete_confirmed"}, page)
    assert r.status_code == 400 and "inactief" in r.text
    page = ok(booker.get("/klanten/nieuw"))
    vid = saved_id(step(booker, "/klanten", {"name": "Weg ermee", "action": "save"}, page))
    page = ok(booker.get(f"/klanten/{vid}"))
    confirm = ok(step(booker, "/klanten", {"action": "delete"}, page))
    assert "verwijderen?" in confirm
    r = step(booker, "/klanten", {"action": "delete_confirmed"}, confirm)
    assert r.headers["location"] == "/klanten?ok=ct_deleted"
    assert cdb.execute("SELECT count(*) AS n FROM customer WHERE id = %s", (vid,)).fetchone()["n"] == 0


def test_delete_many(booker, cdb):
    ids = []
    for n in ("Een", "Twee"):
        page = ok(booker.get("/klanten/nieuw"))
        ids.append(saved_id(step(booker, "/klanten", {"name": n, "action": "save"}, page)))
    page = ok(booker.get("/klanten?run=1&status=orphaned"))
    assert "Bakkerij" not in page and 'name="ids"' in page
    r = booker.post("/klanten/verwijderen", data={"csrf": csrf(booker, page), "ids": [str(i) for i in ids] + ["902"],
                                                  "back": "/klanten?run=1&status=orphaned"})
    assert "Een" in ok(r)
    r = booker.post("/klanten/verwijderen", data={**form_fields(r.text), "csrf": csrf(booker, r.text),
                                                  "ids": [str(i) for i in ids] + ["902"]}, follow_redirects=False)
    assert r.status_code == 400 and "inactief" in r.text           # 902 in gebruik: niets verwijderd
    assert cdb.execute("SELECT count(*) AS n FROM customer WHERE id = ANY(%s)", (ids,)).fetchone()["n"] == 2
    r = booker.post("/klanten/verwijderen", data={"csrf": csrf(booker, page), "ids": [str(i) for i in ids],
                                                  "confirmed": "1", "back": "/klanten?run=1&status=orphaned"},
                    follow_redirects=False)
    assert r.headers["location"] == "/klanten?run=1&status=orphaned&ok=ct_deleted_many"
    assert cdb.execute("SELECT count(*) AS n FROM customer WHERE id = ANY(%s)", (ids,)).fetchone()["n"] == 0


def test_links_to_bookings_and_payments(booker, cdb):
    cdb.execute("UPDATE customer SET enddate = %s WHERE id = 904", (date.today() - timedelta(days=5),))
    page = ok(booker.get("/klanten/904"))
    assert 'href="/verkoop/nieuw?relatie=904"' in page and 'href="/ontvangst/nieuw?relatie=904"' in page
    m = re.search(r'href="(/rapporten/overzicht-verkoop\?[^"]+)"', page)
    report = ok(booker.get(m.group(1).replace("&amp;", "&")))
    assert "K004 Kassaklant" in report
    invoices = {r["invnumber"] for r in cdb.execute("SELECT invnumber FROM ar")}
    mine = {r["invnumber"] for r in cdb.execute("SELECT invnumber FROM ar WHERE customer_id = 904")}
    assert all(i in report for i in mine) and not any(f">{i}<" in report for i in invoices - mine)
    # nieuwe verkoop met de (inactieve) relatie voorgevuld; einddatum blijft staan
    sale = ok(booker.get("/verkoop/nieuw?relatie=904"))
    assert 'value="K004 Kassaklant"' in sale
    assert cdb.execute("SELECT enddate FROM customer WHERE id = 904").fetchone()["enddate"] is not None
    # het rapportformulier onthoudt de relatie niet als voorkeur
    assert "vc_id" not in ok(booker.get("/rapporten/overzicht-verkoop"))


def test_list_filters_and_export(booker, cdb):
    page = ok(booker.get("/klanten?run=1&city=utrecht&cols=number&cols=name&cols=outstanding&cols=creditlimit"))
    assert "Kassaklant" in page and "Bakkerij" not in page and "Openstaand" in page
    r = booker.get("/klanten?run=1&status=all&cols=number&cols=name&export=csv")
    assert r.status_code == 200 and "K002;Bakkerij Cees" in r.text
    r = booker.get("/leveranciers?run=1&export=xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
