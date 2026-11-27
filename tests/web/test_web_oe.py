"""Orders en offertes in de webapp (fase 3 stap 3, O1–O6, ON1–ON4)."""

import re

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_arap import step
from test_web_is import add_item, posted_id, setup  # noqa: F401  (testdata: artikelen A-1/A-2)
from webhelpers import login, make_user, nohelp


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


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


def start(client, vc_text, prefix="verkooporder"):
    page = client.get(f"/{prefix}/nieuw").text
    r = step(client, f"/{prefix}", {"vc_text": vc_text, "transdate": "2026-07-01", "action": "update"}, page)
    assert r.status_code == 200, r.text[:2000]
    return r.text


def saved_id(r, prefix="verkooporder"):
    assert r.status_code == 303, re.sub(r"\s+", " ", r.text)[:3000]
    m = re.fullmatch(rf"/{prefix}/(\d+)\?ok=order_saved", r.headers["location"])
    assert m, r.headers["location"]
    return int(m.group(1))


def test_menu_and_new_forms(booker):
    home = booker.get("/").text
    for url in ("/verkooporder/nieuw", "/inkooporder/nieuw", "/offerte/nieuw", "/offerteaanvraag/nieuw",
                "/verkooporders", "/offertes"):
        assert f'href="{url}"' in home
    assert "Verkooporder toevoegen" in booker.get("/verkooporder/nieuw").text
    assert "Offerteaanvraag toevoegen" in booker.get("/offerteaanvraag/nieuw").text
    assert 'value="A-1 Appels"' in booker.get("/offerte/artikelen", params={"text": "appel"}).text


def test_order_save_no_stock_and_list(booker, cdb):
    page = start(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="4")
    assert 'name="sellprice_1" value="5,00"' in page and "24,20" in page
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    r = cdb.execute("SELECT amount, netamount, closed, ordnumber FROM oe WHERE id = %s", (oid,)).fetchone()
    assert (r["amount"], r["netamount"], r["closed"]) == (24.2, 20.0, False)
    assert cdb.execute("SELECT onhand FROM parts WHERE id = 2001").fetchone()["onhand"] == 0
    page = booker.get(f"/verkooporder/{oid}").text
    assert "Verkooporder wijzigen" in page and "Opslaan en omzetten naar" in page
    lst = booker.get("/verkooporders").text
    assert f'href="/verkooporder/{oid}"' in lst and "24,20" in lst
    csv = booker.get("/verkooporders", params={"run": "1", "open": "1", "export": "csv"})
    assert csv.status_code == 200 and r["ordnumber"] in csv.text


def test_quotation_to_order_to_partial_invoice(booker, cdb):
    page = start(booker, "Bakkerij", prefix="offerte")
    page = add_item(booker, page, "A-1", prefix="offerte", qty="5")
    page = add_item(booker, page, "A-2", prefix="offerte", qty="2")
    qid = saved_id(step(booker, "/offerte", {"action": "save"}, page), prefix="offerte")
    # omzetten naar order: offerte pas dicht bij opslaan
    page = booker.get(f"/offerte/{qid}").text
    r = step(booker, "/offerte", {"action": "to_sales_order"}, page)
    assert r.status_code == 303 and r.headers["location"] == f"/verkooporder/nieuw?van={qid}"
    page = booker.get(r.headers["location"]).text
    assert "Omgezet uit" in page and 'name="source_id" value="%d"' % qid in page
    assert cdb.execute("SELECT closed FROM oe WHERE id = %s", (qid,)).fetchone()["closed"] is False
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    assert cdb.execute("SELECT closed FROM oe WHERE id = %s", (qid,)).fetchone()["closed"] is True
    assert "alleen lezen" in booker.get(f"/offerte/{qid}").text
    # factuur maken: 3 van de 5 appels, appelmoes niet
    page = booker.get(f"/verkooporder/{oid}").text
    r = step(booker, "/verkooporder", {"action": "to_invoice"}, page)
    assert r.headers["location"] == f"/verkoopfactuur/nieuw?order={oid}"
    page = booker.get(r.headers["location"]).text
    assert "Factuur voor verkooporder" in page and f'name="order_id" value="{oid}"' in page
    n = re.search(r'name="orderitems_id_1" value="(\d+)"', page)
    assert n
    r = step(booker, "/verkoopfactuur", {"qty_1": "3", "item_2": "", "qty_2": "", "description_2": "",
                                         "parts_id_2": "", "action": "post"}, page)
    tid = posted_id(r)
    o = cdb.execute("SELECT closed, aa_id FROM oe WHERE id = %s", (oid,)).fetchone()
    assert o["closed"] and o["aa_id"] == tid
    bo = cdb.execute("SELECT id FROM oe WHERE backorder").fetchone()["id"]
    items = cdb.execute("SELECT parts_id, qty FROM orderitems WHERE trans_id = %s ORDER BY id", (bo,)).fetchall()
    assert [(i["parts_id"], i["qty"]) for i in items] == [(2001, 2.0), (2002, 2.0)]
    page = booker.get(f"/verkooporder/{oid}").text
    assert "gefactureerd" in page and f'href="/verkoopfactuur/{tid}"' in page and "Heropenen" not in nohelp(page)
    assert "nabestelling" in booker.get(f"/verkooporder/{bo}").text
    # factuur verwijderen: order open, nabestelling weg
    page = booker.get(f"/verkoopfactuur/{tid}").text
    r = step(booker, "/verkoopfactuur", {"action": "delete_confirmed"}, page)
    assert r.headers["location"].endswith("ok=invoice_deleted")
    assert cdb.execute("SELECT closed, aa_id FROM oe WHERE id = %s", (oid,)).fetchone() == {"closed": False, "aa_id": None}
    assert cdb.execute("SELECT count(*) AS n FROM oe WHERE id = %s", (bo,)).fetchone()["n"] == 0


def test_closed_order_reopen(booker, cdb):
    page = start(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="1")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    cdb.execute("UPDATE oe SET closed = true WHERE id = %s", (oid,))
    page = booker.get(f"/verkooporder/{oid}").text
    assert "Deze order is gesloten." in page and "Heropenen" in page and 'value="save"' not in page
    # vervalst formulier (velden staan uit): de server weigert
    r = step(booker, "/verkooporder", {"action": "save", "rowcount": "1", "item_1": "A-1 Appels", "item_shown_1": "A-1 Appels",
                                       "parts_id_1": "2001", "qty_1": "2", "sellprice_1": "5",
                                       "vc_text": re.search(r'name="vc_shown" value="([^"]*)"', page).group(1),
                                       "transdate": "2026-07-01"}, page)
    assert r.status_code == 400 and "heropen hem eerst" in r.text, re.findall(r"class=\"error\"[^>]*>([^<]*)", r.text)
    r = step(booker, "/verkooporder", {"action": "reopen"}, page)
    assert r.headers["location"] == f"/verkooporder/{oid}?ok=order_reopened"
    assert cdb.execute("SELECT closed FROM oe WHERE id = %s", (oid,)).fetchone()["closed"] is False


def test_purchase_order_to_vendor_invoice(booker, cdb):
    page = start(booker, "Graanhandel", prefix="inkooporder")
    page = add_item(booker, page, "A-1", prefix="inkooporder", qty="10", sellprice="2,00")
    oid = saved_id(step(booker, "/inkooporder", {"action": "save"}, page), prefix="inkooporder")
    page = booker.get(f"/inkooporder/{oid}").text
    r = step(booker, "/inkooporder", {"action": "to_invoice"}, page)
    page = booker.get(r.headers["location"]).text
    tid = posted_id(step(booker, "/inkoopfactuur", {"invnumber": "F-1", "action": "post"}, page), prefix="inkoopfactuur")
    assert cdb.execute("SELECT aa_id FROM oe WHERE id = %s", (oid,)).fetchone()["aa_id"] == tid
    assert cdb.execute("SELECT onhand FROM parts WHERE id = 2001").fetchone()["onhand"] == 10


def test_delete_order(booker, cdb):
    page = start(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="1")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    page = booker.get(f"/verkooporder/{oid}").text
    r = step(booker, "/verkooporder", {"action": "delete"}, page)
    assert "verwijderen?" in r.text
    r = step(booker, "/verkooporder", {"action": "delete_confirmed"}, r.text)
    assert r.headers["location"] == "/verkooporder/nieuw?ok=order_deleted"
    assert cdb.execute("SELECT count(*) AS n FROM oe WHERE id = %s", (oid,)).fetchone()["n"] == 0


def test_reader_readonly(booker, cdb, client, db, company):
    page = start(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="1")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    make_user(db, "lees", memberships=[(company["id"], "readonly", "lees")])
    login(client, "lees")
    page = client.get(f"/verkooporder/{oid}").text
    assert "alleen-lezen" in page and 'value="save"' not in page
    assert client.get("/verkooporder/nieuw").status_code == 403


def test_vc_discount_on_new_order_lines(booker, cdb):
    """VW7: ook op orders en offertes (oe.pl) de korting % van de relatie op nieuwe regels."""
    cdb.execute("UPDATE customer SET discount = 0.05 WHERE name LIKE 'Bakkerij%'")
    for prefix in ("verkooporder", "offerte"):
        page = add_item(booker, start(booker, "Bakkerij", prefix=prefix), "A-1", prefix=prefix, qty="4")
        assert 'name="discount_1" value="5"' in page and "22,99" in page    # 20 − 5% = 19 + 21% btw
    page = add_item(booker, start(booker, "Bakkerij"), "A-1", prefix="verkooporder", qty="4")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    assert cdb.execute("SELECT discount FROM orderitems WHERE trans_id = %s", (oid,)).fetchone()["discount"] == 0.05
