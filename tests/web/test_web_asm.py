"""Samengestelde artikelen in de webapp (fase 3, SA1–SA12): stuklijst in het artikelscherm,
assembleren/demonteren, stuklijsten, waar gebruikt, hoeveel kan ik maken, productiehistorie."""

import re

from test_web_ic import add_part, booker, cdb, fields, ok, reader, saved_id, step  # noqa: F401
from webhelpers import csrf


def build(client, pid, page, data):
    return client.post(f"/artikelen/{pid}/assembleren", data={**fields(page), "csrf": csrf(client, page), **data},
                       follow_redirects=False)


def add_assembly(client, rows, **kw):
    """Samengesteld artikel via het scherm: per onderdeel nummer invullen + Bijwerken."""
    page = ok(client.get("/artikelen/nieuw?soort=samengesteld"))
    for i, (number, qty) in enumerate(rows, start=1):
        page = ok(step(client, page, {"partnumber": kw.get("partnumber", "P-1"), "description": "Taartpakket",
                                      f"comp_find_{i}": number, f"comp_qty_{i}": qty, "action": "update"}))
    return saved_id(step(client, page, {"income_accno": "8520", "action": "save", **kw}))


def test_add_assembly_with_bom(booker, cdb):
    a = add_part(booker, partnumber="O-1", sellprice="2,50", lastcost="1,10")
    b = add_part(booker, partnumber="O-2", sellprice="1,25", lastcost="0,40")
    html = ok(booker.get("/artikelen/nieuw?soort=samengesteld"))
    assert "Samengesteld artikel toevoegen" in html and 'name="inventory_accno"' not in html
    assert 'name="expense_accno"' not in html and 'name="comp_find_1"' in html
    pid = add_assembly(booker, [("O-1", "2"), ("o-2", "1,5")])
    row = cdb.execute("SELECT assembly, sellprice, lastcost, inventory_accno_id FROM parts WHERE id = %s",
                      (pid,)).fetchone()
    assert (row["assembly"], row["sellprice"], row["lastcost"], row["inventory_accno_id"]) == (True, 6.88, 2.8, None)
    comps = cdb.execute("SELECT parts_id, qty FROM assembly WHERE aid = %s ORDER BY id", (pid,)).fetchall()
    assert [(c["parts_id"], c["qty"]) for c in comps] == [(a, 2), (b, 1.5)]
    html = ok(booker.get(f"/artikelen/{pid}"))
    assert "Samengesteld artikel wijzigen" in html and "Totaal per stuk" in html and "6,88" in html
    assert f'href="/artikelen/{pid}/assembleren"' in html
    before = cdb.execute("SELECT * FROM assembly ORDER BY id").fetchall()
    assert saved_id(step(booker, html, {"action": "save"})) == pid          # ongewijzigd
    assert cdb.execute("SELECT * FROM assembly ORDER BY id").fetchall() == before
    # aantal 0 haalt een onderdeel weg; eigen verkoopprijs
    saved_id(step(booker, html, {"comp_qty_2": "0", "own_sellprice": "1", "sellprice": "9,95", "action": "save"}))
    row = cdb.execute("SELECT sellprice, lastcost FROM parts WHERE id = %s", (pid,)).fetchone()
    assert (row["sellprice"], row["lastcost"]) == (9.95, 2.2)
    assert cdb.execute("SELECT count(*) AS n FROM assembly WHERE aid = %s", (pid,)).fetchone()["n"] == 1
    assert ok(booker.get(f"/artikelen/{a}")).count("Waar gebruikt") == 1


def test_component_search_choices_and_errors(booker, cdb):
    add_part(booker, partnumber="AB-1")
    add_part(booker, partnumber="AB-2")
    page = ok(booker.get("/artikelen/nieuw?soort=samengesteld"))
    html = ok(step(booker, page, {"description": "Pakket", "comp_find_1": "AB", "action": "update"}))
    assert "Meer artikelen gevonden voor “AB”: kies er een." in html and 'name="comp_pick_1"' in html
    choice = re.search(r'<option value="(\d+)">AB-2', html).group(1)
    html = ok(step(booker, html, {"comp_pick_1": choice, "action": "update"}))
    assert f'name="comp_id_1" value="{choice}"' in html
    r = step(booker, html, {"comp_find_2": "bestaatniet", "action": "save", "income_accno": "8520"})
    assert r.status_code == 400 and "Geen artikel gevonden voor “bestaatniet”." in r.text
    r = step(booker, page, {"description": "Pakket", "income_accno": "8520", "action": "save"})
    assert r.status_code == 400 and "Voeg minstens één onderdeel toe." in r.text


def test_bom_locked_with_stock_and_build(booker, cdb):
    a = add_part(booker, partnumber="O-1")
    b = add_part(booker, partnumber="O-2")
    pid = add_assembly(booker, [("O-1", "2"), ("O-2", "1")])
    cdb.execute("UPDATE parts SET onhand = 5 WHERE id = %s", (a,))
    cdb.execute("UPDATE parts SET onhand = 1 WHERE id = %s", (b,))
    page = ok(booker.get(f"/artikelen/{pid}/assembleren?aantal=2"))
    assert "Onderdelen voor 2 stuk(s)" in page and "Te weinig voorraad van:" in page and "O-2 (Taart)" in page
    assert "(2)" not in page                    # beweging met minteken, niet tussen haakjes
    r = build(booker, pid, page, {"qty": "2", "reference": "W-1", "action": "build"})
    html = ok(r)                               # waarschuwing, nog niets gedaan
    assert "Ja, toch assembleren" in html
    assert cdb.execute("SELECT onhand FROM parts WHERE id = %s", (pid,)).fetchone()["onhand"] == 0
    r = build(booker, pid, html, {"action": "build_confirmed"})
    assert r.status_code == 303 and r.headers["location"].endswith("ok=asm_built")
    onhand = {x: cdb.execute("SELECT onhand FROM parts WHERE id = %s", (x,)).fetchone()["onhand"]
              for x in (a, b, pid)}
    assert onhand == {a: 1, b: -1, pid: 2}
    html = ok(booker.get(f"/artikelen/{pid}/assembleren?ok=asm_built"))
    assert "Geassembleerd." in html and "W-1" in html
    # stuklijst ligt vast zolang er voorraad is
    form = ok(booker.get(f"/artikelen/{pid}"))
    assert "liggen vast" in form and 'name="comp_find_' not in form
    # demonteren tot de voorraad
    r = build(booker, pid, html, {"mode": "unbuild", "qty": "3", "action": "build"})
    assert r.status_code == 400 and "Demonteren kan niet verder dan de voorraad van het product (2)." in r.text
    r = build(booker, pid, html, {"mode": "unbuild", "qty": "2", "action": "build"})
    assert r.headers["location"].endswith("ok=asm_unbuilt")
    assert cdb.execute("SELECT onhand FROM parts WHERE id = %s", (pid,)).fetchone()["onhand"] == 0
    # checkinventory blokkeert
    cdb.execute("UPDATE parts SET checkinventory = true WHERE id = %s", (b,))
    page = ok(booker.get(f"/artikelen/{pid}/assembleren"))
    r = build(booker, pid, page, {"qty": "5", "action": "build"})
    assert r.status_code == 400 and "wordt de voorraad gecontroleerd" in r.text


def test_lists(booker, cdb):
    a = add_part(booker, partnumber="O-1", lastcost="1")
    sub = add_assembly(booker, [("O-1", "3")], partnumber="S-1")
    top = add_assembly(booker, [("S-1", "2"), ("O-1", "1")], partnumber="T-1")
    cdb.execute("UPDATE parts SET onhand = 7 WHERE id = %s", (a,))
    html = ok(booker.get("/artikelen-assembleren"))
    assert "Te maken" in html and f'href="/artikelen/{top}/assembleren"' in html
    html = ok(booker.get(f"/artikelen/{top}/stuklijst?aantal=2"))
    assert "S-1" in html and "O-1" in html and ">12<" in html            # 2 × 2 × 3
    html = ok(booker.get("/artikelen-stuklijsten?run=1&top=1"))
    assert "T-1" in html and f'href="/artikelen/{sub}"' in html
    r = booker.get("/artikelen-stuklijsten?run=1&export=csv")
    assert r.status_code == 200 and "T-1" in r.text and r.headers["content-type"].startswith("text/csv")
    assert booker.get("/artikelen-assembleren?export=xlsx").status_code == 200
    html = ok(booker.get(f"/artikelen/{a}/waar-gebruikt"))
    assert "S-1" in html and "T-1" in html
    page = ok(booker.get(f"/artikelen/{sub}/assembleren"))
    assert build(booker, sub, page, {"qty": "1", "notes": "proef", "action": "build"}).status_code == 303
    html = ok(booker.get("/artikelen-productie"))
    assert "proef" in html and "assembleren" in html
    build_id = re.search(r'href="/artikelen-productie/(\d+)"', html).group(1)
    html = ok(booker.get(f"/artikelen-productie/{build_id}"))
    assert "O-1" in html and "proef" in html
    assert booker.get("/artikelen-productie/99999").status_code == 404
    assert booker.get(f"/artikelen/{a}/assembleren").status_code == 404


def test_menu_and_reader(reader, cdb):
    html = ok(reader.get("/artikelen-assembleren"))
    assert "Stuklijsten" in html and "Productiehistorie" in html
    assert reader.get("/artikelen/nieuw?soort=samengesteld").status_code == 403
