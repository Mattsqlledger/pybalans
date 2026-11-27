"""Artikelen en diensten in de webapp (fase 3, AT1–AT7)."""

import re
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_ct import form_fields
from webhelpers import csrf, login, make_user

TABLES = ("parts", "partstax", "partsvendor", "partscustomer", "makemodel", "defaults", "audittrail")


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        conn.execute("UPDATE chart SET link = link || ':IC_income' WHERE accno = '8520'")
        conn.execute("SELECT setval('invoiceid', (SELECT max(id) FROM invoice))")
        yield conn


@pytest.fixture
def booker(client, db, company, cdb):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


@pytest.fixture
def reader(client, db, company, cdb):
    make_user(db, "lees", memberships=[(company["id"], "readonly", "jan")])
    login(client, "lees")
    return client


def dump(cdb):
    return {t: sorted(map(repr, cdb.execute(f"SELECT * FROM {t}").fetchall())) for t in TABLES}


def fields(html):
    data = form_fields(html)
    for k, v in list(data.items()):          # browser: eerste regeleinde in een textarea telt niet
        if isinstance(v, str) and v.startswith("\n"):
            data[k] = v[1:]
    return data


def step(client, page, data):
    return client.post("/artikelen", data={**fields(page), "csrf": csrf(client, page), **data},
                       follow_redirects=False)


def ok(r):
    assert r.status_code == 200, re.sub(r"\s+", " ", r.text)[:3000]
    return r.text


def saved_id(r):
    assert r.status_code == 303, re.sub(r"\s+", " ", r.text)[:3000]
    m = re.match(r"/artikelen/(\d+)\?ok=ic_saved", r.headers["location"])
    assert m, r.headers["location"]
    return int(m.group(1))


NEW_PART = {"partnumber": "T-1", "description": "Taart", "unit": "st", "sellprice": "12,50", "listprice": "15",
            "lastcost": "6,125", "inventory_accno": "7001", "income_accno": "8520", "expense_accno": "8020",
            "tax": ["1810"], "rop": "4", "bin": "A3", "action": "save"}


def add_part(client, **kw):
    page = ok(client.get("/artikelen/nieuw"))
    return saved_id(step(client, page, {**NEW_PART, **kw}))


def test_menu_and_new_page(booker):
    html = ok(booker.get("/artikelen/nieuw"))
    assert "Artikel toevoegen" in html and 'href="/artikelen"' in html
    assert 'name="inventory_accno"' in html
    # alle btw-rekeningen standaard aangevinkt (ic.pl add)
    assert len(re.findall(r'name="tax" value="18\d\d" checked', html)) == 4
    svc = ok(booker.get("/artikelen/nieuw?soort=dienst"))
    assert "Dienst toevoegen" in svc and 'name="inventory_accno"' not in svc


def test_add_edit_part(booker, cdb):
    pid = add_part(booker)
    row = cdb.execute("SELECT * FROM parts WHERE id = %s", (pid,)).fetchone()
    assert (row["partnumber"], row["description"], row["sellprice"], row["lastcost"], row["rop"], row["bin"]) == \
        ("T-1", "Taart", 12.5, 6.125, 4, "A3")
    assert [r["chart_id"] for r in cdb.execute("SELECT chart_id FROM partstax WHERE parts_id = %s", (pid,))] == \
        [cdb.execute("SELECT id FROM chart WHERE accno = '1810'").fetchone()["id"]]
    html = ok(booker.get(f"/artikelen/{pid}?ok=ic_saved"))
    assert "Opgeslagen." in html and 'value="6,125"' in html and 'value="12,50"' in html
    before = dump(cdb)
    assert saved_id(step(booker, html, {"action": "save"})) == pid
    assert dump(cdb) == before                       # ongewijzigd opslaan schrijft niets
    saved_id(step(booker, html, {"action": "save", "sellprice": "13", "notes": "met room"}))
    row = cdb.execute("SELECT sellprice, notes FROM parts WHERE id = %s", (pid,)).fetchone()
    assert (row["sellprice"], row["notes"]) == (13, "met room")


def test_service(booker, cdb):
    page = ok(booker.get("/artikelen/nieuw?soort=dienst"))
    pid = saved_id(step(booker, page, {"partnumber": "", "description": "Advies", "income_accno": "8520",
                                       "expense_accno": "8100", "action": "save"}))
    row = cdb.execute("SELECT inventory_accno_id, income_accno_id, partnumber FROM parts WHERE id = %s",
                      (pid,)).fetchone()
    assert row["inventory_accno_id"] is None and row["income_accno_id"] and row["partnumber"]
    assert "Dienst wijzigen" in ok(booker.get(f"/artikelen/{pid}"))


def test_errors(booker):
    page = ok(booker.get("/artikelen/nieuw"))
    r = step(booker, page, {**NEW_PART, "description": ""})
    assert r.status_code == 400 and "Vul een omschrijving in." in r.text
    r = step(booker, page, {**NEW_PART, "sellprice": "abc"})
    assert r.status_code == 400 and "Ongeldig getal: abc" in r.text
    r = step(booker, page, {**NEW_PART, "inventory_accno": ""})
    assert r.status_code == 400 and "Kies een voorraadrekening." in r.text


def test_duplicate_warning_and_new_number(booker, cdb):
    add_part(booker)
    page = ok(booker.get("/artikelen/nieuw"))
    html = ok(step(booker, page, {**NEW_PART, "action": "update"}))
    assert "Artikelnummer T-1 bestaat al" in html
    cdb.execute("INSERT INTO defaults (fldname, fldvalue) VALUES ('partnumber', 'P-100')")
    html = ok(step(booker, page, {**NEW_PART, "action": "new_number"}))
    assert 'value="P-101"' in html
    assert cdb.execute("SELECT fldvalue FROM defaults WHERE fldname = 'partnumber'").fetchone()["fldvalue"] == "P-100"
    pid = saved_id(step(booker, html, {"action": "save"}))
    assert cdb.execute("SELECT partnumber FROM parts WHERE id = %s", (pid,)).fetchone()["partnumber"] == "P-101"


def test_save_as_new(booker, cdb):
    pid = add_part(booker)
    html = ok(booker.get(f"/artikelen/{pid}"))
    new = saved_id(step(booker, html, {"action": "save_as_new", "description": "Taart 2"}))
    assert new != pid
    assert cdb.execute("SELECT partnumber FROM parts WHERE id = %s", (new,)).fetchone()["partnumber"] != "T-1"


def test_used_part_locked_and_not_deletable(booker, cdb):
    pid = add_part(booker)
    cdb.execute("INSERT INTO invoice (trans_id, parts_id, description, qty) VALUES (950, %s, 'x', 1)", (pid,))
    html = ok(booker.get(f"/artikelen/{pid}"))
    assert "liggen vast" in html and 'value="delete"' not in html
    assert '<select name="income_accno"' not in html
    r = step(booker, html, {"action": "delete_confirmed"})
    assert r.status_code == 400 and "zet het op vervallen" in r.text
    saved_id(step(booker, html, {"action": "save", "obsolete": "1"}))
    assert cdb.execute("SELECT obsolete FROM parts WHERE id = %s", (pid,)).fetchone()["obsolete"]


def test_delete(booker, cdb):
    pid = add_part(booker)
    html = ok(booker.get(f"/artikelen/{pid}"))
    html = ok(step(booker, html, {"action": "delete"}))
    assert "Artikel T-1 verwijderen?" in html
    r = step(booker, html, {"action": "delete_confirmed"})
    assert r.status_code == 303 and r.headers["location"] == "/artikelen?ok=ic_deleted"
    assert cdb.execute("SELECT count(*) AS n FROM parts WHERE id = %s", (pid,)).fetchone()["n"] == 0


def test_list_filters_and_export(booker, cdb):
    pid = add_part(booker)
    html = ok(booker.get("/artikelen?run=1&status=all&description=taart"))
    assert f'href="/artikelen/{pid}"' in html and "1 gevonden" in html
    html = ok(booker.get("/artikelen?run=1&status=all&kind=service"))
    assert f'href="/artikelen/{pid}"' not in html
    r = booker.get("/artikelen?run=1&status=all&export=csv")
    assert r.status_code == 200 and "T-1" in r.text
    r = booker.get("/artikelen?run=1&status=all&export=xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_list_transactions_and_history(booker, cdb):
    pid = add_part(booker)
    cdb.execute("""INSERT INTO invoice (trans_id, parts_id, description, qty, sellprice, discount)
                   VALUES (950, %s, 'Taart groot', 3, 12.5, 0.1)""", (pid,))
    html = ok(booker.get("/artikelen?run=1&status=all&partnumber=T-1&transactions=sold&open=1&closed=1"))
    assert "Taart groot" in html and 'href="/boekingen/950"' in html
    html = ok(booker.get(f"/artikelen/{pid}/historie"))
    assert "F2026-001" in html and "12,50" in html and "10,00" in html
    assert "Niets gevonden." in ok(booker.get(f"/artikelen/{pid}/historie?kant=inkoop"))


def test_history_price_basis_and_order_links(booker, cdb, db, company):
    """F39: kolomkop met de standaardvaluta; F40: link naar de order als de module aanstaat."""
    pid = add_part(booker)
    cdb.execute("""INSERT INTO oe (id, ordnumber, transdate, customer_id, curr, exchangerate, quotation, amount,
                                   netamount, closed)
                   VALUES (971, 'O-USD', '2026-05-03', 900, 'USD', 0.9, false, 81, 81, false)""")
    cdb.execute("""INSERT INTO orderitems (trans_id, parts_id, description, qty, sellprice, discount)
                   VALUES (971, %s, 'Taart', 1, 100, 0.1)""", (pid,))
    html = ok(booker.get(f"/artikelen/{pid}/historie"))
    assert "Prijs (EUR)" in html and "90,00" in html and 'href="/verkooporder/971"' in html
    lines = ok(booker.get("/artikelen?run=1&status=all&partnumber=T-1&transactions=ordered&open=1&closed=1"))
    assert "Prijs (EUR)" in lines and "90,00" in lines and 'href="/verkooporder/971"' in lines
    mods = [m for m in company["modules"] if m != "oe"]
    db.execute("UPDATE company SET modules = %s WHERE id = %s", (mods, company["id"]))
    html = ok(booker.get(f"/artikelen/{pid}/historie"))
    assert "O-USD" in html and 'href="/verkooporder/971"' not in html


def test_groups(booker, cdb):
    page = ok(booker.get("/artikelen-groepen"))
    r = booker.post("/artikelen-groepen", data={"csrf": csrf(booker, page), "partsgroup": "Gebak", "code": "G"},
                    follow_redirects=False)
    assert r.status_code == 303
    gid = cdb.execute("SELECT id FROM partsgroup WHERE partsgroup = 'Gebak'").fetchone()["id"]
    r = booker.post("/artikelen-groepen", data={"csrf": csrf(booker, page), "partsgroup": "gebak"})
    assert r.status_code == 400 and "bestaat al" in r.text
    pid = add_part(booker, partsgroup_id=str(gid))
    assert cdb.execute("SELECT partsgroup_id FROM parts WHERE id = %s", (pid,)).fetchone()["partsgroup_id"] == gid
    r = booker.post("/artikelen-groepen", data={"csrf": csrf(booker, page), "id": gid, "action": "delete"})
    assert r.status_code == 400 and "in gebruik" in r.text


def test_stock_adjustment(booker, cdb):
    pid = add_part(booker)
    page = ok(booker.get(f"/artikelen/{pid}/voorraad"))
    r = booker.post(f"/artikelen/{pid}/voorraad", data={"csrf": csrf(booker, page), "transdate": "2026-03-01",
                                                       "mode": "new", "amount": "12", "reason": "telling"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = booker.post(f"/artikelen/{pid}/voorraad", data={"csrf": csrf(booker, page), "transdate": "2026-03-02",
                                                       "mode": "diff", "amount": "-2", "reason": "breuk"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert cdb.execute("SELECT onhand FROM parts WHERE id = %s", (pid,)).fetchone()["onhand"] == 10
    html = ok(booker.get(f"/artikelen/{pid}/voorraad"))
    assert "telling" in html and "breuk" in html
    r = booker.post(f"/artikelen/{pid}/voorraad", data={"csrf": csrf(booker, page), "transdate": "2026-03-02",
                                                       "mode": "diff", "amount": "1", "reason": ""})
    assert r.status_code == 400 and "Vul een reden in." in r.text
    assert "<strong>10</strong>" in ok(booker.get(f"/artikelen/{pid}"))


def test_reader_read_only(reader, cdb):
    cdb.execute("""INSERT INTO parts (partnumber, description, inventory_accno_id, income_accno_id, expense_accno_id)
                   SELECT 'R-1', 'Lezen', (SELECT id FROM chart WHERE accno='7001'),
                          (SELECT id FROM chart WHERE accno='8520'), (SELECT id FROM chart WHERE accno='8020')""")
    pid = cdb.execute("SELECT id FROM parts WHERE partnumber = 'R-1'").fetchone()["id"]
    html = ok(reader.get(f"/artikelen/{pid}"))
    assert "alleen lezen" in html and 'value="save"' not in html
    assert reader.get("/artikelen/nieuw").status_code == 403
    assert ok(reader.get("/artikelen"))
