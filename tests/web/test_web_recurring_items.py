"""Herhalen van facturen met artikelen en orders in de webapp (fase 3 R1, RH1–RH6)."""

import re
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from test_web_arap import page_fields, step
from test_web_is import add_item, posted_id, setup  # noqa: F401  (testdata: artikelen A-1/A-2)
from test_web_is import start as start_invoice
from test_web_oe import saved_id
from test_web_oe import start as start_order
from test_web_recurring import save, schedule, text
from webhelpers import csrf, login, make_user


@pytest.fixture
def cdb(settings, company_db):
    with psycopg.connect(**settings.company_conninfo(company_db), autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def purchase_stock(booker, qty="10"):
    page = start_invoice(booker, "Groothandel", prefix="inkoopfactuur")
    page = add_item(booker, page, "A-1", prefix="inkoopfactuur", qty=qty, sellprice="2")
    return posted_id(step(booker, "/inkoopfactuur", {"action": "post"}, page), prefix="inkoopfactuur")


def process(booker, tid):
    page = booker.get("/terugkerend").text
    # "toch verwerken" (v1.0.1 PB16): de schema's hier beginnen in juli 2026 en lopen inmiddels meer dan één
    # termijn achter; verwerken boekt er nog steeds één
    fields = {**page_fields(page), "csrf": csrf(booker, page), "sel": [str(tid)], "behind_ok": [str(tid)]}
    r = booker.post("/terugkerend", data=fields)
    assert r.status_code == 200
    return text(r)


def test_sale_invoice_button_schedule_and_process(booker, cdb):
    new = booker.get("/verkoopfactuur/nieuw").text
    assert "Sla de factuur eerst op" in new
    purchase_stock(booker)
    page = start_invoice(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", qty="3")
    tid = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    page = booker.get(f"/verkoopfactuur/{tid}").text
    assert f'href="/herhalen/{tid}">Herhalen</a>' in page
    sp = booker.get(f"/herhalen/{tid}").text
    assert "Verkoopfactuur" in sp and 'name="payment"' in sp and "voorraad controleren" in sp
    r = save(booker, tid, startdate="2026-07-01", howmany="3", reference="REC-1")
    assert r.headers["location"] == f"/verkoopfactuur/{tid}?ok=recurring_saved"
    assert schedule(cdb, tid)["nextdate"] == date(2026, 8, 1)
    assert "Herhaalschema actief" in booker.get(f"/verkoopfactuur/{tid}").text
    lst = booker.get("/terugkerend").text
    assert f'href="/verkoopfactuur/{tid}"' in lst
    t = process(booker, tid)
    assert "Verwerkt: 1, overgeslagen: 0" in t
    m = re.search(r'geboekt <a href="/verkoopfactuur/(\d+)">REC-1</a>', t)
    assert m, t[:3000]
    copy = cdb.execute("SELECT transdate, amount FROM ar WHERE id = %s", (int(m.group(1)),)).fetchone()
    assert copy == {"transdate": date(2026, 8, 1), "amount": 18.15}
    assert cdb.execute("SELECT onhand FROM parts WHERE id = 2001").fetchone()["onhand"] == 4
    assert "Factuur" in booker.get(f"/verkoopfactuur/{m.group(1)}").text


def test_inventory_skip_message(booker, cdb):
    cdb.execute("UPDATE parts SET checkinventory = true WHERE id = 2001")
    purchase_stock(booker, qty="3")
    page = start_invoice(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", qty="3")
    tid = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    save(booker, tid, startdate="2026-07-01", howmany="2")
    t = process(booker, tid)
    assert "Onvoldoende voorraad in regel 1" in t and "Verwerkt: 0, overgeslagen: 1" in t
    assert schedule(cdb, tid)["nextdate"] == date(2026, 8, 1)


def test_order_schedule_and_process(booker, cdb):
    assert "Sla de order eerst op" in booker.get("/verkooporder/nieuw").text
    page = start_order(booker, "Bakkerij")
    page = add_item(booker, page, "A-1", prefix="verkooporder", qty="4")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))
    assert f'href="/herhalen/{oid}">Herhalen</a>' in booker.get(f"/verkooporder/{oid}").text
    sp = booker.get(f"/herhalen/{oid}").text
    assert "Verkooporder" in sp and 'name="payment"' not in sp and "nieuwe open order" in sp
    r = save(booker, oid, startdate="2026-07-01", howmany="2")
    assert r.headers["location"] == f"/verkooporder/{oid}?ok=recurring_saved"
    cdb.execute("UPDATE oe SET closed = true WHERE id = %s", (oid,))
    page = booker.get(f"/verkooporder/{oid}").text
    assert "alleen lezen" in page and f'href="/herhalen/{oid}">Herhalen</a>' in page   # gesloten: sjabloon
    t = process(booker, oid)
    m = re.search(r'geboekt <a href="/verkooporder/(\d+)">', t)
    assert m, t[:3000]
    copy = cdb.execute("SELECT transdate, closed, amount FROM oe WHERE id = %s", (int(m.group(1)),)).fetchone()
    assert copy == {"transdate": date(2026, 8, 1), "closed": False, "amount": 24.2}


def test_quotation_has_no_schedule(booker, cdb):
    page = start_order(booker, "Bakkerij", prefix="offerte")
    page = add_item(booker, page, "A-1", prefix="offerte", qty="1")
    qid = saved_id(step(booker, "/offerte", {"action": "save"}, page), prefix="offerte")
    assert "/herhalen/" not in booker.get(f"/offerte/{qid}").text
    sp = booker.get(f"/herhalen/{qid}").text
    assert "zet ze eerst om naar een order" in sp and 'name="startdate"' not in sp
    assert save(booker, qid, startdate="2026-07-01", howmany="2").status_code == 400
    assert schedule(cdb, qid) is None
