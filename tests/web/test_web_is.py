"""Facturen met artikelen in de webapp (fase 3 stap 2, F1–F9)."""

import re
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_arap import acc, page_fields, step
from webhelpers import csrf, login, make_user


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(autouse=True)
def setup(cdb):
    """Testdata: eenduidige btw-tarieven, artikel met voorraad en een dienst."""
    cdb.execute("DELETE FROM tax WHERE taxnumber IS NULL")
    cdb.execute("SELECT setval('invoiceid', 1000)")
    cdb.execute("""INSERT INTO parts (id, partnumber, description, unit, sellprice, lastcost, listprice,
                                      inventory_accno_id, income_accno_id, expense_accno_id, onhand)
                   VALUES (2001, 'A-1', 'Appels', 'kg', 5, 2, 5, (SELECT id FROM chart WHERE accno = '7001'),
                           (SELECT id FROM chart WHERE accno = '8520'), (SELECT id FROM chart WHERE accno = '8020'), 0),
                          (2002, 'A-2', 'Appelmoes', 'pot', 3.25, 1, 3.25, (SELECT id FROM chart WHERE accno = '7001'),
                           (SELECT id FROM chart WHERE accno = '8520'), (SELECT id FROM chart WHERE accno = '8020'), 0)""")
    cdb.execute("""INSERT INTO partstax (parts_id, chart_id) SELECT p, c.id FROM (VALUES (2001), (2002)) v(p)
                   CROSS JOIN chart c WHERE c.accno IN ('1810', '1800')""")


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


@pytest.fixture
def reader(client, db, company):
    make_user(db, "lees", memberships=[(company["id"], "readonly", "lees")])
    login(client, "lees")
    return client


def start(client, vc_text, prefix="verkoopfactuur", query=""):
    page = client.get(f"/{prefix}/nieuw{query}").text
    r = step(client, f"/{prefix}", {"vc_text": vc_text, "transdate": "2026-07-01", "action": "update"}, page)
    assert r.status_code == 200, r.text[:2000]
    return r.text


def add_item(client, page, text, prefix="verkoopfactuur", **fields):
    """Artikel in de lege regel typen en verversen (zoals het scherm), daarna velden van die regel invullen."""
    n = int(re.search(r'name="rowcount" value="(\d+)"', page).group(1))
    r = step(client, f"/{prefix}", {f"item_{n}": text, "action": "update"}, page)
    assert r.status_code == 200, r.text[:2000]
    page = r.text
    if fields:
        data = {f"{k}_{n}": v for k, v in fields.items()}
        r = step(client, f"/{prefix}", {**data, "action": "update"}, page)
        page = r.text
    return page


def posted_id(r, prefix="verkoopfactuur"):
    assert r.status_code == 303, re.sub(r"\s+", " ", r.text)[:3000]
    m = re.fullmatch(rf"/{prefix}/(\d+)\?ok=invoice_posted", r.headers["location"])
    assert m, r.headers["location"]
    return int(m.group(1))


def test_menu_and_new_form(booker):
    page = booker.get("/verkoopfactuur/nieuw").text
    assert "Verkoopfactuur toevoegen" in page and 'name="item_1"' in page
    assert 'href="/verkoopfactuur/nieuw"' in page and 'href="/inkoopfactuur/nieuw"' in page
    assert "Creditnota verkoop (artikelen) toevoegen" in booker.get("/verkoopfactuur/nieuw?creditnota=1").text
    assert "Inkoopfactuur toevoegen" in booker.get("/inkoopfactuur/nieuw").text
    r = booker.get("/verkoopfactuur/artikelen", params={"text": "appel"})
    assert 'value="A-1 Appels"' in r.text and 'value="A-2 Appelmoes"' in r.text


def test_purchase_and_sale_with_cost(booker, cdb):
    page = start(booker, "Graanhandel", prefix="inkoopfactuur")
    page = add_item(booker, page, "A-1", prefix="inkoopfactuur", qty="10", sellprice="2,00")
    assert 'name="sellprice_1" value="2,00"' in page and 'name="description_1" value="Appels"' in page
    r = step(booker, "/inkoopfactuur", {"action": "post"}, page)
    pid = posted_id(r, "inkoopfactuur")
    assert acc(cdb, pid) == [("1600", Decimal("24.20")), ("1800", Decimal("-4.20")), ("7001", Decimal("-20.00"))]

    page = start(booker, "K001")
    page = add_item(booker, page, "Appels", qty="4")
    assert 'name="sellprice_1" value="5,00"' in page                     # verkoopprijs voorgesteld
    r = step(booker, "/verkoopfactuur", {"sellprice_1": "5,10", "action": "update"}, page)
    assert 'name="sellprice_1" value="5,10"' in r.text                   # eigen prijs blijft na verversen
    page = step(booker, "/verkoopfactuur", {"sellprice_1": "5,00", "action": "update"}, r.text).text
    assert "24,20" in page                                               # totaal door de server
    r = step(booker, "/verkoopfactuur", {"action": "post"}, page)
    sid = posted_id(r)
    rows = acc(cdb, sid)
    assert ("8020", Decimal("-8.00")) in rows and ("7001", Decimal("8.00")) in rows    # kostprijs FIFO
    assert ("8520", Decimal("20.00")) in rows and ("1200", Decimal("-24.20")) in rows
    assert cdb.execute("SELECT onhand FROM parts WHERE id = 2001").fetchone()["onhand"] == 6

    # openen, aantal wijzigen, bevestigen (V11)
    page = booker.get(f"/verkoopfactuur/{sid}").text
    assert "Verkoopfactuur wijzigen" in page and 'name="qty_1" value="4"' in page
    r = step(booker, "/verkoopfactuur", {"qty_1": "5", "action": "post"}, page)
    assert "U wijzigt een bestaande factuur" in r.text
    r = step(booker, "/verkoopfactuur", {"action": "post_confirmed"}, r.text)
    assert posted_id(r) == sid
    assert ("8020", Decimal("-10.00")) in acc(cdb, sid)
    assert cdb.execute("SELECT count(*) AS n FROM x_cogs_allocation").fetchone()["n"] == 2

    # /boekingen/<id> gaat naar het factuurscherm (F9)
    assert booker.get(f"/boekingen/{sid}", follow_redirects=False).headers["location"] == f"/verkoopfactuur/{sid}"


def test_calc_and_stock_warning(booker):
    page = start(booker, "K001")
    page = add_item(booker, page, "A-1")
    n = 1
    r = step(booker, "/verkoopfactuur", {f"qty_{n}": "3", f"sellprice_{n}": "5", "action": "calc"}, page)
    assert 'data-lt="1">15,00<' in r.text and 'id="is-calc"' in r.text
    m = re.search(r'name="qty_1"[^>]*>(.{0,200})', r.text, re.S)
    assert "voorraad 0" in r.text, m.group(1) if m else r.text[-3000:]   # F4: waarschuwing, geen weigering


def test_unknown_item(booker):
    page = start(booker, "K001")
    r = step(booker, "/verkoopfactuur", {"item_1": "bestaat niet", "action": "update"}, page)
    assert "Geen artikel gevonden" in r.text
    r = step(booker, "/verkoopfactuur", {"item_1": "appel", "action": "update"}, page)
    assert "Meer dan één artikel gevonden" in r.text


def test_credit_note_and_delete(booker, cdb):
    page = start(booker, "Graanhandel", prefix="inkoopfactuur")
    page = add_item(booker, page, "A-1", prefix="inkoopfactuur", qty="5", sellprice="2")
    posted_id(step(booker, "/inkoopfactuur", {"action": "post"}, page), "inkoopfactuur")
    page = start(booker, "K001")
    page = add_item(booker, page, "A-1", qty="2")
    sid = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    page = start(booker, "K001", query="?creditnota=1")
    assert 'name="type" value="credit_invoice"' in page
    page = add_item(booker, page, "A-1", qty="1")
    cid = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    rows = acc(cdb, cid)
    assert ("8520", Decimal("-5.00")) in rows and ("7001", Decimal("-2.00")) in rows    # F5: retour tegen kostprijs
    page = booker.get(f"/verkoopfactuur/{cid}").text
    r = step(booker, "/verkoopfactuur", {"action": "delete"}, page)
    assert "verwijderen?" in r.text
    r = step(booker, "/verkoopfactuur", {"action": "delete_confirmed"}, r.text)
    assert r.status_code == 303 and "invoice_deleted" in r.headers["location"]
    assert not cdb.execute("SELECT 1 FROM ar WHERE id = %s", (cid,)).fetchall()
    assert cdb.execute("SELECT onhand FROM parts WHERE id = 2001").fetchone()["onhand"] == 3


def test_readonly(reader):
    page = reader.get("/verkoopfactuur/3007").text                      # seed: factuur met artikelen
    assert "alleen lezen" in page and 'value="post"' not in page


def test_closed_period(booker, cdb):
    cdb.execute("UPDATE defaults SET fldvalue = '20260630' WHERE fldname = 'closedto'")
    page = booker.get("/verkoopfactuur/3001").text                       # 2026-06-25
    assert "afgesloten periode" in page and 'name="action" value="post"' not in page


def test_tax_settings_warning(booker, cdb):
    page = start(booker, "K001")
    page = add_item(booker, page, "A-1", qty="1")
    sid = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    cdb.execute("DELETE FROM partstax WHERE parts_id = 2001")            # btw van het artikel later gewijzigd
    page = booker.get(f"/verkoopfactuur/{sid}").text
    assert "Let op: met de huidige btw-instellingen" in page and "5,00" in page and "6,05" in page


def test_english(booker, db):
    db.execute("UPDATE app_user SET language = 'en' WHERE login = 'boek'")
    page = booker.get("/verkoopfactuur/nieuw").text
    assert "Add sales invoice" in page and "Delivery date" in page


def test_item_search_list_options(booker, monkeypatch):
    """Zoeklijst artikelen (PB17): nummer en omschrijving apart; boven het maximum het aantal dat er nog is."""
    from sqlledger_web import routes_is
    r = booker.get("/verkoopfactuur/artikelen", params={"text": "appel"})
    assert 'value="A-1 Appels" data-n="A-1" data-name="Appels"' in r.text and "data-more" not in r.text
    monkeypatch.setattr(routes_is, "OPTIONS_MAX", 1)
    r = booker.get("/verkoopfactuur/artikelen", params={"text": "appel"})
    assert r.text.count("data-n=") == 1 and '<option data-more="1" value="" disabled>' in r.text
    page = booker.get("/verkoopfactuur/nieuw").text
    assert '<datalist id="itemlist" data-remote></datalist>' in page


def test_item_label_with_spaces_in_number(booker, cdb):
    """PB17: de zoeklijst zet het label "nummer omschrijving"; ook een nummer met spaties (en een spatie aan het
    eind, zoals bij Cookie) geeft precies dat artikel en niet "Geen/Meer dan één artikel gevonden"."""
    cdb.execute("""INSERT INTO parts (id, partnumber, description, unit, sellprice, income_accno_id, expense_accno_id)
                   SELECT 2003 + n, '100 enkel stuks ', 'Koek ' || n, 'st', 1, (SELECT id FROM chart WHERE accno = '8520'),
                          (SELECT id FROM chart WHERE accno = '8020') FROM generate_series(0, 1) n""")
    page = start(booker, "Bakkerij")
    page = add_item(booker, page, "100 enkel stuks  Koek 1")
    assert 'name="description_1" value="Koek 1"' in page and "artikel gevonden" not in page
    r = booker.get("/verkoopfactuur/artikelen", params={"text": "enkel"})
    assert 'data-n="100 enkel stuks " data-name="Koek 0"' in r.text


def test_vendor_label_with_spaces_in_number(booker, cdb):
    cdb.execute("UPDATE vendor SET vendornumber = 'Per Incasso' WHERE id = (SELECT min(id) FROM vendor)")
    name = cdb.execute("SELECT name FROM vendor WHERE vendornumber = 'Per Incasso'").fetchone()["name"]
    page = start(booker, f"Per Incasso {name}", prefix="inkoopfactuur")
    assert f'name="vc_text" value="Per Incasso {name}"' in page and "gevonden" not in page


def test_vc_discount_on_new_lines(booker, cdb):
    """VW7: korting % van de klant/leverancier wordt de regelkorting van een nieuw gekozen artikel (is.pl/ir.pl);
    een zelf ingevulde korting en bestaande regels blijven staan."""
    cdb.execute("UPDATE customer SET discount = 0.1 WHERE customernumber = 'K001'")
    cdb.execute("UPDATE vendor SET discount = 0.025 WHERE name LIKE 'Graanhandel%'")
    page = start(booker, "K001")
    page = add_item(booker, page, "A-1", qty="2")
    assert 'name="discount_1" value="10"' in page and "10,89" in page        # 2 × 5 − 10% + btw
    page = step(booker, "/verkoopfactuur", {"discount_2": "3", "item_2": "A-2", "action": "update"}, page).text
    assert 'name="discount_2" value="3"' in page                             # eigen korting gaat voor
    r = step(booker, "/verkoopfactuur", {"action": "post"}, page)
    sid = posted_id(r)
    rows = cdb.execute("SELECT parts_id, discount FROM invoice WHERE trans_id = %s ORDER BY parts_id",
                       (sid,)).fetchall()
    assert [(x["parts_id"], round(x["discount"], 3)) for x in rows] == [(2001, 0.1), (2002, 0.03)]

    # bestaande factuur: korting van de klant wijzigen raakt de regels niet
    cdb.execute("UPDATE customer SET discount = 0.2 WHERE customernumber = 'K001'")
    page = booker.get(f"/verkoopfactuur/{sid}").text
    assert 'name="discount_1" value="10"' in page and 'name="discount_2" value="3"' in page
    page = step(booker, "/verkoopfactuur", {"action": "update"}, page).text
    assert 'name="discount_1" value="10"' in page and 'name="discount_2" value="3"' in page

    page = start(booker, "Graanhandel", prefix="inkoopfactuur")
    page = add_item(booker, page, "A-1", prefix="inkoopfactuur", qty="1")
    assert 'name="discount_1" value="2,5"' in page

    # zonder korting op de klant: regelkorting blijft leeg
    cdb.execute("UPDATE customer SET discount = NULL WHERE customernumber = 'K001'")
    page = add_item(booker, start(booker, "K001"), "A-1")
    assert 'name="discount_1" value=""' in page
