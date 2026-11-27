"""Documenten > Batch met facturen en orders uit de schermen (fase 4 stap 4c): factuur, pakbon uit factuur én
verkooporder, samengevoegde PDF met de pagina's van elk document (SB14)."""

import re
import zlib

import pytest

from test_web_arap import step
from test_web_batch import listed, post
from test_web_is import add_item, cdb, posted_id, setup, start  # noqa: F401 (fixtures)
from test_web_oe import saved_id, start as oe_start
from webhelpers import login, make_user

URL = "/documenten/batch"
ALL = {"van": "", "tot": "", "relatie": "", "nummer": "", "staat": "alle", "geprint": "alle", "gemaild": "alle"}


@pytest.fixture
def booker(client, db, company):
    make_user(db, "boek", memberships=[(company["id"], "bookkeeper", "jan")])
    login(client, "boek")
    return client


def pages(pdf: bytes) -> int:
    """Aantal pagina's (WeasyPrint zet de objecten in gecomprimeerde objectstromen)."""
    parts = [pdf]
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        try:
            parts.append(zlib.decompress(m.group(1)))
        except zlib.error:
            pass
    return sum(len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", p)) for p in parts)


def test_invoices_and_packing_lists(booker, cdb):
    page = add_item(booker, start(booker, "K001"), "A-1", qty="2")
    inv1 = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    page = add_item(booker, start(booker, "K002"), "A-2", qty="1")
    inv2 = posted_id(step(booker, "/verkoopfactuur", {"action": "post"}, page))
    page = add_item(booker, oe_start(booker, "Bakkerij"), "A-1", prefix="verkooporder", qty="4")
    oid = saved_id(step(booker, "/verkooporder", {"action": "save"}, page))

    page = booker.get(URL, params={**ALL, "soort": "invoice"}).text
    assert inv1 in listed(page) and inv2 in listed(page) and oid not in listed(page)
    one = post(booker, page, "preview", [inv1])
    both = post(booker, page, "pdf", [inv1, inv2])
    assert both.status_code == 200 and pages(one.content) >= 1
    assert pages(both.content) == pages(one.content) + pages(post(booker, page, "preview", [inv2]).content)
    st = {r["trans_id"]: r["printed"] for r in cdb.execute(
        "SELECT trans_id, printed FROM status WHERE formname = 'invoice' AND trans_id = ANY(%s)", ([inv1, inv2],))}
    assert st == {inv1: True, inv2: True}

    # pakbon: uit verkoopfacturen en verkooporders, met een label "order"; status apart per soort
    page = booker.get(URL, params={**ALL, "soort": "packing_list"}).text
    assert {inv1, inv2, oid} <= set(listed(page))
    assert f'href="/verkooporder/{oid}"' in page and ">order</span>" in page
    r = post(booker, page, "pdf", [oid, inv1])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    rows = cdb.execute("SELECT trans_id, formname, printed FROM status WHERE formname = 'packing_list' "
                       "ORDER BY trans_id").fetchall()
    assert [(r["trans_id"], r["printed"]) for r in rows] == sorted([(inv1, True), (oid, True)])
    page = booker.get(URL, params={**ALL, "soort": "packing_list", "geprint": "nee"}).text
    assert inv2 in listed(page) and inv1 not in listed(page) and oid not in listed(page)

    # verkooporder als eigen soort
    page = booker.get(URL, params={**ALL, "soort": "sales_order"}).text
    assert listed(page) == [oid]
    assert post(booker, page, "preview", [oid]).content.startswith(b"%PDF")
